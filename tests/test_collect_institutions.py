# SPDX-License-Identifier: MIT
"""People from institutions: sub-units, several parents, a person who moved, split records."""

from __future__ import annotations

import pytest
from _collect_world import LEVELS, client

from cartolex.collect.http import IncompleteResults
from cartolex.collect.institutions import (
    find_institutions,
    propose_levels,
    propose_people,
    take_people,
)
from cartolex.collect.openalex import OpenAlexApi
from cartolex.collect.resolve import confirm
from cartolex.collect.snapshot import Snapshot, SnapshotSource
from cartolex.collect.tables import read_runs
from cartolex.demo import generate
from cartolex.demo.services import DemoServices, write_snapshot
from cartolex.project import Project
from cartolex.project.checkpoints import JobPaused
from cartolex.project.tables import read_decision_csv, read_source_table


@pytest.fixture(scope="module")
def services():
    with DemoServices(generate("S", 0)) as svc:
        yield svc


@pytest.fixture()
def project(tmp_path, services):
    services.faults.clear()
    p = Project.init(tmp_path / "p", name="By institution", domain_title="Coastal systems")
    p.save_config(p.config.model_copy(update={"levels": LEVELS}), action="levels")
    yield p
    p.close()


def _api(services, project):
    return OpenAlexApi(client(services, project))


def _table(project, name):
    return read_source_table(project.layout.table(name), name).to_pylist()


def test_an_institution_brings_its_units_and_their_people(services, project) -> None:
    bib = services.bibliography
    joint = bib.institutions[bib.joint_lab]
    top = bib.institutions[joint.parent]
    proposal = propose_people(project, _api(services, project), [top.id])
    labs = {i.id for i in bib.institutions.values() if top.id in i.parents}
    assert labs and labs | {top.id} <= set(proposal.units)
    assert proposal.levels == {"education": "institution", "facility": "lab"}
    assert proposal.people and all(p.works >= 2 for p in proposal.people)
    stated = {u["id"] for p in proposal.people for u in p.units}
    assert stated & labs, "people of the labs whose works cite them are found through the lab"
    # Every author proposed signed at least two works there, with a unit of the tree.
    for person in proposal.people:
        assert {u["id"] for u in person.units} <= set(proposal.units)
        assert sum(u["works"] for u in person.units) >= person.works
    # The proposal is kept, and builds no row.
    assert read_runs(project.layout, proposal.slot, "institution_proposals")
    assert not project.layout.table("people").exists() or not _table(project, "people")


def test_taking_people_gives_levels_and_every_parent(services, project) -> None:
    bib = services.bibliography
    joint = bib.institutions[bib.joint_lab]
    top = bib.institutions[joint.parent]
    proposal = propose_people(project, _api(services, project), [top.id])
    report = take_people(project, "all")
    assert len(report.taken) == len(proposal.people) and not report.known
    orgs = {o["ids"][0][1] if o["ids"] else None: o for o in _table(project, "organisations")}
    by_openalex = {dict(o["ids"])["openalex"]: o for o in _table(project, "organisations")}
    assert by_openalex[top.id]["level"] == "institution"
    joint_row = by_openalex[joint.id]
    assert joint_row["level"] == "lab"
    parents = {o["org_id"]: o for o in by_openalex.values()}
    assert sorted(dict(parents[p]["ids"])["openalex"] for p in joint_row["parents"]) == sorted(
        joint.parents
    )
    assert dict(by_openalex[top.id]["ids"])["ror"] == top.ror
    people = read_decision_csv(project.layout.people_csv, "people")
    assert {r["role"] for r in people} == {"mapped"} and {r["identity"] for r in people} == {
        "confirmed"
    }
    assert {r["records"] for r in people} == {p.record for p in proposal.people}
    assert {p["source"] for p in _table(project, "people")} == {"institution"}
    assert orgs  # every taken person's units are organisations of the tables
    # Taking them again finds them already there.
    again = take_people(project, "all")
    assert not again.taken and set(again.known) == {p.record for p in proposal.people}


def test_a_person_who_moved_keeps_the_years_they_were_there(services, project) -> None:
    bib = services.bibliography
    moved = bib.world.person(bib.specials["moved"])
    record = bib.authors[moved.openalex_id]
    earlier = {
        i
        for w in record.works
        for a in bib.works[w].authorships
        if a.person_id == moved.person_id and bib.works[w].year < bib.moved_year
        for i in a.institutions
    }
    (outside,) = earlier
    proposal = propose_people(project, _api(services, project), [outside], min_works=1)
    person = next(p for p in proposal.people if p.record == f"openalex:{moved.openalex_id}")
    assert person.last_year < bib.moved_year
    take_people(project, [moved.openalex_id])
    (pid,) = [r["person_id"] for r in read_decision_csv(project.layout.people_csv, "people")]
    org = next(o for o in _table(project, "organisations") if dict(o["ids"])["openalex"] == outside)
    (aff,) = [a for a in _table(project, "affiliations") if a["org_id"] == org["org_id"]]
    assert aff["person_id"] == pid and aff["source"] == "stated"
    assert aff["start_year"] == person.first_year and aff["end_year"] == person.last_year
    assert aff["end_year"] < bib.moved_year


def test_split_records_are_suggested_never_merged(services, project) -> None:
    bib = services.bibliography
    split = bib.world.person(bib.specials["split"])
    records = sorted(f"openalex:{r}" for r in bib.record_ids(split.person_id))
    inst = bib.works[bib.authors[split.openalex_id].works[0]].authorships
    stated = next(a.institutions[0] for a in inst if a.person_id == split.person_id)
    root = bib.institutions[stated].parent or stated
    proposal = propose_people(project, _api(services, project), [root], min_works=1)
    suggested = [m for m in proposal.merges if sorted(m.records) == records]
    assert suggested and "same name" in suggested[0].reason
    shown = {p.record for p in proposal.people}
    assert set(records) <= shown, "both records stay proposed on their own"
    main, other = (r.split(":")[1] for r in records)
    report = take_people(project, [f"{main}+{other}"])
    ((pid, taken),) = report.taken.items()
    assert sorted(taken) == records
    person = next(p for p in _table(project, "people") if p["person_id"] == pid)
    assert sorted(dict(person["ids"])["openalex"]) == sorted(r.split(":")[1] for r in records)
    assert person["aliases"], "the other record's name form is an alias"


def test_a_shared_orcid_is_a_suggested_merge(services, project) -> None:
    from cartolex.collect.institutions import _Authors, _merges, _TableView

    authors = {
        "A1": {"name": "Ada Tavelin", "orcid": "0000-0000-0000-0001", "works": [{"id": "W1", "year": 2020, "units": ["I1"]}]},
        "A2": {"name": "A. Tavelin-Quell", "orcid": "0000-0000-0000-0001", "works": [{"id": "W2", "year": 2021, "units": ["I2"]}]},
        "A3": {"name": "Ada Tavelin", "orcid": "0000-0000-0000-0002", "works": [{"id": "W3", "year": 2021, "units": ["I1"]}]},
    }  # fmt: skip
    table = _Authors()
    for aid, rec in authors.items():
        table.add_entry({**rec, "record": f"openalex:{aid}"})
    merges = {tuple(m.records): m.reason for m in _merges(_TableView(table), 1)}
    assert merges == {("openalex:A1", "openalex:A2"): "the same ORCID"}


def test_institutions_by_ror_by_name_and_refusals(services, project) -> None:
    bib = services.bibliography
    top = next(i for i in bib.institutions.values() if i.ror and i.name in bib.institution_of)
    api = _api(services, project)
    found = find_institutions(api, top.name)
    assert found[0]["id"] == top.id and found[0]["ror"] == top.ror
    by_ror = propose_people(project, api, [f"https://ror.org/{top.ror}"])
    assert by_ror.roots == [top.id]
    with pytest.raises(ValueError, match="no such institution"):
        propose_people(project, api, ["I9990000000"])
    with pytest.raises(ValueError, match="not an OpenAlex institution id"):
        propose_people(project, api, ["nothing"])


def test_levels_are_proposed_and_editable() -> None:
    from cartolex.collect.institutions import Unit

    units = {
        "I1": Unit("I1", "A university", "education", None, [], ["I1"]),
        "I2": Unit("I2", "A lab", "facility", None, ["I1"], ["I2", "I1"]),
        "I3": Unit("I3", "A team", "other", None, ["I2"], ["I3", "I2", "I1"]),
    }
    assert propose_levels(units, LEVELS) == {
        "education": "institution",
        "facility": "lab",
        "other": "lab",
    }
    edited = propose_levels(units, LEVELS, {"facility": "institution"})
    assert edited["facility"] == "institution"
    assert propose_levels(units, [])["education"] == "institution"


def test_a_person_confirmed_before_is_not_taken_twice(services, project) -> None:
    from cartolex.collect.people_import import import_people

    bib = services.bibliography
    top = bib.institutions[bib.institutions[bib.joint_lab].parent]
    proposal = propose_people(project, _api(services, project), [top.id])
    first = proposal.people[0]
    last, given = first.name.rsplit(" ", 1)[1], first.name.rsplit(" ", 1)[0]
    import_people(project, f"last_name,first_name\n{last},{given}\n")
    (pid,) = [r["person_id"] for r in read_decision_csv(project.layout.people_csv, "people")]
    confirm(project, pid, [first.record])
    report = take_people(project, [first.record])
    assert report.known == {first.record: pid} and not report.taken


def test_a_page_cut_short_pauses_and_keeps_no_proposal(services, project) -> None:
    bib = services.bibliography
    top = bib.institutions[bib.institutions[bib.joint_lab].parent]
    services.faults.add("cut_page", service="openalex", path=r"^works\?", times=None)
    with pytest.raises(JobPaused) as paused:
        propose_people(project, _api(services, project), [top.id])
    services.faults.clear()
    assert isinstance(paused.value.cause, IncompleteResults)
    slot = project.config.slots[0].id
    assert not read_runs(project.layout, slot, "institution_proposals")


def test_the_snapshot_gives_the_same_proposal(services, project, tmp_path) -> None:
    bib = services.bibliography
    top = bib.institutions[bib.institutions[bib.joint_lab].parent]
    folder = tmp_path / "snapshot"
    write_snapshot(bib, folder)
    by_api = propose_people(project, _api(services, project), [top.id], years=(2015, 2026))
    services.requests.clear()
    source = SnapshotSource(Snapshot(folder))
    by_snapshot = propose_people(project, source, [top.id], years=(2015, 2026))
    assert services.requests == []
    assert by_snapshot.people == by_api.people and by_snapshot.merges == by_api.merges
    assert by_snapshot.units == by_api.units and by_snapshot.levels == by_api.levels
