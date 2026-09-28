# SPDX-License-Identifier: MIT
"""End to end: a demo world collected from HAL and SciELO, then improved by the providers.

No OpenAlex: the texts come from the archive and the journal platform, are
merged, and get their full texts (on request) from Europe PMC, bioRxiv,
SciELO, arXiv and HAL's files. The expected texts and parts are worked out
from the demo's sources layer — what each service holds for which world work
— and must be exactly what the tables hold.
"""

from __future__ import annotations

import json

import pytest
from _collect_world import SLOT, client_for, project_with_people

from cartolex.collect import rebuild_sources
from cartolex.collect.finders import normalise_title, people_refs
from cartolex.collect.hal import HAL_DOC_TYPES, collect_hal
from cartolex.collect.providers import PROVIDERS, improve_texts
from cartolex.collect.providers.services import BiorxivProvider
from cartolex.collect.scielo import collect_scielo
from cartolex.demo import generate
from cartolex.demo.services import DemoServices, sources_layer
from cartolex.demo.services.sources import SCIELO_COLLECTION
from cartolex.project.tables import read_source_table

WINDOW = (2012, 2026)


@pytest.fixture(scope="module")
def collected(tmp_path_factory):
    world = generate("XS", 0, "en,fr,pt", bodies=True)
    root = tmp_path_factory.mktemp("e2e")
    with DemoServices(world) as services:
        project = project_with_people(root / "p", world)
        client = client_for(services, project)
        people = people_refs(project.layout)
        hal = collect_hal(client, project.layout, SLOT, people, window=WINDOW)
        scielo = collect_scielo(
            client, project.layout, SLOT, people, window=WINDOW, collection=SCIELO_COLLECTION
        )
        rebuild_sources(project.layout, project.config)
        registry = dict(PROVIDERS)
        registry["biorxiv"] = BiorxivProvider(doi_prefixes=("10.5555/",))
        chosen = [name for name in registry if name != "openalex"]
        improved = improve_texts(
            client, project.layout, project.config, providers=chosen, full_text=True,
            registry=registry,
        )  # fmt: skip
        report = rebuild_sources(project.layout, project.config)
        layer = sources_layer(services.bibliography)
        yield {
            "world": world, "layer": layer, "project": project, "hal": hal, "scielo": scielo,
            "improved": improved, "report": report, "egress": client.egress.summary(),
        }  # fmt: skip


# ── what the services hold, worked out from the layer ────────────────────────


def _expected(world, layer):
    idhal_of = {p.idhal: p.person_id for p in world.people if p.idhal}
    orcid_of = {p.orcid: p.person_id for p in world.people if p.orcid}
    records = {}
    for d in layer.deposits.values():
        people = {idhal_of[a.idhal] for a in d.authors if a.idhal in idhal_of}
        if people and WINDOW[0] <= d.year <= WINDOW[1]:
            records[f"hal:{d.hal_id}"] = {
                "work": d.world_work, "doi": d.doi, "year": d.year, "people": people,
                "title": d.titles[d.language], "preprint": HAL_DOC_TYPES[d.doc_type] == "preprint",
                "source": d,
            }  # fmt: skip
    for a in layer.scielo.values():
        people = {orcid_of[o] for _g, _s, o, _x in a.authors if o in orcid_of}
        if people and WINDOW[0] <= a.year <= WINDOW[1]:
            records[f"scielo:{a.pid}"] = {
                "work": a.world_work, "doi": a.doi, "year": a.year, "people": people,
                "title": a.titles[a.language], "preprint": False, "source": a,
            }  # fmt: skip

    def same(x, y) -> bool:  # the merge rules, on two records of one world work
        if x["doi"] and x["doi"] == y["doi"]:
            return True
        return (
            bool(x["people"] & y["people"])
            and normalise_title(x["title"]) == normalise_title(y["title"])
            and abs(x["year"] - y["year"]) <= 1
            and x["preprint"] == y["preprint"]
        )

    groups: list[set[str]] = []
    for key in sorted(records):
        joined = [g for g in groups if any(
            records[k]["work"] == records[key]["work"] and same(records[k], records[key]) for k in g
        )]  # fmt: skip
        merged = {key}.union(*joined)
        groups = [g for g in groups if g not in joined] + [merged]
    texts = {frozenset(g) for g in groups}
    versions = set()
    for g in texts:
        if not all(records[k]["preprint"] for k in g):
            continue
        published = [
            h for h in texts
            if h != g and not any(records[k]["preprint"] for k in h)
            and any(same_version(records[a], records[b]) for a in g for b in h)
        ]  # fmt: skip
        if len(published) == 1:
            versions.add((g, published[0]))
    return records, texts, versions


def same_version(pre, pub) -> bool:
    return (
        bool(pre["people"] & pub["people"])
        and normalise_title(pre["title"]) == normalise_title(pub["title"])
        and abs(pre["year"] - pub["year"]) <= 1
    )


def _actual(project):
    layout = project.layout
    texts = read_source_table(layout.table("texts"), "texts").to_pylist()
    key_of = {}
    for t in texts:
        ids = dict(t["ids"])
        key = set()
        if "hal" in ids:
            key.add(f"hal:{ids['hal']}")
        if "scielo" in ids:
            key.add(f"scielo:{ids['scielo'].split(':', 1)[1]}")
        key_of[t["text_id"]] = frozenset(key)
    return texts, key_of


def test_the_texts_are_exactly_the_works_the_services_hold(collected) -> None:
    records, expected, versions = _expected(collected["world"], collected["layer"])
    texts, key_of = _actual(collected["project"])
    # A merged text keeps one HAL id: compare on the records each text stands for.
    merges = json.loads((collected["project"].layout.sources / "merges.json").read_text())
    assert len(texts) == len(expected)
    got = set()
    for t in texts:
        got.add(key_of[t["text_id"]])
    assert got == expected
    got_versions = {
        (key_of[t["text_id"]], key_of[t["version_of"]]) for t in texts if t["version_of"]
    }
    assert got_versions == versions
    counts = collected["report"].merges
    assert counts.get("merged by hal_doi", 0) + counts.get("merged by title_year", 0) == sum(
        len(g) - 1 for g in expected
    )
    assert counts["version links"] == len(versions) and not merges["refused"]
    assert collected["hal"].works and collected["scielo"].works


def _first_full_text(text, key, records, layer):
    """The provider that gives a text its full text, in the providers' order, and its parts."""
    ids = dict(text["ids"])
    doi = text["doi"]
    pmc = {e.doi: e for e in layer.pmc.values()}
    if doi in pmc and pmc[doi].open_access:
        e = pmc[doi]
        lang = layer.work(e.world_work).language
        return "europepmc", {("abstract", lang, "jats"): e.abstract, ("body", lang, "jats"): e.body}
    if text["doc_type"] == "preprint" and doi in layer.biorxiv:
        e = layer.biorxiv[doi]
        lang = layer.work(e.world_work).language
        return "biorxiv", {("abstract", lang, "jats"): e.abstract, ("body", lang, "jats"): e.body}
    if "scielo" in ids:
        a = layer.scielo[ids["scielo"].split(":", 1)[1]]
        parts = {("abstract", lang, "jats"): v for lang, v in a.abstracts.items()}
        parts |= {("body", lang, "jats"): v for lang, v in a.bodies.items()}
        return "scielo", parts
    if "arxiv" in ids:
        e = layer.arxiv[ids["arxiv"]]
        return "arxiv", {("abstract", e.language, "latex"): e.abstract,
                         ("body", e.language, "latex"): e.body}  # fmt: skip
    hal_keys = [k for k in key if k.startswith("hal:")]
    if hal_keys:
        d = records[hal_keys[0]]["source"]
        if d.file:
            work = layer.work(d.world_work)
            blocks = [d.titles[d.language], d.abstracts[d.language]]
            blocks += [b for b in work.body.split("\n\n") if b]
            return "hal", {("full", work.language, "plain"): " ".join(blocks)}
    return None, {}


def test_every_part_is_the_one_expected(collected) -> None:
    layer = collected["layer"]
    records, _expected_texts, _versions = _expected(collected["world"], layer)
    texts, key_of = _actual(collected["project"])
    parts = read_source_table(
        collected["project"].layout.table("text_parts"), "text_parts"
    ).to_pylist()
    by_text: dict[str, dict] = {}
    for p in parts:
        key = (p["part"], p["language"], p["provider"])
        by_text.setdefault(p["text_id"], {})[key] = (p["format"], p["content"])
    n_full = 0
    for text in texts:
        key = key_of[text["text_id"]]
        want: dict = {}
        for k in key:
            src = records[k]["source"]
            provider = k.split(":", 1)[0]
            for lang, v in src.titles.items():
                want[("title", lang, provider)] = ("plain", v)
            for lang, v in src.abstracts.items():
                want[("abstract", lang, provider)] = ("plain", v)
        provider, extra = _first_full_text(text, key, records, layer)
        # A provider's part replaces the one the same service gave as a finder (same key).
        for (part, lang, fmt), v in extra.items():
            want[(part, lang, provider)] = (fmt, v)
        n_full += provider is not None
        got = by_text[text["text_id"]]
        assert set(got) == set(want), text["text_id"]
        for k, (fmt, v) in want.items():
            assert got[k][0] == fmt, (text["text_id"], k)
            if k[0] == "full":
                assert got[k][1].split() == v.split()
            else:
                assert got[k][1] == v, (text["text_id"], k)
    assert n_full and collected["improved"].counts
    kinds = {p["provider"] for p in parts if p["part"] in ("body", "full")}
    assert kinds == {"europepmc", "scielo", "arxiv", "hal"} | ({"biorxiv"} & kinds)


def test_nothing_was_sent_to_openalex_and_every_host_was_recorded(collected) -> None:
    services = {e["service"] for e in collected["egress"]}
    assert "openalex" not in services
    assert {"hal", "scielo", "europepmc", "arxiv"} <= services
    for entry in collected["egress"]:
        assert set(entry["sends"]) <= {"identifier", "DOI", "name"}
