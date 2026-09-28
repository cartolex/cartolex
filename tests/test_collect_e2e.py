# SPDX-License-Identifier: MIT
"""End to end on the demo services: import, resolve, confirm, harvest, then build."""

from __future__ import annotations

import pytest
from _collect_world import (
    actual_corpus,
    client,
    confirm_truth,
    demo_project,
    expected_corpus,
    identities,
    world_ids,
)

from cartolex.collect.harvest import harvest
from cartolex.collect.resolve import resolve
from cartolex.collect.tables import rebuild_sources
from cartolex.demo import generate
from cartolex.demo.services import DemoServices
from cartolex.project.layout import SOURCE_TABLES


@pytest.fixture(scope="module")
def services():
    with DemoServices(generate("XS", 0)) as svc:
        yield svc


@pytest.fixture(scope="module")
def collected(services, tmp_path_factory):
    bib = services.bibliography
    project = demo_project(tmp_path_factory.mktemp("e2e") / "project", bib)
    ids = world_ids(project, bib)
    http = client(services, project)
    report = resolve(project, http, auto=True)
    autos = {r.person_id: r.records for r in report.resolutions if r.status == "auto"}
    confirm_truth(project, bib, ids)
    result = harvest(project, http)
    yield project, bib, ids, autos, result
    project.close()


def test_automatic_acceptances_are_right_and_flagged(collected) -> None:
    project, bib, ids, autos, _ = collected
    assert autos, "some people are accepted automatically"
    for pid, records in autos.items():
        assert tuple(records) == bib.truth[ids[pid]].records, pid
    special = {bib.truth[ids[p]].special for p in autos}
    assert not special & {"mixed", "trap", "split", "none"}


def test_the_tables_equal_the_expected_corpus(collected) -> None:
    project, bib, ids, _, result = collected
    assert result.people == sum(1 for w in ids.values() if bib.truth[w].records)
    expected, actual = expected_corpus(bib, ids), actual_corpus(project)
    for name in ("texts", "parts", "authorships", "affiliations"):
        assert actual[name] == expected[name], (
            name,
            sorted(actual[name] - expected[name])[:5],
            sorted(expected[name] - actual[name])[:5],
        )


def test_a_rebuild_gives_the_same_bytes(collected) -> None:
    project = collected[0]
    before = {n: project.layout.table(n).read_bytes() for n in SOURCE_TABLES}
    rebuild_sources(project.layout, project.config)
    assert {n: project.layout.table(n).read_bytes() for n in SOURCE_TABLES} == before


def test_harvesting_again_from_the_cache_changes_nothing(collected, services) -> None:
    project = collected[0]
    before = {n: project.layout.table(n).read_bytes() for n in ("texts", "authorships")}
    services.requests.clear()
    offline = client(services, project, mode="cache_only")
    harvest(project, offline)
    assert services.requests == []
    after = {n: project.layout.table(n).read_bytes() for n in ("texts", "authorships")}
    assert after == before
    assert {r["identity"] for r in identities(project).values()} <= {"confirmed", "none"}


@pytest.mark.models("en", "fr")
def test_the_collected_project_builds(collected) -> None:
    from cartolex.build import build

    project = collected[0]
    result = build(project, year=2026, budget_mb=1e9)
    assert result.outcome == "succeeded", result.summary()


def test_the_expected_corpus_is_not_empty(collected) -> None:
    _project, bib, ids, _, _ = collected
    expected = expected_corpus(bib, ids)
    assert len(expected["texts"]) > 40 and len(expected["authorships"]) > 60
    sources = {a[-1] for a in expected["affiliations"]}
    assert sources == {"stated", "openalex", "orcid", "import"}
