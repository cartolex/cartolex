# SPDX-License-Identifier: MIT
"""Coverage: what was collected for whom, said honestly.

Every person of the project gets one **state**:

``good``
    at least ``good`` texts with an abstract (or a full text): 3 by default,
    ``collect.coverage.good`` in ``params.json``;
``thin``
    some texts, but fewer with an abstract; titles without an abstract are
    counted apart, never as texts with words;
``failed``
    the latest collection for the person failed (a service error, a page cut
    short, an answer missing from the cache), with its cause;
``no data``
    nothing exists: no text, and no failure.

A failure is never shown as « no data », and never remembered beyond the next
attempt: the HTTP cache keeps no failed answer, and a later collection that
reaches the person supersedes it (:mod:`cartolex.collect.outcomes`).

The **first blocking cause** says why a profile is missing or thin, in the
order collection meets it: a service failure; no record found (no identity
confirmed, or « none »); not collected yet; a record found but no works in
the window; works without abstracts; fewer texts with an abstract than
``good``. The **person sheet** (:func:`person_sheet`) adds the sources used
(each finder and provider, with its texts) and discarded (candidate records not
confirmed, proposals by name, preprints read through their published version),
and the actions the interface offers: **retry** (for a failure only, see
:func:`retry_failed`), **add documents** (a folder for that person,
:func:`add_documents`) and **exclude** (:func:`exclude_person`).

:func:`coverage_report` aggregates the states **by organisation** and the
texts **by year** and **by language**, beside the texts' summary per slot.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from cartolex.project import Project
from cartolex.project.tables import read_source_table

from .decisions import collect_params, read_people, update_people
from .outcomes import Outcome, latest_outcomes
from .tables import read_runs

__all__ = [
    "CAUSES",
    "STATES",
    "PersonCoverage",
    "add_documents",
    "coverage_report",
    "exclude_person",
    "person_coverage",
    "person_sheet",
    "retry_failed",
]

STATES = ("good", "thin", "failed", "no_data")
#: The first blocking causes, in the order collection meets them, and their words.
CAUSES = {
    "service_failure": "the service failed",
    "no_record": "no record found",
    "not_collected": "not collected yet",
    "no_works_in_window": "a record found, but no works in the window",
    "no_abstracts": "works without abstracts",
    "few_abstracts": "fewer texts with an abstract than a good profile has",
}
WORD_PARTS = frozenset({"abstract", "body", "full"})


@dataclass
class PersonCoverage:
    """One person's coverage: the state, the counts, and the first blocking cause."""

    person_id: str
    name: str
    role: str
    identity: str
    records: list[str]
    state: str
    texts: int = 0
    with_abstract: int = 0
    titles_only: int = 0
    years: dict[str, dict[str, int]] = field(default_factory=dict)
    languages: dict[str, int] = field(default_factory=dict)
    cause: str | None = None
    cause_text: str = ""
    failure: dict[str, Any] | None = None
    organisations: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)

    def line(self) -> str:
        counts = f"{self.with_abstract} with an abstract, {self.titles_only} title(s) only"
        why = f" — {self.cause_text}" if self.cause_text else ""
        return f"{self.person_id}  {self.state:<8} {self.name}: {counts}{why}"


@dataclass
class _Tables:
    people: dict[str, dict[str, Any]]
    texts: dict[str, dict[str, Any]]
    parts: dict[str, dict[str, set[str]]]  # text id → part → languages
    providers: dict[str, set[str]]  # text id → providers
    by_person: dict[str, list[str]]
    orgs: dict[str, dict[str, Any]]
    affiliations: dict[str, set[str]]


def _load(project: Project) -> _Tables:
    layout = project.layout

    def rows(name: str, columns: list[str] | None = None) -> list[dict[str, Any]]:
        path = layout.table(name)
        if not path.exists():
            return []
        return read_source_table(path, name, columns).to_pylist()

    texts = {t["text_id"]: t for t in rows("texts")}
    # A preprint read through its published version counts once, as the build reads it.
    superseded = {tid for tid, t in texts.items() if t["version_of"] in texts}
    parts: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    providers: dict[str, set[str]] = defaultdict(set)
    for p in rows("text_parts", ["text_id", "part", "language", "provider"]):
        parts[p["text_id"]][p["part"]].add(p["language"])
        providers[p["text_id"]].add(p["provider"])
    by_person: dict[str, list[str]] = defaultdict(list)
    for a in rows("authorships", ["text_id", "person_id"]):
        if a["text_id"] in texts and a["text_id"] not in superseded:
            by_person[a["person_id"]].append(a["text_id"])
    affiliations: dict[str, set[str]] = defaultdict(set)
    for a in rows("affiliations", ["person_id", "org_id"]):
        affiliations[a["person_id"]].add(a["org_id"])
    return _Tables(
        people={p["person_id"]: p for p in rows("people")},
        texts=texts,
        parts=parts,
        providers=providers,
        by_person=by_person,
        orgs={o["org_id"]: o for o in rows("organisations")},
        affiliations=affiliations,
    )


def _works_count(project: Project) -> dict[str, tuple[int, int]]:
    """person id → (works of their author records in the index, works received in the window),
    from their latest harvest."""
    from .digests import DigestCache

    out: dict[str, tuple[int, int]] = {}
    digests = DigestCache(project.layout, write=False)
    for slot in (s.id for s in project.config.slots):
        latest: dict[str, str] = {}
        runs = read_runs(project.layout, slot, "openalex", digests=digests)
        for run in runs:
            for pid in run.header.get("people") or {}:
                latest[pid] = run.run_id
        for run in runs:
            if run.run_id not in set(latest.values()):
                continue
            totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
            for rec in run.records():
                pid = rec.get("person_id")
                if latest.get(pid) != run.run_id:
                    continue
                if rec.get("type") == "author":
                    totals[pid][0] += int((rec.get("record") or {}).get("works_count") or 0)
                elif rec.get("type") == "work":
                    totals[pid][1] += 1
            for pid in latest:
                if latest[pid] == run.run_id:
                    out[pid] = tuple(totals.get(pid, [0, 0]))  # type: ignore[assignment]
    return out


def _resolved_without_candidates(project: Project) -> set[str]:
    """People whose latest resolution found no candidate record."""
    latest: dict[str, tuple[str, bool]] = {}
    for slot in (s.id for s in project.config.slots):
        for run in read_runs(project.layout, slot, "resolve"):
            for rec in run.records():
                pid = rec.get("person_id")
                if pid and (pid not in latest or run.run_id >= latest[pid][0]):
                    latest[pid] = (run.run_id, not rec.get("candidates"))
    return {pid for pid, (_, empty) in latest.items() if empty}


def _text_counts(tables: _Tables, tid: str) -> tuple[str, str, bool]:
    """A text's year, its language (of its words, else of its title) and whether it has words."""
    text = tables.texts[tid]
    parts = tables.parts.get(tid, {})
    year = str(text["year"]) if text["year"] is not None else "unknown"
    worded = [p for p in parts if p in WORD_PARTS]
    if worded:
        langs = sorted({lang for p in worded for lang in parts[p]})
        return year, "+".join(langs), True
    langs = sorted(parts.get("title", {"und"}))
    return year, f"{'+'.join(langs)} (title only)", False


def person_coverage(
    project: Project, *, good: int | None = None, people: Sequence[str] | None = None
) -> list[PersonCoverage]:
    """The coverage of every person of the project (or of *people*), in ``person_id`` order.

    People merged into another count with that person; *good* defaults to
    ``params.json``'s ``collect.coverage.good``.
    """
    good = collect_params(project, "coverage")["good"] if good is None else good
    tables = _load(project)
    decisions = read_people(project.layout)
    outcomes = latest_outcomes(project.layout, project.config)
    harvested = _works_count(project)
    nothing_found = _resolved_without_candidates(project)
    merged_into = {
        pid: row["merged_into"] for pid, row in decisions.items() if row.get("merged_into")
    }
    texts_of: dict[str, set[str]] = defaultdict(set)
    for pid, tids in tables.by_person.items():
        texts_of[merged_into.get(pid, pid)].update(tids)
    wanted = set(people) if people is not None else None
    out = []
    for pid in sorted(tables.people):
        if pid in merged_into or (wanted is not None and pid not in wanted):
            continue
        row = tables.people[pid]
        dec = decisions.get(pid, {})
        name = " ".join(x for x in (row["first_name"], row["last_name"]) if x)
        cov = PersonCoverage(
            person_id=pid,
            name=name,
            role=dec.get("role") or "undecided",
            identity=dec.get("identity") or "pending",
            records=[r for r in (dec.get("records") or "").split(";") if r],
            state="no_data",
        )
        years: dict[str, Counter[str]] = defaultdict(Counter)
        languages: Counter[str] = Counter()
        for tid in sorted(texts_of.get(pid, ())):
            year, language, worded = _text_counts(tables, tid)
            cov.texts += 1
            if worded:
                cov.with_abstract += 1
            else:
                cov.titles_only += 1
            years[year]["with_abstract" if worded else "titles_only"] += 1
            languages[language] += 1
        cov.years = {y: dict(c) for y, c in sorted(years.items())}
        cov.languages = dict(sorted(languages.items()))
        cov.organisations = sorted(tables.affiliations.get(pid, ()))
        failed = sorted(
            (o for o in outcomes.get(pid, {}).values() if not o.ok), key=lambda o: o.run_id
        )
        if failed:
            last: Outcome = failed[-1]
            cov.state = "failed"
            cov.cause = "service_failure"
            cov.failure = {
                "finder": last.finder,
                "cause": last.cause,
                "error": last.error,
                "run": last.run_id,
            }
            cov.cause_text = f"{CAUSES['service_failure']} ({last.finder}): {last.cause}"
        elif cov.texts == 0:
            cov.state = "no_data"
            cov.cause, cov.cause_text = _why_nothing(
                cov, harvested.get(pid), pid in nothing_found, outcomes.get(pid, {})
            )
        elif cov.with_abstract >= good:
            cov.state = "good"
        else:
            cov.state = "thin"
            cov.cause = "no_abstracts" if cov.with_abstract == 0 else "few_abstracts"
            cov.cause_text = (
                f"{CAUSES['no_abstracts']}: {cov.titles_only} title(s) without an abstract"
                if cov.with_abstract == 0
                else f"{cov.with_abstract} text(s) with an abstract, fewer than {good}"
            )
        cov.actions = _actions(cov)
        out.append(cov)
    return out


def _why_nothing(
    cov: PersonCoverage,
    harvested: tuple[int, int] | None,
    nothing_found: bool,
    outcomes: Mapping[str, Outcome],
) -> tuple[str, str]:
    if cov.identity == "none":
        return "no_record", f"{CAUSES['no_record']}: the identity was confirmed as « none »"
    if cov.identity == "pending" and nothing_found:
        return "no_record", f"{CAUSES['no_record']}: the resolution found no candidate record"
    if cov.identity == "pending":
        return "not_collected", f"{CAUSES['not_collected']}: the identity waits for confirmation"
    if harvested is None and "harvest" not in outcomes:
        return "not_collected", f"{CAUSES['not_collected']}: the records were never harvested"
    total, received = harvested or (0, 0)
    if received == 0:
        if total:
            return (
                "no_works_in_window",
                f"{CAUSES['no_works_in_window']}: the record(s) hold {total} work(s), none "
                "in the window",
            )
        return "no_works_in_window", f"{CAUSES['no_works_in_window']}: the record(s) hold none"
    return "no_works_in_window", CAUSES["no_works_in_window"]


def _actions(cov: PersonCoverage) -> list[str]:
    out = []
    if cov.state == "failed":
        out.append("retry")
    if cov.role != "excluded":
        out += ["add_documents", "exclude"]
    return out


def coverage_report(
    project: Project, *, good: int | None = None, people: Sequence[str] | None = None
) -> dict[str, Any]:
    """Every person's coverage, and its aggregates: states by organisation (each person
    counted for every organisation they belong to), texts by year and by language, and the
    texts' summary per slot. Excluded people are listed, never counted."""
    from .providers import coverage as slot_coverage

    good = collect_params(project, "coverage")["good"] if good is None else good
    persons = person_coverage(project, good=good, people=people)
    tables = _load(project)
    counted = [p for p in persons if p.role != "excluded"]
    states = Counter(p.state for p in counted)
    by_org: dict[str, Counter[str]] = defaultdict(Counter)
    for p in counted:
        for oid in p.organisations:
            by_org[oid][p.state] += 1
    organisations = {
        oid: {
            "name": tables.orgs[oid]["name"] if oid in tables.orgs else oid,
            "level": tables.orgs[oid]["level"] if oid in tables.orgs else None,
            **{s: c[s] for s in STATES if c[s]},
        }
        for oid, c in sorted(by_org.items())
    }
    # Texts, each counted once however many of its authors are in the project.
    years: dict[str, Counter[str]] = defaultdict(Counter)
    languages: Counter[str] = Counter()
    merged = {pid: row["merged_into"] for pid, row in read_people(project.layout).items()
              if row.get("merged_into")}  # fmt: skip
    wanted = {p.person_id for p in counted}
    texts = {
        tid
        for pid, tids in tables.by_person.items()
        if merged.get(pid, pid) in wanted
        for tid in tids
    }
    for tid in sorted(texts):
        year, language, worded = _text_counts(tables, tid)
        years[year]["with_abstract" if worded else "titles_only"] += 1
        languages[language] += 1
    return {
        "good": good,
        "people": len(counted),
        "excluded": len(persons) - len(counted),
        "states": {s: states[s] for s in STATES},
        "causes": dict(sorted(Counter(p.cause for p in counted if p.cause).items())),
        "by_organisation": organisations,
        "by_year": {y: dict(c) for y, c in sorted(years.items())},
        "by_language": dict(sorted(languages.items())),
        "slots": slot_coverage(project.layout),
        "persons": [asdict(p) for p in persons],
    }


def person_sheet(project: Project, person_id: str, *, good: int | None = None) -> dict[str, Any]:
    """Why a profile is what it is: the coverage, the sources used and discarded, the
    attempts of each finder and the first blocking cause."""
    found = person_coverage(project, good=good, people=[person_id])
    if not found:
        raise ValueError(f"{person_id} is not a person of the project (or is merged)")
    cov = found[0]
    tables = _load(project)
    used: Counter[str] = Counter()
    provided: Counter[str] = Counter()
    for tid in tables.by_person.get(person_id, ()):
        used[tables.texts[tid]["source"]] += 1
        for provider in tables.providers.get(tid, ()):
            provided[provider] += 1
    discarded: list[dict[str, Any]] = []
    confirmed = set(cov.records)
    for slot in (s.id for s in project.config.slots):
        for run in read_runs(project.layout, slot, "resolve"):
            for rec in run.records():
                if rec.get("person_id") != person_id:
                    continue
                for cand in rec.get("candidates") or []:
                    if cand["record"] not in confirmed:
                        discarded.append(
                            {
                                "what": f"{cand['record']} ({cand['name']}, score {cand['score']})",
                                "why": "a candidate record not confirmed",
                                "code": "discarded_candidate",
                                "params": {
                                    "record": cand["record"],
                                    "name": cand["name"],
                                    "score": cand["score"],
                                },
                            }
                        )
        for kind, what in (("hal_candidates", "HAL"), ("scielo_candidates", "SciELO")):
            for run in read_runs(project.layout, slot, kind):
                for rec in run.records():
                    if rec.get("person_id") != person_id:
                        continue
                    record = rec.get("record")
                    if record and record in confirmed:
                        continue
                    shown = rec.get("full_name") or rec.get("name") or ""
                    discarded.append(
                        {
                            "what": f"{what}: {shown}" + (f" ({record})" if record else ""),
                            "why": "found by the name only; confirm its record to collect it",
                            "code": "discarded_name_only",
                            "params": {"source": what, "name": shown, "record": record or ""},
                        }
                    )
    for tid in sorted(_all_texts(project, person_id)):
        text = tables.texts.get(tid)
        if text is not None and text["version_of"] in tables.texts:
            discarded.append(
                {
                    "what": f"{tid} ({text['title'][:60]})",
                    "why": "a preprint read through its published version",
                    "code": "discarded_preprint",
                    "params": {"text_id": tid, "title": text["title"][:60]},
                }
            )
    attempts = [
        {"finder": o.finder, "ok": o.ok, "run": o.run_id, "cause": o.cause}
        for o in sorted(
            latest_outcomes(project.layout, project.config).get(person_id, {}).values(),
            key=lambda o: o.finder,
        )
    ]
    return {
        **asdict(cov),
        "sources": [{"finder": finder, "texts": n} for finder, n in sorted(used.items())],
        "providers": [{"provider": p, "texts": n} for p, n in sorted(provided.items())],
        "discarded": discarded,
        "attempts": attempts,
    }


def _all_texts(project: Project, person_id: str) -> set[str]:
    path = project.layout.table("authorships")
    if not path.exists():
        return set()
    table = read_source_table(path, "authorships", ["text_id", "person_id"]).to_pylist()
    return {a["text_id"] for a in table if a["person_id"] == person_id}


# ── actions ──────────────────────────────────────────────────────────────────


def retry_failed(
    project: Project,
    client: Any,
    *,
    people: Sequence[str] | None = None,
    source: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Collect again for the people whose latest collection failed (only them): the
    harvest, the resolution or the HAL search that failed. Returns each finder's report."""
    from .finders import people_refs
    from .harvest import harvest
    from .resolve import resolve

    failed = [
        p for p in person_coverage(project, people=people) if p.state == "failed" and p.failure
    ]
    by_finder: dict[str, list[str]] = defaultdict(list)
    for p in failed:
        by_finder[p.failure["finder"]].append(p.person_id)  # type: ignore[index]
    reports: dict[str, Any] = {}
    if by_finder.get("resolve"):
        reports["resolve"] = resolve(project, client, people=by_finder["resolve"], now=now)
    if by_finder.get("harvest"):
        reports["harvest"] = harvest(
            project, client, people=by_finder["harvest"], source=source, now=now
        )
    if by_finder.get("hal"):
        from .decisions import slot_window
        from .hal import collect_hal
        from .people_import import _collection_slot

        slot = _collection_slot(project, None, "collection")
        window = slot_window(project.config, slot) or (None, None)
        first = window[0] or 1900
        last = window[1] or (now or datetime.now()).year
        reports["hal"] = collect_hal(
            client,
            project.layout,
            slot,
            people_refs(project.layout, person_ids=by_finder["hal"]),
            window=(first, last),
        )
        from .tables import rebuild_sources

        rebuild_sources(project.layout, project.config)
    other = sorted(set(by_finder) - {"resolve", "harvest", "hal"})
    if other:
        reports["not retried"] = {
            f: by_finder[f] for f in other
        }  # asked again by their own command
    return reports


def add_documents(project: Project, person_id: str, folder: Path) -> Any:
    """Import a folder of documents that all belong to *person_id*."""
    from .people_import import import_folder

    return import_folder(project, Path(folder), person_id=person_id)


def exclude_person(project: Project, person_id: str, *, note: str = "") -> int:
    """Set the person's role to ``excluded``: nothing uses them any more."""
    return update_people(
        project.layout,
        {person_id: {"role": "excluded", "note": note or "excluded from the coverage report"}},
        action=f"exclude {person_id}",
    )
