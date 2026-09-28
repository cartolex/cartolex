# SPDX-License-Identifier: MIT
"""Text providers: JATS, LaTeX and PDF readers, missing abstracts, full texts on request."""

from __future__ import annotations

import gzip
import io
import tarfile
from datetime import datetime, timezone

import pyarrow as pa
import pytest
from _collect_world import T0, client_for

from cartolex.collect import CollectSettings, HttpClient, RateLimit, RetryPolicy, rebuild_sources
from cartolex.collect.providers import (
    PROVIDERS,
    improve_texts,
    provider_egress,
    text_refs,
)
from cartolex.collect.providers.base import UnsafeLink, check_link
from cartolex.collect.providers.formats import (
    check_pdf,
    check_xml,
    latex_source,
    pdf_text,
    read_jats,
    read_latex,
)
from cartolex.collect.providers.services import BiorxivProvider
from cartolex.collect.services import SERVICES
from cartolex.demo import generate
from cartolex.demo.services import DemoServices, sources_layer
from cartolex.demo.services.render import Document, render_jats, render_latex, render_pdf
from cartolex.project import Project
from cartolex.project.models import Slot
from cartolex.project.tables import SOURCE_SCHEMAS, read_source_table, write_source_table

SLOT = "collected"


# ── formats ──────────────────────────────────────────────────────────────────

BODY = "Introduction\n\nFirst paragraph here.\n\nResults\n\nA finding.\n\nFigure 1. A caption."


def test_jats_keeps_sections_captions_and_every_language() -> None:
    doc = Document(
        "pt",
        {"pt": "Um título", "en": "A title"},
        {"pt": "Um resumo.", "en": "An abstract."},
        BODY,
        translations={"en": "Introduction\n\nIn English."},
    )
    read = read_jats(render_jats(doc, sub_articles=True))
    assert read.title == "Um título"
    assert read.abstracts == [("pt", "Um resumo."), ("en", "An abstract.")]
    assert read.bodies == [("pt", BODY), ("en", "Introduction\n\nIn English.")]
    plain = read_jats(render_jats(doc))
    assert ("en", "An abstract.") in plain.abstracts
    xml = (
        b'<article xml:lang="en"><front><article-meta><title-group><article-title>T</article-title>'
        b"</title-group><abstract><title>Background</title><p>Why&nbsp;it <italic>matters</italic>"
        b'<xref ref-type="bibr">[3]</xref>.</p></abstract></article-meta></front><body><sec><title>'
        b"Methods</title><p>We used <inline-formula>x=1</inline-formula>a model.</p></sec></body>"
        b"<back><ref-list><ref>Cited</ref></ref-list></back></article>"
    )
    read = read_jats(xml)
    assert read.abstracts == [("en", "Why it matters.")]
    assert read.bodies == [("en", "Methods\n\nWe used a model.")]
    with pytest.raises(ValueError, match="entities"):
        read_jats(b'<!DOCTYPE a [<!ENTITY x "y">]><article/>')
    with pytest.raises(ValueError):
        check_xml(render_jats(doc)[:200])


def _tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return gzip.compress(buf.getvalue())


def test_latex_sources_are_read_whole_and_clean() -> None:
    doc = Document("fr", {"fr": "Érosion des côtes"}, {"fr": "Un résumé à 50 %."}, BODY)
    for archive in (False, True):
        for macros in (False, True):
            read = read_latex(latex_source(render_latex(doc, archive=archive, macros=macros)))
            assert read.title == "Érosion des côtes"
            assert read.abstracts == [(None, "Un résumé à 50 %.")]
            assert read.bodies == [(None, BODY)]
    source = _tar(
        {
            "paper/main.tex": rb"\documentclass{article}\begin{document}\input{paper/intro}"
            rb"\end{document}",
            "paper/intro.tex": rb"\section{Intro} Text with $x^2$ maths% a comment"
            b"\n\\cite{a} and \\emph{style}~here.\n\n\\begin{equation}y=2\\end{equation}",
        }
    )
    read = read_latex(latex_source(source))
    assert read.bodies == [(None, "Intro\n\nText with maths and style here.")]
    with pytest.raises(ValueError, match="PDF"):
        latex_source(gzip.compress(b"%PDF-1.4 ..."))
    with pytest.raises(ValueError):
        latex_source(render_latex(doc, archive=True)[:50])


def test_a_pdf_is_read_on_its_own_and_a_broken_one_only_fails_itself(tmp_path) -> None:
    pdf = render_pdf(["A title of the work", "An abstract with several words in it."])
    check_pdf(pdf)
    text, error = pdf_text(pdf, tmp_path)
    assert (
        error is None
        and text.split() == "A title of the work An abstract with several words in it.".split()
    )
    broken = b"%PDF-1.4\n" + b"\x00garbage" * 50 + b"\n%%EOF\n"
    text, error = pdf_text(broken, tmp_path)
    assert text == "" or error is not None
    with pytest.raises(ValueError):
        check_pdf(pdf[: len(pdf) // 2])
    assert not list(tmp_path.iterdir()) or all(p.is_dir() for p in tmp_path.iterdir())


def test_links_are_followed_only_when_safe() -> None:
    assert check_link("https://files.example.org/a.pdf", local_ok=False)
    for bad in ("file:///etc/passwd", "ftp://x.org/a", "http://127.0.0.1:9/a", "http://10.0.0.2/a"):
        with pytest.raises(UnsafeLink):
            check_link(bad, local_ok=False)
    assert check_link("http://127.0.0.1:9/a", local_ok=True)


# ── providers on the demo services ───────────────────────────────────────────


@pytest.fixture(scope="module")
def services():
    with DemoServices(generate("XS", 0, "en,fr,pt", bodies=True)) as svc:
        yield svc


@pytest.fixture()
def demo(services):
    services.faults.clear()
    services.requests.clear()
    return services


def _texts(tmp_path, rows: list[dict]) -> Project:
    """A project whose tables hold *rows* as texts (with a title part each), nothing else."""
    project = Project.init(
        tmp_path / "p",
        name="Providers test",
        domain_title="Invented field",
        slots=(Slot(id=SLOT, kind="collection"),),
    )
    full = []
    for i, r in enumerate(rows):
        full.append(
            {
                "text_id": r["text_id"],
                "slot": SLOT,
                "position": i,
                "year": r.get("year", 2020),
                "doc_type": r.get("doc_type", "article"),
                "title": r.get("title", "A title"),
                "doi": r.get("doi"),
                "ids": sorted(r.get("ids", {}).items()),
                "n_authors": 1,
                "source": "import",
                "retrieved_at": T0,
            }
        )
    layout = project.layout
    schema = SOURCE_SCHEMAS["texts"]
    write_source_table(
        layout.table("texts"), "texts",
        pa.table({f.name: [r.get(f.name) for r in full] for f in schema}, schema=schema),
    )  # fmt: skip
    schema = SOURCE_SCHEMAS["text_parts"]
    parts = [
        {"text_id": r["text_id"], "part": "title", "language": "en", "provider": "import",
         "format": "plain", "content": r.get("title", "A title"), "retrieved_at": T0}
        for r in full
    ]  # fmt: skip
    write_source_table(
        layout.table("text_parts"), "text_parts",
        pa.table({f.name: [r.get(f.name) for r in parts] for f in schema}, schema=schema),
    )  # fmt: skip
    return project


def _registry():
    registry = dict(PROVIDERS)
    registry["biorxiv"] = BiorxivProvider(doi_prefixes=("10.5555/",))
    return registry


def _cases(demo) -> dict[str, dict]:
    layer = sources_layer(demo.bibliography)
    index = {w.world_work: w.id for w in demo.bibliography.works.values() if w.world_work}
    arxiv = next(e for e in layer.arxiv.values() if e.preprint_of)
    pmc = next(e for e in layer.pmc.values() if e.open_access)
    closed = next(e for e in layer.pmc.values() if not e.open_access)
    bio = next(iter(layer.biorxiv.values()))
    deposit = next(
        d for d in layer.deposits.values() if d.file and d.world_work and not d.preprint_of
    )
    article = next(iter(layer.scielo.values()))
    oa_work = next(
        w for w in sorted(layer.oa_links) if w in index and w != deposit.world_work
        and w not in {e.world_work for e in layer.pmc.values()}
    )  # fmt: skip
    return {
        "x_arxiv": {"text_id": "x_arxiv", "doc_type": "preprint", "ids": {"arxiv": arxiv.arxiv_id},
                    "_entry": arxiv},
        "x_pmc": {"text_id": "x_pmc", "doi": pmc.doi, "_entry": pmc},
        "x_pmc_closed": {"text_id": "x_pmc_closed", "doi": closed.doi, "_entry": closed},
        "x_bio": {"text_id": "x_bio", "doc_type": "preprint", "doi": bio.doi, "_entry": bio},
        "x_hal": {"text_id": "x_hal", "ids": {"hal": deposit.hal_id}, "_entry": deposit},
        "x_scielo": {"text_id": "x_scielo", "ids": {"scielo": f"dmo:{article.pid}"},
                     "_entry": article},
        "x_oa": {"text_id": "x_oa", "ids": {"openalex": index[oa_work]}, "_entry": oa_work},
    }  # fmt: skip


def _parts(project) -> dict[tuple[str, str, str, str], dict]:
    """Parts providers added: (text, part, provider, language) → row."""
    rows = read_source_table(project.layout.table("text_parts"), "text_parts").to_pylist()
    return {
        (r["text_id"], r["part"], r["provider"], r["language"]): r
        for r in rows
        if r["provider"] != "import"
    }


#: Which provider serves which case.
SERVES = {
    "x_arxiv": "arxiv",
    "x_pmc": "europepmc",
    "x_pmc_closed": "europepmc",
    "x_bio": "biorxiv",
    "x_hal": "hal",
    "x_scielo": "scielo",
    "x_oa": "openalex",
}


def _one_by_one(demo, project, **kw) -> None:
    client = client_for(demo, project)
    for text_id, provider in SERVES.items():
        improve_texts(
            client, project.layout, project.config, providers=[provider], text_ids=[text_id],
            registry=_registry(), **kw,
        )  # fmt: skip
    rebuild_sources(project.layout, project.config)


def test_each_provider_fills_a_missing_abstract_and_full_texts_wait(demo, tmp_path) -> None:
    cases = _cases(demo)
    project = _texts(tmp_path, list(cases.values()))
    _one_by_one(demo, project)
    parts = _parts(project)
    layer = sources_layer(demo.bibliography)
    assert {k[1] for k in parts} == {"abstract"}  # full texts only on request
    abstracts = {(k[0], k[2], k[3]): v["content"] for k, v in parts.items()}
    arxiv = cases["x_arxiv"]["_entry"]
    assert abstracts[("x_arxiv", "arxiv", arxiv.language)] == arxiv.abstract
    for key in ("x_pmc", "x_pmc_closed"):
        entry = cases[key]["_entry"]
        lang = layer.work(entry.world_work).language
        assert abstracts[(key, "europepmc", lang)] == entry.abstract
    bio = cases["x_bio"]["_entry"]
    assert abstracts[("x_bio", "biorxiv", layer.work(bio.world_work).language)] == bio.abstract
    deposit = cases["x_hal"]["_entry"]
    assert {(k[2]): v for k, v in abstracts.items() if k[0] == "x_hal"} == deposit.abstracts
    article = cases["x_scielo"]["_entry"]
    assert {k[2]: v for k, v in abstracts.items() if k[0] == "x_scielo"} == article.abstracts
    work = layer.work(cases["x_oa"]["_entry"])
    assert abstracts[("x_oa", "openalex", work.language)] == work.abstract
    assert all(t.has_abstract for t in text_refs(project.layout))


def test_each_provider_gives_its_full_text_on_request(demo, tmp_path) -> None:
    cases = _cases(demo)
    project = _texts(tmp_path, list(cases.values()))
    _one_by_one(demo, project, abstracts=False, full_text=True)
    parts = _parts(project)
    layer = sources_layer(demo.bibliography)
    arxiv = cases["x_arxiv"]["_entry"]
    body = parts[("x_arxiv", "body", "arxiv", arxiv.language)]
    assert body["content"] == arxiv.body and body["format"] == "latex"
    assert parts[("x_arxiv", "abstract", "arxiv", arxiv.language)]["content"] == arxiv.abstract
    pmc = cases["x_pmc"]["_entry"]
    lang = layer.work(pmc.world_work).language
    body = parts[("x_pmc", "body", "europepmc", lang)]
    assert body["content"] == pmc.body and body["format"] == "jats"
    assert not any(k[0] == "x_pmc_closed" for k in parts)  # not open: no full text
    bio = cases["x_bio"]["_entry"]
    lang = layer.work(bio.world_work).language
    assert parts[("x_bio", "body", "biorxiv", lang)]["content"] == bio.body
    article = cases["x_scielo"]["_entry"]
    bodies = {
        k[3]: v["content"] for k, v in parts.items() if k[:3] == ("x_scielo", "body", "scielo")
    }
    assert bodies == article.bodies
    deposit = cases["x_hal"]["_entry"]
    full = [v for k, v in parts.items() if k[:3] == ("x_hal", "full", "hal")]
    expected = [deposit.titles[deposit.language], deposit.abstracts[deposit.language]]
    expected += [b for b in layer.work(deposit.world_work).body.split("\n\n") if b]
    assert len(full) == 1 and full[0]["format"] == "plain"
    assert full[0]["content"].split() == " ".join(expected).split()
    oa = [v for k, v in parts.items() if k[:3] == ("x_oa", "full", "openalex")]
    work = layer.work(cases["x_oa"]["_entry"])
    assert len(oa) == 1 and oa[0]["content"].split() == " ".join(layer.pdf_blocks(work)).split()


def test_structured_full_texts_come_first_and_stay_private(demo, tmp_path) -> None:
    layer = sources_layer(demo.bibliography)
    filed = {d.doi for d in layer.deposits.values() if d.file and d.doi and not d.preprint_of}
    entry = next((e for e in layer.pmc.values() if e.open_access and e.doi in filed), None)
    if entry is None:
        pytest.skip("no open article of this world is also deposited with a file")
    project = _texts(tmp_path, [{"text_id": "x1", "doi": entry.doi}])
    report = improve_texts(
        client_for(demo, project), project.layout, project.config, full_text=True,
        registry=_registry(),
    )  # fmt: skip
    asked = [(o["provider"], o["request"]) for o in report.outcomes]
    assert ("europepmc", "full_text") in asked
    assert ("hal", "full_text") not in asked and ("openalex", "full_text") not in asked
    rebuild_sources(project.layout, project.config)
    from cartolex.project.tables import PRIVATE_PARTS, shareable_parts

    table = read_source_table(project.layout.table("text_parts"), "text_parts")
    assert "body" in table["part"].to_pylist()
    assert not set(shareable_parts(table)["part"].to_pylist()) & PRIVATE_PARTS


def _notes(runs, builder) -> None:
    for run in runs:
        for r in run.records():
            builder.text(
                slot=run.slot, keys=r["keys"], title=r["title"], doc_type=r["type"],
                source="import", retrieved_at=T0, year=r["year"], doi=r.get("doi"),
                ids=r.get("ids", {}), n_authors=1,
            )  # fmt: skip


def test_a_provider_states_the_published_version_of_a_preprint(demo, tmp_path) -> None:
    from cartolex.collect.tables import RawWriter, default_readers

    arxiv = next(e for e in sources_layer(demo.bibliography).arxiv.values() if e.preprint_of)
    project = Project.init(
        tmp_path / "p", name="Versions", domain_title="Invented field",
        slots=(Slot(id=SLOT, kind="collection"),),
    )  # fmt: skip
    with RawWriter(project.layout, SLOT, "notes", {}) as w:
        w.add({"keys": ["k:1"], "title": "Another title for the preprint", "type": "preprint",
               "year": arxiv.year, "ids": {"arxiv": arxiv.arxiv_id}})  # fmt: skip
        w.add({"keys": ["k:2"], "title": arxiv.title, "type": "article", "year": arxiv.year + 1,
               "doi": arxiv.doi})  # fmt: skip
    readers = {"notes": _notes, **default_readers()}
    rebuild_sources(project.layout, project.config, readers=readers)
    improve_texts(client_for(demo, project), project.layout, project.config, providers=["arxiv"])
    report = rebuild_sources(project.layout, project.config, readers=readers)
    assert report.merges["version links"] == 1
    texts = {
        t["doc_type"]: t
        for t in read_source_table(project.layout.table("texts"), "texts").to_pylist()
    }
    assert texts["preprint"]["version_of"] == texts["article"]["text_id"]


def test_a_broken_file_only_loses_its_own_text(demo, tmp_path, monkeypatch) -> None:
    layer = sources_layer(demo.bibliography)
    others = [d for d in layer.deposits.values() if d.file and d.world_work and not d.preprint_of]
    rows = [{"text_id": f"h{i}", "ids": {"hal": d.hal_id}} for i, d in enumerate(others[:3])]
    project = _texts(tmp_path, rows)
    demo.faults.add("malformed", service="hal", path=others[0].hal_id + "/document", times=None)
    from cartolex.collect import pdfworker

    # The PDF worker process reads with a reader whose first file fails.
    monkeypatch.setattr(pdfworker, "EXTRACTOR", "_pdf_fakes:fails_first")
    report = improve_texts(
        client_for(demo, project), project.layout, project.config, providers=["hal"],
        abstracts=False, full_text=True,
    )  # fmt: skip
    outcome = {o["text_id"]: o["outcome"] for o in report.outcomes}
    assert outcome == {"h0": "failed", "h1": "failed", "h2": "improved"}
    rebuild_sources(project.layout, project.config)
    assert {k[0] for k in _parts(project)} == {"h2"}


def test_a_fault_once_is_overcome(demo, tmp_path) -> None:
    cases = _cases(demo)
    project = _texts(tmp_path, [cases["x_pmc"], cases["x_arxiv"]])
    demo.faults.add("status", service="europepmc", path="search", status=429, retry_after="0")
    demo.faults.add("status", service="arxiv", path="query", status=503)
    demo.faults.add("malformed", service="arxiv", path="e-print")
    report = improve_texts(
        client_for(demo, project), project.layout, project.config, full_text=True,
        registry=_registry(),
    )  # fmt: skip
    assert report.counts["europepmc"]["improved"] == 2  # the abstract, then the full text
    assert report.counts["arxiv"]["improved"] == 2
    assert not any(o["outcome"] == "failed" for o in report.outcomes)


def test_arxiv_is_asked_once_every_three_seconds(demo, tmp_path) -> None:
    assert SERVICES["arxiv"].rate == RateLimit(per_second=1 / 3, burst=1)
    layer = sources_layer(demo.bibliography)
    entries = sorted(layer.arxiv.values(), key=lambda e: e.arxiv_id)[:3]
    project = _texts(tmp_path, [{"text_id": f"a{i}", "doc_type": "preprint",
                                 "ids": {"arxiv": e.arxiv_id}} for i, e in enumerate(entries)])  # fmt: skip
    now = {"t": 0.0}
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now["t"] += seconds

    endpoints = demo.endpoints()
    settings = CollectSettings(
        endpoints=endpoints,
        rates={k: RateLimit(1000.0, 1000) for k in endpoints if k != "arxiv"},
        retry=RetryPolicy(max_attempts=2, base_delay=0.01, max_delay=0.01),
        use_system_proxy=False,
    )
    client = HttpClient(settings, clock=lambda: now["t"], sleep=sleep)
    improve_texts(client, project.layout, project.config, providers=["arxiv"], full_text=True)
    arxiv_requests = [r for r in demo.requests if r.service == "arxiv"]
    assert len(arxiv_requests) == 4  # one query for the three abstracts, three sources
    assert sum(slept) == pytest.approx(9.0)  # three waits of three seconds


def test_each_provider_says_what_it_sends(demo, tmp_path) -> None:
    declared = {e["provider"]: e for e in provider_egress()}
    assert set(declared) == {"arxiv", "biorxiv", "europepmc", "hal", "scielo", "openalex"}
    assert declared["arxiv"]["sends"] == ["identifier"]
    assert declared["biorxiv"]["sends"] == ["DOI"]
    assert all(e["describes"] and e["service"] for e in declared.values())
    cases = _cases(demo)
    project = _texts(tmp_path, [cases["x_arxiv"], cases["x_pmc"]])
    client = client_for(demo, project)
    improve_texts(client, project.layout, project.config, providers=["arxiv", "europepmc"])
    sent = {e["service"]: set(e["sends"]) for e in client.egress.summary()}
    assert sent["arxiv"] <= set(declared["arxiv"]["sends"])
    assert sent["europepmc"] <= set(declared["europepmc"]["sends"])
    with pytest.raises(ValueError, match="unknown provider"):
        improve_texts(client, project.layout, project.config, providers=["nowhere"])


def test_improving_twice_asks_nothing_new(demo, tmp_path) -> None:
    cases = _cases(demo)
    project = _texts(tmp_path, [cases["x_pmc"]])
    client = client_for(demo, project)
    improve_texts(client, project.layout, project.config, providers=["europepmc"])
    rebuild_sources(project.layout, project.config)
    before = len(demo.requests)
    report = improve_texts(client, project.layout, project.config, providers=["europepmc"])
    assert len(demo.requests) == before and report.outcomes == []
    assert datetime.now(timezone.utc) > T0
