# SPDX-License-Identifier: MIT
"""The HAL finder on the demo archive: idHAL searches, paging, structures, names, faults."""

from __future__ import annotations

import pytest
from _collect_world import SLOT, client_for, project_with_people

from cartolex.collect import Timeouts, rebuild_sources
from cartolex.collect import hal as hal_mod
from cartolex.collect.finders import PersonRef, people_refs
from cartolex.collect.hal import HAL_DOC_TYPES, collect_hal, parse_hal_doc
from cartolex.collect.tables import read_runs
from cartolex.demo import generate
from cartolex.demo.services import DemoServices, sources_layer
from cartolex.project.tables import read_source_table

WINDOW = (2012, 2026)


@pytest.fixture(scope="module")
def services():
    with DemoServices(generate("XS", 0, "en,fr,pt", bodies=True)) as svc:
        yield svc


@pytest.fixture()
def demo(services):
    services.faults.clear()
    services.requests.clear()
    return services


def _expected(demo, idhal: str, window=WINDOW) -> set[str]:
    layer = sources_layer(demo.bibliography)  # built by the services
    return {
        d.hal_id
        for d in layer.deposits.values()
        if any(a.idhal == idhal for a in d.authors) and window[0] <= d.year <= window[1]
    }


def _found(project, person_id: str) -> set[str]:
    out = set()
    for run in read_runs(project.layout, SLOT, "hal"):
        for rec in run.records():
            if rec["type"] == "work" and rec["person_id"] == person_id:
                out.add(rec["doc"]["halId_s"])
    return out


def test_people_are_found_by_idhal_within_the_window(demo, tmp_path) -> None:
    world = demo.world
    project = project_with_people(tmp_path / "p", world)
    people = [p for p in people_refs(project.layout) if p.idhal]
    assert people
    report = collect_hal(client_for(demo, project), project.layout, SLOT, people, window=WINDOW)
    assert not report.failures and report.works > 0
    for p in people:
        assert _found(project, p.person_id) == _expected(demo, p.idhal[0]), p.person_id
    narrow = (2019, 2021)
    project2 = project_with_people(tmp_path / "q", world)
    collect_hal(client_for(demo, project2), project2.layout, SLOT, people, window=narrow)
    for p in people:
        assert _found(project2, p.person_id) == _expected(demo, p.idhal[0], narrow)
    with pytest.raises(ValueError):
        collect_hal(client_for(demo), project.layout, SLOT, people, window=(2020, 2010))


def test_every_page_of_a_cursor_list_is_read(demo, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(hal_mod, "PAGE_ROWS", 2)
    project = project_with_people(tmp_path / "p", demo.world)
    person = max(
        (p for p in people_refs(project.layout) if p.idhal),
        key=lambda p: len(_expected(demo, p.idhal[0])),
    )
    assert len(_expected(demo, person.idhal[0])) > 4
    collect_hal(client_for(demo, project), project.layout, SLOT, [person], window=WINDOW)
    marks = {
        r.query["cursorMark"] for r in demo.requests if r.service == "hal" and r.path == "search/"
    }
    assert len(marks) > 2 and "*" in marks
    assert _found(project, person.person_id) == _expected(demo, person.idhal[0])
    for r in demo.requests:
        if r.path == "search/":
            assert r.query["sort"] == "docid asc" and r.query["wt"] == "json"


def test_deposits_become_texts_parts_authorships_and_structures(demo, tmp_path) -> None:
    project = project_with_people(tmp_path / "p", demo.world)
    people = [p for p in people_refs(project.layout) if p.idhal]
    collect_hal(client_for(demo, project), project.layout, SLOT, people, window=WINDOW)
    rebuild_sources(project.layout, project.config)
    layout = project.layout
    texts = {
        dict(r["ids"])["hal"]: r
        for r in read_source_table(layout.table("texts"), "texts").to_pylist()
    }
    parts = read_source_table(layout.table("text_parts"), "text_parts").to_pylist()
    authorships = read_source_table(layout.table("authorships"), "authorships").to_pylist()
    orgs = {o["org_id"]: o for o in read_source_table(layout.table("organisations"), "organisations").to_pylist()}  # fmt: skip
    affs = read_source_table(layout.table("affiliations"), "affiliations").to_pylist()
    layer = sources_layer(demo.bibliography)
    idhal_people = {p.idhal[0]: p.person_id for p in people}
    for hal_id, text in texts.items():
        d = layer.deposits[hal_id]
        assert text["source"] == "hal" and text["doc_type"] == HAL_DOC_TYPES[d.doc_type]
        assert text["doi"] == (d.doi.lower() if d.doi else None)
        assert text["year"] == d.year and text["n_authors"] == len(d.authors)
        mine = {(p["part"], p["language"]): p for p in parts if p["text_id"] == text["text_id"]}
        for lang, title in d.titles.items():
            assert mine[("title", lang)]["content"] == title
        for lang, abstract in d.abstracts.items():
            assert mine[("abstract", lang)]["content"] == abstract
            assert mine[("abstract", lang)]["provider"] == "hal"
        who = {a["person_id"]: a for a in authorships if a["text_id"] == text["text_id"]}
        for rank, author in enumerate(d.authors, start=1):
            pid = idhal_people.get(author.idhal)
            if pid is None:
                continue
            assert who[pid]["position"] == rank
            assert who[pid]["last"] == (rank == len(d.authors))
            names = {orgs[o]["name"] for o in who[pid]["orgs"]}
            assert names == {layer.structures[s].name for s in author.structures}
    # Structures keep their parents: a lab-level structure belongs to its institution.
    labs = [o for o in orgs.values() if o["parents"]]
    assert labs and all(o["source"] == "hal" for o in orgs.values())
    for lab in labs:
        hal_id = int(dict(lab["ids"])["hal"])
        parent = layer.structures[hal_id].parents[0]
        assert {dict(orgs[p]["ids"])["hal"] for p in lab["parents"]} == {str(parent)}
    assert affs and {a["source"] for a in affs} == {"stated"}


def test_a_name_only_proposes_candidates(demo, tmp_path) -> None:
    project = project_with_people(tmp_path / "p", demo.world)
    layer = sources_layer(demo.bibliography)
    homonym = demo.bibliography.specials["homonym"]
    person = next(p for p in people_refs(project.layout) if p.person_id == homonym)
    nameless = PersonRef(person.person_id, person.last_name, person.first_name)
    report = collect_hal(client_for(demo, project), project.layout, SLOT, [nameless], window=WINDOW)
    assert report.works == 0 and report.candidates
    forms = {c["idhal"] or c["full_name"]: c for c in report.candidates}
    outside = {d.hal_id for d in layer.deposits.values() if d.world_work is None}
    assert outside, "the demo archive holds an outside homonym's deposits"
    by_outside = [c for c in report.candidates if set(c["works"]) & outside]
    assert by_outside and all(c["idhal"] is None for c in by_outside)
    if person.idhal:  # the person's own deposits come under their idHAL, apart from the homonym
        assert person.idhal[0] in forms
        assert not set(forms[person.idhal[0]]["works"]) & outside
    rebuild_sources(project.layout, project.config)
    texts = read_source_table(project.layout.table("texts"), "texts")
    assert texts.num_rows == 0  # a candidate never becomes a text
    assert read_runs(project.layout, SLOT, "hal_candidates")


@pytest.mark.parametrize(
    ("kind", "options"),
    [
        ("status", {"status": 429, "retry_after": "0"}),
        ("status", {"status": 503}),
        ("malformed", {}),
        ("drop", {}),
        ("cut_page", {}),
        ("early_end", {}),
        ("hang", {"delay": 2.0}),
    ],
)
def test_a_fault_once_is_overcome(demo, tmp_path, kind, options) -> None:
    project = project_with_people(tmp_path / "p", demo.world)
    person = max(
        (p for p in people_refs(project.layout) if p.idhal),
        key=lambda p: len(_expected(demo, p.idhal[0])),
    )
    demo.faults.add(kind, service="hal", path=r"^search", times=1, **options)
    client = client_for(demo, project, timeouts=Timeouts(connect=2.0, read=0.5))
    report = collect_hal(client, project.layout, SLOT, [person], window=WINDOW)
    assert not report.failures
    assert _found(project, person.person_id) == _expected(demo, person.idhal[0])


def test_a_service_that_keeps_failing_is_reported_then_left(demo, tmp_path) -> None:
    project = project_with_people(tmp_path / "p", demo.world)
    people = [p for p in people_refs(project.layout) if p.idhal]
    demo.faults.add("status", service="hal", status=500, times=None)
    report = collect_hal(client_for(demo, project), project.layout, SLOT, people, window=WINDOW)
    assert len(report.failures) == 3 and report.stopped
    assert report.skipped["not searched"] == len(people) - 3
    assert report.works == 0


def test_a_malformed_record_is_skipped_not_fatal() -> None:
    assert parse_hal_doc({"halId_s": "hal-0990001", "title_s": ["A"], "docType_s": "IMG"}) is None
    with pytest.raises(ValueError):
        parse_hal_doc({"docType_s": "ART"})
    work = parse_hal_doc(
        {
            "halId_s": "hal-09900001",
            "docType_s": "COMM",
            "title_s": ["Un titre", "A title"],
            "fr_title_s": ["Un titre"],
            "en_title_s": ["A title"],
            "language_s": ["fr"],
            "doiId_s": "https://doi.org/10.5555/ABC",
            "authFullName_s": ["Ada Varno", "Ivo Quell"],
            "authIdHalFullName_fs": ["demo-ivo-quell_FacetSep_Ivo Quell"],
            "authIdHasStructure_fs": ["7_FacetSep_Ivo Quell_JoinSep_9900001_FacetSep_Tide Lab"],
            "producedDateY_i": 2020,
        }
    )
    assert work is not None and work.doc_type == "communication" and work.doi == "10.5555/abc"
    assert work.title == "Un titre" and work.titles == [("en", "A title"), ("fr", "Un titre")]
    form = work.form_of("demo-ivo-quell")
    assert form is not None and form.rank == 2 and form.structures == ((9900001, "Tide Lab"),)


def test_a_malformed_record_in_an_answer_is_skipped(demo, tmp_path) -> None:
    project = project_with_people(tmp_path / "p", demo.world)
    person = next(p for p in people_refs(project.layout) if p.idhal)
    expected = _expected(demo, person.idhal[0])
    service = demo.server.services["hal"]
    broken = sorted(expected)[0]
    saved = service._docs[broken]
    service._docs[broken] = {k: v for k, v in saved.items() if k != "title_s"}
    try:
        report = collect_hal(
            client_for(demo, project), project.layout, SLOT, [person], window=WINDOW
        )
    finally:
        service._docs[broken] = saved
    assert report.skipped == {"malformed record": 1}
    assert _found(project, person.person_id) == expected - {broken}


def test_a_name_candidate_is_confirmed_by_its_idhal_like_any_record(demo, tmp_path) -> None:
    """A HAL author form proposed by name is confirmed as hal:<idHAL> with resolve.confirm,
    the same way as an OpenAlex record; the next search finds the person's deposits."""
    import pyarrow as pa

    from cartolex.collect.resolve import confirm, identity_queue, parse_record
    from cartolex.project.tables import SOURCE_SCHEMAS, write_source_table

    project = project_with_people(tmp_path / "p", demo.world)
    path = project.layout.table("people")
    rows = read_source_table(path, "people").to_pylist()
    target = next(r for r in rows if dict(r["ids"]).get("idhal"))
    idhal = dict(target["ids"])["idhal"][0]
    for r in rows:  # the list gave no idHAL
        r["ids"] = []
    schema = SOURCE_SCHEMAS["people"]
    write_source_table(
        path, "people", pa.table({f.name: [r[f.name] for r in rows] for f in schema}, schema=schema)
    )
    person = next(p for p in people_refs(project.layout) if p.person_id == target["person_id"])
    assert not person.idhal
    report = collect_hal(client_for(demo, project), project.layout, SLOT, [person], window=WINDOW)
    assert report.works == 0
    form = next(c for c in report.candidates if c["idhal"] == idhal)
    assert form["record"] == f"hal:{idhal}" and parse_record(form["record"]) == form["record"]
    (entry,) = [q for q in identity_queue(project) if q["person_id"] == person.person_id]
    assert f"hal:{idhal}" in {c["record"] for c in entry["candidates"] if c["finder"] == "hal"}
    confirm(project, person.person_id, [form["record"]])
    confirmed = next(p for p in people_refs(project.layout) if p.person_id == person.person_id)
    assert confirmed.idhal == (idhal,)
    found = collect_hal(client_for(demo, project), project.layout, SLOT, [confirmed], window=WINDOW)
    assert found.found[person.person_id] == len(_expected(demo, idhal))
    with pytest.raises(ValueError, match="hal:<idHAL>"):
        confirm(project, person.person_id, ["hal:"])
