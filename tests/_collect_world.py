# SPDX-License-Identifier: MIT
"""Helpers of the collection tests: a project fed from the demo services, the corpus a
perfect collector would get from them (computed from the bibliographic layer alone), and a
project holding a demo world's people with a quick client."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa

from cartolex.collect import HttpClient, RetryPolicy, Timeouts, local_settings
from cartolex.collect.decisions import read_people
from cartolex.collect.people_import import import_people
from cartolex.collect.resolve import confirm, confirm_none
from cartolex.demo.model import DemoWorld
from cartolex.demo.services import Bibliography, DemoServices
from cartolex.project import Project
from cartolex.project.models import Level, Slot
from cartolex.project.tables import SOURCE_SCHEMAS, read_source_table, write_source_table

LEVELS = [
    Level(id="lab", names={"en": "Lab"}),
    Level(id="institution", names={"en": "Institution"}),
]
DOC_TYPES = {
    "article": "article",
    "proceedings": "communication",
    "preprint": "preprint",
    "report": "report",
    "thesis": "thesis",
}


def demo_project(root: Path, bib: Bibliography, rows: Sequence[dict] | None = None) -> Project:
    """A project with the layer's import list (or *rows*) imported."""
    project = Project.init(
        root, name="Collected", domain_title="Coastal systems", corpus_languages=("en", "fr")
    )
    project.save_config(project.config.model_copy(update={"levels": LEVELS}), action="levels")
    rows = list(rows if rows is not None else bib.people_rows())
    columns = list(rows[0])
    text = "\n".join(
        [",".join(columns)]
        + [",".join(f'"{r[c]}"' if "," in r[c] else r[c] for c in columns) for r in rows]
    )
    import_people(project, text + "\n")
    return project


def world_ids(project: Project, bib: Bibliography) -> dict[str, str]:
    """Project person id → world person id, matched by the names of the list."""
    by_name = {(t.last_name, t.first_name): pid for pid, t in bib.truth.items()}
    people = read_source_table(project.layout.table("people"), "people").to_pylist()
    return {p["person_id"]: by_name[(p["last_name"], p["first_name"])] for p in people}


def client(services: DemoServices, project: Project | None = None, **kw: Any) -> HttpClient:
    cache = project.layout.cache_http if project is not None else None
    return HttpClient(local_settings(services.endpoints()), cache_dir=cache, **kw)


def confirm_truth(project: Project, bib: Bibliography, ids: dict[str, str]) -> None:
    """Confirm every person as the truth says (their records, or none)."""
    for pid, wid in sorted(ids.items()):
        truth = bib.truth[wid]
        if truth.records:
            confirm(project, pid, truth.records)
        else:
            confirm_none(project, pid)


def expected_corpus(
    bib: Bibliography, ids: dict[str, str], years: tuple[int, int] | None = None
) -> dict[str, set]:
    """What the tables must hold after a harvest of every person confirmed as the truth says."""
    world_works = {w.work_id: w for w in bib.world.works}
    reach: dict[str, list[Any]] = defaultdict(list)  # project pid -> index works
    for pid, wid in ids.items():
        for record in bib.truth[wid].records:
            scheme, value = record.split(":", 1)
            if scheme == "openalex":
                works = [
                    w
                    for w in bib.works.values()
                    if any(a.author_id == value for a in w.authorships)
                ]
            else:
                dois = {rw.doi for rw in bib.registry[value].works}
                works = [w for w in bib.works.values() if w.doi in dois]
            reach[pid] += [w for w in works if years is None or years[0] <= w.year <= years[1]]

    def identity(w) -> str:
        return w.world_work or w.id

    first = {}  # the index work that stands for each text: the one with a DOI first
    for works in reach.values():
        for w in sorted(works, key=lambda w: (w.doi is None, w.id)):
            first.setdefault(identity(w), w)
    texts, parts, authorships = set(), set(), set()
    stated: dict[tuple[str, str], list[int]] = {}
    for key, w in first.items():
        doc = DOC_TYPES[world_works[w.world_work].doc_type] if w.world_work else "article"
        texts.add((w.doi, w.title, w.year, doc, len(w.authorships)))
        abstract = " ".join(w.abstract.split())
        parts.add((w.title, "title", w.language, w.title))
        parts.add((w.title, "abstract", w.language, abstract))
        for pid, works in reach.items():
            if key not in {identity(x) for x in works}:
                continue
            wid = ids[pid]
            rank = next(k for k, a in enumerate(w.authorships, 1) if a.person_id == wid)
            authorships.add((w.title, pid, rank))
            for inst in w.authorships[rank - 1].institutions:
                span = stated.setdefault((pid, bib.institutions[inst].name), [w.year, w.year])
                span[0], span[1] = min(span[0], w.year), max(span[1], w.year)
    affiliations = {(p, org, a, b, "stated") for (p, org), (a, b) in stated.items()}
    for pid, wid in ids.items():
        truth = bib.truth[wid]
        spans: dict[str, list[int]] = {}
        for record in truth.records:
            scheme, value = record.split(":", 1)
            if scheme != "openalex":
                continue
            for w in bib.works.values():
                for a in w.authorships:
                    if a.author_id == value:
                        for inst in a.institutions:
                            span = spans.setdefault(bib.institutions[inst].name, [w.year, w.year])
                            span[0], span[1] = min(span[0], w.year), max(span[1], w.year)
        affiliations |= {(pid, org, a, b, "openalex") for org, (a, b) in spans.items()}
        for record in truth.records:
            if record.startswith("orcid:"):
                for e in bib.registry[record[6:]].employments:
                    affiliations.add((pid, e.organisation, e.start, e.end, "orcid"))
        person = bib.world.person(wid)
        group = bib.world.group(person.group)
        affiliations.add((pid, group.name, None, None, "import"))
        affiliations.add((pid, group.institution, None, None, "import"))
    return {
        "texts": texts,
        "parts": parts,
        "authorships": authorships,
        "affiliations": affiliations,
    }


def actual_corpus(project: Project) -> dict[str, set]:
    """The same view of the project's tables."""
    layout = project.layout

    def table(name: str) -> list[dict]:
        return read_source_table(layout.table(name), name).to_pylist()

    texts = {t["text_id"]: t for t in table("texts")}
    orgs = {o["org_id"]: o["name"] for o in table("organisations")}
    return {
        "texts": {
            (t["doi"], t["title"], t["year"], t["doc_type"], t["n_authors"]) for t in texts.values()
        },
        "parts": {
            (texts[p["text_id"]]["title"], p["part"], p["language"], p["content"])
            for p in table("text_parts")
        },
        "authorships": {
            (texts[a["text_id"]]["title"], a["person_id"], a["position"])
            for a in table("authorships")
        },
        "affiliations": {
            (a["person_id"], orgs[a["org_id"]], a["start_year"], a["end_year"], a["source"])
            for a in table("affiliations")
        },
    }


def identities(project: Project) -> dict[str, dict[str, str]]:
    return read_people(project.layout)


T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
SLOT = "collected"


def project_with_people(root: Path, world: DemoWorld, *, orcids: bool = True) -> Project:
    """A project with one collection slot and the world's people in its tables (as an
    import would give them: names, ORCID, idHAL)."""
    project = Project.init(
        root,
        name="Collection test",
        domain_title="Invented field",
        slots=(Slot(id=SLOT, kind="collection"),),
    )
    rows = [
        {
            "person_id": p.person_id,
            "last_name": p.last_name,
            "first_name": p.first_name,
            "orcid": (p.orcid or None) if orcids else None,
            "ids": [("idhal", [p.idhal])] if p.idhal else [],
            "source": "import",
            "columns": [],
            "aliases": [],
            "retrieved_at": T0,
        }
        for p in world.people
    ]
    schema = SOURCE_SCHEMAS["people"]
    table = pa.table({f.name: [r.get(f.name) for r in rows] for f in schema}, schema=schema)
    write_source_table(project.layout.table("people"), "people", table)
    return project


def client_for(services, project: Project | None = None, **kw) -> HttpClient:
    """A client of the demo services: no pacing, quick retries, the project's cache."""
    settings = local_settings(
        services.endpoints(),
        timeouts=kw.pop("timeouts", Timeouts(connect=2.0, read=5.0)),
        retry=kw.pop("retry", RetryPolicy(max_attempts=3, base_delay=0.01, max_delay=0.05)),
    )
    cache = project.layout.cache_http if project is not None else None
    return HttpClient(settings, cache_dir=cache, **kw)
