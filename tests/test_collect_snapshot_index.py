# SPDX-License-Identifier: MIT
"""The index of a snapshot: the parts cut into members holding the same lines, a query that
reads only the members that may answer it and gets the same records as a whole scan, a
build stopped and resumed, an index left aside once a part changed."""

from __future__ import annotations

import gzip
import json
import shutil

import pytest
from _collect_world import client, confirm_truth, demo_project, world_ids

from cartolex.collect.harvest import harvest
from cartolex.collect.snapshot import Snapshot, SnapshotSource
from cartolex.collect.snapshot_index import SnapshotIndex, build_index, index_state
from cartolex.demo import generate
from cartolex.demo.services import DemoServices, write_snapshot
from cartolex.project.layout import SOURCE_TABLES
from cartolex.project.tables import read_source_table

BLOCK = 4096  # small members: several per part of the mini snapshot


@pytest.fixture(scope="module")
def services():
    with DemoServices(generate("XS", 0)) as svc:
        yield svc


@pytest.fixture(scope="module")
def original(services, tmp_path_factory):
    folder = tmp_path_factory.mktemp("snapshot") / "openalex"
    write_snapshot(services.bibliography, folder, per_part=20)
    return folder


@pytest.fixture(scope="module")
def indexed(original, tmp_path_factory):
    folder = tmp_path_factory.mktemp("indexed") / "openalex"
    shutil.copytree(original, folder)
    report = build_index(folder, jobs=2, block_bytes=BLOCK)
    assert report.complete and report.parts > 0
    return folder


def _lines(folder, entity) -> dict[str, list[bytes]]:
    out = {}
    for part in Snapshot(folder).partitions(entity):
        with gzip.open(part.path, "rb") as fh:
            out[f"{part.date}/{part.path.name}"] = fh.read().splitlines()
    return out


def _members(path) -> int:
    data, n = path.read_bytes(), 0
    while data:
        import zlib

        d = zlib.decompressobj(31)
        d.decompress(data)
        data, n = d.unused_data, n + 1
    return n


def test_the_parts_are_cut_into_members_of_the_same_lines(original, indexed) -> None:
    for entity in ("works", "authors"):
        assert _lines(indexed, entity) == _lines(original, entity)
    parts = Snapshot(indexed).partitions("works")
    assert max(_members(p.path) for p in parts) > 1
    check = Snapshot(indexed).check()
    assert check.complete and check.indexed  # the sizes the index wrote are accepted
    assert index_state(indexed)["state"] == "complete"
    assert not (indexed / "cartolex-index" / "spill").exists()


def test_a_query_reads_only_its_members_and_finds_the_same_records(services, original, indexed):
    bib = services.bibliography
    people = [p for p in bib.world.people if p.openalex_id][:3]
    authors = [p.openalex_id for p in people]
    works = Snapshot(original).works(author_ids=authors)
    some_dois = sorted({w["doi"] for w in works.values() if w.get("doi")})[:2]
    institution = next(
        i["id"].rsplit("/", 1)[-1]
        for w in works.values()
        for a in w["authorships"]
        for i in a["institutions"]
    )
    queries = [
        dict(author_ids=authors),
        dict(dois=some_dois),
        dict(lineage=[institution]),
        dict(author_ids=authors[:1], years=(2015, 2020)),
    ]
    for query in queries:
        whole = Snapshot(original)
        fast = Snapshot(indexed)
        assert fast.works(**query) == whole.works(**query), query
        assert fast.report.members > 0 and fast.report.bytes < whole.report.bytes
    whole, fast = Snapshot(original), Snapshot(indexed)
    assert fast.authors(authors) == whole.authors(authors) and len(fast.authors(authors)) == 3
    # Institutions are read whole; a search by name too.
    assert Snapshot(indexed).institutions(everything=True) == Snapshot(original).institutions(
        everything=True
    )


def _tables(project) -> dict[str, list[dict]]:
    out = {}
    for name in SOURCE_TABLES:
        rows = read_source_table(project.layout.table(name), name).to_pylist()
        out[name] = [{k: v for k, v in r.items() if k != "retrieved_at"} for r in rows]
    return out


def test_a_harvest_on_the_index_writes_the_same_tables(services, original, indexed, tmp_path):
    bib = services.bibliography
    out = {}
    for name, folder in (("whole", original), ("indexed", indexed)):
        project = demo_project(tmp_path / name, bib)
        confirm_truth(project, bib, world_ids(project, bib))
        source = SnapshotSource(Snapshot(folder), spill=project.layout.cache / "snapshot")
        harvest(project, client(services, project), source=source)
        source.close()
        out[name] = _tables(project)
        project.close()
    assert out["indexed"] == out["whole"] and out["whole"]["texts"]


def test_a_stopped_build_resumes_and_a_changed_part_sets_the_index_aside(original, tmp_path):
    folder = tmp_path / "openalex"
    shutil.copytree(original, folder)
    first = build_index(folder, block_bytes=BLOCK, stop_after=3)
    assert first.parts == 3 and not first.complete
    assert index_state(folder)["state"] == "building" and index_state(folder)["done"] == 3
    assert Snapshot(folder).check().complete and Snapshot(folder).index is None
    # A hard stop after the last sync point: a journal line and postings not synced.
    index = folder / "cartolex-index"
    spilled = next((index / "spill").glob("*/*.bin"))
    with open(spilled, "ab") as fh:
        fh.write(b"\x00" * 12 * 5)
    with open(index / "journal.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"entity": "works", "path": "x", "part": 99, "size": 1}) + "\n")
    second = build_index(folder, block_bytes=BLOCK)
    assert second.complete and second.resumed == 3
    whole, fast = Snapshot(original), Snapshot(folder)
    lineage = sorted(whole.institutions(everything=True))[:2]
    assert fast.works(lineage=lineage) == whole.works(lineage=lineage)
    assert fast.report.members > 0 and SnapshotIndex.open(folder) is not None
    # A part replaced since (another download): the index is not used, the scan reads it.
    part = Snapshot(folder).partitions("works")[0].path
    shutil.copy(original / part.relative_to(folder), part)
    assert Snapshot(folder).index is None
    assert Snapshot(folder).works(lineage=lineage) == whole.works(lineage=lineage)
