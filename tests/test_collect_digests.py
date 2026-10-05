# SPDX-License-Identifier: MIT
"""Rebuilding from digests: the same bytes as from the raw runs, and only new runs read whole."""

from __future__ import annotations

import gzip
import shutil

import pytest
from _collect_world import client, confirm_truth, demo_project, world_ids

from cartolex.collect import tables as tables_mod
from cartolex.collect.digests import DigestCache
from cartolex.collect.harvest import harvest
from cartolex.collect.tables import RAW_SUFFIX, open_run, raw_folder, read_runs, rebuild_sources
from cartolex.demo import generate
from cartolex.demo.services import DemoServices
from cartolex.project.layout import SOURCE_TABLES
from cartolex.project.tables import read_source_table


@pytest.fixture(scope="module")
def services():
    with DemoServices(generate("XS", 0)) as svc:
        yield svc


@pytest.fixture()
def harvested(services, tmp_path):
    bib = services.bibliography
    project = demo_project(tmp_path / "p", bib)
    ids = world_ids(project, bib)
    confirm_truth(project, bib, ids)
    report = harvest(project, client(services, project))
    yield project, bib, ids, report
    project.close()


def _texts(project) -> list[dict]:
    """The texts, without the time each was received."""
    rows = read_source_table(project.layout.table("texts"), "texts").to_pylist()
    return [{k: v for k, v in r.items() if k != "retrieved_at"} for r in rows]


def _bytes(project) -> dict[str, bytes]:
    names = [*SOURCE_TABLES]
    out = {n: project.layout.table(n).read_bytes() for n in names}
    out["merges"] = (project.layout.sources / "merges.json").read_bytes()
    return out


def _count_raw_reads(monkeypatch) -> dict[str, int]:
    """How many harvest runs are read whole (by a reader, or to be digested)."""
    from cartolex.collect import digests as digests_mod

    reads = {"openalex": 0, "orcid": 0}
    real = tables_mod.RawRun.raw_records
    real_digest = digests_mod._write_digest

    def counted(self):
        if self.kind in reads:
            reads[self.kind] += 1
        return real(self)

    def digested(raw_path, kind, out_path):
        reads[kind] += 1
        return real_digest(raw_path, kind, out_path)

    monkeypatch.setattr(tables_mod.RawRun, "raw_records", counted)
    monkeypatch.setattr(digests_mod, "_write_digest", digested)
    return reads


def test_digests_give_the_bytes_of_the_raw_runs(harvested) -> None:
    project, _bib, _ids, report = harvested
    assert report.rebuild.digested >= 2  # the harvest's two runs, digested once
    with_digests = _bytes(project)
    rebuild_sources(project.layout, project.config, incremental=False)
    assert _bytes(project) == with_digests
    shutil.rmtree(project.layout.cache / "sources")
    again = rebuild_sources(project.layout, project.config)
    assert again.digested == report.rebuild.digested and _bytes(project) == with_digests
    # Many new runs are digested in worker processes, with the same result.
    shutil.rmtree(project.layout.cache / "sources")
    parallel = rebuild_sources(project.layout, project.config, jobs=2)
    assert parallel.digested == report.rebuild.digested >= 2
    assert _bytes(project) == with_digests


def test_a_rebuild_reads_only_the_new_runs_whole(harvested, services, monkeypatch) -> None:
    project, bib, ids, _ = harvested
    reads = _count_raw_reads(monkeypatch)
    nothing = rebuild_sources(project.layout, project.config)
    assert nothing.digested == 0 and reads == {"openalex": 0, "orcid": 0}
    index = DigestCache(project.layout).index
    runs = read_runs(project.layout, "collected", "openalex")
    entry = index[f"collected/openalex/{runs[0].run_id}"]
    assert entry["records"] > 0 and set(entry["people"]) == set(runs[0].header["people"])
    # A new harvest of one person: its runs are read whole, the earlier ones are not.
    person = sorted(runs[0].header["people"])[0]
    before = _texts(project)
    harvest(project, client(services, project), people=[person])
    assert reads["openalex"] == 1
    fresh = _bytes(project)
    rebuild_sources(project.layout, project.config, incremental=False)
    assert _bytes(project) == fresh
    # The same works, harvested again; asked for on their own now, so received later.
    assert _texts(project) == before


def test_a_run_superseded_for_everyone_is_not_read(harvested, services, monkeypatch) -> None:
    project, _bib, _ids, _ = harvested
    harvest(project, client(services, project))  # everyone again: the first runs are superseded
    shutil.rmtree(project.layout.cache / "sources")
    reads = _count_raw_reads(monkeypatch)
    report = rebuild_sources(project.layout, project.config)
    assert reads["openalex"] == 1 and report.digested == 2
    index = DigestCache(project.layout).index
    first = read_runs(project.layout, "collected", "openalex")[0]
    assert f"collected/openalex/{first.run_id}" not in index


def test_a_run_changed_on_disk_is_digested_again(harvested) -> None:
    project, _bib, _ids, _ = harvested
    run = read_runs(project.layout, "collected", "openalex")[0]
    with open_run(run.path) as fh:
        lines = fh.read().splitlines()
    run.path.write_bytes(gzip.compress(("\n".join(lines[:-1]) + "\n").encode()))  # one work fewer
    report = rebuild_sources(project.layout, project.config)
    assert report.digested == 1
    fresh = _bytes(project)
    rebuild_sources(project.layout, project.config, incremental=False)
    assert _bytes(project) == fresh
    # A run removed takes its digest with it.
    run.path.unlink()
    (raw_folder(project.layout, "collected") / "orcid" / f"{run.run_id}{RAW_SUFFIX}").unlink()
    rebuild_sources(project.layout, project.config)
    assert not DigestCache(project.layout).index
    assert not list((project.layout.cache / "sources").rglob("*.jsonl.gz"))


def test_a_heavy_run_digested_in_workers_gives_the_same_digest(harvested, tmp_path, monkeypatch):
    import gzip

    from cartolex.collect import digests as digests_mod

    project, _bib, _ids, report = harvested
    run = next(
        p for p in (project.layout.sources / "collected" / "raw" / "openalex").glob("*.jsonl.gz")
    )
    monkeypatch.setattr(digests_mod, "SPLIT_LINES", 3)  # many small batches
    one = digests_mod._write_digest(str(run), "openalex", str(tmp_path / "one.jsonl.gz"))
    many = digests_mod._write_digest(str(run), "openalex", str(tmp_path / "many.jsonl.gz"), jobs=2)
    assert many == one and one[0] > 3
    text = {n: gzip.decompress((tmp_path / f"{n}.jsonl.gz").read_bytes()) for n in ("one", "many")}
    assert text["many"] == text["one"]
