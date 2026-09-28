# SPDX-License-Identifier: MIT
"""Parameters: effective values with their origin, validation, what changed since the last run."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Request, Response
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
    }


def params_view(runtime: Any, project: Any) -> dict[str, Any]:
    """Every stage's parameters: value, origin, limits, and the value of the last run."""
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
                "last_run": None if last is None else last.value,
                "changed_since_last_run": None
                if record is None
                else (last is None or last.value != pv.value),
            }
            if name in specs:
                entry.update(_spec(specs[name]))
            elif name == "seed":
                entry.update({"type": "int", "description": "fixes every random choice"})
            elif name == "year":
                entry.update(
                    {"type": "int", "description": "the year date windows count back from"}
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
