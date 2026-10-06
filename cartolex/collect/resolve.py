# SPDX-License-Identifier: MIT
"""Resolution: which author records, if any, are each person.

The rules come from real use; each has a scenario test:

* **Affiliation ranks candidates, never filters them.** The name is searched
  flat, in each of its variants (:func:`~cartolex.collect.names.variants`);
  a search restricted to the stated institutions is added, on the full name
  only; the lists are merged. A lab-level affiliation often resolves to an
  institution record people never cite, so filtering by it would drop the
  right person and keep a homonym.
* **A person can have several records**: a confirmation lists them all
  (``records`` in ``decisions/people.csv``) and the harvest unites their works.
* **« None » is a valid answer** (:func:`confirm_none`), and so is a pasted
  OpenAlex id, ORCID or URL holding one (:func:`confirm_pasted`).
* **Every finder is confirmed alike**: a HAL author form is confirmed as
  ``hal:<idHAL>``, a SciELO author as the ``orcid:`` the article shows, with the
  same :func:`confirm`; :func:`identity_queue` lists every finder's candidates
  of each person waiting, each with the record that confirms it.
* **Automatic acceptance** only when a single candidate scores above the
  threshold and nothing contradicts it; it is recorded as ``identity = auto``
  and stays to be reviewed. Everything else waits (``pending``).
* **The registry separates people an index merged.** When a candidate or the
  person carries an ORCID, the works declared in the registry are counted and
  compared with the record's; a large gap either way is shown with both counts
  and the person decides (a record holding many more works may mix two
  people; one holding fewer may leave works on another record).

Each candidate comes with its evidence: its name, institutions with years,
number of works, first and last year, top topics, ORCID, and the points each
piece of evidence gave its score.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from cartolex.project import Project
from cartolex.project.tables import read_source_table

from .decisions import read_people, update_people
from .http import CacheMiss, Cancelled, CollectError, HttpClient, ServiceError
from .names import name_similarity, variants, words
from .openalex import (
    author,
    authors_by_orcid,
    record_dois,
    record_summary,
    search_authors,
    search_institutions,
    short_id,
)
from .orcid import declared_works
from .outcomes import MAX_FAILURES_IN_A_ROW, failure_record, stops_the_job, write_failures
from .people_import import _collection_slot, normalise_openalex_author, normalise_orcid
from .tables import RawWriter

__all__ = [
    "THRESHOLD",
    "Candidate",
    "RegistryCount",
    "Resolution",
    "ResolveReport",
    "confirm",
    "confirm_none",
    "confirm_pasted",
    "identity_queue",
    "parse_record",
    "resolve",
]

#: A candidate at or above this score may be accepted automatically.
THRESHOLD = 0.8
#: How many stated organisations are searched as institutions, and how many candidates are kept.
MAX_STATED = 3
MAX_CANDIDATES = 10

#: The pieces of evidence a candidate's score is made of, as codes with their English
#: words (an interface words them from its catalogues, ``corpus.evidence.<code>``).
EVIDENCE_CODES = {
    "name_same": "name: same name",
    "name_compound": "name: one part of a compound surname",
    "name_initial": "name: the first name as an initial",
    "name_other_first_names": "name: the same name with other first names",
    "name_same_surname": "name: the same surname, another first name",
    "name_other": "name: another name",
    "same_orcid": "the same ORCID",
    "other_orcid": "another ORCID",
    "listed_id": "the identifier in the list",
    "stated_institution": "stated institution on the record ({first}–{last})",
    "related_institution": "an institution the stated one belongs to, or part of it",
    "similar_institution": "an institution with a similar name",
    "no_works": "no works",
}
#: :func:`~cartolex.collect.names.name_similarity`'s reasons → evidence codes.
NAME_REASONS = {
    "same name": "name_same",
    "one part of a compound surname": "name_compound",
    "the first name as an initial": "name_initial",
    "the same name with other first names": "name_other_first_names",
    "the same surname, another first name": "name_same_surname",
    "another name": "name_other",
}


def evidence_words(code: str, params: Mapping[str, Any] | None = None) -> str:
    """The English words of a piece of evidence."""
    return EVIDENCE_CODES.get(code, code).format(**(params or {}))


@dataclass
class Candidate:
    """An author record that may be the person, with its evidence and its explained score."""

    record: str
    name: str
    alternatives: list[str]
    orcid: str | None
    institutions: list[dict[str, Any]]
    works: int
    first_year: int | None
    last_year: int | None
    topics: list[str]
    found_by: list[str] = field(default_factory=list)
    score: float = 0.0
    evidence: list[tuple[str, float]] = field(default_factory=list)
    #: The evidence as codes for an interface's catalogues, in the order of
    #: :attr:`evidence`: ``{"code", "params"}`` (see :data:`EVIDENCE_CODES`).
    evidence_codes: list[dict[str, Any]] = field(default_factory=list)

    def describe(self) -> str:
        years = f"{self.first_year}–{self.last_year}" if self.first_year is not None else "no year"
        insts = "; ".join(
            f"{i['name']} ({i['first_year']}–{i['last_year']})" for i in self.institutions[:3]
        )
        points = ", ".join(f"{why} {pts:+.2f}" for why, pts in self.evidence)
        return (
            f"{self.record}  {self.score:.2f}  {self.name}"
            + (f"  ORCID {self.orcid}" if self.orcid else "")
            + f"\n      {self.works} works, {years}; {insts or 'no institution'}"
            + (f"; topics: {', '.join(self.topics)}" if self.topics else "")
            + f"\n      score: {points}"
        )


@dataclass
class RegistryCount:
    """What the registry holds for one ORCID: works declared, and those with a DOI."""

    orcid: str
    declared: int
    with_doi: int

    @property
    def record(self) -> str:
        return f"orcid:{self.orcid}"


@dataclass
class Resolution:
    """The outcome for one person."""

    person_id: str
    name: str
    status: str  # "auto", "pending"
    candidates: list[Candidate] = field(default_factory=list)
    registry: list[RegistryCount] = field(default_factory=list)
    records: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResolveReport:
    """What a resolution run did."""

    resolutions: list[Resolution] = field(default_factory=list)
    skipped: int = 0
    cancelled: bool = False
    #: People whose resolution failed, with the cause (see :mod:`cartolex.collect.outcomes`).
    failures: list[dict[str, Any]] = field(default_factory=list)
    stopped: str | None = None

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.resolutions:
            out[r.status] = out.get(r.status, 0) + 1
        return out


# ── confirmations ────────────────────────────────────────────────────────────


_IDHAL = re.compile(r"^[a-z0-9][a-z0-9._-]{1,99}$", re.IGNORECASE)


def parse_record(text: str) -> str | None:
    """``openalex:A…``, ``orcid:…`` or ``hal:<idHAL>`` from a record, an id, or a URL holding
    one (an idHAL needs its ``hal:`` prefix: it is a free word)."""
    text = (text or "").strip()
    if not text:
        return None
    if text.lower().startswith("hal:"):
        idhal = text.split(":", 1)[1].strip()
        return f"hal:{idhal}" if _IDHAL.match(idhal) else None
    if text.lower().startswith("openalex:"):
        found = normalise_openalex_author(text.split(":", 1)[1])
        return f"openalex:{found}" if found else None
    if text.lower().startswith("orcid:"):
        found = normalise_orcid(text.split(":", 1)[1])
        return f"orcid:{found}" if found else None
    orcid = normalise_orcid(text)
    if orcid and ("orcid" in text.lower() or len(text.replace("-", "")) <= 16):
        return f"orcid:{orcid}"
    found = normalise_openalex_author(text)
    return f"openalex:{found}" if found else None


def _records(records: Iterable[str]) -> list[str]:
    out = []
    for text in records:
        parsed = parse_record(text)
        if parsed is None:
            raise ValueError(f"{text!r} is not an OpenAlex author id, an ORCID or hal:<idHAL>")
        if parsed not in out:
            out.append(parsed)
    return out


def confirm(
    project: Project,
    person_id: str,
    records: Sequence[str],
    *,
    note: str = "",
    now: datetime | None = None,
) -> list[str]:
    """Record that *records* (all of them) are this person: ``identity = confirmed``."""
    found = _records(records)
    if not found:
        raise ValueError("a confirmation names at least one record; use confirm_none for none")
    update_people(
        project.layout,
        {person_id: {"identity": "confirmed", "records": ";".join(found), "note": note}},
        action=f"confirm {person_id}",
        now=now,
    )
    return found


def confirm_none(
    project: Project, person_id: str, *, note: str = "", now: datetime | None = None
) -> None:
    """Record that no record exists for this person: ``identity = none``."""
    update_people(
        project.layout,
        {person_id: {"identity": "none", "records": "", "note": note or "no record exists"}},
        action=f"no record for {person_id}",
        now=now,
    )


def confirm_pasted(
    project: Project,
    client: HttpClient,
    person_id: str,
    text: str,
    *,
    now: datetime | None = None,
) -> str:
    """Confirm the record a pasted id or URL names, after checking the service holds it."""
    record = parse_record(text)
    if record is None:
        raise ValueError(f"{text!r} holds no OpenAlex author id and no ORCID")
    scheme, value = record.split(":", 1)
    exists = (
        author(client, value) is not None
        if scheme == "openalex"
        else declared_works(client, value) is not None
    )
    if not exists:
        raise ValueError(f"{record} does not exist in {scheme}")
    confirm(project, person_id, [record], note="pasted", now=now)
    return record


# ── resolving ────────────────────────────────────────────────────────────────


def _stated(project: Project) -> dict[str, list[str]]:
    """person id → the names of the organisations stated for them, smallest first, then parents."""
    layout = project.layout
    if not layout.table("affiliations").exists() or not layout.table("organisations").exists():
        return {}
    orgs = {
        o["org_id"]: o
        for o in read_source_table(layout.table("organisations"), "organisations").to_pylist()
    }
    order = {lv.id: i for i, lv in enumerate(project.config.levels)}
    out: dict[str, list[str]] = {}
    affs = read_source_table(layout.table("affiliations"), "affiliations").to_pylist()
    for pid in sorted({a["person_id"] for a in affs}):
        own = [orgs[a["org_id"]] for a in affs if a["person_id"] == pid and a["org_id"] in orgs]
        own.sort(key=lambda o: (order.get(o["level"] or "", 99), o["name"]))
        names: list[str] = []
        for org in own:
            chain = [org] + [orgs[p] for p in org["parents"] or [] if p in orgs]
            for o in chain:
                if o["name"] not in names:
                    names.append(o["name"])
        out[pid] = names
    return out


def _add(cands: dict[str, Candidate], record: dict[str, Any], found_by: str) -> None:
    summary = record_summary(record)
    rid = summary["record"]
    if rid not in cands:
        cands[rid] = Candidate(**summary)
    if found_by not in cands[rid].found_by:
        cands[rid].found_by.append(found_by)


def _overlap(a: str, b: str) -> float:
    wa, wb = set(words(a)), set(words(b))
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def _score(
    cand: Candidate,
    names: list[tuple[str, str]],
    orcid: str | None,
    known: set[str],
    stated_ids: dict[str, str],
    stated_lineage: dict[str, str],
    stated_names: list[str],
) -> None:
    points: list[tuple[str, float]] = []
    codes: list[dict[str, Any]] = []

    def add(code: str, points_: float, **params: Any) -> None:
        points.append((evidence_words(code, params), points_))
        codes.append({"code": code, "params": params})

    similarity, why = name_similarity(names, cand.name, cand.alternatives)
    add(NAME_REASONS.get(why, "name_other"), round(0.6 * similarity, 3))
    if orcid and cand.orcid:
        add("same_orcid", 0.4) if cand.orcid == orcid else add("other_orcid", -0.5)
    if cand.record in known:
        add("listed_id", 0.4)
    held = {i["id"]: i for i in cand.institutions if i["id"]}
    lineages = {x for i in cand.institutions for x in i["lineage"]}
    same = [held[i] for i in held if i in stated_ids]
    near = [held[i] for i in held if i in stated_lineage] or [
        i for i in cand.institutions if set(i["lineage"]) & set(stated_ids)
    ]
    if same:
        inst = same[0]
        first, last = inst["first_year"] or "—", inst["last_year"] or "—"
        add("stated_institution", 0.25, first=first, last=last)
    elif near or (lineages & set(stated_ids)):
        add("related_institution", 0.2)
    elif any(_overlap(i["name"], s) >= 0.5 for i in cand.institutions for s in stated_names):
        add("similar_institution", 0.15)
    if cand.works == 0:
        add("no_works", -0.2)
    cand.evidence = points
    cand.evidence_codes = codes
    cand.score = round(max(0.0, min(1.0, sum(p for _, p in points))), 3)


def _gap(record_dois: set[str], declared: set[str]) -> str | None:
    """Why a record and the registry disagree, if they do (both compared by DOI)."""
    if len(declared) < 3 or not record_dois:
        return None
    undeclared = record_dois - declared
    if len(undeclared) >= 3 and len(undeclared) >= 0.25 * len(record_dois):
        return (
            f"{len(undeclared)} of the record's {len(record_dois)} works with a DOI are not "
            f"declared in the registry, which declares {len(declared)}: the record may mix "
            "two people"
        )
    missing = declared - record_dois
    if len(missing) >= 3 and len(missing) >= 0.25 * len(declared):
        return (
            f"the registry declares {len(missing)} works with a DOI the record does not hold: "
            "another record may hold them"
        )
    return None


def resolve_person(
    client: HttpClient,
    person: dict[str, Any],
    stated: Sequence[str],
    *,
    threshold: float = THRESHOLD,
) -> Resolution:
    """Candidates for one person (a row of the people table), scored; auto or pending."""
    last, first = person["last_name"], person["first_name"] or ""
    names = [(last, first)] + [
        (a["last_name"], a["first_name"] or "") for a in person.get("aliases") or []
    ]
    ids = dict(person.get("ids") or [])
    orcid = person.get("orcid")
    known = {f"openalex:{a}" for a in ids.get("openalex", [])}
    res = Resolution(
        person_id=person["person_id"], name=f"{first} {last}".strip(), status="pending"
    )
    cands: dict[str, Candidate] = {}
    for rid in sorted(known):
        fetched = author(client, rid.split(":", 1)[1])
        if fetched is None:
            res.notes.append(f"{rid} (given in the list) does not exist")
        else:
            _add(cands, fetched.data, "the identifier in the list")
    if orcid:
        for rec in authors_by_orcid(client, orcid):
            _add(cands, rec, "the ORCID in the list")
    for text in variants(last, first):
        for rec in search_authors(client, text):
            _add(cands, rec, f"name « {text} »")
    stated_ids: dict[str, str] = {}
    stated_lineage: dict[str, str] = {}
    for org in list(stated)[:MAX_STATED]:
        for inst in search_institutions(client, org)[:5]:
            if _overlap(inst.get("display_name") or "", org) < 0.5:
                continue
            iid = short_id(inst.get("id"))
            if iid:
                stated_ids.setdefault(iid, org)
                for parent in inst.get("lineage") or []:
                    pid_ = short_id(parent)
                    if pid_ and pid_ != iid:
                        stated_lineage.setdefault(pid_, org)
    if stated_ids:
        full = f"{first} {last}".strip()
        for rec in search_authors(client, full, institution_ids=sorted(stated_ids)[:10]):
            _add(cands, rec, "name at a stated institution")
    for cand in cands.values():
        _score(cand, names, orcid, known, stated_ids, stated_lineage, list(stated))
    ranked = sorted(cands.values(), key=lambda c: (-c.score, -c.works, c.record))
    res.candidates = ranked[:MAX_CANDIDATES]
    above = [c for c in ranked if c.score >= threshold]
    # The registry: the person's ORCID, and those the likely records carry.
    wanted = [orcid] if orcid else []
    wanted += [c.orcid for c in above if c.orcid and c.orcid not in wanted]
    registry: dict[str, RegistryCount] = {}
    declared_dois: dict[str, set[str]] = {}
    for o in wanted:
        found = declared_works(client, o)
        if found is None:
            res.notes.append(f"the registry has no record for ORCID {o}")
            continue
        declared = found[0]
        registry[o] = RegistryCount(o, len(declared), sum(1 for w in declared if w.doi))
        declared_dois[o] = {w.doi for w in declared if w.doi}
    res.registry = list(registry.values())
    if not ranked:
        res.notes.append("no record found: confirm « none », or paste an id")
    conflicts = []
    for cand in above:
        key = cand.orcid if cand.orcid in declared_dois else orcid
        if key in declared_dois and len(declared_dois[key]) >= 3:
            gap = _gap(record_dois(client, cand.record.split(":", 1)[1]), declared_dois[key])
            if gap:
                conflicts.append(f"{cand.record}: {gap}")
    res.notes += conflicts
    if len(above) == 1 and not conflicts:
        best = above[0]
        res.status = "auto"
        res.records = [best.record]
        own = best.orcid if best.orcid and (not orcid or best.orcid == orcid) else None
        if own and own in registry and registry[own].with_doi:
            res.records.append(f"orcid:{own}")
    elif len(above) > 1:
        res.notes.append(f"{len(above)} candidates score {threshold} or more: choose")
    return res


def resolve(
    project: Project,
    client: HttpClient,
    *,
    people: Sequence[str] | None = None,
    auto: bool = False,
    threshold: float = THRESHOLD,
    again: bool = False,
    slot: str | None = None,
    now: datetime | None = None,
) -> ResolveReport:
    """Look for each pending person's records; with *auto*, accept the single clear matches.

    People whose identity is ``pending`` or not set are resolved (with *again*,
    also those accepted automatically); *people* narrows the list. Each
    person's candidates are kept in ``sources/<slot>/raw/resolve/``; accepted
    matches go to ``decisions/people.csv`` as ``identity = auto``. A cancel
    keeps what was resolved before it.
    """
    now = now or datetime.now(timezone.utc)
    layout = project.layout
    if not layout.table("people").exists():
        raise FileNotFoundError("the project has no people yet: import a list first")
    rows = read_source_table(layout.table("people"), "people").to_pylist()
    decisions = read_people(layout)
    wanted = set(people) if people is not None else None
    targets = []
    report = ResolveReport()
    for row in rows:
        pid = row["person_id"]
        dec = decisions.get(pid, {})
        if wanted is not None and pid not in wanted:
            continue
        identity = dec.get("identity", "")
        if dec.get("merged_into") or dec.get("role") == "excluded":
            report.skipped += 1
            continue
        if identity in ("", "pending") or (again and identity == "auto") or wanted is not None:
            targets.append(row)
        else:
            report.skipped += 1
    stated = _stated(project)
    slot = _collection_slot(project, slot, "collection")
    changes: dict[str, dict[str, str]] = {}
    in_a_row = 0
    last_error: CollectError | None = None
    with RawWriter(layout, slot, "resolve", {"threshold": threshold, "auto": auto}, now=now) as out:
        try:
            for k, person in enumerate(targets):
                client.check_cancel()
                client.progress(k / max(1, len(targets)), f"person {k + 1} of {len(targets)}")
                try:
                    res = resolve_person(
                        client, person, stated.get(person["person_id"], []), threshold=threshold
                    )
                except (ServiceError, CacheMiss) as exc:
                    report.failures.append(
                        failure_record(person["person_id"], "resolve", exc, now=now)
                    )
                    in_a_row += 1
                    last_error = exc
                    if stops_the_job(client, exc) or in_a_row >= MAX_FAILURES_IN_A_ROW:
                        report.stopped = (
                            f"the resolution stopped after {in_a_row} failure(s) in a row; "
                            f"{len(targets) - k - 1} person(s) were not asked for"
                        )
                        break
                    continue
                in_a_row = 0
                report.resolutions.append(res)
                out.add(res.to_json())
                current = decisions.get(res.person_id, {}).get("identity", "")
                if auto and res.status == "auto" and current not in ("confirmed", "none"):
                    changes[res.person_id] = {
                        "identity": "auto",
                        "records": ";".join(res.records),
                        "note": "accepted automatically; to review",
                    }
                elif current == "":
                    changes[res.person_id] = {"identity": "pending"}
        except Cancelled:
            report.cancelled = True
        out.header["people"] = len(report.resolutions)
        if not report.resolutions:
            out.discard()
    write_failures(layout, slot, "resolve", report.failures, now=now)
    if changes:
        update_people(layout, changes, action="resolve", now=now)
    client.progress(1.0, "resolution done")
    if report.cancelled:
        raise Cancelled(
            f"resolution cancelled after {len(report.resolutions)} of {len(targets)} people; "
            "their results are kept"
        )
    if report.stopped and last_error is not None:
        raise last_error
    return report


def identity_queue(
    project: Project, *, auto: bool = False, people: Collection[str] | None = None
) -> list[dict[str, Any]]:
    """The people whose identity waits (with *auto*, also those accepted automatically, to
    review), each with every finder's candidates; with *people*, those people whatever
    was decided of their identity (to change it), merged and excluded rows left out.

    A candidate carries the ``record`` that :func:`confirm` takes: an OpenAlex
    record from the resolution (with its score and evidence), a HAL author form
    with its idHAL (``hal:<idHAL>``), a SciELO author whose article shows an
    ORCID (``orcid:…``). Proposals without such a record (a HAL form without an
    idHAL, a SciELO name without an ORCID) are listed without one: they can only
    be seen, not confirmed. The latest run of each finder counts.
    """
    from .tables import read_runs

    rows = read_source_table(project.layout.table("people"), "people").to_pylist()
    decisions = read_people(project.layout)
    asked = set(people) if people is not None else None
    states = ("", "pending", "auto") if auto else ("", "pending")
    waiting = {
        r["person_id"]: r
        for r in rows
        if (
            r["person_id"] in asked
            if asked is not None
            else decisions.get(r["person_id"], {}).get("identity", "") in states
        )
        and not decisions.get(r["person_id"], {}).get("merged_into")
        and decisions.get(r["person_id"], {}).get("role") != "excluded"
    }
    found: dict[str, dict[str, list[dict[str, Any]]]] = {pid: {} for pid in waiting}
    for slot in (s.id for s in project.config.slots):
        for kind in ("resolve", "hal_candidates", "scielo_candidates"):
            for run in read_runs(project.layout, slot, kind):
                latest: dict[str, list[dict[str, Any]]] = {}
                for rec in run.records():
                    pid = rec.get("person_id")
                    if pid not in waiting:
                        continue
                    latest.setdefault(pid, []).extend(_queue_entries(kind, rec))
                for pid, entries in latest.items():
                    found[pid][kind] = entries  # a later run replaces an earlier one
    out = []
    for pid in sorted(waiting):
        row = waiting[pid]
        cands = [c for kind in ("resolve", "hal_candidates", "scielo_candidates")
                 for c in found[pid].get(kind, [])]  # fmt: skip
        out.append(
            {
                "person_id": pid,
                "name": f"{row['first_name'] or ''} {row['last_name']}".strip(),
                "candidates": cands,
            }
        )
    return out


def _queue_entries(kind: str, rec: dict[str, Any]) -> list[dict[str, Any]]:
    if kind == "resolve":
        return [
            {
                "finder": "openalex",
                "record": c["record"],
                "name": c["name"],
                "score": c["score"],
                "evidence": c.get("evidence") or [],
                "evidence_codes": c.get("evidence_codes") or [],
                "detail": f"{c['works']} works, {c['first_year']}–{c['last_year']}",
                "detail_code": "openalex_record",
                "detail_params": {
                    "works": c["works"],
                    "first": c["first_year"] or "—",
                    "last": c["last_year"] or "—",
                },
            }
            for c in rec.get("candidates") or []
        ]
    if kind == "hal_candidates":
        return [
            {
                "finder": "hal",
                "record": f"hal:{rec['idhal']}" if rec.get("idhal") else None,
                "name": rec.get("full_name") or "",
                "score": None,
                "evidence": [],
                "detail": f"{len(rec.get('works') or [])} deposit(s); "
                + ", ".join((rec.get("structures") or [])[:3]),
                "detail_code": "hal_form",
                "detail_params": {
                    "n": len(rec.get("works") or []),
                    "structures": list((rec.get("structures") or [])[:3]),
                },
            }
        ]
    return [
        {
            "finder": "scielo",
            "record": f"orcid:{rec['orcid']}" if rec.get("orcid") else None,
            "name": rec.get("name") or "",
            "score": None,
            "evidence": [],
            "detail": f"{rec.get('title') or ''} ({rec.get('year')})",
        }
    ]
