# SPDX-License-Identifier: MIT
"""Coverage: states, first blocking causes, aggregates, person sheets and actions.

A service failure is never shown as « no data », and a failure is never
remembered beyond the next attempt that reaches the person.
"""

from __future__ import annotations

import json

import pytest
from _collect_world import client, confirm_truth, demo_project, world_ids

from cartolex.collect.coverage import (
    add_documents,
    coverage_report,
    exclude_person,
    person_coverage,
    person_sheet,
    retry_failed,
)
from cartolex.collect.harvest import harvest
from cartolex.collect.http import ServiceUnavailable
from cartolex.collect.resolve import resolve
from cartolex.collect.tables import raw_folder, rebuild_sources
from cartolex.demo import generate
from cartolex.demo.services import DemoServices


@pytest.fixture(scope="module")
def services():
    with DemoServices(generate("XS", 0)) as svc:
        yield svc


@pytest.fixture()
def collected(services, tmp_path):
    services.faults.clear()
    bib = services.bibliography
    project = demo_project(tmp_path / "p", bib)
    ids = world_ids(project, bib)
    resolve(project, client(services, project))
    confirm_truth(project, bib, ids)
    yield project, bib, ids
    project.close()


def _by_world(project, ids) -> dict[str, object]:
    return {ids[c.person_id]: c for c in person_coverage(project)}


def _pid(ids, world_id: str) -> str:
    return next(p for p, w in ids.items() if w == world_id)


def test_states_follow_the_texts_and_the_identities(services, collected) -> None:
    project, bib, ids = collected
    harvest(project, client(services, project))
    by_world = _by_world(project, ids)
    nobody = by_world[bib.specials["none"]]
    assert nobody.state == "no_data" and nobody.cause == "no_record"
    assert "none" in nobody.cause_text
    for cov in by_world.values():
        assert cov.state in ("good", "no_data")
        if cov.state == "good":
            assert cov.with_abstract >= 3 and cov.cause is None
        else:
            assert cov.texts == 0 and cov.identity == "none"
    # The world's thin people have no record at all here: nothing, never « thin ».
    thin_people = [p.person_id for p in bib.world.people if p.coverage == "thin"]
    assert thin_people and all(by_world[w].state == "no_data" for w in thin_people)
    # A higher threshold makes some profiles thin, with the reason.
    higher = person_coverage(project, good=10)
    thin = [c for c in higher if c.state == "thin"]
    assert thin and all(0 < c.with_abstract < 10 and c.cause == "few_abstracts" for c in thin)
    assert all("fewer than 10" in c.cause_text for c in thin)


def test_not_collected_and_no_works_in_the_window(services, collected) -> None:
    project, bib, ids = collected
    before = {c.person_id: c for c in person_coverage(project)}
    confirmed = [c for c in before.values() if c.identity == "confirmed"]
    assert confirmed and all(c.cause == "not_collected" for c in confirmed)
    harvest(project, client(services, project), years=(2012, 2012))
    after = {c.person_id: c for c in person_coverage(project)}
    empty = [c for c in after.values() if c.identity == "confirmed" and c.texts == 0]
    assert empty and all(c.cause == "no_works_in_window" for c in empty)
    assert any("none in the window" in c.cause_text for c in empty)


def test_works_without_abstracts_are_counted_apart(services, collected) -> None:
    project, bib, ids = collected
    harvest(project, client(services, project))
    before = {c.person_id: c for c in person_coverage(project)}
    # An index that gives the works without their abstracts.
    folder = raw_folder(project.layout, "collected") / "openalex"
    for path in folder.glob("*.jsonl"):
        lines = path.read_text(encoding="utf-8").splitlines()
        out = [lines[0]]
        for line in lines[1:]:
            rec = json.loads(line)
            if rec["type"] == "work":
                rec["record"]["abstract_inverted_index"] = None
            out.append(json.dumps(rec, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    rebuild_sources(project.layout, project.config)
    after = {c.person_id: c for c in person_coverage(project)}
    with_texts = [c for c in after.values() if c.texts]
    assert with_texts
    for cov in with_texts:
        assert cov.state == "thin" and cov.cause == "no_abstracts"
        assert cov.with_abstract == 0 and cov.titles_only == before[cov.person_id].texts
        assert cov.cause_text.startswith("works without abstracts")
    report = coverage_report(project)
    assert all(k.endswith("(title only)") for k in report["by_language"])
    assert all(set(v) == {"titles_only"} for v in report["by_year"].values())


def test_a_service_failure_is_never_no_data(services, collected) -> None:
    project, bib, ids = collected
    wid = next(
        p.person_id
        for p in bib.world.people
        if bib.truth[p.person_id].records
        and all(r.startswith("openalex:") for r in bib.truth[p.person_id].records)
        and p.openalex_id
    )
    pid = _pid(ids, wid)
    aid = bib.world.person(wid).openalex_id
    services.faults.add(
        "status", service="openalex", path=rf"^works\?.*{aid}", status=503, times=None
    )
    report = harvest(project, client(services, project))
    assert [f["person_id"] for f in report.failures] == [pid] and report.people > 1
    cov = next(c for c in person_coverage(project) if c.person_id == pid)
    assert cov.state == "failed" and cov.texts == 0
    assert cov.cause == "service_failure" and "503" in cov.cause_text
    assert cov.failure["finder"] == "harvest" and cov.actions[0] == "retry"
    others = [c for c in person_coverage(project) if c.person_id != pid]
    assert all("retry" not in c.actions for c in others)
    # Nothing of the failure is cached: offline, the answer is missing, not empty.
    offline = client(services, project, mode="cache_only")
    harvest(project, offline, people=[pid])
    cov = next(c for c in person_coverage(project) if c.person_id == pid)
    assert cov.state == "failed" and "not in the cache" in cov.cause_text
    # Once the service answers, a retry of the failed people only supersedes the failure.
    services.faults.clear()
    services.requests.clear()
    reports = retry_failed(project, client(services, project))
    assert reports["harvest"].people == 1
    assert {aid} >= {r.query.get("filter", "").split(":")[1].split(",")[0] for r in services.requests
                     if r.path.startswith("works") and "author.id" in r.query.get("filter", "")}  # fmt: skip
    cov = next(c for c in person_coverage(project) if c.person_id == pid)
    assert cov.state in ("good", "thin") and cov.failure is None


def test_a_failing_service_stops_after_three_people(services, collected) -> None:
    project, _bib, _ids = collected
    services.faults.add("status", service="openalex", path=r"^authors/", status=503, times=None)
    with pytest.raises(ServiceUnavailable):
        harvest(project, client(services, project))
    services.faults.clear()
    failed = [c for c in person_coverage(project) if c.state == "failed"]
    assert len(failed) == 3


def test_aggregates_by_organisation_year_and_language(services, collected) -> None:
    project, bib, ids = collected
    harvest(project, client(services, project))
    report = coverage_report(project)
    assert report["people"] == len(bib.world.people) and report["excluded"] == 0
    assert sum(report["states"].values()) == report["people"]
    orgs = report["by_organisation"]
    assert orgs and all(o["name"] for o in orgs.values())
    groups = {g.name for g in bib.world.groups}
    assert groups & {o["name"] for o in orgs.values()}
    assert set(report["by_year"]) and all(y.isdigit() or y == "unknown" for y in report["by_year"])
    total = sum(sum(v.values()) for v in report["by_year"].values())
    assert total == sum(report["by_language"].values()) == report["slots"]["collected"]["texts"]
    assert {"en", "fr"} <= set(report["by_language"])
    json.dumps(report)  # the interface reads it as JSON


def test_the_person_sheet_names_sources_and_discarded_candidates(services, collected) -> None:
    project, bib, ids = collected
    harvest(project, client(services, project))
    homonym = _pid(ids, bib.specials["homonym"])
    sheet = person_sheet(project, homonym)
    assert {s["finder"] for s in sheet["sources"]} <= {"openalex", "orcid"} and sheet["sources"]
    assert any(d["why"] == "a candidate record not confirmed" for d in sheet["discarded"])
    assert {a["finder"] for a in sheet["attempts"]} >= {"harvest", "resolve"}
    assert all(a["ok"] for a in sheet["attempts"])
    nobody = person_sheet(project, _pid(ids, bib.specials["none"]))
    assert nobody["state"] == "no_data" and nobody["cause"] == "no_record" and not nobody["sources"]
    with pytest.raises(ValueError, match="not a person"):
        person_sheet(project, "p999999")


def test_documents_can_be_added_and_people_excluded(services, collected, tmp_path) -> None:
    project, bib, ids = collected
    harvest(project, client(services, project))
    nobody = _pid(ids, bib.specials["none"])
    docs = tmp_path / "docs"
    docs.mkdir()
    for n in range(3):
        (docs / f"report-{2020 + n}.txt").write_text(
            f"Salt marsh accretion survey number {n} along a sheltered estuary shore.",
            encoding="utf-8",
        )
    report = add_documents(project, nobody, docs)
    assert report.texts == 3 and not report.refused
    cov = next(c for c in person_coverage(project) if c.person_id == nobody)
    assert cov.state == "good" and cov.with_abstract == 3
    exclude_person(project, nobody)
    report = coverage_report(project)
    assert report["excluded"] == 1 and report["people"] == len(bib.world.people) - 1
    excluded = next(p for p in report["persons"] if p["person_id"] == nobody)
    assert excluded["role"] == "excluded" and "exclude" not in excluded["actions"]
