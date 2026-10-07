# SPDX-License-Identifier: MIT
"""The AI steps of a build, and the route chosen for each: none, a copilot, or the API.

Two steps can take AI help: the keyword clean-up (after the extraction, before
the vocabulary) and the theme curation (after the grouping, before the themes
are applied). ``decisions/params.json`` keeps the route chosen for each
(``ai``, :data:`cartolex.project.models.AI_ROUTES`); the clean-up by API is
the opt-in stage ``keywords.triage``, whose ``enabled`` switch stays the one
the build reads. The theme curation has no API route.

With the copilot route, a build **pauses**: it runs up to the step and stops
before the stage that reads its result, until the result is imported and
accepted (or the person continues without it). A build pauses at a step when
the stage after it would run and either the stage before it runs in the same
build (new candidates, a new proposal) or no copilot result was accepted
since that stage's current run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "AI_SOURCES",
    "STEPS",
    "AiStep",
    "copilot_done",
    "copilot_status",
    "pause_of",
    "route_copilot_triage",
    "routes_of",
    "step_of",
    "with_routes",
]

#: The sources of ``keywords.csv`` that are an AI's answers the person accepted.
AI_SOURCES = ("ai-handoff", "ai-copilot")


@dataclass(frozen=True)
class AiStep:
    """An AI step: the stage it follows, the stage that reads its result, where the copilot
    is (the page that exports its bundle and imports its result)."""

    id: str
    after: str
    resumes: str
    page: str


STEPS = (
    AiStep("keywords.triage", "keywords.extract", "keywords.build", "/keywords?copilot=1"),
    AiStep("themes.curation", "themes.group", "themes.apply", "/themes?copilot=1"),
)
_BY_ID = {s.id: s for s in STEPS}


def routes_of(params: Any) -> dict[str, str]:
    """The route of each AI step in *params* (a ``ParamsFile``).

    The clean-up runs by API when ``keywords.triage.enabled`` is true, whatever
    ``ai`` says; otherwise ``ai`` tells a copilot from nothing.
    """
    chosen = dict(params.ai or {})
    enabled = (params.stages.get("keywords.triage") or {}).get("enabled") is True
    copilot = chosen.get("keywords.triage") == "copilot"
    return {
        "keywords.triage": "api" if enabled else "copilot" if copilot else "none",
        "themes.curation": chosen.get("themes.curation", "none"),
    }


def with_routes(params: Any, routes: dict[str, str]) -> Any:
    """*params* with these routes: ``ai`` records them, and the clean-up's ``enabled``
    switch follows the keyword route (on for the API, off otherwise)."""
    ai = {**(params.ai or {}), **routes}
    stages = dict(params.stages)
    if "keywords.triage" in routes:
        own = {k: v for k, v in (stages.get("keywords.triage") or {}).items() if k != "enabled"}
        if routes["keywords.triage"] == "api":
            own["enabled"] = True
        if own:
            stages["keywords.triage"] = own
        else:
            stages.pop("keywords.triage", None)
    return params.model_validate(
        {**params.model_dump(mode="json", by_alias=True), "ai": ai, "stages": stages}
    )


def route_copilot_triage(project: Any) -> bool:
    """After a copilot's keyword triage is accepted: set the clean-up's route to the copilot
    when it was « No AI » (never over the API), as a params change kept in the history
    like any other; whether the route changed."""
    params, fp = project.read_params()
    if routes_of(params)["keywords.triage"] != "none":
        return False
    project.save_params(
        with_routes(params, {"keywords.triage": "copilot"}),
        expected=fp,
        action="AI route keywords.triage=copilot (a copilot's triage accepted)",
    )
    return True


def _run_time(run_id: str) -> datetime | None:
    try:
        return datetime.strptime(run_id[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _stamp(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


def _keyword_rows(project: Any) -> list[dict[str, str]]:
    from cartolex.project.tables import read_decision_csv

    try:
        return read_decision_csv(project.layout.keywords_csv, "keywords")
    except (OSError, ValueError):
        return []


def _imported_triage(project: Any) -> list[str]:
    """The copilot's triage results imported into ``decisions/history/ai/``, newest first."""
    import re

    folder = project.layout.history / "ai"
    if not folder.is_dir():
        return []
    pattern = re.compile(r"^\d{8}T\d{6}Z-copilot-triage(-\d+)?$")
    names = {p.name.split(".", 1)[0] for p in folder.iterdir() if p.suffix == ".json"}
    return sorted((n for n in names if pattern.match(n)), reverse=True)


def copilot_status(project: Any, after_run: str | None = None) -> dict[str, Any]:
    """What the copilot did for the keyword clean-up, from ``keywords.csv`` and the results
    imported: ``decisions``, the copilot's decisions accepted since the run *after_run* of
    the extraction (all of them without a run), and ``last``, the latest one's time;
    ``total``, every accepted copilot decision; ``ai``, every accepted AI answer (a copilot's
    or an earlier handoff's); ``reviewed``, the decisions of any source since the run;
    ``pending``, the imported results newer than the last accepted
    decision (imported, not accepted yet), newest first."""
    rows = _keyword_rows(project)
    since = _run_time(after_run) if after_run else None
    floor = _stamp(since) if since is not None else ""
    own = [r for r in rows if r.get("source") == "ai-copilot"]
    recent = [r.get("decided_at", "") for r in own if r.get("decided_at", "") >= floor]
    latest = max((r.get("decided_at", "") for r in own), default="")
    pending = [
        i
        for i in _imported_triage(project)
        if (at := _run_time(i)) is not None and _stamp(at) > latest
    ]
    return {
        "decisions": len(recent),
        "last": max(recent) if recent else None,
        "total": len(own),
        "ai": sum(r.get("source") in AI_SOURCES for r in rows),
        "reviewed": sum(r.get("decided_at", "") >= floor for r in rows) if floor else len(rows),
        "pending": pending,
    }


def copilot_done(project: Any, step: str, after_run: str) -> bool:
    """Whether a copilot's result was accepted for *step* since the run *after_run* of the
    stage before it: a keyword decision of the copilot, or a theme tree version it made."""
    since = _run_time(after_run)
    if since is None:
        return False
    if step == "keywords.triage":
        return copilot_status(project, after_run)["decisions"] > 0
    from cartolex.project.themes_versions import list_versions

    return any(
        (v.made_by or "").startswith("ai-copilot") and v.made_at is not None and v.made_at >= since
        for v in list_versions(project)
    )


def pause_of(
    project: Any, registry: Any, the_plan: Any, routes: dict[str, str], passed: set[str]
) -> dict[str, Any] | None:
    """Where a build of *the_plan* pauses for a copilot, or ``None``: the step, the stage it
    follows and the one it holds, the page of its copilot, and the stages held back (the one
    that reads its result and every stage after it that the plan runs). The steps in
    *passed* do not pause (« continue the build »)."""
    from cartolex.build.records import read_record

    to_run = list(the_plan.to_run)
    for step in STEPS:
        if routes.get(step.id) != "copilot" or step.id in passed:
            continue
        if step.resumes not in registry or step.resumes not in to_run:
            continue
        if step.after not in to_run:
            record = read_record(project.layout, step.after)
            if record is None or copilot_done(project, step.id, record.run_id):
                continue
        held = {step.resumes, *registry.downstream_of(step.resumes)}
        return {
            "step": step.id,
            "after": step.after,
            "resumes": step.resumes,
            "page": step.page,
            "held": [s for s in to_run if s in held],
        }
    return None


def step_of(step_id: str) -> AiStep:
    """The AI step called *step_id*."""
    return _BY_ID[step_id]
