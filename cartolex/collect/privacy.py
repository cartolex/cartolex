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
from typing import Any, Literal
from urllib.parse import urlsplit

from cartolex.project import Project
from cartolex.project.files import atomic_write_bytes
from cartolex.project.tables import read_source_table

from .decisions import read_people
from .names import variants
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
    wanted = set(people) if people is not None else None
    out = []
    for row in rows:
        dec = decisions.get(row["person_id"], {})
        if wanted is not None and row["person_id"] not in wanted:
            continue
        if dec.get("merged_into") or dec.get("role") == "excluded":
            continue
        identity = dec.get("identity", "")
        if action == "resolve" and (identity in ("", "pending") or wanted is not None):
            out.append({**row, "_records": []})
        elif action == "harvest" and identity in ("confirmed", "auto") and dec.get("records"):
            out.append({**row, "_records": dec["records"].split(";")})
    return out


def plan_collection(
    project: Project,
    action: Literal["resolve", "harvest"],
    settings: CollectSettings,
    *,
    people: Sequence[str] | None = None,
) -> CollectionPlan:
    """The summary of what a planned *action* sends, before anything is sent.

    Request counts are estimates: a resolution makes one search per name
    variant and per stated institution, a harvest at least one list per person
    and record kind (more for people with many works). Answers already in the
    cache are not sent again, so the real count can be lower.
    """
    targets = _targets(project, action, people)
    plan = CollectionPlan(action=action, people=len(targets))
    counts: dict[str, dict[str, int]] = {"openalex": {}, "orcid": {}}

    def add(service: str, kind: str, n: int = 1) -> None:
        counts[service][kind] = counts[service].get(kind, 0) + n

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
            if n_oa:
                add("openalex", "list")
            if n_orcid:
                add("orcid", "works", n_orcid)
                add("orcid", "record", n_orcid)
                add("openalex", "list", n_orcid)
    sends = {
        ("resolve", "openalex"): ("names", "identifiers", "institution names"),
        ("resolve", "orcid"): ("ORCID iDs",),
        ("harvest", "openalex"): ("author identifiers", "DOIs"),
        ("harvest", "orcid"): ("ORCID iDs",),
    }
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
            cost = sum(OPENALEX_PRICES[k] * v for k, v in counts[name].items())
        plan.hosts.append(
            PlannedHost(
                service=name,
                label=svc.label,
                host=_host(svc.base_url),
                purpose=svc.purpose,
                sends=tuple(sends[(action, name)]) + tuple(extra),
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
            plan.notes.append(
                f"OpenAlex gives a daily budget of ${budget:.2f} "
                + ("with your key" if settings.api_key("openalex") else "without a key")
                + f": this collection needs about {days} days of it"
                + ("" if settings.api_key("openalex") else ", or a free API key")
            )
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
