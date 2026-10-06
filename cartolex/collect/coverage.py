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
from collections.abc import Collection, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from cartolex.project import Project
from cartolex.project.tables import read_source_table
from cartolex.project.text_columns import TextColumns, read_text_columns
from cartolex.scale import sorted_unique

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
    """What the coverage reads of the tables: the people, the organisations, each
    person's organisations, and the texts as columns (:mod:`cartolex.project.text_columns`)
    with each one's year and language as the coverage names them."""

    people: dict[str, dict[str, Any]]
    orgs: dict[str, dict[str, Any]]
    affiliations: dict[str, set[str]]
    columns: TextColumns
    #: Each person's texts read (rows of *columns*).
    by_person: dict[str, np.ndarray]
    #: Each text's year (``unknown`` without one) and language, as codes.
    years: list[str]
    year_code: np.ndarray
    languages: list[str]
    language_code: np.ndarray

    def counts(self, rows: np.ndarray) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
        """The texts at *rows* by year (with an abstract, titles only) and by language."""
        worded = self.columns.has_words()[rows]
        years: dict[str, dict[str, int]] = {}
        for code, flag, n in _counted(self.year_code[rows], worded):
            years.setdefault(self.years[code], {})["with_abstract" if flag else "titles_only"] = n
        languages = {
            self.languages[code]: n
            for code, _, n in _counted(self.language_code[rows], np.zeros(len(rows), dtype=bool))
        }
        return {y: years[y] for y in sorted(years)}, dict(sorted(languages.items()))


def _counted(codes: np.ndarray, flags: np.ndarray) -> list[tuple[int, bool, int]]:
    """How many rows have each (code, flag)."""
    if not len(codes):
        return []
    pairs, n = np.unique(codes.astype(np.int64) * 2 + flags, return_counts=True)
    return [(int(p) // 2, bool(p % 2), int(k)) for p, k in zip(pairs, n, strict=True)]


def _load(
    project: Project,
    people: Collection[str] | None = None,
    *,
    detail: bool = True,
    columns: TextColumns | None = None,
) -> _Tables:
    """The tables as the coverage reads them: every person's, or only *people*'s rows;
    without *detail*, the people's names and their texts' counts only (no years,
    languages or organisations). *columns*: every text's, already read."""
    layout = project.layout

    def rows(
        name: str, columns: list[str] | None, key: str, wanted: Collection[str] | None
    ) -> list[dict[str, Any]]:
        path = layout.table(name)
        if not path.exists() or (wanted is not None and not wanted):
            return []
        filters = [(key, "in", sorted(wanted))] if wanted is not None else None
        return read_source_table(path, name, columns, filters=filters).to_pylist()

    if columns is None or people is not None:
        columns = read_text_columns(layout, people=people)
    people_rows = {
        p["person_id"]: p
        for p in rows(
            "people",
            None if detail else ["person_id", "first_name", "last_name"],
            "person_id",
            people,
        )
    }
    if not detail:
        none = np.zeros(0, dtype=np.int64)
        return _Tables(people_rows, {}, {}, columns, {}, [], none, [], none)
    year_values, year_code = np.unique(
        np.where(columns.has_year, columns.year, -1), return_inverse=True
    )
    language_code, sets = columns.language_sets()
    # A text without words is named by its title's languages (« und »: none), apart.
    pairs = language_code.astype(np.int64) * 2 + columns.has_words()
    named, language_code = np.unique(pairs, return_inverse=True) if len(pairs) else (pairs, pairs)
    affiliations: dict[str, set[str]] = defaultdict(set)
    for a in rows("affiliations", ["person_id", "org_id"], "person_id", people):
        affiliations[a["person_id"]].add(a["org_id"])
    org_ids = (
        None if people is None else sorted({o for orgs in affiliations.values() for o in orgs})
    )
    return _Tables(
        people=people_rows,
        orgs={o["org_id"]: o for o in rows("organisations", None, "org_id", org_ids)},
        affiliations=affiliations,
        columns=columns,
        by_person=columns.texts_by_person(),
        years=[str(y) if y >= 0 else "unknown" for y in year_values.tolist()],
        year_code=np.asarray(year_code, dtype=np.int64).reshape(-1),
        languages=[
            "+".join(sets[p // 2]) if p % 2 else f"{'+'.join(sets[p // 2]) or 'und'} (title only)"
            for p in named.tolist()
        ],
        language_code=np.asarray(language_code, dtype=np.int64).reshape(-1),
    )


def _with_merged(people: Collection[str] | None, merged_into: Mapping[str, str]) -> set[str] | None:
    """*people* and the people merged into them (``None``: everyone)."""
    if people is None:
        return None
    wanted = set(people)
    return wanted | {pid for pid, into in merged_into.items() if into in wanted}


def _per_person(
    tables: _Tables, merged_into: Mapping[str, str], *, detail: bool = True
) -> dict[str, tuple[int, int, dict[str, dict[str, int]], dict[str, int]]]:
    """Each person's texts read, with those of the people merged into them (a text once):
    how many, how many with an abstract, and with *detail* by year and by language."""
    cols = tables.columns
    owners = sorted({merged_into.get(pid, pid) for pid in cols.person_ids})
    code = {pid: i for i, pid in enumerate(owners)}
    remap = np.array([code[merged_into.get(pid, pid)] for pid in cols.person_ids], dtype=np.int64)
    keep = ~cols.superseded[cols.author_text]
    width = max(cols.n, 1)
    pairs = sorted_unique(remap[cols.author_person[keep]] * width + cols.author_text[keep])
    who, rows = pairs // width, pairs % width
    worded = cols.has_words()[rows].astype(np.int64)
    texts = np.bincount(who, minlength=len(owners))
    abstracts = np.bincount(who, weights=worded, minlength=len(owners)).astype(np.int64)
    years: dict[int, dict[str, dict[str, int]]] = defaultdict(dict)
    languages: dict[int, dict[str, int]] = defaultdict(dict)
    if not detail:
        return {
            pid: (int(texts[i]), int(abstracts[i]), {}, {})
            for i, pid in enumerate(owners)
            if texts[i]
        }
    span = 2 * max(len(tables.years), 1)
    keys, counts = np.unique(who * span + tables.year_code[rows] * 2 + worded, return_counts=True)
    for key, n in zip(keys.tolist(), counts.tolist(), strict=True):
        person, rest = divmod(key, span)
        year, flag = divmod(rest, 2)
        years[person].setdefault(tables.years[year], {})[
            "with_abstract" if flag else "titles_only"
        ] = n
    span = max(len(tables.languages), 1)
    keys, counts = np.unique(who * span + tables.language_code[rows], return_counts=True)
    for key, n in zip(keys.tolist(), counts.tolist(), strict=True):
        person, language = divmod(key, span)
        languages[person][tables.languages[language]] = n
    return {
        pid: (
            int(texts[i]),
            int(abstracts[i]),
            {y: years[i][y] for y in sorted(years[i])},
            dict(sorted(languages[i].items())),
        )
        for i, pid in enumerate(owners)
        if texts[i]
    }


def _works_count(project: Project, people: Collection[str]) -> dict[str, tuple[int, int] | None]:
    """For each of *people* their latest harvest named: (works their author records hold in
    the index, works received in the window), as the harvest wrote them in its run's
    header (``None``: a run of an earlier version, which does not say). Only the runs'
    headers are read."""
    wanted = set(people)
    out: dict[str, tuple[int, int] | None] = {}
    if not wanted:
        return out
    for slot in (s.id for s in project.config.slots):
        latest: dict[str, Any] = {}
        for run in read_runs(project.layout, slot, "openalex"):
            for pid, meta in (run.header.get("people") or {}).items():
                if pid in wanted:
                    latest[pid] = meta
        for pid, meta in latest.items():
            works = meta.get("works") if isinstance(meta, dict) else None
            out[pid] = (
                (int(works[0]), int(works[1]))
                if isinstance(works, list) and len(works) == 2
                else None
            )
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


def person_coverage(
    project: Project,
    *,
    good: int | None = None,
    people: Sequence[str] | None = None,
    tables: _Tables | None = None,
    decisions: Mapping[str, dict[str, Any]] | None = None,
    outcomes: Mapping[str, dict[str, Outcome]] | None = None,
    detail: bool = True,
    columns: TextColumns | None = None,
) -> list[PersonCoverage]:
    """The coverage of every person of the project (or of *people*), in ``person_id`` order.

    People merged into another count with that person; *good* defaults to
    ``params.json``'s ``collect.coverage.good``. *tables*, *decisions* and *outcomes*
    are what was already read (:func:`_load` for *people* and the people merged into
    them, ``people.csv``, :func:`~cartolex.collect.outcomes.latest_outcomes`). Without
    *detail*, the counts and the states only: no years, languages or organisations.
    *columns*: every text's (:func:`~cartolex.project.text_columns.read_text_columns`),
    when the caller has read them.
    """
    good = collect_params(project, "coverage")["good"] if good is None else good
    decisions = read_people(project.layout) if decisions is None else decisions
    merged_into = {
        pid: row["merged_into"] for pid, row in decisions.items() if row.get("merged_into")
    }
    if tables is None:
        tables = _load(project, _with_merged(people, merged_into), detail=detail, columns=columns)
    if outcomes is None:
        outcomes = latest_outcomes(project.layout, project.config)
    nothing_found = _resolved_without_candidates(project)
    found = _per_person(tables, merged_into, detail=detail)
    wanted = set(people) if people is not None else None
    out = []
    empty: list[PersonCoverage] = []
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
        cov.texts, cov.with_abstract, cov.years, cov.languages = found.get(pid, (0, 0, {}, {}))
        cov.titles_only = cov.texts - cov.with_abstract
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
            empty.append(cov)  # its cause once the harvests of everyone without texts are read
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
        out.append(cov)
    harvested = _works_count(project, [c.person_id for c in empty])
    for cov in empty:
        cov.cause, cov.cause_text = _why_nothing(
            cov,
            harvested.get(cov.person_id),
            cov.person_id in harvested,
            cov.person_id in nothing_found,
            outcomes.get(cov.person_id, {}),
        )
    for cov in out:
        cov.actions = _actions(cov)
    return out


def _why_nothing(
    cov: PersonCoverage,
    harvested: tuple[int, int] | None,
    was_harvested: bool,
    nothing_found: bool,
    outcomes: Mapping[str, Outcome],
) -> tuple[str, str]:
    if cov.identity == "none":
        return "no_record", f"{CAUSES['no_record']}: the identity was confirmed as « none »"
    if cov.identity == "pending" and nothing_found:
        return "no_record", f"{CAUSES['no_record']}: the resolution found no candidate record"
    if cov.identity == "pending":
        return "not_collected", f"{CAUSES['not_collected']}: the identity waits for confirmation"
    if not was_harvested and "harvest" not in outcomes:
        return "not_collected", f"{CAUSES['not_collected']}: the records were never harvested"
    if was_harvested and harvested is None:  # a harvest that did not count the works
        return "no_works_in_window", CAUSES["no_works_in_window"]
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
    decisions = read_people(project.layout)
    merged = {pid: row["merged_into"] for pid, row in decisions.items() if row.get("merged_into")}
    tables = _load(project, _with_merged(people, merged))
    persons = person_coverage(project, good=good, people=people, tables=tables, decisions=decisions)
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
    cols = tables.columns
    wanted = {p.person_id for p in counted}
    chosen = np.array([merged.get(pid, pid) in wanted for pid in cols.person_ids], dtype=bool)
    keep = (chosen[cols.author_person] if len(chosen) else np.zeros(0, bool)) & ~cols.superseded[
        cols.author_text
    ]
    years, languages = tables.counts(sorted_unique(cols.author_text[keep]))
    return {
        "good": good,
        "people": len(counted),
        "excluded": len(persons) - len(counted),
        "states": {s: states[s] for s in STATES},
        "causes": dict(sorted(Counter(p.cause for p in counted if p.cause).items())),
        "by_organisation": organisations,
        "by_year": years,
        "by_language": languages,
        "slots": slot_coverage(project.layout),
        "persons": [asdict(p) for p in persons],
    }


def person_sheet(project: Project, person_id: str, *, good: int | None = None) -> dict[str, Any]:
    """Why a profile is what it is: the coverage, the sources used and discarded, the
    attempts of each finder and the first blocking cause."""
    decisions = read_people(project.layout)
    merged = {pid: row["merged_into"] for pid, row in decisions.items() if row.get("merged_into")}
    tables = _load(project, _with_merged([person_id], merged))
    outcomes = latest_outcomes(project.layout, project.config)
    found = person_coverage(
        project,
        good=good,
        people=[person_id],
        tables=tables,
        decisions=decisions,
        outcomes=outcomes,
    )
    if not found:
        raise ValueError(f"{person_id} is not a person of the project (or is merged)")
    cov = found[0]
    cols = tables.columns
    used: Counter[str] = Counter()
    provided: Counter[str] = Counter()
    for row in tables.by_person.get(person_id, np.zeros(0, dtype=np.int32)).tolist():
        used[cols.sources[cols.source[row]]] += 1
        bits = int(cols.providers[row])
        provided.update(p for b, p in enumerate(cols.provider_names) if bits >> b & 1)
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
    mine = (
        cols.author_text[cols.author_person == cols.person_ids.index(person_id)]
        if (person_id in cols.person_ids)
        else np.zeros(0, dtype=np.int32)
    )
    preprints = sorted({cols.tid(r) for r in mine.tolist() if cols.superseded[r]})
    titles = _titles(project, preprints)
    for tid in preprints:
        title = (titles.get(tid) or "")[:60]
        discarded.append(
            {
                "what": f"{tid} ({title})",
                "why": "a preprint read through its published version",
                "code": "discarded_preprint",
                "params": {"text_id": tid, "title": title},
            }
        )
    attempts = [
        {"finder": o.finder, "ok": o.ok, "run": o.run_id, "cause": o.cause}
        for o in sorted(outcomes.get(person_id, {}).values(), key=lambda o: o.finder)
    ]
    return {
        **asdict(cov),
        "sources": [{"finder": finder, "texts": n} for finder, n in sorted(used.items())],
        "providers": [{"provider": p, "texts": n} for p, n in sorted(provided.items())],
        "discarded": discarded,
        "attempts": attempts,
    }


def _titles(project: Project, text_ids: list[str]) -> dict[str, str]:
    """The titles of *text_ids* (read from the row groups that hold them)."""
    if not text_ids:
        return {}
    table = read_source_table(
        project.layout.table("texts"),
        "texts",
        ["text_id", "title"],
        filters=[("text_id", "in", text_ids)],
    )
    return dict(zip(table["text_id"].to_pylist(), table["title"].to_pylist(), strict=True))


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
