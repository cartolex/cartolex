# SPDX-License-Identifier: MIT
"""The OpenAlex snapshot: streamed partitions, the deletion log, and the same tables as the API."""

from __future__ import annotations

import gzip
import json

import pytest
from _collect_world import client, confirm_truth, demo_project, world_ids

from cartolex.collect.harvest import harvest
from cartolex.collect.snapshot import Snapshot, SnapshotSource
from cartolex.demo import generate
from cartolex.demo.services import DemoServices, write_snapshot
from cartolex.project.layout import SOURCE_TABLES
from cartolex.project.tables import read_source_table


@pytest.fixture(scope="module")
def services():
    with DemoServices(generate("XS", 0)) as svc:
        yield svc


@pytest.fixture(scope="module")
def snapshot_dir(services, tmp_path_factory):
    folder = tmp_path_factory.mktemp("snapshot") / "openalex"
    write_snapshot(services.bibliography, folder, per_part=20)
    return folder


def tables_without_times(project) -> dict[str, list[dict]]:
    """The six tables, without the retrieval times (the snapshot's are its release date)."""
    out = {}
    for name in SOURCE_TABLES:
        rows = read_source_table(project.layout.table(name), name).to_pylist()
        out[name] = [{k: v for k, v in r.items() if k != "retrieved_at"} for r in rows]
    return out


def test_the_snapshot_is_found_and_streamed(snapshot_dir, tmp_path) -> None:
    snap = Snapshot(snapshot_dir)
    assert snap.release() == "2026-01-14"
    works = snap.partitions("works")
    assert len(works) >= 4 and [p.date for p in works] == sorted(p.date for p in works)
    # The entity folders can be given directly, and a folder without a snapshot is refused.
    assert Snapshot(snapshot_dir / "data" / "jsonl").partitions("authors")
    with pytest.raises(FileNotFoundError, match="no OpenAlex snapshot"):
        Snapshot(tmp_path)


def test_queries_parse_only_the_lines_that_may_match(services, snapshot_dir) -> None:
    bib = services.bibliography
    snap = Snapshot(snapshot_dir)
    person = next(p for p in bib.world.people if p.openalex_id and bib.record_ids(p.person_id))
    found = snap.works(author_ids=[person.openalex_id])
    expected = {w.id for w in bib.works.values() if any(a.author_id == person.openalex_id
                for a in w.authorships)}  # fmt: skip
    assert set(found) == expected
    assert snap.report.parsed < snap.report.lines / 2
    top = next(i for i in bib.institutions.values() if i.ror)
    by_ror = snap.institutions(rors=[top.ror])
    assert list(by_ror) == [top.id]
    units = snap.institutions(lineage=[top.id])
    assert top.id in units and all(top.id in [x.rsplit("/", 1)[-1] for x in u["lineage"]]
                                   for u in units.values())  # fmt: skip


def test_deleted_works_are_left_out_and_the_newest_copy_wins(snapshot_dir, tmp_path) -> None:
    snap = Snapshot(snapshot_dir)
    log = snapshot_dir / "data/jsonl/works/deleted_ids.csv.gz"
    gone = gzip.open(log, "rt").read().splitlines()[1].split(",")[0].rsplit("/", 1)[-1]
    first_part = snap.partitions("works")[0].path
    withdrawn = json.loads(gzip.open(first_part, "rt").readline())
    assert withdrawn["id"].endswith(gone)
    author = next(a["author"]["id"] for a in withdrawn["authorships"] if a["author"]["id"])
    assert gone not in snap.works(author_ids=[author.rsplit("/", 1)[-1]])
    # A copy synced without deletions holds a record twice: the newest partition's wins.
    newer = snapshot_dir / "data/jsonl/works/updated_date=2026-01-14"
    extra = newer / "part_9999.gz"
    changed = dict(withdrawn, title="a newer copy")
    changed["id"] = json.loads(gzip.open(first_part, "rt").readlines()[1])["id"]
    extra.write_bytes(gzip.compress((json.dumps(changed) + "\n").encode()))
    try:
        again = Snapshot(snapshot_dir).works(author_ids=[author.rsplit("/", 1)[-1]])
        assert again[changed["id"].rsplit("/", 1)[-1]]["title"] == "a newer copy"
    finally:
        extra.unlink()


def test_a_harvest_from_the_snapshot_gives_the_tables_of_the_api(
    services, snapshot_dir, tmp_path
) -> None:
    bib = services.bibliography
    by_api = demo_project(tmp_path / "api", bib)
    ids = world_ids(by_api, bib)
    confirm_truth(by_api, bib, ids)
    harvest(by_api, client(services, by_api))
    by_snapshot = demo_project(tmp_path / "snapshot", bib)
    confirm_truth(by_snapshot, bib, world_ids(by_snapshot, bib))
    services.requests.clear()
    http = client(services, by_snapshot)
    source = SnapshotSource(Snapshot(snapshot_dir))
    report = harvest(by_snapshot, http, source=source)
    assert report.source == "snapshot" and report.people
    # OpenAlex was not asked: only the registry was.
    assert {r.service for r in services.requests} == {"orcid"}
    assert source.snapshot.report.passes == 2  # one over the authors, one over the works
    assert tables_without_times(by_snapshot) == tables_without_times(by_api)
    at = {t["retrieved_at"] for t in read_source_table(by_snapshot.layout.table("texts"), "texts")
          .to_pylist() if t["source"] == "openalex"}  # fmt: skip
    assert {a.date().isoformat() for a in at} == {"2026-01-14"}
    by_api.close()
    by_snapshot.close()


def test_parts_read_in_worker_processes_give_the_same_records(services, snapshot_dir) -> None:
    bib = services.bibliography
    person = next(p for p in bib.world.people if p.openalex_id and bib.record_ids(p.person_id))
    top = next(i for i in bib.institutions.values() if i.ror)
    alone, together = Snapshot(snapshot_dir), Snapshot(snapshot_dir, jobs=2)
    assert together.works(author_ids=[person.openalex_id]) == alone.works(
        author_ids=[person.openalex_id]
    )
    assert together.institutions(lineage=[top.id]) == alone.institutions(lineage=[top.id])
    assert together.report.lines == alone.report.lines
    assert together.report.parsed == alone.report.parsed
