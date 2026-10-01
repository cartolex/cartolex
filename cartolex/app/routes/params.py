# SPDX-License-Identifier: MIT
"""Parameters: effective values with their origin, validation, what changed since the last run."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import Query, Request, Response
from pydantic import BaseModel, Field

from ..deps import ProjectDep
from ..etags import check_version, etag_of, expected_version, version_of
from ..routing import Routes, runtime_of

routes = Routes(tags=["params"])


class ParamsBody(BaseModel):
    """The whole of ``decisions/params.json``: what people set, nothing else."""

    seed: Annotated[int, Field(ge=0, lt=2**32)] = 0
    pinned_year: Annotated[int, Field(ge=1900, le=2200)] | None = None
    stages: Annotated[dict[str, dict[str, Any]], Field(max_length=64)] = {}


def _spec(spec: Any) -> dict[str, Any]:
    return {
        "type": spec.type,
        "description": spec.description,
        "default": spec.default,
        "rule": spec.rule,
        "minimum": spec.minimum,
        "maximum": spec.maximum,
        "choices": list(spec.choices) if spec.choices is not None else None,
        "nullable": spec.nullable,
        "items": list(spec.items) if spec.items else None,
        "section": spec.section or None,
        "tier": spec.tier,
        "widget": spec.shape,
        "keys": list(spec.keys) if spec.keys is not None else None,
        "suggestions": list(spec.suggestions) if spec.suggestions is not None else None,
    }


def params_view(runtime: Any, project: Any) -> dict[str, Any]:
    """Every stage's parameters: value, origin, limits, the value without ``params.json``
    (``default_value``, and ``differs`` when the value is another), and the value of the last
    run."""
    from cartolex.build import RULES, load_params
    from cartolex.build.params import ParamsError, resolve_params
    from cartolex.build.records import read_record
    from cartolex.build.validity import _this_year, current_sizes

    registry = runtime.registry
    file, fp = project.read_params()
    try:
        params = load_params(project, registry)
    except ParamsError as exc:
        return {
            "valid": False,
            "problems": list(exc.problems),
            "file": file.model_dump(mode="json"),
            "version": version_of(fp),
            "stages": [],
        }
    records = {s.id: read_record(project.layout, s.id) for s in registry}
    sizes = current_sizes(project, registry, records)
    year = runtime.settings.build_year or _this_year()
    stages, problems = [], []
    for stage in registry:
        resolved = resolve_params(stage, params, sizes, year=year)
        # what each value would be without params.json: the default, or the rule's value
        baseline = resolve_params(
            stage, params.model_copy(update={"stages": {}}), sizes, year=year
        ).values
        if stage.skip_reason(project.config, params) is None:
            problems += resolved.problems(stage, sizes, project.config)
        record = records.get(stage.id)
        items = []
        specs = {s.name: s for s in stage.params}
        for name, pv in resolved.values.items():
            last = record.parameters.get(name) if record else None
            entry: dict[str, Any] = {
                "name": name,
                "value": pv.value,
                "from": pv.source,
                "rule": pv.rule,
                "rule_description": RULES[pv.rule].description if pv.rule in RULES else None,
                "waits_for": list(resolved.unknown.get(name, ())) or None,
                "set_in_file": name in (params.stages.get(stage.id) or {}),
                "default_value": baseline[name].value if name in baseline else None,
                "differs": name in baseline and pv.value != baseline[name].value,
                "last_run": None if last is None else last.value,
                # a parameter the last run did not record, at its default, is what that
                # run did (a parameter a newer cartolex declares)
                "changed_since_last_run": None
                if record is None
                else (
                    (last is None and pv.source != "default")
                    or (last is not None and last.value != pv.value)
                ),
            }
            if name in specs:
                entry.update(_spec(specs[name]))
            elif name == "seed":
                entry.update(
                    {
                        "type": "int",
                        "description": "fixes every random choice",
                        "tier": "advanced",
                        "widget": "number",
                    }
                )
            elif name == "year":
                entry.update(
                    {
                        "type": "int",
                        "description": "the year date windows count back from",
                        "tier": "advanced",
                        "widget": "number",
                    }
                )
            items.append(entry)
        if items:
            stages.append({"id": stage.id, "name": stage.name, "params": items})
    return {
        "valid": not problems,
        "problems": problems,
        "global": {
            "seed": {"value": params.seed, "set_in_file": "seed" in params.model_fields_set},
            "pinned_year": {"value": params.pinned_year},
        },
        "sizes": sizes.as_dict(),
        "stages": stages,
        "version": version_of(fp),
    }


@routes.get("/api/params", action="params.read")
def get_params(request: Request, response: Response, ctx: ProjectDep) -> dict[str, Any]:
    """Effective values, where each comes from (``default``, ``rule``, ``params.json``), the
    validation messages, and whether each changed since the stage's last run."""
    view = params_view(runtime_of(request), ctx.project)
    response.headers["ETag"] = etag_of(view["version"])
    return view


def _changes(before: Any, after: ParamsBody) -> str:
    bits = []
    if before.seed != after.seed:
        bits.append(f"seed={after.seed}")
    if before.pinned_year != after.pinned_year:
        bits.append(f"pinned_year={after.pinned_year}")
    names = set(before.stages) | set(after.stages)
    for stage in sorted(names):
        old, new = before.stages.get(stage, {}) or {}, after.stages.get(stage, {}) or {}
        for key in sorted(set(old) | set(new)):
            if old.get(key) != new.get(key):
                bits.append(f"{stage}.{key}={new.get(key, 'default')}")
    return "set " + ", ".join(bits) if bits else "save parameters"


@routes.put("/api/params", action="params.write")
def put_params(
    request: Request, response: Response, body: ParamsBody, ctx: ProjectDep
) -> dict[str, Any]:
    """Replace ``decisions/params.json`` (send ``If-Match``); a value a stage cannot take is
    refused with the reasons (422)."""
    from cartolex.build.params import ParamsError, check_params

    runtime = runtime_of(request)
    expected = expected_version(request)
    project = ctx.project
    with ctx.handle.mutex:
        check_version(ctx.layout.params_json, expected)
        before, _ = project.read_params()
        updated = before.model_validate(
            {
                **before.model_dump(mode="json", by_alias=True),
                "seed": body.seed,
                "pinned_year": body.pinned_year,
                "stages": {k: v for k, v in body.stages.items() if v},
            }
        )
        problems = check_params(updated, runtime.registry)
        if problems:
            raise ParamsError(problems)
        project.save_params(updated, expected=expected, action=_changes(before, body))
    view = params_view(runtime, project)
    response.headers["ETag"] = etag_of(view["version"])
    return view


@routes.get("/api/recipe", action="params.read")
def get_recipe(request: Request, response: Response, ctx: ProjectDep) -> dict[str, Any]:
    """The recipe of the build, read-only: every parameter of every stage with its value, its
    default, its origin (a rule with its reason), whether it differs and its tier, then the
    pinned map version's layout; each row names the page whose « Tune » panel edits it."""
    from ..recipe import recipe_view

    view = recipe_view(runtime_of(request), ctx)
    response.headers["ETag"] = etag_of(view["version"])
    return view


@routes.get("/api/recipe/export", action="params.read")
def export_recipe(
    request: Request,
    ctx: ProjectDep,
    format: Literal["md", "csv"] = "md",
    language: Annotated[str, Query(max_length=8)] = "",
) -> Response:
    """The recipe as Markdown (one table per stage) or CSV (one row per parameter), with the
    parameters' labels in *language* (an interface language; English otherwise)."""
    from datetime import date

    from cartolex.project.project import cartolex_version

    from ..recipe import catalogue, recipe_csv, recipe_markdown, recipe_view
    from ..static_files import PACKAGE_STATIC

    runtime = runtime_of(request)
    lang = language if language in runtime.settings.locales else "en"
    words = catalogue(runtime.settings.static_dir or PACKAGE_STATIC, lang)
    view = recipe_view(runtime, ctx)
    if format == "csv":
        body, media = recipe_csv(view, words), "text/csv; charset=utf-8"
    else:
        made = f"cartolex {cartolex_version()}, {date.today().isoformat()}"
        body, media = recipe_markdown(view, words, made), "text/markdown; charset=utf-8"
    return Response(body.encode("utf-8"), media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="recipe.{format}"'})  # fmt: skip
