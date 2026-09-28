# SPDX-License-Identifier: MIT
"""The SciELO finder on the demo journal platform: ORCIDs accept, names propose, faults."""

from __future__ import annotations

import pytest
from _collect_world import SLOT, client_for, project_with_people

from cartolex.collect import rebuild_sources
from cartolex.collect import scielo as scielo_mod
from cartolex.collect.finders import people_refs
from cartolex.collect.scielo import collect_scielo, parse_scielo_article
from cartolex.collect.tables import read_runs
from cartolex.demo import generate
from cartolex.demo.services import DemoServices, sources_layer
from cartolex.demo.services.sources import SCIELO_COLLECTION
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


def _expected(demo, window=WINDOW) -> set[str]:
    """Articles showing the ORCID of someone in the project, in the window."""
    orcids = {p.orcid for p in demo.world.people if p.orcid}
    return {
        a.pid
        for a in sources_layer(demo.bibliography).scielo.values()
        if window[0] <= a.year <= window[1] and any(o in orcids for _g, _s, o, _a in a.authors)
    }


def _kept(project) -> set[str]:
    return {
        rec["code"]
        for run in read_runs(project.layout, SLOT, "scielo")
        for rec in run.records()
        if rec["type"] == "article"
    }


def _collect(demo, project, **kw):
    people = people_refs(project.layout)
    kw.setdefault("window", WINDOW)
    kw.setdefault("collection", SCIELO_COLLECTION)
    return collect_scielo(client_for(demo, project), project.layout, SLOT, people, **kw)


def test_articles_with_a_persons_orcid_are_kept_with_every_abstract(demo, tmp_path) -> None:
    project = project_with_people(tmp_path / "p", demo.world)
    report = _collect(demo, project)
    assert _kept(project) == _expected(demo) and report.works == len(_expected(demo))
    assert not report.failures
    rebuild_sources(project.layout, project.config)
    layout = project.layout
    layer = sources_layer(demo.bibliography)
    texts = {
        dict(r["ids"])["scielo"].split(":", 1)[1]: r
        for r in read_source_table(layout.table("texts"), "texts").to_pylist()
    }
    assert set(texts) == _expected(demo)
    parts = read_source_table(layout.table("text_parts"), "text_parts").to_pylist()
    authorships = read_source_table(layout.table("authorships"), "authorships").to_pylist()
    by_orcid = {p.orcid: p.person_id for p in demo.world.people if p.orcid}
    trilingual = 0
    for pid, text in texts.items():
        a = layer.scielo[pid]
        assert text["doi"] == a.doi and text["year"] == a.year and text["doc_type"] == "article"
        mine = {
            (p["part"], p["language"]): p["content"]
            for p in parts
            if p["text_id"] == text["text_id"]
        }
        assert {lang for part, lang in mine if part == "abstract"} == set(a.abstracts)
        for lang, abstract in a.abstracts.items():
            assert mine[("abstract", lang)] == abstract
        trilingual += len(a.abstracts) >= 3
        who = {
            x["person_id"]: x["position"] for x in authorships if x["text_id"] == text["text_id"]
        }
        want = {
            by_orcid[o]: rank for rank, (_g, _s, o, _a) in enumerate(a.authors, 1) if o in by_orcid
        }
        assert who == want
    assert trilingual, "some articles have three abstracts"


def test_author_keywords_are_never_read(demo, tmp_path) -> None:
    service = demo.server.services["scielo"]
    original = service._json

    def with_marker(article):
        data = original(article)
        data["article"]["v85"] = [{"l": "en", "k": "zzmarkerkeyword", "_": ""}]
        return data

    service._json = with_marker
    try:
        project = project_with_people(tmp_path / "p", demo.world)
        _collect(demo, project)
        rebuild_sources(project.layout, project.config)
    finally:
        service._json = original
    parts = read_source_table(project.layout.table("text_parts"), "text_parts")
    assert parts.num_rows and not any("zzmarker" in c for c in parts["content"].to_pylist())


def test_a_name_without_orcid_only_proposes(demo, tmp_path) -> None:
    project = project_with_people(tmp_path / "p", demo.world, orcids=False)
    report = _collect(demo, project)
    assert report.works == 0 and report.candidates
    assert {c["article"].rsplit(":", 1)[1] for c in report.candidates} <= set(
        sources_layer(demo.bibliography).scielo
    )
    rebuild_sources(project.layout, project.config)
    assert read_source_table(project.layout.table("texts"), "texts").num_rows == 0
    assert read_runs(project.layout, SLOT, "scielo_candidates")


def test_the_window_journals_and_codes_narrow_what_is_read(demo, tmp_path) -> None:
    layer = sources_layer(demo.bibliography)
    project = project_with_people(tmp_path / "p", demo.world)
    _collect(demo, project, window=(2019, 2022))
    assert _kept(project) == _expected(demo, (2019, 2022))
    issn = sorted({a.issn for a in layer.scielo.values()})[0]
    project = project_with_people(tmp_path / "q", demo.world)
    _collect(demo, project, issns=[issn])
    assert _kept(project) == {p for p in _expected(demo) if layer.scielo[p].issn == issn}
    code = sorted(_expected(demo))[0]
    project = project_with_people(tmp_path / "r", demo.world)
    report = _collect(demo, project, codes=[code, "S9990-0000-unknown"])
    assert _kept(project) == {code} and report.skipped["unknown article"] == 1
    with pytest.raises(ValueError, match="ISSN"):
        _collect(demo, project, max_articles=1)


@pytest.mark.parametrize(
    ("kind", "path", "options"),
    [
        ("cut_page", r"identifiers", {}),
        ("early_end", r"identifiers", {}),
        ("status", r"identifiers", {"status": 503}),
        ("malformed", r"article/\?", {}),
        ("status", r"article/\?", {"status": 429, "retry_after": "0"}),
        ("drop", r"article/\?", {}),
    ],
)
def test_a_fault_is_overcome_without_losing_an_article(
    demo, tmp_path, monkeypatch, kind, path, options
) -> None:
    monkeypatch.setattr(scielo_mod, "PAGE_LIMIT", 4)  # several listing pages
    project = project_with_people(tmp_path / "p", demo.world)
    demo.faults.add(kind, service="scielo", path=path, times=1, **options)
    report = _collect(demo, project)
    assert not report.failures
    assert _kept(project) == _expected(demo)


def test_an_article_that_keeps_failing_is_reported(demo, tmp_path) -> None:
    project = project_with_people(tmp_path / "p", demo.world)
    code = sorted(_expected(demo))[0]
    demo.faults.add("status", service="scielo", path=code, status=500, times=None)
    report = _collect(demo, project)
    assert [f["article"] for f in report.failures] == [code]
    assert _kept(project) == _expected(demo) - {code}


def test_reading_an_article_record() -> None:
    assert parse_scielo_article({"code": "S1", "document_type": "correction",
                                 "article": {"v12": [{"l": "en", "_": "T"}]}}) is None  # fmt: skip
    with pytest.raises(ValueError):
        parse_scielo_article({"article": {}})
    work = parse_scielo_article(
        {
            "code": "S9990-00012020000100001",
            "collection": "dmo",
            "doi": "10.5555/X",
            "publication_year": "2020",
            "article": {
                "v12": [{"l": "pt", "_": "Um título"}, {"l": "en", "_": "A title"}],
                "v40": [{"_": "pt"}],
                "v83": [{"l": "pt", "a": "Resumo."}, {"l": "en", "_": "Abstract."}],
                "v85": [{"l": "en", "k": "a keyword"}],
                "v10": [{"n": "Ada", "s": "Varno", "k": "0000-0000-0000-0001"}],
            },
        }
    )
    assert work is not None and work.title == "Um título" and work.doi == "10.5555/x"
    assert work.abstracts == [("pt", "Resumo."), ("en", "Abstract.")]
    assert work.authors == (("Ada", "Varno", "0000-0000-0000-0001"),)


def test_a_malformed_article_is_skipped(demo, tmp_path) -> None:
    service = demo.server.services["scielo"]
    original = service._json
    broken = sorted(_expected(demo))[0]

    def without_titles(article):
        data = original(article)
        if article.pid == broken:
            data["article"].pop("v12")
        return data

    service._json = without_titles
    try:
        project = project_with_people(tmp_path / "p", demo.world)
        report = _collect(demo, project)
    finally:
        service._json = original
    assert report.skipped["malformed record"] == 1
    assert _kept(project) == _expected(demo) - {broken}


def test_a_name_candidate_is_confirmed_by_its_orcid_like_any_record(demo, tmp_path) -> None:
    """A SciELO author proposed by name carries the ORCID the article shows: confirming it
    with resolve.confirm, as an OpenAlex record, makes the next search find the person."""
    from cartolex.collect.resolve import confirm, identity_queue

    project = project_with_people(tmp_path / "p", demo.world, orcids=False)
    report = _collect(demo, project)
    with_orcid = [c for c in report.candidates if c["record"]]
    assert with_orcid and all(c["record"] == f"orcid:{c['orcid']}" for c in with_orcid)
    queue = {q["person_id"]: q for q in identity_queue(project)}
    chosen = with_orcid[0]
    offered = [c for c in queue[chosen["person_id"]]["candidates"] if c["finder"] == "scielo"]
    assert chosen["record"] in {c["record"] for c in offered}
    confirm(project, chosen["person_id"], [chosen["record"]])
    ref = next(p for p in people_refs(project.layout) if p.person_id == chosen["person_id"])
    assert ref.orcid == chosen["orcid"]
    again = _collect(demo, project)
    assert again.found.get(chosen["person_id"])
    assert chosen["person_id"] not in {q["person_id"] for q in identity_queue(project)}
