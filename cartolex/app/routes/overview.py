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


def review_item(code: str, proposal: str) -> dict:
    """An item whose action opens the review of an imported copilot result."""
    out = item(code, proposal=proposal)
    out["next"] = {**out["next"], "action": f"open:/keywords?copilot=1&proposal={proposal}"}
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


def _languages_split(config: Any, by_id: dict[str, dict], triage: dict[str, Any]) -> list[dict]:
    """Several corpus languages and no AI clean-up yet (no verdict by API, no AI answer
    accepted): themes may split by language. The keywords page's test, once the candidates
    are found."""
    langs = list(config.languages.corpus)
    extract = by_id.get("keywords.extract")
    if len(langs) < 2 or extract is None or not extract["has_results"]:
        return []
    if triage["api_verdicts"] or triage["ai"]:
        return []
    return [item("health_languages_split", level="warning", languages=langs)]


def _guidance_health(people: dict[str, int], by_id: dict[str, dict], triage: dict) -> list[dict]:
    """Notes once the texts are gathered: people whose identity waits for a check or whose
    texts were never collected; a copilot's result imported and not accepted yet."""
    out = []
    pending = triage.get("pending") or []
    if pending:
        out.append(review_item("health_copilot_pending", pending[0]))
    if (by_id.get("corpus.assemble") or {}).get("has_results"):
        if people["identities"]:
            out.append(item("health_identities_pending", n=people["identities"]))
        if people["to_harvest"]:
            out.append(item("health_not_harvested", n=people["to_harvest"]))
    return out


def space_languages(ctx: Any) -> list[dict]:
    """A space of texts whose vocabulary has many keywords outside the reference language:
    its themes may split by language (``themes.space``'s last run)."""
    from cartolex.build.engine import space_languages_apart
    from cartolex.build.records import read_record

    record = read_record(ctx.layout, "themes.space")
    if record is None or "space_unit" not in record.parameters:
        return []
    share = space_languages_apart(
        record.measures.counts, str(record.parameters["space_unit"].value)
    )
    if share is None:
        return []
    language = ctx.project.config.languages.reference
    return [
        item("health_space_languages", level="warning", share=round(100 * share), language=language)
    ]


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


def quiet_failures(stages: list[dict], jobs: list[Any]) -> dict[str, str]:
    """The failed stages whose failure is no longer the last thing tried (a job started
    after it and ended): stage id → the time of the failed attempt."""
    from ..guidance import ended_after

    out = {}
    for s in stages:
        attempt = s.get("attempt") or {}
        if s["state"] == "failed" and ended_after(jobs, attempt.get("finished_at")):
            out[s["id"]] = attempt.get("finished_at")
    return out


def next_step(
    stages: list[dict],
    health: list[dict],
    running: Any,
    *,
    waiting: str | None = None,
    people: dict[str, int] | None = None,
    facts: dict[str, Any] | None = None,
    quiet: Any = (),
) -> dict:
    """The single most useful action now, in this order: watch a running job, the copilot
    a paused build waits for, add people, install a missing model, look at a failure that
    is the last thing tried, before the texts are gathered set the roles, check the
    identities and collect the texts, a first build, restore a stale map, bring the rest up
    to date, review the keywords, curate the themes, draw the map, share it, look at it."""
    people = people or {"people": 0, "mapped": 0, "identities": 0, "to_harvest": 0}
    facts = facts or {}
    by_id = {s["id"]: s for s in stages}
    states = [s["state"] for s in stages]

    def built(stage: str) -> bool:
        return bool((by_id.get(stage) or {}).get("has_results"))

    if running is not None:
        if running.kind == "build":
            return item("next_watch_build")
        return item("next_job_running", kind=running.kind)
    if waiting is not None:
        return item("next_copilot_waiting", step=waiting)
    if not any(s == "up_to_date" for s in states) and people["people"] == 0:
        return item("next_import_people")
    if any(h["code"] == "health_model_missing" for h in health):
        return item("next_install_model")
    failed = next((s for s in stages if s["state"] == "failed" and s["id"] not in quiet), None)
    if failed is not None:
        code = (failed.get("attempt") or {}).get("code")
        if code == "stage_no_mapped":
            return item("next_set_roles")
        if code == "stage_no_texts":
            return item("next_collect_texts", n=people["to_harvest"])
        return item("next_see_failure", stage=failed["id"], scope=[failed["id"]])
    if not built("corpus.assemble") and people["people"]:
        if people["mapped"] == 0:
            return item("next_set_roles")
        if people["identities"]:
            return item("next_check_identities", n=people["identities"])
        if people["to_harvest"]:
            return item("next_collect_texts", n=people["to_harvest"])
    if not any(s in ("up_to_date", "needs_update") for s in states):
        return item("next_first_build")
    if any(h["code"] == "health_map_stale" for h in health):
        return item("next_restore_map", scope=["map"])
    if any(s in ("needs_update", "never_built") for s in states):
        return item("next_update")
    if built("keywords.extract") and not facts.get("reviewed"):
        return item("next_review_keywords")
    if not facts.get("themes_saved"):
        return item("next_curate_themes")
    if not built("map.layout"):
        return item("next_build_map", scope=["map"])
    if not facts.get("shared"):
        return item("next_share")
    return item("next_open_map")


# ── the map's preview ────────────────────────────────────────────────────────


def preview(runtime: Any, ctx: Any) -> dict[str, Any] | None:
    """An even sample of the map's people, each with the index of its top-level theme."""
    from .atlas import FORMAT, build_bundle, lineage

    runs = lineage(ctx)
    if runs.get("map.layout") is None:
        return None
    key = ("atlas", FORMAT, ctx.id, tuple(sorted(runs.items())))
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


def _lexicon(ctx: Any) -> dict[str, Any] | None:
    """The lexicon's word cloud to show (``{"run": …}``, the keywords' build it is drawn
    from), once a vocabulary build exists; ``None`` before."""
    from ..lexicon_view import lexicon_run

    run = lexicon_run(ctx)
    return {"run": run} if run else None


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
            memory_mb=runtime.budget.budget().memory_mb,
        )
    except (BuildBusy, ParamsError):
        the_plan = None
    from ..guidance import checklist, checklist_key, people_facts
    from ..jobs import read_job_logs
    from .me import preference
    from .state import triage_status

    triage = triage_status(runtime, ctx.project)
    try:
        people = people_facts(runtime, ctx.project)
    except Exception:  # the counts guide; the overview shows without them
        log.warning("the people's counts could not be read", extra={"event": "overview_people"})
        people = {k: 0 for k in ("people", "mapped", "with_texts", "without_texts")}
        people |= {"identities": 0, "to_harvest": 0}
    health = [
        *_map_stale(by_id),
        *_missing_models(config, by_id),
        *_too_large(the_plan),
        *_languages_split(config, by_id, triage),
        *space_languages(ctx),
        *_snowball_cut(ctx),
        *_guidance_health(people, by_id, triage),
    ]
    builds = runtime.site_builder.builds(ctx.project)
    facts = {
        "reviewed": triage["reviewed"] if triage["extraction"] else 0,
        "themes_saved": ctx.layout.themes_json.is_file(),
        "shared": bool(builds),
    }
    jobs = [*runtime.jobs.list(ctx.id), *read_job_logs(ctx.layout.jobs, ctx.id, limit=20)]
    quiet = quiet_failures(stages, jobs)
    key = checklist_key(ctx.id)
    try:
        atlas = preview(runtime, ctx)
    except Exception:  # the preview is a convenience: the overview shows without it
        log.warning("the map's preview could not be read", extra={"event": "overview_preview"})
        atlas = None
    shares = builds[:RECENT_SHARES]
    areas = {a: [by_id[s]["state"] for s in ids if s in by_id] for a, _, ids in AREAS}
    return {
        "project": {
            "id": ctx.id,
            "name": config.name,
            "state": summary([s for own in areas.values() for s in own]),
        },
        "next": next_step(
            stages,
            health,
            running,
            waiting=None if running else _waiting_step(runtime, ctx),
            people=people,
            facts=facts,
            quiet=quiet,
        ),
        "health": health,
        "quiet_failures": quiet,
        "steps": checklist(by_id, people, facts),
        "checklist": {"key": key, "hidden": preference(request, key) is True},
        "people": people,
        "preview": atlas,
        "lexicon": _lexicon(ctx),
        "shares": {"items": shares, "available": runtime.site_builder.available},
    }
