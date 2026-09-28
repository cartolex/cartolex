# SPDX-License-Identifier: MIT
"""Source writers: raw runs, stable ids, rebuilds that give the same bytes, kept foreign rows."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cartolex.collect.tables import (
    IdRegistry,
    RawRun,
    RawWriter,
    SourceBuilder,
    parse_time,
    raw_folder,
    read_runs,
    rebuild_sources,
)
from cartolex.demo import generate
from cartolex.demo.project import write_project
from cartolex.project import Project
from cartolex.project.layout import SOURCE_TABLES
from cartolex.project.models import Slot
from cartolex.project.tables import read_source_table

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _notes(runs: list[RawRun], builder: SourceBuilder) -> None:
    """A small reader for the tests: people, organisations and texts described plainly."""
    for run in runs:
        for rec in run.records():
            at = parse_time(rec["at"])
            if rec["type"] == "org":
                builder.organisation(
                    slot=run.slot,
                    keys=[rec["key"]],
                    name=rec["name"],
                    parent_keys=rec.get("parents", []),
                    source="import",
                    retrieved_at=at,
                )
            elif rec["type"] == "person":
                pid = builder.person(
                    slot=run.slot,
                    keys=[rec["key"]],
                    last_name=rec["last"],
                    first_name=rec["first"],
                    source="import",
                    retrieved_at=at,
                )
                if rec.get("org"):
                    oid = builder.registry.lookup("organisations", [rec["org"]], run.slot)
                    builder.affiliation(pid, oid, rec.get("from"), rec.get("to"), "import")
            else:
                tid = builder.text(
                    slot=run.slot,
                    keys=rec["keys"],
                    title=rec["title"],
                    doc_type="article",
                    year=rec.get("year"),
                    doi=rec.get("doi"),
                    source="import",
                    retrieved_at=at,
                    n_authors=len(rec.get("authors", [])),
                )
                builder.part(
                    tid,
                    part="title",
                    language="en",
                    provider="import",
                    content=rec["title"],
                    retrieved_at=at,
                )
                for rank, key in enumerate(rec.get("authors", []), start=1):
                    pid = builder.registry.lookup("people", [key], run.slot)
                    builder.authorship(tid, pid, position=rank)


READERS = {"notes": _notes}


def _project(root: Path, slots=("collected",)) -> Project:
    return Project.init(
        root,
        name="Tables test",
        domain_title="Invented field",
        slots=tuple(Slot(id=s, kind="collection") for s in slots),
    )


def _write(project: Project, records: list[dict], slot: str = "collected", **kw) -> Path:
    with RawWriter(project.layout, slot, "notes", {"source": "test"}, **kw) as w:
        for rec in records:
            w.add({"at": T0.isoformat(), **rec})
    return w.path


FIRST = [
    {"type": "org", "key": "lab:a", "name": "Tide Lab", "parents": ["inst:x"]},
    {"type": "org", "key": "inst:x", "name": "Institute X"},
    {"type": "person", "key": "row:1", "last": "Varno", "first": "Ada", "org": "lab:a", "from": 2015},
    {"type": "person", "key": "row:2", "last": "Quell", "first": "Ivo", "org": "lab:a", "to": 2020},
    {"type": "text", "keys": ["doi:10.5555/a"], "title": "Tidal flats", "year": 2021,
     "doi": "10.5555/A", "authors": ["row:1", "row:2"]},
    {"type": "text", "keys": ["hal:1"], "title": "Sand waves", "year": 2019, "authors": ["row:2"]},
]  # fmt: skip


def _bytes(project: Project) -> dict[str, bytes]:
    return {n: project.layout.table(n).read_bytes() for n in SOURCE_TABLES}


def test_a_raw_run_appears_whole_or_not_at_all(tmp_path) -> None:
    project = _project(tmp_path / "p")
    with pytest.raises(RuntimeError):
        with RawWriter(project.layout, "collected", "notes", {"source": "test"}) as w:
            w.add({"type": "org", "key": "k", "name": "n", "at": T0.isoformat()})
            raise RuntimeError("stopped halfway")
    folder = raw_folder(project.layout, "collected") / "notes"
    assert list(folder.iterdir()) == []
    path = _write(project, FIRST)
    (run,) = read_runs(project.layout, "collected")
    assert run.path == path and run.kind == "notes" and run.header["source"] == "test"
    assert len(list(run.records())) == len(FIRST)


def test_rebuilding_gives_the_same_bytes(tmp_path) -> None:
    project = _project(tmp_path / "p")
    _write(project, FIRST)
    report = rebuild_sources(project.layout, project.config, readers=READERS)
    assert report.rows["people"] == 2 and report.rows["texts"] == 2
    first = _bytes(project)
    rebuild_sources(project.layout, project.config, readers=READERS)
    assert _bytes(project) == first
    shutil.rmtree(project.layout.tables)
    rebuild_sources(project.layout, project.config, readers=READERS)
    assert _bytes(project) == first


def test_rows_are_linked_by_their_ids(tmp_path) -> None:
    project = _project(tmp_path / "p")
    _write(project, FIRST)
    rebuild_sources(project.layout, project.config, readers=READERS)
    layout = project.layout
    orgs = {
        r["name"]: r
        for r in read_source_table(layout.table("organisations"), "organisations").to_pylist()
    }
    assert orgs["Tide Lab"]["parents"] == [orgs["Institute X"]["org_id"]]
    people = read_source_table(layout.table("people"), "people").to_pylist()
    assert [p["person_id"] for p in people] == ["p000001", "p000002"]
    affs = read_source_table(layout.table("affiliations"), "affiliations").to_pylist()
    assert {(a["person_id"], a["start_year"], a["end_year"]) for a in affs} == {
        ("p000001", 2015, None),
        ("p000002", None, 2020),
    }
    texts = read_source_table(layout.table("texts"), "texts").to_pylist()
    assert [(t["text_id"], t["position"], t["doi"]) for t in texts] == [
        ("t000001", 1, "10.5555/a"),
        ("t000002", 0, None),
    ]
    auth = read_source_table(layout.table("authorships"), "authorships").to_pylist()
    assert [(a["text_id"], a["person_id"], a["position"]) for a in auth] == [
        ("t000001", "p000001", 1),
        ("t000001", "p000002", 2),
        ("t000002", "p000002", 1),
    ]


def test_a_new_collection_never_renumbers_anything(tmp_path) -> None:
    project = _project(tmp_path / "p")
    _write(project, FIRST, now=T0)
    rebuild_sources(project.layout, project.config, readers=READERS)
    ids_before = {
        r["title"]: r["text_id"]
        for r in read_source_table(project.layout.table("texts"), "texts").to_pylist()
    }
    _write(
        project,
        [
            {"type": "person", "key": "row:0", "last": "Arden", "first": "Zora"},
            {"type": "text", "keys": ["doi:10.5555/0"], "title": "Aaa first", "year": 2010,
             "authors": ["row:0", "row:1"]},
            # The same text again, now also known by another key.
            {"type": "text", "keys": ["hal:1", "doi:10.5555/b"], "title": "Sand waves", "year": 2019,
             "doi": "10.5555/b", "authors": ["row:2"]},
        ],
        now=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )  # fmt: skip
    rebuild_sources(project.layout, project.config, readers=READERS)
    texts = read_source_table(project.layout.table("texts"), "texts").to_pylist()
    after = {r["title"]: r["text_id"] for r in texts}
    assert {k: after[k] for k in ids_before} == ids_before
    assert after["Aaa first"] == "t000003"
    assert next(t for t in texts if t["title"] == "Sand waves")["doi"] == "10.5555/b"
    people = read_source_table(project.layout.table("people"), "people").to_pylist()
    assert {p["last_name"]: p["person_id"] for p in people}["Arden"] == "p000003"


def test_an_id_is_never_given_twice(tmp_path) -> None:
    project = _project(tmp_path / "p", slots=("one", "two"))
    reg = IdRegistry(project.layout, ["one", "two"])
    a = reg.assign("texts", ["doi:x"], "one")
    b = reg.assign("texts", ["doi:x"], "two")  # texts belong to one slot
    c = reg.assign("people", ["row:1"], "one")
    assert (a, b, c) == ("t000001", "t000002", "p000001")
    assert reg.assign("people", ["row:1"], "two") == c  # people are shared
    assert reg.assign("people", ["new"], "one", taken={"p000002"}) == "p000003"
    reg.save()
    again = IdRegistry(project.layout, ["one", "two"])
    assert again.lookup("texts", ["doi:x"], "one") == "t000001"
    assert again.assign("texts", ["doi:y"], "one") == "t000003"


def test_rows_of_another_tool_are_kept(tmp_path) -> None:
    world = generate("XS", 0)
    project = write_project(world, tmp_path / "demo")
    config = project.config.model_copy(
        update={"slots": [*project.config.slots, Slot(id="collected", kind="collection")]}
    )
    project.save_config(config, action="add a slot")
    before = {n: read_source_table(project.layout.table(n), n) for n in SOURCE_TABLES}
    rebuild_sources(project.layout, project.config, readers=READERS)
    for name in SOURCE_TABLES:  # nothing collected yet: the tables come back unchanged
        assert read_source_table(project.layout.table(name), name).equals(before[name]), name
    _write(project, FIRST)
    rebuild_sources(project.layout, project.config, readers=READERS)
    people = read_source_table(project.layout.table("people"), "people").to_pylist()
    ids = [p["person_id"] for p in people]
    assert len(ids) == len(world.people) + 2
    assert {"p0001", "p000001", "p000002"} <= set(ids)
    texts = read_source_table(project.layout.table("texts"), "texts")
    assert texts.num_rows == len(world.works) + 2
    project.close()


def test_a_new_run_always_comes_after_the_others(tmp_path) -> None:
    project = _project(tmp_path / "p")
    first = _write(project, FIRST[:1], now=T0)
    second = _write(project, FIRST[1:2], now=T0)  # the same time, twice
    earlier = _write(project, FIRST[2:3], now=datetime(2020, 1, 1, tzinfo=timezone.utc))
    runs = read_runs(project.layout, "collected")
    assert [r.path for r in runs] == [first, second, earlier]
