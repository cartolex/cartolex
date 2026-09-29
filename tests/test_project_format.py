# SPDX-License-Identifier: MIT
"""The project format: creating and opening a project, the lock, guarded writes, models, tables."""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pytest

from cartolex.project import (
    LockHeld,
    NotAProject,
    Project,
    ProjectLayout,
    StaleLock,
    StaleWrite,
    UnsupportedFormat,
    fingerprint,
    json_bytes,
    remove_stale_lock,
    write_decision,
)
from cartolex.project.models import (
    MapsFile,
    ParamsFile,
    ProjectFile,
    RunRecord,
    ThemesFile,
)
from cartolex.project.schemas import main as schemas_main
from cartolex.project.tables import (
    SOURCE_SCHEMAS,
    TableError,
    decision_csv_bytes,
    empty_table,
    read_decision_csv,
    read_source_table,
    write_source_table,
)

NOW = datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc)


def _new(tmp_path: Path, **kw) -> Project:
    return Project.init(
        tmp_path / "proj",
        name="Coastal systems map",
        domain_title="Coastal and marine systems",
        corpus_languages=("en", "fr"),
        now=NOW,
        **kw,
    )


# ── creating and opening ─────────────────────────────────────────────────────


def test_init_writes_a_valid_project_and_holds_the_lock(tmp_path):
    project = _new(tmp_path)
    layout = project.layout
    for folder in layout.skeleton():
        assert folder.is_dir()
    raw = json.loads(layout.project_json.read_text(encoding="utf-8"))
    assert raw["format"] == "cartolex-project/1"
    assert raw["identity"]["frozen"] is False
    assert raw["languages"] == {"corpus": ["en", "fr"], "reference": "en", "display": ["en", "fr"]}
    assert ParamsFile.model_validate_json(layout.params_json.read_text()).seed == 0
    assert project.writable and layout.lock.exists()
    with pytest.raises(LockHeld):
        Project.open(layout.root, write=True)
    reader = Project.open(layout.root)  # reading needs no lock
    assert reader.config.name == "Coastal systems map" and not reader.writable
    project.close()
    assert not layout.lock.exists()


def test_init_refuses_a_folder_that_is_not_empty(tmp_path):
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "notes.txt").write_text("x")
    with pytest.raises(FileExistsError):
        _new(tmp_path)


def test_open_refuses_what_is_not_a_project_or_a_newer_major(tmp_path):
    with pytest.raises(NotAProject):
        Project.open(tmp_path)
    (tmp_path / "project.json").write_text(json.dumps({"format": "something-else/1"}))
    with pytest.raises(NotAProject):
        Project.open(tmp_path)
    (tmp_path / "project.json").write_text(json.dumps({"format": "cartolex-project/2"}))
    with pytest.raises(UnsupportedFormat, match="cartolex-project/2"):
        Project.open(tmp_path)


def test_a_stale_lock_is_reported_never_stolen(tmp_path):
    project = _new(tmp_path)
    layout = project.layout
    project.close()
    dead = {"pid": 2**22 + 12345, "host": socket.gethostname(), "app": "cartolex", "since": "x"}
    layout.lock.write_text(json.dumps(dead))
    with pytest.raises(StaleLock, match="cartolex project unlock"):
        Project.open(layout.root, write=True)
    assert layout.lock.exists()
    assert remove_stale_lock(layout).pid == dead["pid"]
    Project.open(layout.root, write=True).close()


def test_a_lock_from_another_host_is_never_judged_stale(tmp_path):
    project = _new(tmp_path)
    layout = project.layout
    project.close()
    other = {"pid": 1, "host": socket.gethostname() + "-elsewhere", "app": "x", "since": "x"}
    layout.lock.write_text(json.dumps(other))
    with pytest.raises(LockHeld) as info:
        Project.open(layout.root, write=True)
    assert not isinstance(info.value, StaleLock)
    with pytest.raises(LockHeld):
        remove_stale_lock(layout)


def test_a_live_lock_cannot_be_removed(tmp_path):
    project = _new(tmp_path)
    with pytest.raises(LockHeld):
        remove_stale_lock(project.layout)
    project.close()


# ── guarded writes and history ───────────────────────────────────────────────


def test_decision_writes_are_guarded_and_keep_history(tmp_path):
    project = _new(tmp_path)
    layout = project.layout
    path = layout.keywords_csv
    fp1 = write_decision(layout, path, b"v1\n", expected=None, action="first", now=NOW)
    with pytest.raises(StaleWrite):
        write_decision(layout, path, b"v2\n", expected=None, action="blind", now=NOW)
    fp2 = write_decision(layout, path, b"v2\n", expected=fp1, action="second edit", now=NOW)
    assert path.read_bytes() == b"v2\n" and fp2 == fingerprint(path)
    kept = sorted(p.name for p in layout.history_of(path).iterdir())
    assert kept == ["20260928T100000Z-second-edit.csv"]
    # a second change in the same second gets its own file
    write_decision(layout, path, b"v3\n", expected=fp2, action="second edit", now=NOW)
    assert len(list(layout.history_of(path).iterdir())) == 2
    assert not [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]
    project.close()


def test_save_config_keeps_unknown_keys_and_refuses_stale_writes(tmp_path):
    project = _new(tmp_path)
    layout = project.layout
    raw = json.loads(layout.project_json.read_text())
    raw["from_a_newer_version"] = {"kept": True}
    layout.project_json.write_text(json.dumps(raw))
    project.close()
    project = Project.open(layout.root, write=True)
    changed = project.config.model_copy(update={"name": "Renamed"})
    project.save_config(changed, action="rename")
    raw = json.loads(layout.project_json.read_text())
    assert raw["name"] == "Renamed" and raw["from_a_newer_version"] == {"kept": True}
    assert list(layout.history_of(layout.project_json).iterdir())
    layout.project_json.write_text(layout.project_json.read_text() + " ")
    with pytest.raises(StaleWrite):
        project.save_config(changed, action="again")
    project.close()


def test_read_only_projects_refuse_writes(tmp_path):
    _new(tmp_path).close()
    project = Project.open(tmp_path / "proj")
    params, fp = project.read_params()
    with pytest.raises(PermissionError):
        project.save_params(params, expected=fp, action="x")


# ── models ───────────────────────────────────────────────────────────────────


def test_project_ids_must_be_unique_and_overlays_never_slots():
    base = json.loads(
        json_bytes(
            ProjectFile(
                name="p",
                identity={"domain_title": "d"},
                languages={"corpus": ["en"]},
                created={"at": NOW, "by": "t"},
                app={"id": "cartolex", "version": "1"},
            )
        )
    )
    ok = dict(base, slots=[{"id": "a", "kind": "folder"}], overlays=[{"id": "b", "root": "../b"}])
    ProjectFile.model_validate(ok)
    with pytest.raises(ValueError, match="twice"):
        ProjectFile.model_validate(dict(base, slots=[{"id": "a", "kind": "folder"}] * 2))
    with pytest.raises(ValueError, match="overlay cannot share"):
        ProjectFile.model_validate(
            dict(base, slots=[{"id": "a", "kind": "folder"}], overlays=[{"id": "a", "root": "x"}])
        )
    with pytest.raises(ValueError):
        ProjectFile.model_validate(dict(base, languages={"corpus": ["deu"]}))


def test_params_refuse_unknown_stages():
    ParamsFile.model_validate({"stages": {"themes.group": {"top_groups": 15}}})
    with pytest.raises(ValueError, match="unknown stage"):
        ParamsFile.model_validate({"stages": {"themes.grup": {}}})


def _themes(**kw):
    doc = {
        "depth": 2,
        "levels": [{"names": {"en": "Theme"}}, {"names": {"en": "Topic"}}],
        "nodes": [
            {"id": "n1", "parent": None, "names": {"en": "Hazards"}},
            {"id": "n7", "parent": "n1", "names": {"en": "Storm surge"}},
        ],
        "keywords": {"storm surge model": "n7"},
    }
    doc.update(kw)
    return doc


def test_theme_trees_are_checked():
    ThemesFile.model_validate(_themes())
    with pytest.raises(ValueError, match="level"):
        ThemesFile.model_validate(_themes(depth=3))
    ThemesFile.model_validate(_themes(keywords={"storm surge model": "n1"}))  # any level
    with pytest.raises(ValueError, match="unknown node"):
        ThemesFile.model_validate(_themes(keywords={"storm surge model": "n9"}))
    with pytest.raises(ValueError, match="own ancestor"):
        ThemesFile.model_validate(
            _themes(nodes=[{"id": "a", "parent": "b"}, {"id": "b", "parent": "a"}], keywords={})
        )
    with pytest.raises(ValueError, match="unknown parent"):
        ThemesFile.model_validate(_themes(nodes=[{"id": "a", "parent": "zz"}], keywords={}))
    with pytest.raises(ValueError, match="both placed and set aside"):
        ThemesFile.model_validate(
            _themes(set_aside={"storm surge model": {"from": "n7", "reason": "x"}})
        )
    four_deep = _themes(
        depth=1,
        levels=[{"names": {"en": "Theme"}}],
        nodes=[{"id": "a"}, {"id": "b", "parent": "a"}],
        keywords={},
    )
    with pytest.raises(ValueError, match="below the tree's depth"):
        ThemesFile.model_validate(four_deep)


def test_maps_and_run_records():
    version = {"id": "v1", "shows": ["people", "organisations:lab"], "layout": {"method": "umap"}}
    version["created_at"] = NOW
    MapsFile.model_validate({"pinned": "v1", "versions": [version]})
    with pytest.raises(ValueError, match="pinned"):
        MapsFile.model_validate({"pinned": "v9", "versions": [version]})
    with pytest.raises(ValueError):
        MapsFile.model_validate({"versions": [dict(version, shows=["everything"])]})
    record = RunRecord.model_validate(
        {
            "stage": "keywords.extract",
            "run_id": "20260928T101200Z-7c1e",
            "outcome": "succeeded",
            "started_at": NOW,
            "code": {"version": "1.0.0", "fingerprint": "sha256:" + "0" * 64},
            "parameters": {"min_people": {"value": 3, "from": "default"}},
            "inputs": [
                {"kind": "stage", "stage": "corpus.assemble", "run_id": "20260928T101150Z-0a9d"},
                {
                    "kind": "decision",
                    "path": "decisions/stopwords.json",
                    "fingerprint": "sha256:" + "1" * 64,
                },
            ],
        }
    )
    dumped = json.loads(json_bytes(record))
    assert dumped["parameters"]["min_people"] == {"value": 3, "from": "default", "rule": None}
    assert RunRecord.model_validate(dumped) == record
    with pytest.raises(ValueError):
        RunRecord.model_validate(dict(dumped, stage="nope"))


def test_stored_schemas_match_the_models():
    assert schemas_main(["--check"]) == 0


# ── tables ───────────────────────────────────────────────────────────────────


def _texts(rows):
    columns = {f.name: [r.get(f.name) for r in rows] for f in SOURCE_SCHEMAS["texts"]}
    return pa.table(columns, schema=SOURCE_SCHEMAS["texts"])


ROW = {
    "slot": "s",
    "position": 0,
    "year": 2024,
    "doc_type": "article",
    "title": "T",
    "ids": [("openalex", "W1")],
    "n_authors": 2,
    "source": "import",
    "retrieved_at": NOW,
}


def test_source_tables_round_trip_sorted(tmp_path):
    layout = ProjectLayout(tmp_path)
    table = _texts([dict(ROW, text_id="t2", position=1), dict(ROW, text_id="t1")])
    write_source_table(layout.table("texts"), "texts", table)
    back = read_source_table(layout.table("texts"), "texts")
    assert back["text_id"].to_pylist() == ["t1", "t2"]
    for name in SOURCE_SCHEMAS:
        write_source_table(layout.table(name), name, empty_table(name))
        assert read_source_table(layout.table(name), name).num_rows == 0


def test_source_tables_refuse_broken_rows(tmp_path):
    path = tmp_path / "texts.parquet"
    with pytest.raises(TableError, match="repeats"):
        write_source_table(path, "texts", _texts([dict(ROW, text_id="t1")] * 2))
    with pytest.raises(TableError, match="empty value"):
        write_source_table(path, "texts", _texts([dict(ROW, text_id="t1", title=None)]))
    with pytest.raises(TableError, match="missing"):
        write_source_table(path, "texts", _texts([dict(ROW, text_id="t1")]).drop_columns(["slot"]))
    parts = pa.table(
        {
            "text_id": ["t1"],
            "part": ["preface"],
            "language": ["en"],
            "provider": ["folder"],
            "format": ["plain"],
            "content": ["x"],
            "retrieved_at": [NOW],
        }
    )
    with pytest.raises(TableError, match="unknown value"):
        write_source_table(tmp_path / "parts.parquet", "text_parts", parts)
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_an_unsorted_file_is_refused_when_read(tmp_path):
    import pyarrow.parquet as pq

    path = tmp_path / "texts.parquet"
    pq.write_table(_texts([dict(ROW, text_id="t2"), dict(ROW, text_id="t1")]), path)
    with pytest.raises(TableError, match="not sorted"):
        read_source_table(path, "texts")


def test_decision_csvs_are_checked_and_canonical(tmp_path):
    path = tmp_path / "people.csv"
    rows = [
        {"person_id": "p2", "role": "context", "identity": "auto"},
        {"person_id": "p1", "role": "mapped", "identity": "confirmed", "note": "a, b"},
    ]
    path.write_bytes(decision_csv_bytes("people", rows))
    text = path.read_text()
    assert text.splitlines()[0] == "person_id,role,set,identity,records,merged_into,note,decided_at"
    assert [r["person_id"] for r in read_decision_csv(path, "people")] == ["p1", "p2"]
    path.write_bytes(decision_csv_bytes("people", [{"person_id": "p1", "role": "boss"}]))
    with pytest.raises(TableError, match="role"):
        read_decision_csv(path, "people")
    path.write_bytes(decision_csv_bytes("people", [{"person_id": "p1"}, {"person_id": "p1"}]))
    with pytest.raises(TableError, match="repeats"):
        read_decision_csv(path, "people")
    assert read_decision_csv(tmp_path / "missing.csv", "people") == []


def test_layout_names_known_stages_only(tmp_path):
    layout = ProjectLayout(tmp_path)
    assert layout.staging("map.layout", "20260928T101200Z-7c1e").name == (
        "map.layout.20260928T101200Z-7c1e"
    )
    with pytest.raises(KeyError):
        layout.stage("map.layuot")
    assert layout.history_of(layout.people_csv) == layout.history / "people.csv"
    assert layout.history_of(layout.project_json) == layout.history / "project.json"
    assert (
        layout.history_of(layout.prompts / "triage.txt")
        == layout.history / "prompts" / "triage.txt"
    )
    assert os.fspath(layout.table("texts")).endswith(
        "sources/tables/texts.parquet".replace("/", os.sep)
    )


def test_a_frozen_identity_changes_only_on_purpose(tmp_path):
    from cartolex.project import IdentityFrozen

    project = _new(tmp_path)
    identity = project.config.identity
    frozen = project.config.model_copy(
        update={"identity": identity.model_copy(update={"frozen": True})}
    )
    project.save_config(frozen, action="freeze")
    renamed = frozen.model_copy(
        update={"identity": frozen.identity.model_copy(update={"domain_title": "Another field"})}
    )
    with pytest.raises(IdentityFrozen, match="domain title"):
        project.save_config(renamed, action="rename the field")
    project.save_config(frozen.model_copy(update={"name": "A new name"}), action="rename")
    project.save_config(renamed, action="rename the field", identity_change=True)
    assert project.config.identity.domain_title == "Another field"
    from cartolex.project.models import AIIdentity

    first_ai = project.config.identity.model_copy(
        update={"ai": AIIdentity(provider="p", model="m")}
    )
    project.save_config(
        project.config.model_copy(update={"identity": first_ai}), action="choose AI"
    )
    other_ai = first_ai.model_copy(update={"ai": AIIdentity(provider="p", model="m2")})
    with pytest.raises(IdentityFrozen, match="AI identity"):
        project.save_config(project.config.model_copy(update={"identity": other_ai}), action="x")
    project.close()


def test_languages_are_open_codes_and_packs_are_checked(tmp_path):
    from cartolex.project.validate import validate_project

    base = dict(
        name="p",
        identity={"domain_title": "d"},
        created={"at": NOW, "by": "t"},
        app={"id": "cartolex", "version": "1"},
    )
    ProjectFile.model_validate(dict(base, languages={"corpus": ["es", "en"]}))
    with pytest.raises(ValueError):
        ProjectFile.model_validate(dict(base, languages={"corpus": ["spa"]}))
    project = _new(tmp_path)
    config = project.config.model_copy(
        update={"languages": project.config.languages.model_copy(update={"corpus": ["en", "es"]})}
    )
    project.save_config(config, action="add a language without a pack")
    problems = [str(p) for p in validate_project(project.layout.root)]
    assert any("no language pack for 'es'" in p for p in problems)
    project.close()


def test_an_older_file_reads_new_optional_columns_as_empty(tmp_path):
    import pyarrow.parquet as pq

    path = tmp_path / "authorships.parquet"
    old = pa.table(
        {
            "text_id": ["t1"],
            "person_id": ["p1"],
            "position": pa.array([1], pa.int32()),
            "orgs": [["o1"]],
        }
    )
    pq.write_table(old, path)
    table = read_source_table(path, "authorships")
    assert table["last"].to_pylist() == [None] and table["corresponding"].null_count == 1


def test_the_identity_freezes_once(tmp_path):
    project = _new(tmp_path)
    assert not project.has_curation()
    project.layout.keywords_csv.write_text("term,language,decision\n")
    assert not project.has_curation()  # a header alone decides nothing
    project.layout.keywords_csv.write_text("term,language,decision\nsea,en,exclude\n")
    assert project.has_curation()
    assert project.freeze_identity("first curation decision") is True
    assert project.freeze_identity("again") is False
    assert project.config.identity.frozen
    project.close()


def test_a_slot_window_and_the_collection_parameters_are_optional_keys():
    from cartolex.project.models import Slot

    plain = Slot(id="collected", kind="collection")
    assert "years" not in plain.model_dump(mode="json", by_alias=True)
    windowed = Slot.model_validate(
        {"id": "c", "kind": "collection", "years": {"from": 2015, "to": None}}
    )
    assert windowed.years.as_tuple() == (2015, None)
    assert windowed.model_dump(mode="json", by_alias=True)["years"] == {"from": 2015, "to": None}
    with pytest.raises(ValueError, match="starts"):
        Slot.model_validate({"id": "c", "kind": "collection", "years": {"from": 2020, "to": 2010}})
    # A file without the collection key keeps its bytes; with it, it reads back.
    assert "collect" not in json.loads(json_bytes(ParamsFile()))
    params = ParamsFile.model_validate({"collect": {"snowball": {"cap": 50, "max_authors": 25}}})
    assert json.loads(json_bytes(params))["collect"] == {"snowball": {"cap": 50, "max_authors": 25}}
    for bad in (
        {"nowhere": {"cap": 1}},
        {"snowball": {"size": 3}},
        {"snowball": {"cap": 0}},
        {"snowball": {"max_authors": True}},
        {"coverage": {"good": "3"}},
    ):
        with pytest.raises(ValueError):
            ParamsFile.model_validate({"collect": bad})


def test_a_rename_refused_by_a_reader_on_windows_is_retried(tmp_path, monkeypatch) -> None:
    """On Windows a reader holding the old file refuses the rename for a moment."""
    import cartolex.project.files as files

    src, dst = tmp_path / "new", tmp_path / "old"
    src.write_text("new", encoding="utf-8")
    dst.write_text("old", encoding="utf-8")
    real, refusals = os.replace, [PermissionError("in use")]

    def replace(a, b):
        if refusals:
            raise refusals.pop()
        real(a, b)

    monkeypatch.setattr(files.sys, "platform", "win32")
    monkeypatch.setattr(files.os, "replace", replace)
    monkeypatch.setattr(files.time, "sleep", lambda s: None)
    files.replace_path(src, dst)
    assert dst.read_text(encoding="utf-8") == "new" and not src.exists()
