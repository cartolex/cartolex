# SPDX-License-Identifier: MIT
"""The overview: the one next step, the health of the project, a small preview of the map.

``GET /api/overview`` adds to the project's state (``GET /api/project/state``)
what the overview page shows besides the stages: the single most useful next
action, the health panel (things that may spoil the map without failing a
build), a sample of the map's people for a preview, and the recent site builds.
Every item carries a message code, its params and the English text
(:mod:`cartolex.app.messages`), and names its next action; a build action may
carry a ``scope``, the areas a build of that step covers (a stale map offers
the build of the map, which restores it).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from ..deps import ProjectDep
from ..messages import MESSAGES, message
from ..routing import Routes, runtime_of
from .state import AREAS, stage_states, summary

routes = Routes(tags=["overview"])

log = logging.getLogger("cartolex.app")

#: At most this many people in the map's preview (an even sample of them).
PREVIEW_POINTS = 1500
#: The recent site builds listed.
RECENT_SHARES = 3


def item(code: str, *, level: str = "info", scope: list[str] | None = None, **params: Any) -> dict:
    """A health item or a next step: its message, its level and its next action."""
    kind = MESSAGES[code]
    out = {
        **message(code, **params),
        "level": level,
        "next": {"label": kind.next_label, "action": kind.next_action}
        if kind.next_action
        else None,
    }
    if scope:
        out["scope"] = scope
    return out


# ── health ───────────────────────────────────────────────────────────────────


def _missing_models(config: Any, by_id: dict[str, dict]) -> list[dict]:
    """Corpus languages whose model the next extraction needs and does not find."""
    extract = by_id.get("keywords.extract")
    if extract is None or extract["state"] in ("up_to_date", "skipped"):
        return []
    from cartolex.lexicon import language_models as lm

    out = []
    for lang in config.languages.corpus:
        try:
            if not lm.installed(lang):
                out.append(
                    item(
                        "health_model_missing",
                        level="warning",
                        language=lang,
                        model=lm.spec(lang).identity,
                    )
                )
        except lm.LanguageModelMissing:
            out.append(item("health_model_missing", level="warning", language=lang, model="—"))
    return out


def _languages_split(config: Any, by_id: dict[str, dict], ai_given: bool) -> list[dict]:
    """Several corpus languages and no AI clean-up: themes may split by language."""
    langs = list(config.languages.corpus)
    triage = by_id.get("keywords.triage")
    if len(langs) < 2 or triage is None:
        return []
    if triage["state"] == "up_to_date" or (ai_given and triage["state"] != "skipped"):
        return []
    return [item("health_languages_split", languages=langs)]


def _snowball_cut(ctx: Any) -> list[dict]:
    """The last proposal of collaborators stopped at the cap (``collect.snowball.cap``)."""
    from cartolex.collect.tables import read_runs

    out = []
    for slot in ctx.project.config.slots:
        try:
            runs = read_runs(ctx.layout, slot.id, "snowball")
        except (OSError, ValueError):
            continue
        if runs and runs[-1].header.get("cut"):
            out.append(item("health_snowball_cap", slot=slot.id, cap=runs[-1].header.get("cap")))
    return out


def _too_large(the_plan: Any) -> list[dict]:
    """Stages whose estimated peak memory exceeds what this machine has."""
    if the_plan is None:
        return []
    return [
        item(
            "health_too_large",
            level="warning",
            stage=i.stage,
            need_mb=round(i.estimate.peak_memory_mb),
            budget_mb=round(the_plan.budget_mb or 0),
        )
        for i in the_plan.items
        if i.over_budget and i.estimate and i.estimate.peak_memory_mb
    ]


def _map_stale(by_id: dict[str, dict]) -> list[dict]:
    """A map drawn before, whose inputs changed since: the map build restores it."""
    layout = by_id.get("map.layout")
    if layout is None or not layout["has_results"] or layout["state"] != "needs_update":
        return []
    return [item("health_map_stale", level="warning", scope=["map"])]


# ── the one next step ────────────────────────────────────────────────────────


def _people_count(ctx: Any) -> int:
    from cartolex.project.tables import read_decision_csv

    try:
        return len(read_decision_csv(ctx.layout.people_csv, "people"))
    except (OSError, ValueError):
        return 0


def _waiting_step(runtime: Any, ctx: Any) -> str | None:
    """The AI step the last build paused at, when its route is still a copilot."""
    from ..ai_steps import routes_of
    from .build import last_build

    last = last_build(runtime, ctx)
    pause = (
        (last.result or {}).get("waiting") if last is not None and last.state == "waiting" else None
    )
    if not pause:
        return None
    params, _ = ctx.project.read_params()
    step = pause.get("step")
    return step if routes_of(params).get(step) == "copilot" else None


def next_step(
    ctx: Any, stages: list[dict], health: list[dict], running: Any, waiting: str | None = None
) -> dict:
    """The single most useful action now, in this order: watch a running build, the copilot
    a paused build waits for, add people, install a missing model, look at a failure, a first
    build, restore a stale map, bring the rest up to date, curate the themes, look at the
    map."""
    states = [s["state"] for s in stages]
    if running is not None:
        return item("next_watch_build")
    if waiting is not None:
        return item("next_copilot_waiting", step=waiting)
    if not any(s == "up_to_date" for s in states) and _people_count(ctx) == 0:
        return item("next_import_people")
    if any(h["code"] == "health_model_missing" for h in health):
        return item("next_install_model")
    failed = next((s for s in stages if s["state"] == "failed"), None)
    if failed is not None:
        return item("next_see_failure", stage=failed["id"], scope=[failed["id"]])
    if not any(s in ("up_to_date", "needs_update") for s in states):
        return item("next_first_build")
    if any(h["code"] == "health_map_stale" for h in health):
        return item("next_restore_map", scope=["map"])
    if any(s in ("needs_update", "never_built") for s in states):
        return item("next_update")
    if not ctx.layout.themes_json.is_file():
        return item("next_curate_themes")
    return item("next_open_map")


# ── the map's preview ────────────────────────────────────────────────────────


def preview(runtime: Any, ctx: Any) -> dict[str, Any] | None:
    """An even sample of the map's people, each with the index of its top-level theme."""
    from .atlas import build_bundle, lineage

    runs = lineage(ctx)
    if runs.get("map.layout") is None:
        return None
    key = ("atlas", "cartolex-atlas/2", ctx.id, tuple(sorted(runs.items())))
    bundle = runtime.atlas_cache.get(key, lambda: build_bundle(ctx, runs))
    tops = sorted(
        (n for n in bundle["nodes"] if n["level"] == 1), key=lambda n: (n["order"], n["id"])
    )
    hue = {n["id"]: i for i, n in enumerate(tops)}
    people = [p for p in bundle["people"] if p["x"] is not None and p["y"] is not None]
    step = max(1, -(-len(people) // PREVIEW_POINTS))
    points = []
    for p in people[::step]:
        first = p["shares"][0] if p["shares"] else {}
        top = max(first, key=first.get) if first else None
        points.append([p["x"], p["y"], hue.get(top, -1)])
    return {
        "people": len(people),
        "points": points,
        "themes": [{"id": n["id"], "names": n["names"]} for n in tops],
        "bounds": bundle["bounds"],
    }


@routes.get("/api/overview", action="project.read")
def overview(request: Request, ctx: ProjectDep) -> dict[str, Any]:
    """The next step, the health panel, the map's preview and the recent site builds."""
    from cartolex.build import plan
    from cartolex.build.params import ParamsError
    from cartolex.build.planning import BuildBusy

    runtime = runtime_of(request)
    config = ctx.project.config
    stages = stage_states(runtime, ctx.project)
    by_id = {s["id"]: s for s in stages}
    running = runtime.jobs.running(ctx.id)
    try:
        the_plan = plan(
            ctx.project,
            None,
            registry=runtime.registry,
            budget_mb=runtime.settings.build_budget_mb,
            year=runtime.settings.build_year,
        )
    except (BuildBusy, ParamsError):
        the_plan = None
    ai = runtime.settings.ai_access
    ai_given = config.identity.ai is not None and bool(
        ai is not None and (ai.api_key or ai.client_factory)
    )
    health = [
        *_map_stale(by_id),
        *_missing_models(config, by_id),
        *_too_large(the_plan),
        *_languages_split(config, by_id, ai_given),
        *_snowball_cut(ctx),
    ]
    try:
        atlas = preview(runtime, ctx)
    except Exception:  # the preview is a convenience: the overview shows without it
        log.warning("the map's preview could not be read", extra={"event": "overview_preview"})
        atlas = None
    shares = runtime.site_builder.builds(ctx.project)[:RECENT_SHARES]
    areas = {a: [by_id[s]["state"] for s in ids if s in by_id] for a, _, ids in AREAS}
    return {
        "project": {
            "id": ctx.id,
            "name": config.name,
            "state": summary([s for own in areas.values() for s in own]),
        },
        "next": next_step(
            ctx, stages, health, running, None if running else _waiting_step(runtime, ctx)
        ),
        "health": health,
        "preview": atlas,
        "shares": {"items": shares, "available": runtime.site_builder.available},
    }
