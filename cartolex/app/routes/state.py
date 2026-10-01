# SPDX-License-Identifier: MIT
"""The project's state: the six-state validity of every stage, grouped by area."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import Request

from ..deps import ProjectDep
from ..messages import attempt_message, reason_message, skip_message
from ..routing import Routes, runtime_of

routes = Routes(tags=["state"])

#: The areas of the interface and the stages each sums up, in order.
AREAS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("corpus", "area.corpus", ("corpus.assemble",)),
    ("keywords", "area.keywords", ("keywords.extract", "keywords.triage", "keywords.build")),
    ("themes", "area.themes", ("themes.space", "themes.group", "themes.apply")),
    ("map", "area.map", ("map.layout", "map.trajectories", "overlays.position")),
    ("share", "area.share", ()),
)


def state_key(state: Any) -> str:
    """A state as a key: ``up to date`` → ``up_to_date``."""
    return str(state).strip().lower().replace(" ", "_").replace("-", "_")


def summary(states: Sequence[str]) -> str:
    """The state of an area from its stages' (the interface's rule)."""
    if not states:
        return "never_built"
    if "running" in states:
        return "running"
    if "failed" in states:
        return "failed"
    if "needs_update" in states:
        return "needs_update"
    if all(s == "skipped" for s in states):
        return "skipped"
    built = sum(s == "up_to_date" for s in states)
    never = sum(s == "never_built" for s in states)
    if never and not built:
        return "never_built"
    if never:
        return "needs_update"
    return "up_to_date"


def _run(record: Any) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "run_id": record.run_id,
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "finished_at": record.finished_at.isoformat() if record.finished_at else None,
        "seconds": record.measures.seconds,
        "warnings": list(record.warnings),
    }


def stage_states(runtime: Any, project: Any) -> list[dict[str, Any]]:
    """Every stage of the app's registry with its state and reasons (from the records only)."""
    from cartolex.build import status

    area_of = {s: a for a, _, stages in AREAS for s in stages}
    out = []
    for st in status(project, runtime.registry, year=runtime.settings.build_year).values():
        attempt = st.attempt
        out.append(
            {
                "id": st.stage,
                "name": st.name,
                "area": area_of.get(st.stage, "other"),
                "state": state_key(st.state.value),
                "label": st.state.value,
                "reasons": [
                    {
                        "kind": r.kind,
                        "subject": r.subject,
                        "detail": r.detail,
                        **reason_message(r.kind, r.subject, r.detail),
                    }
                    for r in st.reasons
                ],
                "skip_reason": st.skip_reason,
                "skip": skip_message(st.skip_reason) if st.skip_reason else None,
                "has_results": st.has_results,
                "run": _run(st.record),
                "attempt": None
                if attempt is None
                else {
                    "outcome": attempt.outcome,
                    "error": attempt.error,
                    "run_id": attempt.run_id,
                    **attempt_message(attempt.outcome, attempt.error),
                },
                "interrupted": st.interrupted is not None and st.running is None,
                "code_changed": st.code_changed,
                "describe": st.describe(),
            }
        )
    return out


@routes.get("/api/project/state", action="project.read")
def project_state(request: Request, ctx: ProjectDep) -> dict[str, Any]:
    """The six-state validity of every stage, by area, with the reasons.

    Derived from the run records alone (``cartolex.build.status``); an area's
    state sums up its stages' (running, failed, needs update, never built, up
    to date, skipped). Extensions add areas of their own.
    """
    from ..recipe import changed_counts

    runtime = runtime_of(request)
    stages = stage_states(runtime, ctx.project)
    by_id = {s["id"]: s for s in stages}
    areas = []
    for area_id, label, ids in AREAS:
        own = [by_id[s]["state"] for s in ids if s in by_id]
        entry: dict[str, Any] = {
            "id": area_id,
            "label": label,
            "stages": [s for s in ids if s in by_id],
        }
        if area_id == "share":
            builds = runtime.site_builder.builds(ctx.project)
            latest = next((b for b in builds if b.get("latest")), builds[0] if builds else None)
            entry["state"] = (
                "never_built"
                if latest is None
                else "needs_update"
                if latest.get("stale")
                else "up_to_date"
            )
            entry["items"] = builds[:1]
        else:
            entry["state"] = summary(own)
        areas.append(entry)
    for extra in runtime.extensions.status_areas:
        items = extra.probe(ctx.project) if extra.probe else []
        own = [by_id[s]["state"] for s in extra.stages if s in by_id]
        own += [state_key(i.get("state", "never_built")) for i in items]
        areas.append(
            {
                "id": extra.id,
                "label": extra.label,
                "stages": [s for s in extra.stages if s in by_id],
                "items": items,
                "state": summary(own),
            }
        )
    running = runtime.jobs.running(ctx.id)
    return {
        "project": {"id": ctx.id, "name": ctx.project.config.name},
        "areas": areas,
        "stages": stages,
        "job": running.as_dict() if running else None,
        "identity_frozen": ctx.project.config.identity.frozen,
        "changed_params": changed_counts(runtime, ctx),
    }
