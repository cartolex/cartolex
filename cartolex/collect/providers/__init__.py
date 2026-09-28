# SPDX-License-Identifier: MIT
"""Text providers: better texts for works already found.

A finder brings a work in with what its service knows; a provider improves
the text afterwards, from another service: a **missing abstract**, and on
request (off by default) a **full text**. The providers are arXiv, bioRxiv
and medRxiv, Europe PMC, HAL (its files), SciELO and OpenAlex (its
open-access links); :data:`PROVIDERS` lists them.

* **Abstracts**: for each text without one, the providers are tried in
  :data:`ABSTRACT_ORDER` until one gives it.
* **Full texts**: structured texts first — JATS (Europe PMC, bioRxiv/medRxiv,
  SciELO) and LaTeX (arXiv) — then PDF files (HAL, OpenAlex's links)
  (:data:`FULL_TEXT_ORDER`). A JATS or LaTeX text gives ``abstract`` and
  ``body`` parts, sections kept as paragraphs; a PDF gives a ``full`` part,
  extracted on its own so that a broken file only loses itself.
* Providers only **store** parts (``provider`` and ``format`` recorded): which
  parts feed the lexicon and how much each weighs are build parameters.
* ``body`` and ``full`` parts are **private**: they never reach a shared
  output (:data:`cartolex.project.tables.PRIVATE_PARTS`).
* Each provider declares what it sends (:func:`provider_egress`), for the
  privacy summary; the HTTP client records the hosts and kinds of data.

What a provider returned goes to ``sources/<slot>/raw/improve/``, one run per
job and slot; :func:`read_improve_runs` adds the parts when the tables are
rebuilt.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cartolex.project.layout import ProjectLayout
from cartolex.project.models import ProjectFile
from cartolex.project.tables import read_source_table

from ..http import Cancelled, HttpClient, ServiceError, ServiceUnavailable
from ..tables import RawRun, RawWriter, SourceBuilder, iso, parse_time
from .base import Found, Provided, Provider, TextRef, UnsafeLink
from .services import (
    ArxivProvider,
    BiorxivProvider,
    EuropePmcProvider,
    HalProvider,
    OpenAlexProvider,
    ScieloProvider,
)

__all__ = [
    "ABSTRACT_ORDER",
    "FULL_TEXT_ORDER",
    "PROVIDERS",
    "Found",
    "ImproveReport",
    "Provided",
    "coverage",
    "Provider",
    "TextRef",
    "improve_texts",
    "provider_egress",
    "read_improve_runs",
    "text_refs",
]

#: Every provider, by the name its parts carry.
PROVIDERS: Mapping[str, Provider] = {
    p.name: p
    for p in (
        ArxivProvider(),
        BiorxivProvider(),
        EuropePmcProvider(),
        HalProvider(),
        ScieloProvider(),
        OpenAlexProvider(),
    )
}
#: The order providers are asked for a missing abstract.
ABSTRACT_ORDER: tuple[str, ...] = ("scielo", "hal", "europepmc", "biorxiv", "arxiv", "openalex")
#: The order providers are asked for a full text: JATS and LaTeX first, then PDF files.
FULL_TEXT_ORDER: tuple[str, ...] = ("europepmc", "biorxiv", "scielo", "arxiv", "hal", "openalex")
FULL_PARTS = frozenset({"body", "full"})
#: A provider whose service fails this many times in a row is not asked again in the job.
MAX_FAILURES_IN_A_ROW = 3


def provider_egress(
    providers: Iterable[str] | None = None,
    *,
    registry: Mapping[str, Provider] | None = None,
) -> list[dict[str, Any]]:
    """What each provider sends, and to which service, for the privacy summary."""
    known = registry or PROVIDERS
    names = list(providers) if providers is not None else list(known)
    return [
        {
            "provider": known[n].name,
            "service": known[n].service,
            "sends": list(known[n].sends),
            "describes": known[n].describes,
        }
        for n in names
    ]


def text_refs(
    layout: ProjectLayout,
    *,
    slots: Iterable[str] | None = None,
    text_ids: Iterable[str] | None = None,
) -> list[TextRef]:
    """The texts of the tables, as providers see them (with what parts they already have)."""
    if not layout.table("texts").exists():
        return []
    wanted_slots = set(slots) if slots is not None else None
    wanted_ids = set(text_ids) if text_ids is not None else None
    have: dict[str, set[str]] = {}
    if layout.table("text_parts").exists():
        parts = read_source_table(layout.table("text_parts"), "text_parts", ["text_id", "part"])
        for tid, part in zip(parts["text_id"].to_pylist(), parts["part"].to_pylist(), strict=True):
            have.setdefault(tid, set()).add(part)
    out = []
    for row in read_source_table(layout.table("texts"), "texts").to_pylist():
        if wanted_slots is not None and row["slot"] not in wanted_slots:
            continue
        if wanted_ids is not None and row["text_id"] not in wanted_ids:
            continue
        got = have.get(row["text_id"], set())
        out.append(
            TextRef(
                text_id=row["text_id"],
                slot=row["slot"],
                doc_type=row["doc_type"],
                title=row["title"],
                year=row["year"],
                doi=row["doi"],
                ids=dict(row["ids"] or []),
                has_abstract="abstract" in got,
                has_full_text=bool(got & FULL_PARTS),
            )
        )
    return sorted(out, key=lambda t: t.text_id)


def coverage(layout: ProjectLayout) -> dict[str, dict[str, Any]]:
    """What the texts of each slot hold, for a coverage report: how many texts, how many
    with a title, an abstract, a full text (``body`` or ``full``), and which providers gave
    their parts; plus the merges of ``sources/merges.json`` when present (key ``merges``)."""
    out: dict[str, dict[str, Any]] = {}
    texts = text_refs(layout)
    parts_of: dict[str, set[tuple[str, str]]] = {}
    if layout.table("text_parts").exists():
        table = read_source_table(
            layout.table("text_parts"), "text_parts", ["text_id", "part", "provider"]
        )
        for tid, part, provider in zip(
            *(table[c].to_pylist() for c in ("text_id", "part", "provider")), strict=True
        ):
            parts_of.setdefault(tid, set()).add((part, provider))
    for t in texts:
        slot = out.setdefault(
            t.slot,
            {"texts": 0, "title": 0, "abstract": 0, "full_text": 0, "providers": {}},
        )
        got = parts_of.get(t.text_id, set())
        slot["texts"] += 1
        slot["title"] += any(p == "title" for p, _ in got)
        slot["abstract"] += t.has_abstract
        slot["full_text"] += t.has_full_text
        for part, provider in got:
            key = f"{part}:{provider}"
            slot["providers"][key] = slot["providers"].get(key, 0) + 1
    log = layout.sources / "merges.json"
    if log.exists():
        import json

        data = json.loads(log.read_text(encoding="utf-8"))
        rules: dict[str, int] = {}
        for m in data.get("merges", []):
            rules[m["rule"]] = rules.get(m["rule"], 0) + max(1, len(m["merged"]))
        out["merges"] = {
            "rules": rules,
            "versions": len(data.get("versions", [])),
            "refused": len(data.get("refused", [])),
            "conflicts": len(data.get("conflicts", [])),
        }
    return out


@dataclass
class ImproveReport:
    """What an improvement job did, per provider and per text.

    *counts* holds, per provider, how many texts it was asked about (``asked``),
    improved (``improved``), had nothing for (``none``) or failed on
    (``failed``); *outcomes* one line per text and provider; *runs* the raw runs
    written; *stopped* the providers given up in this job, and why.
    """

    texts: int = 0
    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    outcomes: list[dict[str, str]] = field(default_factory=list)
    runs: list[Path] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)

    def note(self, provider: str, what: str, text: TextRef, request: str, detail: str = "") -> None:
        counts = self.counts.setdefault(
            provider, {"asked": 0, "improved": 0, "none": 0, "failed": 0}
        )
        counts["asked"] += 1
        counts[what] += 1
        self.outcomes.append(
            {
                "text_id": text.text_id,
                "provider": provider,
                "request": request,
                "outcome": what,
                "detail": detail,
            }
        )


class _Runs:
    """One raw run per slot, opened when the slot gets its first record."""

    def __init__(self, layout: ProjectLayout, header: Mapping[str, Any]) -> None:
        self.layout, self.header = layout, dict(header)
        self.writers: dict[str, RawWriter] = {}

    def add(self, slot: str, record: Mapping[str, Any]) -> None:
        if slot not in self.writers:
            self.writers[slot] = RawWriter(self.layout, slot, "improve", self.header)
        self.writers[slot].add(record)

    def close(self) -> list[Path]:
        return [w.close() for _, w in sorted(self.writers.items())]

    def discard(self) -> None:
        for w in self.writers.values():
            w.discard()


def _record(text: TextRef, provider: str, request: str, found: Found, at: datetime) -> dict:
    return {
        "type": "parts",
        "text_id": text.text_id,
        "doi": text.doi,
        "provider": provider,
        "request": request,
        "read": found.read,
        "retrieved_at": iso(at),
        "parts": [
            {"part": p.part, "language": p.language, "format": p.format, "content": p.content}
            for p in found.parts
        ],
        "links": [{"relation": r, "value": v} for r, v in found.links],
    }


def improve_texts(
    client: HttpClient,
    layout: ProjectLayout,
    config: ProjectFile,
    *,
    slots: Iterable[str] | None = None,
    text_ids: Iterable[str] | None = None,
    providers: Sequence[str] | None = None,
    abstracts: bool = True,
    full_text: bool = False,
    work_dir: Path | None = None,
    registry: Mapping[str, Provider] | None = None,
    now: Callable[[], datetime] | None = None,
) -> ImproveReport:
    """Improve the texts of the tables: fill missing abstracts, and (with *full_text*, off by
    default) fetch full texts, from *providers* (every one by default, in their stated orders).

    *slots* and *text_ids* narrow the texts looked at; *work_dir* is where PDF
    files are extracted, one temporary file each (default: ``cache/tmp/``);
    *registry* replaces :data:`PROVIDERS` (a provider configured otherwise).
    Progress, cancel and the egress record go through *client*. A failure is
    reported per text and the job goes on; a provider whose service fails three
    times in a row is not asked again in this job. The tables are not rebuilt
    here: call :func:`~cartolex.collect.rebuild_sources` afterwards.
    """
    known = registry or PROVIDERS
    unknown = sorted(set(providers or ()) - set(known))
    if unknown:
        raise ValueError(f"unknown provider(s) {unknown}; known: {sorted(known)}")
    chosen = [n for n in (providers or list(known))]
    clock = now or (lambda: datetime.now(timezone.utc))
    slot_ids = list(slots) if slots is not None else [s.id for s in config.slots]
    texts = text_refs(layout, slots=slot_ids, text_ids=text_ids)
    report = ImproveReport(texts=len(texts))
    work = work_dir or layout.cache / "tmp"
    runs = _Runs(layout, {"providers": chosen, "abstracts": abstracts, "full_text": full_text})
    steps: list[tuple[str, Provider]] = []
    if abstracts:
        steps += [("abstract", known[n]) for n in ABSTRACT_ORDER if n in chosen]
        steps += [("abstract", known[n]) for n in chosen if n not in ABSTRACT_ORDER]
    if full_text:
        steps += [("full_text", known[n]) for n in FULL_TEXT_ORDER if n in chosen]
        steps += [("full_text", known[n]) for n in chosen if n not in FULL_TEXT_ORDER]
    done: dict[str, set[str]] = {"abstract": set(), "full_text": set()}
    try:
        for step, (request, provider) in enumerate(steps):
            client.progress(step / max(1, len(steps)), f"texts: {provider.name}, {request}")
            if request == "abstract":
                todo = [
                    t
                    for t in texts
                    if not t.has_abstract
                    and t.text_id not in done["abstract"]
                    and provider.for_abstract(t)
                ]
                results = provider.abstracts(client, todo) if todo else {}
            else:
                todo = [
                    t
                    for t in texts
                    if not t.has_full_text
                    and t.text_id not in done["full_text"]
                    and provider.for_full_text(t)
                ]
                results = _full_texts(client, provider, todo, work, report)
            for text in todo:
                _settle(
                    text, provider, request, results.get(text.text_id), runs, done, report, clock
                )
        report.runs = runs.close()
    except BaseException:
        runs.discard()
        raise
    client.progress(1.0, "texts: done")
    return report


def _settle(
    text: TextRef,
    provider: Provider,
    request: str,
    found: Any,
    runs: _Runs,
    done: dict[str, set[str]],
    report: ImproveReport,
    clock: Callable[[], datetime],
) -> None:
    if isinstance(found, Cancelled):
        raise found
    if isinstance(found, Exception):
        report.note(provider.name, "failed", text, request, str(found)[:200])
        return
    if found is None or (not found.parts and not found.links):
        detail = found.error if found is not None and found.error else ""
        report.note(provider.name, "failed" if detail else "none", text, request, detail)
        return
    runs.add(text.slot, _record(text, provider.name, request, found, clock()))
    kinds = {p.part for p in found.parts}
    if "abstract" in kinds:
        done["abstract"].add(text.text_id)
    if kinds & FULL_PARTS:
        done["full_text"].add(text.text_id)
    report.note(provider.name, "improved", text, request, ",".join(sorted(kinds)))


def _full_texts(
    client: HttpClient,
    provider: Provider,
    todo: Sequence[TextRef],
    work: Path,
    report: ImproveReport,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    failures = 0
    for n, text in enumerate(todo):
        if failures >= MAX_FAILURES_IN_A_ROW:
            report.stopped.append(f"{provider.name}: stopped after {failures} failures in a row")
            for rest in todo[n:]:
                out[rest.text_id] = ServiceUnavailable(
                    provider.service, None, "not asked", "the service had stopped answering"
                )
            break
        client.check_cancel()
        try:
            out[text.text_id] = provider.full_text(client, text, work)
            failures = 0
        except Cancelled:
            raise
        except ServiceUnavailable as exc:
            out[text.text_id] = exc
            failures += 1
        except (ServiceError, UnsafeLink, ValueError) as exc:  # this text only
            out[text.text_id] = exc
    return out


def read_improve_runs(runs: list[RawRun], builder: SourceBuilder) -> None:
    """Parts (and the links providers stated) from the provider runs of one slot."""
    for run in runs:
        for rec in run.records():
            if rec.get("type") != "parts":
                continue
            at = parse_time(rec["retrieved_at"])
            tid = rec["text_id"]
            for p in rec.get("parts", []):
                builder.part(
                    tid,
                    part=p["part"],
                    language=p["language"],
                    provider=rec["provider"],
                    content=p["content"],
                    retrieved_at=at,
                    format=p.get("format", "plain"),
                )
            for link in rec.get("links", []):
                builder.link(tid, link["relation"], link["value"])
            builder.count(f"improve: {rec['provider']}")
