# SPDX-License-Identifier: MIT
"""What leaves the computer: the summary shown before every collection, and the job record.

:func:`plan_collection` says, before anything is sent, which hosts a planned
resolution or harvest will contact, why, which kinds of data each receives
(names, identifiers, DOIs, institution names; never texts or decisions), how
many requests that takes about, and what it may cost where a service charges.
:func:`record_job` writes what a job did to ``logs/jobs/<job id>.jsonl``: the
hosts it contacted and the kinds of data sent, counts and times; never a
name, an identifier or a text. See ``docs/privacy.md``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from cartolex.project import Project
from cartolex.project.files import atomic_write_bytes
from cartolex.project.identity import merge_roots, merged_groups
from cartolex.project.tables import read_source_table

from .decisions import read_people
from .names import variants
from .openalex import author_batches
from .services import CollectSettings
from .tables import new_run_id

__all__ = [
    "NEVER_SENT",
    "OPENALEX_PRICES",
    "STORED",
    "CollectionPlan",
    "PlannedHost",
    "plan_collection",
    "record_job",
]

#: OpenAlex's published prices per request, in US dollars (checked 2026-09-28): a lookup
#: by id is free, a list or filter call costs $0.10 per 1,000, a search $1 per 1,000.
OPENALEX_PRICES = {"singleton": 0.0, "list": 0.0001, "search": 0.001}
#: The daily budgets OpenAlex gives, in US dollars (checked 2026-09-28).
OPENALEX_BUDGETS = {"without a key": 0.10, "with a free key": 1.0}

NEVER_SENT = (
    "texts, titles and abstracts you imported or collected",
    "your decisions (roles, identities, keywords, themes)",
    "the list you imported beyond the names, identifiers and institution names searched",
    "e-mail addresses (they are never stored)",
)
STORED = (
    "answers of the services: cache/http/ (delete it to remove them; collection fetches again)",
    "records as received: sources/<slot>/raw/ (the tables are rebuilt from them)",
    "the tables: sources/tables/; your decisions: decisions/",
    "what each job sent, as hosts and kinds of data only: logs/jobs/",
)


@dataclass(frozen=True)
class PlannedHost:
    """One host a planned collection will contact."""

    service: str
    label: str
    host: str
    purpose: str
    sends: tuple[str, ...]
    requests: int
    cost_usd: float | None
    policy: str


@dataclass
class CollectionPlan:
    """What a planned collection sends, where, and about how many requests."""

    action: str
    people: int
    hosts: list[PlannedHost] = field(default_factory=list)
    never_sent: tuple[str, ...] = NEVER_SENT
    stored: tuple[str, ...] = STORED
    notes: list[str] = field(default_factory=list)
    #: The notes as codes and parameters for an interface's catalogues, in the order
    #: of :attr:`notes` (``{"code", "params", "message"}``, ``message`` the English).
    coded_notes: list[dict[str, Any]] = field(default_factory=list)

    def note(self, code: str, message: str, **params: Any) -> None:
        """Add a note, in words and as a code with its parameters."""
        self.notes.append(message)
        self.coded_notes.append({"code": code, "params": params, "message": message})

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    def lines(self) -> list[str]:
        """The summary in words, as shown before a collection."""
        out = [f"{self.action} for {self.people} person(s). What leaves this computer:"]
        if not self.hosts:
            out.append("  nothing: no request is needed")
        for h in self.hosts:
            cost = ""
            if h.cost_usd is not None:
                cost = f", about ${h.cost_usd:.3f} at the service's published prices"
            out.append(
                f"  {h.host} ({h.label}), to {h.purpose}: {', '.join(h.sends)}; "
                f"about {h.requests} request(s){cost}"
            )
        out.append("What never leaves it: " + "; ".join(self.never_sent) + ".")
        out.append("What is kept, and where: " + "; ".join(self.stored) + ".")
        return out + self.notes


def _host(url: str) -> str:
    return urlsplit(url).netloc


def _targets(project: Project, action: str, people: Sequence[str] | None) -> list[dict]:
    layout = project.layout
    if not layout.table("people").exists():
        return []
    rows = read_source_table(layout.table("people"), "people").to_pylist()
    decisions = read_people(layout)
    roots = merge_roots(decisions)
    groups = merged_groups(roots)
    wanted = set(people) if people is not None else None
    out = []
    for row in rows:
        dec = decisions.get(row["person_id"], {})
        if wanted is not None and row["person_id"] not in wanted:
            continue
        if row["person_id"] in roots or dec.get("role") == "excluded":
            continue
        identity = dec.get("identity", "")
        # a person's records: theirs and those of the rows merged into them, when accepted
        records = [
            r
            for one in (row["person_id"], *groups.get(row["person_id"], ()))
            if (decisions.get(one) or {}).get("identity") in ("confirmed", "auto")
            for r in ((decisions.get(one) or {}).get("records") or "").split(";")
            if r
        ]
        if action == "resolve" and (identity in ("", "pending") or wanted is not None):
            out.append({**row, "_records": []})
        elif action == "harvest" and records:
            out.append({**row, "_records": list(dict.fromkeys(records))})
    return out


#: What each action sends to each service, in words.
SENDS = {
    ("resolve", "openalex"): ("names", "identifiers", "institution names"),
    ("resolve", "orcid"): ("ORCID iDs",),
    ("harvest", "openalex"): ("author identifiers", "DOIs"),
    ("harvest", "orcid"): ("ORCID iDs",),
    ("institutions", "openalex"): ("institution identifiers",),
    ("institution search", "openalex"): ("institution names",),
    ("collaborators", "openalex"): ("author identifiers",),
}
#: Why each action contacts a service.
PURPOSES = {
    "institutions": "find an institution, the units below it and the works signed there",
    "institution search": "find the institutions that bear a name, for you to choose",
    "collaborators": "read the works of the seeds and of each round's collaborators",
}
ACTIONS = ("resolve", "harvest", "institutions", "collaborators", "coverage")


def plan_collection(
    project: Project,
    action: str,
    settings: CollectSettings,
    *,
    people: Sequence[str] | None = None,
    institutions: Sequence[str] = (),
    search: str | None = None,
    rounds: int = 1,
    seeds: int | None = None,
    cap: int | None = None,
    snapshot: str | None = None,
) -> CollectionPlan:
    """The summary of what a planned *action* sends, before anything is sent.

    *action* is ``resolve``, ``harvest``, ``institutions`` (the *institutions*
    named, or a *search* by name), ``collaborators`` (*rounds* rounds from
    *seeds* seeds, up to *cap* people) or ``coverage`` (a retry of the people
    whose collection failed, or of *people*). With *snapshot* (its folder's
    name), OpenAlex is read on this computer: only the registry is asked.

    Request counts are estimates: a resolution makes one search per name
    variant and per stated institution, a harvest at least one list per 50
    OpenAlex records of consecutive people and one per registry record (more
    for people with many works), an institution one list
    of its units and at least one of its works, a round of collaborators a
    list per 50 records. Answers already in the cache are not sent again, so
    the real count can be lower.
    """
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}; expected one of {ACTIONS}")
    counts: dict[str, dict[str, int]] = {"openalex": {}, "orcid": {}}
    purposes: dict[str, str] = {}
    sends: dict[str, tuple[str, ...]] = {}

    def add(service: str, kind: str, n: int = 1) -> None:
        counts[service][kind] = counts[service].get(kind, 0) + n

    notes: list[tuple[str, str, dict[str, Any]]] = []  # (code, words, params)
    n_people = 0
    if action == "coverage":
        from .coverage import person_coverage

        failed = [p for p in person_coverage(project, people=people) if p.state == "failed"]
        by_finder: dict[str, list[str]] = {}
        for p in failed:
            by_finder.setdefault((p.failure or {}).get("finder", ""), []).append(p.person_id)
        n_people = len(failed)
        for finder, pids in sorted(by_finder.items()):
            if finder in ("resolve", "harvest"):
                # A snapshot answers a harvest; identities are still searched by name, online.
                sub = plan_collection(
                    project,
                    finder,
                    settings,
                    people=pids,
                    snapshot=snapshot if finder == "harvest" else None,
                )
                for h in sub.hosts:
                    counts[h.service][f"{finder}"] = counts[h.service].get(finder, 0) + h.requests
                    sends[h.service] = tuple(dict.fromkeys(sends.get(h.service, ()) + h.sends))
            else:
                notes.append((
                    "note_retry_own",
                    f"{len(pids)} failure(s) of {finder} are retried by its own command",
                    {"n": len(pids), "finder": finder},
                ))  # fmt: skip
        if not failed:
            notes.append(("note_nothing_to_retry", "nobody's collection failed: nothing to retry",
                          {}))  # fmt: skip
    elif action == "institutions":
        n_people = 0
        if search:
            add("openalex", "search")
            purposes["openalex"] = PURPOSES["institution search"]
            sends["openalex"] = SENDS[("institution search", "openalex")]
        else:
            n = max(1, len(institutions))
            add("openalex", "singleton", 2 * n)  # the institutions, and the other parents of units
            add("openalex", "list", 2)  # the units below them, and the works signed there
            purposes["openalex"] = PURPOSES["institutions"]
            sends["openalex"] = SENDS[("institutions", "openalex")]
            notes.append(
                (
                    "note_large_institution",
                    "the works signed at a large institution take one request per 100 works",
                    {},
                )
            )
    elif action == "collaborators":
        if seeds is None:
            from .snowball import _seed_people

            seeds = len(_seed_people(project, None))
        n_people = seeds
        per_round = math.ceil(max(1, n_people) / 50) + math.ceil(max(1, cap or 200) / 50)
        add("openalex", "list", per_round * max(1, rounds))
        purposes["openalex"] = PURPOSES["collaborators"]
        sends["openalex"] = SENDS[("collaborators", "openalex")]
    else:
        targets = _targets(project, action, people)
        n_people = len(targets)
        for person in targets:
            ids = dict(person.get("ids") or [])
            if action == "resolve":
                add("openalex", "search", len(variants(person["last_name"], person["first_name"])))
                add("openalex", "search", 2)  # a stated institution and the restricted search
                add("openalex", "singleton", len(ids.get("openalex", [])))
                if person.get("orcid"):
                    add("openalex", "list")
                    add("orcid", "works")
                add("orcid", "works")  # the registry of a likely record's ORCID
                add("openalex", "list")  # that record's DOIs, to compare
            else:
                records = person["_records"]
                n_oa = sum(1 for r in records if r.startswith("openalex:"))
                n_orcid = sum(1 for r in records if r.startswith("orcid:"))
                add("openalex", "singleton", n_oa)
                if n_orcid:
                    add("orcid", "works", n_orcid)
                    add("orcid", "record", n_orcid)
                    add("openalex", "list", n_orcid)
        if action == "harvest":  # the works of several people's records in one list
            groups = [[r for r in p["_records"] if r.startswith("openalex:")] for p in targets]
            batches = author_batches(groups)
            add("openalex", "list", sum(1 for b in batches if any(groups[i] for i in b)))
        for name in ("openalex", "orcid"):
            sends.setdefault(name, SENDS[(action, name)])
    plan = CollectionPlan(action=action, people=n_people)
    if snapshot is not None and action == "coverage":
        # The harvests' part was planned on the snapshot above, the identities' online.
        if "harvest" in by_finder and "resolve" in by_finder:
            notes.append((
                "note_snapshot_harvests",
                f"the harvests are read from the snapshot {snapshot} on this computer; "
                "identities are still searched on OpenAlex",
                {"snapshot": str(snapshot)},
            ))  # fmt: skip
        elif "harvest" in by_finder:
            notes.append((
                "note_snapshot",
                f"OpenAlex is read from the snapshot {snapshot} on this computer: nothing is sent to it",
                {"snapshot": str(snapshot)},
            ))  # fmt: skip
    elif snapshot is not None and counts["openalex"]:
        counts["openalex"] = {}
        notes.append((
            "note_snapshot",
            f"OpenAlex is read from the snapshot {snapshot} on this computer: nothing is sent to it",
            {"snapshot": str(snapshot)},
        ))  # fmt: skip
    for name in ("openalex", "orcid"):
        n = sum(counts[name].values())
        if not n:
            continue
        svc = settings.service(name)
        extra = []
        if settings.contact:
            extra.append("your contact address")
        if settings.api_key(name):
            extra.append("your API key")
        cost = None
        if name == "openalex":
            cost = sum(OPENALEX_PRICES.get(k, OPENALEX_PRICES["list"]) * v
                       for k, v in counts[name].items())  # fmt: skip
        plan.hosts.append(
            PlannedHost(
                service=name,
                label=svc.label,
                host=_host(svc.base_url),
                purpose=purposes.get(name, svc.purpose),
                sends=tuple(dict.fromkeys(sends.get(name, ()) + tuple(extra))),
                requests=n,
                cost_usd=cost,
                policy=svc.policy,
            )
        )
    openalex = next((h for h in plan.hosts if h.service == "openalex"), None)
    if openalex is not None and openalex.cost_usd:
        budget = OPENALEX_BUDGETS[
            "with a free key" if settings.api_key("openalex") else "without a key"
        ]
        days = max(1, math.ceil(openalex.cost_usd / budget))
        if days > 1:
            keyed = bool(settings.api_key("openalex"))
            plan.note(
                "note_openalex_budget_key" if keyed else "note_openalex_budget",
                f"OpenAlex gives a daily budget of ${budget:.2f} "
                + ("with your key" if keyed else "without a key")
                + f": this collection needs about {days} days of it"
                + ("" if keyed else ", or a free API key")
                + "; from a national size up, read the snapshot instead (collect snapshot)",
                usd=budget,
                days=days,
            )
    for code, words, params in notes:
        plan.note(code, words, **params)
    return plan


def record_job(
    project: Project,
    kind: str,
    *,
    started: datetime,
    outcome: str,
    counts: Mapping[str, Any],
    egress: Sequence[Mapping[str, Any]],
    finished: datetime | None = None,
) -> str:
    """Write ``logs/jobs/<job id>.jsonl`` for a collection job; returns the job id.

    It holds the job's kind, times, outcome and counts, and one line per host
    contacted with the kinds of data sent: never a name, an identifier or a text.
    """
    finished = finished or datetime.now(timezone.utc)
    job_id = f"collect-{kind}-{new_run_id(started)}"
    events = [
        {"event": "start", "job": job_id, "kind": f"collect.{kind}", "at": started.isoformat()},
        *(
            {
                "event": "egress",
                "service": e["service"],
                "host": e["host"],
                "requests": e["requests"],
                "sends": list(e["sends"]),
            }
            for e in egress
        ),
        {
            "event": "end",
            "outcome": outcome,
            "at": finished.isoformat(),
            "seconds": round((finished - started).total_seconds(), 3),
            "counts": {str(k): v for k, v in counts.items() if isinstance(v, int | float)},
        },
    ]
    text = "".join(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n" for e in events)
    atomic_write_bytes(project.layout.jobs / f"{job_id}.jsonl", text.encode("utf-8"))
    return job_id
