# SPDX-License-Identifier: MIT
"""The project's state: the six-state validity of every stage, grouped by area."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import Request

from ..deps import ProjectDep
from ..messages import attempt_message, message, reason_message, skip_message
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


def _attempt(project: Any, attempt: Any) -> dict[str, Any]:
    """A failed or cancelled attempt: its outcome, time, error and message code."""
    said = attempt_message(attempt.outcome, attempt.error)
    if said["code"] == "stage_no_texts":
        from ..build_run import someone_mapped

        if not someone_mapped(project):
            said = message("stage_no_mapped")
    return {
        "outcome": attempt.outcome,
        "error": attempt.error,
        "run_id": attempt.run_id,
        "finished_at": attempt.finished_at.isoformat() if attempt.finished_at else None,
        **said,
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
                "attempt": None if attempt is None else _attempt(project, attempt),
                "interrupted": st.interrupted is not None and st.running is None,
                "code_changed": st.code_changed,
                "describe": st.describe(),
            }
        )
    triage = next((row for row in out if row["id"] == "keywords.triage"), None)
    if triage is not None:
        ai_row(runtime, project, triage)
    return out


def triage_status(runtime: Any, project: Any) -> dict[str, Any]:
    """The route of the keyword clean-up and the copilot's work since the current extraction
    (:func:`cartolex.app.ai_steps.copilot_status`), kept while ``keywords.csv``, the imported
    results and the extraction's run are the same."""
    from cartolex.build.records import read_record

    from ..ai_steps import copilot_status, routes_of

    layout = project.layout
    params, _ = project.read_params()
    record = read_record(layout, "keywords.extract")
    run = record.run_id if record is not None else None

    def stat(path: Any) -> Any:
        try:
            st = path.stat()
            return (st.st_size, st.st_mtime_ns)
        except OSError:
            return None

    key = (
        "copilot-status",
        str(layout.root),
        run,
        stat(layout.keywords_csv),
        stat(layout.history / "ai"),
    )
    status = runtime.table_cache.get(key, lambda: copilot_status(project, run))
    api = read_record(layout, "keywords.triage") is not None
    return {
        "route": routes_of(params)["keywords.triage"],
        "extraction": run,
        "api_verdicts": api,
        **status,
    }


def ai_row(runtime: Any, project: Any, row: dict[str, Any]) -> None:
    """The AI clean-up's row (``keywords.triage``, skipped unless it runs by API) as the
    person sees it: done with the copilot (its decisions since the extraction), waiting for
    it, or without AI; ``ai`` adds the route and the copilot's counts."""
    status = triage_status(runtime, project)
    row["ai"] = status
    if row["state"] != "skipped" or status["route"] == "api":
        return
    if status["decisions"] and status["last"]:
        row["state"], row["label"] = "up_to_date", "up to date"
        row["skip"] = message("stage_copilot_done", n=status["decisions"], date=status["last"])
    elif status["route"] == "copilot":
        row["skip"] = message("stage_copilot_waiting", total=status["total"])
    else:
        row["skip"] = message("stage_ai_none")


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
