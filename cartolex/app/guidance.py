# SPDX-License-Identifier: MIT
"""What guides a person from an empty project to a shared map: the facts the overview's
next step and its « Your first map » checklist read.

Everything here is read from what the app already keeps: the people's counts of the
corpus screen (:func:`cartolex.app.corpus_view.people_view`, computed once per version of
the tables and of ``people.csv``), the copilot's status
(:func:`cartolex.app.routes.state.triage_status`), the stages' states and the jobs. Nothing
here reads every text, so the overview stays fast for a project of 170,000 people.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

__all__ = [
    "STEPS",
    "checklist",
    "checklist_key",
    "ended_after",
    "people_facts",
]

#: The steps of « Your first map », in order.
STEPS = (
    "project",
    "people",
    "identities",
    "texts",
    "build",
    "review",
    "themes",
    "map",
    "share",
)


def checklist_key(project_id: str) -> str:
    """The key of the person's preferences (``/api/me/preferences``, ``other``) that hides
    the checklist of one project: a digest of its id, short and of the keys' alphabet."""
    import hashlib

    return "first_map." + hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:16]


def people_facts(runtime: Any, project: Any) -> dict[str, int]:
    """The people's counts that guide the next step: ``people``; ``mapped``; ``with_texts``
    and ``without_texts``, the mapped people with and without texts; ``identities``, the
    mapped people without texts whose identity waits for a check; ``to_harvest``, the mapped
    people without texts whose identity is settled and whose texts were never collected.
    People merged into another are left out. Kept while the people's view is the same."""
    from .corpus_view import people_view, stamp

    if not project.layout.table("people").exists() and not project.layout.people_csv.exists():
        return dict.fromkeys(
            ("people", "mapped", "with_texts", "without_texts", "identities", "to_harvest"), 0
        )

    def compute() -> dict[str, int]:
        view = people_view(project, runtime.table_cache)
        out = dict.fromkeys(
            ("people", "mapped", "with_texts", "without_texts", "identities", "to_harvest"), 0
        )
        for p in view["people"]:
            if p.get("merged_into"):
                continue
            out["people"] += 1
            if p["role"] != "mapped":
                continue
            out["mapped"] += 1
            if (p["coverage"] or {}).get("texts", 0) > 0:
                out["with_texts"] += 1
                continue
            out["without_texts"] += 1
            cause = (p.get("cause") or {}).get("code")
            if p["identity"] == "pending" and cause != "no_record":
                out["identities"] += 1
            elif p["identity"] != "pending" and cause == "not_collected":
                out["to_harvest"] += 1
        return out

    return runtime.table_cache.get(("people-facts", stamp(project)), compute)


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def ended_after(jobs: list[Any], at: Any) -> bool:
    """Whether a job started after the time *at* (an ISO time) and has ended since: a
    failure before it is no longer the last thing tried. Previews do not count."""
    since = _when(at)
    if since is None:
        return False
    for job in jobs:
        if job.kind == "preview" or not job.finished_at:
            continue
        started = _when(job.started_at or job.submitted_at)
        if started is not None and started > since:
            return True
    return False


def checklist(by_id: dict[str, dict], people: dict[str, int], facts: dict[str, Any]) -> list[dict]:
    """« Your first map »: each step of :data:`STEPS` with its state (``done`` or ``todo``)
    and the counts its words name. A step a later milestone made moot counts as done (the
    identities still pending, the texts never collected, once the texts are gathered)."""

    def built(stage: str) -> bool:
        return bool((by_id.get(stage) or {}).get("has_results"))

    gathered = built("corpus.assemble")
    done = {
        "project": True,
        "people": people["people"] > 0,
        "identities": people["people"] > 0 and (people["identities"] == 0 or gathered),
        "texts": gathered or (people["with_texts"] > 0 and people["to_harvest"] == 0),
        "build": built("keywords.build"),
        "review": built("keywords.extract") and facts["reviewed"] > 0,
        "themes": facts["themes_saved"],
        "map": built("map.layout"),
        "share": facts["shared"],
    }
    counts = {
        "people": {"n": people["people"]},
        "identities": {"n": people["identities"]},
        "texts": {"n": people["to_harvest"]},
    }
    return [{"id": s, "state": "done" if done[s] else "todo", **counts.get(s, {})} for s in STEPS]
