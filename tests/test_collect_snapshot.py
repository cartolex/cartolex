# SPDX-License-Identifier: MIT
"""The OpenAlex snapshot: streamed partitions, the deletion log, and the same tables as the API."""

from __future__ import annotations

import gzip
import json

import pytest
from _collect_world import client, confirm_truth, demo_project, world_ids

from cartolex.collect.harvest import harvest
from cartolex.collect.snapshot import RecordStore, Snapshot, SnapshotSource
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


def test_a_store_keeps_records_on_disk_the_newest_copy_replacing_the_others(tmp_path) -> None:
    store = RecordStore(tmp_path / "spill")
    store.put("W1", {"id": "W1", "title": "first"})
    store.put("W2", {"id": "W2", "title": "élan"})
    store.put("W1", {"id": "W1", "title": "newer"})
    store.drop("W2")
    assert store.get("W1") == {"id": "W1", "title": "newer"}
    assert store.get("W2") is None and store.ids() == ["W1"] and len(store) == 1
    store.close()
    assert list((tmp_path / "spill").iterdir()) == []  # nothing left behind


def test_an_institutions_works_come_in_pages_and_resume_from_a_cursor(
    services, snapshot_dir, monkeypatch
) -> None:
    monkeypatch.setattr("cartolex.collect.snapshot.PER_PAGE", 5)
    top = next(i for i in services.bibliography.institutions.values() if i.ror)
    source = SnapshotSource(Snapshot(snapshot_dir))
    pages = list(source.institution_work_pages([top.id], None))
    assert len(pages) > 2 and all(len(p.items) == 5 for p in pages[:-1])
    assert pages[-1].next_cursor is None and pages[-1].read == pages[-1].total
    works = [w["id"] for p in pages for w in p.items]
    assert len(set(works)) == len(works) == pages[0].total
    # From the second page's cursor, the works after the first page, in the same order.
    rest = source.institution_work_pages([top.id], None, cursor=pages[0].next_cursor, read=5)
    assert [w["id"] for p in rest for w in p.items] == works[5:]


def test_a_retry_on_the_snapshot_still_says_identities_are_searched_online(
    services, tmp_path
) -> None:
    from cartolex.collect import local_settings
    from cartolex.collect.coverage import person_coverage
    from cartolex.collect.privacy import plan_collection
    from cartolex.collect.resolve import resolve

    bib = services.bibliography
    project = demo_project(tmp_path / "p", bib)
    ids = world_ids(project, bib)
    found = [pid for pid, wid in ids.items() if bib.world.person(wid).openalex_id]
    searched, harvested = found[0], found[1]
    confirm_truth(project, bib, {p: w for p, w in ids.items() if p != searched})
    # One person's identity search fails, another's harvest.
    services.faults.add("status", service="openalex", path=r"^authors\?", status=503, times=None)
    resolve(project, client(services, project), people=[searched], auto=False)
    services.faults.clear()
    aid = bib.world.person(ids[harvested]).openalex_id
    services.faults.add("status", service="openalex", path=rf"^works\?.*{aid}", status=503,
                        times=None)  # fmt: skip
    harvest(project, client(services, project))
    services.faults.clear()
    failed = {c.failure["finder"] for c in person_coverage(project) if c.state == "failed"}
    assert failed == {"resolve", "harvest"}

    settings = local_settings(services.endpoints())
    plan = plan_collection(project, "coverage", settings, snapshot="2026-01-14")
    openalex = next(h for h in plan.hosts if h.service == "openalex")
    assert openalex.requests > 0 and "names" in openalex.sends  # the searches still go online
    codes = [n["code"] for n in plan.coded_notes]
    assert "note_snapshot_harvests" in codes and "note_snapshot" not in codes
    project.close()


def test_a_parallel_reading_reads_only_a_few_parts_ahead(services, tmp_path, monkeypatch) -> None:
    from concurrent.futures import ProcessPoolExecutor

    from cartolex.collect import snapshot as module
    from cartolex.collect.snapshot import Query

    folder = tmp_path / "openalex"
    write_snapshot(services.bibliography, folder, per_part=5)  # many small parts
    submitted: list[str] = []
    real = ProcessPoolExecutor.submit

    def spy(self, fn, *args, **kwargs):
        submitted.append(args[0])
        return real(self, fn, *args, **kwargs)

    monkeypatch.setattr(ProcessPoolExecutor, "submit", spy)
    snap = Snapshot(folder, jobs=2)
    parts = snap.partitions("works")
    ahead = 2 * module.READ_AHEAD
    assert len(parts) > ahead + 2
    reading = snap._parallel(parts, Query("works", everything=True), 1, "works")
    first = next(reading)
    # The first part is given back with only a few others read ahead, not all of them.
    assert len(submitted) == ahead + 1 and first[0]
    rest = list(reading)
    assert len(rest) + 1 == len(parts) == len(submitted)
    assert submitted == [str(p.path) for p in parts]


def test_a_store_on_file_comes_back_as_it_was_at_its_checkpoint(tmp_path) -> None:
    path = tmp_path / "works.records"
    store = RecordStore(path=path)
    store.put("W1", {"a": 1})
    store.put("W2", {"a": 2})
    store.drop("W1")
    sizes = store.checkpoint()
    store.put("W3", {"a": 3})  # after the checkpoint: lost with the job that wrote it
    store.close()
    again = RecordStore(path=path, sizes=sizes)
    assert again.ids() == ["W2"] and again.get("W2") == {"a": 2} and again.get("W3") is None
    assert list(again.records()) == [("W2", {"a": 2})]
    again.put("W4", {"a": 4})
    assert again.get("W4") == {"a": 4}
    again.close()


def test_a_harvest_stopped_while_reading_the_snapshot_goes_on(
    services, snapshot_dir, tmp_path, monkeypatch
) -> None:
    from cartolex.collect import snapshot as module
    from cartolex.project.checkpoints import JobPaused

    monkeypatch.setattr(module, "SAVE_EVERY_PARTS", 1)
    bib = services.bibliography
    out = {}
    for name in ("whole", "stopped"):
        project = demo_project(tmp_path / name, bib)
        confirm_truth(project, bib, world_ids(project, bib))
        out[name] = project
    spill = tmp_path / "spill"
    reference = SnapshotSource(Snapshot(snapshot_dir), spill=spill / "reference")
    harvest(out["whole"], client(services, out["whole"]), source=reference)
    reference.close()

    project = out["stopped"]
    # Stopped once the authors are read and the first part of the works.
    snap = Snapshot(snapshot_dir, cancel=lambda: snap.report.by_entity.get("works", 0) > 0)
    source = SnapshotSource(snap, spill=spill)
    with pytest.raises(JobPaused, match="reading the snapshot") as stopped:
        harvest(project, client(services, project), source=source)
    source.close()
    kept = next(spill.glob("snapshot-*"))
    state = json.loads((kept / "state.json").read_text())
    assert state["passes"]["authors"]["done"] and len(state["passes"]["works"]["parts"]) == 1

    again = Snapshot(snapshot_dir)
    source = SnapshotSource(again, spill=spill)
    harvest(project, client(services, project), source=source, resume=True)
    source.close()
    assert "authors" not in again.report.by_entity  # read before the stop: not again
    assert again.report.by_entity["works"] < sum(p.size for p in again.partitions("works"))
    assert tables_without_times(project) == tables_without_times(out["whole"])
    assert not kept.exists() and stopped.value.checkpoint
