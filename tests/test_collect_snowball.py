# SPDX-License-Identifier: MIT
"""Collaborators round by round: two rounds, the cap, large collaborations, fit, paths."""

from __future__ import annotations

import pytest
from _collect_world import client, confirm_truth, demo_project, world_ids

from cartolex.collect.harvest import harvest
from cartolex.collect.http import ServiceUnavailable
from cartolex.collect.openalex import (
    AUTHORS_SHOWN,
    OpenAlexApi,
    fit_works,
    most_recent,
    works_of_authors,
)
from cartolex.collect.snapshot import Snapshot, SnapshotSource
from cartolex.collect.snowball import (
    decide_collaborators,
    read_snowball,
    snowball,
    topical_fit,
)
from cartolex.collect.tables import read_runs
from cartolex.demo import generate
from cartolex.demo.services import DemoServices, write_snapshot
from cartolex.project.tables import read_decision_csv, read_source_table


@pytest.fixture(scope="module")
def services():
    with DemoServices(generate("S", 0)) as svc:
        yield svc


def _group_of_the_collaboration(bib) -> str:
    member = next(a.person_id for a in bib.works[bib.consortium].authorships if a.person_id)
    return bib.world.person(member).group


@pytest.fixture()
def seeded(services, tmp_path):
    """A project holding one group only: the seeds, confirmed as the truth says."""
    services.faults.clear()
    bib = services.bibliography
    group = _group_of_the_collaboration(bib)
    rows = [
        r
        for r, p in zip(bib.people_rows(), bib.world.people, strict=True)
        if p.group == group and p.role == "cohort"
    ]
    project = demo_project(tmp_path / "p", bib, rows)
    ids = world_ids(project, bib)
    confirm_truth(project, bib, ids)
    yield project, bib, ids
    project.close()


def _people(project) -> dict[str, dict]:
    return {r["person_id"]: r for r in read_decision_csv(project.layout.people_csv, "people")}


def test_two_rounds_with_their_paths_and_the_default_role(services, seeded) -> None:
    project, bib, ids = seeded
    api = OpenAlexApi(client(services, project))
    first = snowball(project, api)
    seeds = set(first.seeds)
    assert seeds and first.rounds == [{"round": 1, "proposed": len(first.collaborators)}]
    assert first.collaborators
    for c in first.collaborators:
        assert c.path[0] in seeds and c.path[-1] == c.person_id and len(c.path) == 2
        assert set(c.parents) <= seeds and c.joint_texts >= 1
    second = snowball(project, api)
    assert [r["round"] for r in second.rounds] == [2]
    round1 = {c.person_id for c in first.collaborators}
    for c in second.collaborators:
        assert len(c.path) == 3 and c.path[0] in seeds and c.path[1] in round1
        assert set(c.parents) <= round1
    rows = read_snowball(project)
    assert {r["round"] for r in rows} == {"1", "2"}
    assert {r["decision"] for r in rows} == {"context"}
    people = _people(project)
    for r in rows:
        person = people[r["person_id"]]
        assert person["role"] == "context" and person["identity"] == "confirmed"
        assert r["path"].split(">")[0] in seeds and r["path"].endswith(r["person_id"])
    sources = {p["source"] for p in read_source_table(project.layout.table("people"), "people")
               .to_pylist() if p["person_id"] in {r["person_id"] for r in rows}}  # fmt: skip
    assert sources == {"collaborators"}
    # Their texts come with the harvest, as everyone's.
    report = harvest(project, client(services, project), people=[rows[0]["person_id"]])
    assert report.people == 1


def test_a_thirty_author_work_is_left_out_of_the_graph(services, seeded) -> None:
    project, bib, ids = seeded
    big = bib.works[bib.consortium]
    outside = {f"openalex:{a.author_id}" for a in big.authorships if a.person_id is None}
    api = OpenAlexApi(client(services, project))
    report = snowball(project, api, rounds=1)
    assert report.large_works >= 1 and report.max_authors == 25
    assert not outside & {c.record for c in report.collaborators}
    assert any("more than 25 authors" in line for line in report.lines())


def test_a_higher_threshold_keeps_the_large_work(services, seeded) -> None:
    project, bib, ids = seeded
    big = bib.works[bib.consortium]
    outside = {f"openalex:{a.author_id}" for a in big.authorships if a.person_id is None}
    params, fp = project.read_params()
    project.save_params(
        params.model_copy(update={"collect": {"snowball": {"max_authors": 40, "cap": 500}}}),
        expected=fp,
        action="collaborators",
    )
    report = snowball(project, OpenAlexApi(client(services, project)))
    assert report.max_authors == 40 and report.large_works == 0
    assert outside <= {c.record for c in report.collaborators}


def test_a_cut_author_list_is_asked_whole_only_when_the_graph_could_keep_it(
    services, seeded, tmp_path, monkeypatch
) -> None:
    project, bib, _ids = seeded
    openalex = services.server.services["openalex"]
    big = bib.works[bib.consortium]
    try:
        # Lists show 26 of the 30 authors: more than 25, the work leaves the graph as it is.
        openalex.authors_shown = 26
        monkeypatch.setattr("cartolex.collect.openalex.AUTHORS_SHOWN", 26)
        services.requests.clear()
        report = snowball(project, OpenAlexApi(client(services, project)), cap=500)
        assert report.large_works >= 1
        assert not [r for r in services.requests if r.path.startswith("works/")]
        outside = {f"openalex:{a.author_id}" for a in big.authorships if a.person_id is None}
        assert not outside & {c.record for c in report.collaborators}
        # With up to 40 authors kept, the cut work is asked for whole, and kept.
        other = demo_project(tmp_path / "other", bib, _rows_of(project, bib))
        confirm_truth(other, bib, world_ids(other, bib))
        services.requests.clear()
        kept = snowball(other, OpenAlexApi(client(services, other)), cap=500, max_authors=40)
        assert f"works/{big.id}" in {r.path for r in services.requests}
        assert kept.large_works == 0 and outside <= {c.record for c in kept.collaborators}
        other.close()
    finally:
        openalex.authors_shown = AUTHORS_SHOWN


def test_a_fit_reads_the_most_recent_works_one_request_per_prolific_author(
    services, seeded
) -> None:
    project, bib, _ids = seeded
    http = client(services)
    ids = sorted(a.id for a in bib.authors.values())[:12]
    whole, _answers = works_of_authors(http, ids)
    services.requests.clear()
    found = fit_works(http, ids, limit=2)
    for aid in ids:
        recent, total = found[aid]
        assert total == len(whole[aid])
        assert [w["id"] for w in recent] == [w["id"] for w in most_recent(whole[aid], 2)]
    # The works counts in one list, the authors of two works or fewer together, and one
    # request (one page, the most recent first) for each of the others.
    prolific = [a for a in ids if len(whole[a]) > 2]
    assert prolific and len(prolific) < len(ids)
    listed = [r for r in services.requests if r.path in ("works", "authors")]
    one_page = [r for r in listed if r.query.get("sort") == "publication_date:desc"]
    assert len(one_page) == len(prolific) and all(r.query["per_page"] == "2" for r in one_page)
    assert len([r for r in listed if r.path == "authors"]) == 1
    batched = [r for r in listed if r.path == "works" and "sort" not in r.query]
    assert len({r.query["filter"] for r in batched}) == 1  # one list (its pages)


def test_the_cap_cuts_a_whole_round_and_names_it(services, seeded) -> None:
    project, bib, ids = seeded
    api = OpenAlexApi(client(services, project))
    probe = snowball(project, api, rounds=1)
    n1 = len(probe.collaborators)
    decide_collaborators(project, {c.person_id: "context" for c in probe.collaborators})
    report = snowball(project, api, rounds=1, cap=n1 + 1)
    assert report.rounds == [] and report.cut and report.cut.startswith("round 2 (")
    assert f"cap of {n1 + 1}" in report.cut
    assert {r["round"] for r in read_snowball(project)} == {"1"}
    assert "warning: round 2" in "\n".join(report.lines())
    # The run keeps the cut, and no one of the cut round entered the tables.
    (last,) = read_runs(project.layout, "collected", "snowball")[-1:]
    assert last.header["cut"] == report.cut and not list(last.records())


def test_the_cap_can_cut_the_first_round(services, seeded) -> None:
    project, _bib, _ids = seeded
    report = snowball(project, OpenAlexApi(client(services, project)), cap=1)
    assert report.cut.startswith("round 1 (") and not report.collaborators
    assert not read_snowball(project)


def test_fit_ranks_the_seeds_topics_first(services, seeded) -> None:
    project, bib, ids = seeded
    report = snowball(project, OpenAlexApi(client(services, project)), rounds=2, cap=500)
    inside = [c for c in report.collaborators if _world(bib, c.record) is not None]
    outside = [c for c in report.collaborators if _world(bib, c.record) is None]
    assert inside and outside
    mean = lambda cs: sum(c.fit for c in cs) / len(cs)  # noqa: E731
    assert mean(inside) > mean(outside)
    # In round 1, the world's people (on the field's themes) all rank above the outside
    # co-authors (whose other works are on other themes).
    first = sorted((c for c in report.collaborators if c.round == 1), key=lambda c: -c.fit)
    kinds = [_world(bib, c.record) is not None for c in first]
    assert kinds == sorted(kinds, reverse=True) and True in kinds and False in kinds
    assert all(0.0 <= c.fit <= 1.0 for c in report.collaborators)
    # The measure itself: the same words fit 1, other words 0.
    works = [{"id": "W1", "title": "Sediment plume dynamics", "abstract_inverted_index": None}]
    other = [{"id": "W2", "title": "Fishery governance ethics", "abstract_inverted_index": None}]
    fits = topical_fit({"s": works}, {"same": works, "other": other})
    assert fits == {"same": 1.0, "other": 0.0}


def _world(bib, record: str) -> str | None:
    rec = bib.authors.get(record.split(":", 1)[1])
    return rec.person_id if rec is not None else None


def test_decisions_change_roles(services, seeded) -> None:
    project, _bib, _ids = seeded
    report = snowball(project, OpenAlexApi(client(services, project)))
    a, b, *rest = [c.person_id for c in report.collaborators] + [None, None]
    decisions = {a: "no"} | ({b: "projected"} if b else {})
    decide_collaborators(project, decisions)
    people = _people(project)
    assert people[a]["role"] == "excluded"
    if b:
        assert people[b]["role"] == "projected" and people[b]["set"] == "collaborators"
        assert "collaborators" in {o.id for o in project.config.overlays}
    rows = {r["person_id"]: r["decision"] for r in read_snowball(project)}
    assert rows[a] == "no"
    with pytest.raises(ValueError, match="not a decision"):
        decide_collaborators(project, {a: "maybe"})
    # A refused collaborator is not a starting point of the next round.
    after = snowball(project, OpenAlexApi(client(services, project)))
    assert all(a not in c.parents for c in after.collaborators)


def test_a_failing_service_writes_nothing(services, seeded) -> None:
    project, _bib, _ids = seeded
    services.faults.add("status", service="openalex", path=r"^works", status=503, times=None)
    with pytest.raises(ServiceUnavailable):
        snowball(project, OpenAlexApi(client(services, project)))
    services.faults.clear()
    assert not read_snowball(project)
    assert not read_runs(project.layout, "collected", "snowball")


def test_the_snapshot_gives_the_same_rounds(services, seeded, tmp_path) -> None:
    project, bib, ids = seeded
    folder = tmp_path / "snapshot"
    write_snapshot(bib, folder)
    other = demo_project(tmp_path / "other", bib, _rows_of(project, bib))
    confirm_truth(other, bib, world_ids(other, bib))
    by_api = snowball(project, OpenAlexApi(client(services, project)), rounds=2, cap=500)
    services.requests.clear()
    by_snapshot = snowball(other, SnapshotSource(Snapshot(folder)), rounds=2, cap=500)
    assert services.requests == []
    assert read_snowball(other) == [
        dict(r, decided_at=read_snowball(other)[0]["decided_at"]) for r in read_snowball(project)
    ]
    assert [c.fit for c in by_snapshot.collaborators] == [c.fit for c in by_api.collaborators]
    other.close()


def _rows_of(project, bib) -> list[dict]:
    names = {
        (p["last_name"], p["first_name"])
        for p in read_source_table(project.layout.table("people"), "people").to_pylist()
    }
    return [r for r in bib.people_rows() if (r["last_name"], r["first_name"]) in names]
