# SPDX-License-Identifier: MIT
"""A large decision file keeps its earlier versions as deltas from the latest one kept
whole (cartolex.project.files): every version comes back byte for byte, a hand edit of
the current file breaks nothing, and a version is kept whole every FULL_EVERY."""

from __future__ import annotations

import pytest

from cartolex.project import Project
from cartolex.project.files import (
    DELTA_SUFFIX,
    FULL_EVERY,
    HistoryBroken,
    fingerprint,
    history_versions,
    read_version,
    write_decision,
)


def _csv(n: int, changed: dict[int, str] | None = None) -> bytes:
    rows = ["person_id,role,note"]
    for i in range(n):
        note = (changed or {}).get(i, f"note {i}")
        rows.append(f'p{i:06d},mapped,"{note}"')
    return ("\n".join(rows) + "\n").encode("utf-8")


@pytest.fixture()
def project(tmp_path):
    project = Project.init(tmp_path / "p", name="History", domain_title="History")
    yield project
    project.close()


def _write(project, data: bytes, action: str) -> None:
    path = project.layout.people_csv
    write_decision(project.layout, path, data, expected=fingerprint(path), action=action)


def test_every_earlier_version_comes_back_from_its_delta(project):
    path = project.layout.people_csv
    versions = [_csv(20_000)]
    assert len(versions[0]) > 256 * 1024
    _write(project, versions[0], "import")
    # small edits, one with a note over two lines (a quoted field)
    for k in range(1, 6):
        versions.append(_csv(20_000, {k * 7: f"edit {k}", 9: "two\nlines" if k > 2 else "x"}))
        _write(project, versions[-1], f"edit {k}")
    entries = history_versions(project.layout.history_of(path), path)
    assert [e[2] for e in entries] == [False, True, True, True, True]  # the first kept whole
    assert all(e[1].stat().st_size < 50_000 for e in entries[1:])
    for (version, _file, _delta), expected in zip(entries, versions, strict=False):
        assert read_version(project.layout, path, version) == expected
    # a hand edit of the current file breaks nothing
    path.write_bytes(_csv(20_000, {1: "by hand"}))
    for (version, _file, _delta), expected in zip(entries, versions, strict=False):
        assert read_version(project.layout, path, version) == expected


def test_a_version_is_kept_whole_every_so_often_or_when_a_delta_saves_little(project, monkeypatch):
    from cartolex.project import files

    monkeypatch.setattr(files, "FULL_EVERY", 4)
    path = project.layout.people_csv
    for k in range(7):
        _write(project, _csv(20_000, {k: f"edit {k}"}), f"edit {k}")
    kinds = [e[2] for e in history_versions(project.layout.history_of(path), path)]
    assert kinds == [False, True, True, True, False, True]
    # a version with nothing in common with the last one kept whole is kept whole too
    _write(project, b"person_id,role\n" + b"q,mapped\n" * 40_000, "rewrite")
    _write(project, _csv(20_000), "back")
    assert history_versions(project.layout.history_of(path), path)[-1][2] is False
    assert FULL_EVERY == 50


def test_a_damaged_history_is_said_so(project):
    path = project.layout.people_csv
    _write(project, _csv(20_000), "import")
    _write(project, _csv(20_000, {3: "x"}), "edit")
    _write(project, _csv(20_000, {4: "y"}), "edit")
    whole, delta = history_versions(project.layout.history_of(path), path)
    assert delta[1].name.endswith(".csv" + DELTA_SUFFIX)
    whole[1].write_bytes(_csv(20_000, {5: "damaged"}))
    with pytest.raises(HistoryBroken):
        read_version(project.layout, path, delta[0])
    assert read_version(project.layout, path, "no-such-version") is None


def test_a_version_kept_as_a_delta_is_read_and_restored_through_the_api(tmp_path):
    from _app_helpers import TOKEN, Client

    from cartolex.app import AppSettings, create_app

    project = Project.init(tmp_path / "p", name="History", domain_title="History")
    versions = [_csv(20_000), _csv(20_000, {3: "x"}), _csv(20_000, {4: "y"})]
    for k, data in enumerate(versions):
        _write(project, data, f"edit {k}")
    project.close()
    app = create_app(
        AppSettings(project=tmp_path / "p", launch_token=TOKEN, data_dir=tmp_path / "d")
    )
    try:
        client = Client(app)
        items = client.get("/api/snapshots?file=people.csv").json()["items"]
        assert [i["id"] for i in items][0] == "current" and len(items) == 3
        older = items[1]["id"]  # replaced by the last write: kept as a delta
        assert (
            project.layout.history_of(project.layout.people_csv) / f"{older}.csv.delta"
        ).is_file()
        read = client.get(f"/api/snapshots/people.csv/{older}").json()
        assert read["truncated"] is False and read["content"].encode("utf-8") == versions[1]
        restored = client.post(
            f"/api/snapshots/people.csv/{older}/restore",
            headers={"If-Match": f'"{items[0]["version"]}"'},
        )
        assert restored.status_code == 200, restored.text
        assert project.layout.people_csv.read_bytes() == versions[1]
    finally:
        app.state.cartolex.shutdown()
