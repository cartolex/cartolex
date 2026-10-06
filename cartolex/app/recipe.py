# SPDX-License-Identifier: MIT
"""The recipe of a build: every parameter of every stage with its value, its origin and
whether it differs from its default, the map version's layout, and the same as Markdown or
CSV for a paper's methods section.

Nothing here writes to the project. The parameters come from
:func:`~cartolex.app.routes.params.params_view` (what ``GET /api/params`` answers), the
layout from the pinned map version (``decisions/maps.json``). :func:`changed_counts` is what
the project state adds for the pages' « Tune » panels: how many values differ from their
defaults, per stage.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

__all__ = ["PANEL_OF_STAGE", "changed_counts", "recipe_csv", "recipe_markdown", "recipe_view"]

#: The page whose « Tune » panel edits each stage's parameters (``docs/dev/ui.md``).
PANEL_OF_STAGE: dict[str, str] = {
    "corpus.assemble": "texts",
    "keywords.extract": "keywords",
    "keywords.triage": "keywords",
    "keywords.build": "keywords",
    "themes.space": "themes",
    "themes.group": "themes",
    "map.layout": "map",
    "map.trajectories": "map",
}
#: Parameters every stage records but people set once for the build (the seed, the year).
_GLOBAL = {"seed", "year"}


def _layout_rows(ctx: Any, seed: int, sizes: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The pinned map version's method, seed and layout parameters, as recipe rows."""
    from cartolex.build.engine import default_layout_method
    from cartolex.project.maps import pinned, read_maps

    from .method import LAYOUT_DEFAULTS

    maps, _ = read_maps(ctx.layout)
    version = pinned(maps)
    if version is None:
        return []
    lay = version.layout
    common = {"group": "layout", "stage": None, "panel": "map", "rule": None,
              "rule_description": None, "version": version.id}  # fmt: skip
    method_default = default_layout_method(sizes.get("mapped_units"))
    rows = [
        {**common, "name": "method", "value": lay.method, "default_value": method_default,
         "from": "version", "differs": lay.method != method_default, "tier": "essential"},
        {**common, "name": "seed", "value": lay.seed, "default_value": seed,
         "from": "version", "differs": lay.seed != seed, "tier": "essential"},
    ]  # fmt: skip
    for spec in LAYOUT_DEFAULTS.get(lay.method, []):
        value = lay.params.get(spec["name"])
        set_ = value is not None
        rows.append(
            {
                **common,
                "name": spec["name"],
                "value": value if set_ else spec["default"],
                "default_value": spec["default"],
                "from": "version" if set_ else "default",
                "differs": set_ and value != spec["default"],
                "tier": spec.get("tier", "advanced"),
            }
        )
    return rows


def recipe_view(runtime: Any, ctx: Any) -> dict[str, Any]:
    """Every parameter of the build, in pipeline order: the build's seed and pinned year, each
    stage's parameters (value, default, origin with its rule, ``differs``, tier, the panel that
    edits it), then the pinned map version's layout; and ``ai_usage``, the tokens the AI
    clean-up by API spent (:func:`~cartolex.app.ai_usage.recorded_usage`)."""
    from .ai_usage import recorded_usage
    from .routes.params import params_view

    view = params_view(runtime, ctx.project)
    glob = view.get("global") or {}
    seed = (glob.get("seed") or {}).get("value", 0)
    year = (glob.get("pinned_year") or {}).get("value")
    build = {"group": "build", "stage": None, "panel": "texts", "rule": None,
             "rule_description": None, "tier": "advanced"}  # fmt: skip
    rows: list[dict[str, Any]] = [
        {**build, "name": "seed", "value": seed, "default_value": 0,
         "from": "params.json" if (glob.get("seed") or {}).get("set_in_file") else "default",
         "differs": seed != 0},
        {**build, "name": "pinned_year", "value": year, "default_value": None,
         "from": "default" if year is None else "params.json", "differs": year is not None},
    ]  # fmt: skip
    names: dict[str, str] = {}
    for stage in view.get("stages", []):
        names[stage["id"]] = stage["name"]
        for p in stage["params"]:
            if p["name"] in _GLOBAL:
                continue
            rows.append(
                {
                    "group": stage["id"],
                    "stage": stage["id"],
                    "panel": PANEL_OF_STAGE.get(stage["id"]),
                    "name": p["name"],
                    "value": p["value"],
                    "default_value": p["default_value"],
                    "from": p["from"],
                    "rule": p["rule"],
                    "rule_description": p["rule_description"],
                    "differs": bool(p["differs"]),
                    "tier": p.get("tier") or "advanced",
                }
            )
    rows += _layout_rows(ctx, seed, view.get("sizes") or {})
    return {
        "project": ctx.project.config.name,
        "valid": view.get("valid", True),
        "problems": view.get("problems", []),
        "stages": names,
        "rows": rows,
        "changed": sum(bool(r["differs"]) for r in rows),
        "version": view.get("version"),
        # the tokens the AI clean-up by API spent: its last run, every run of the project
        "ai_usage": recorded_usage(ctx.layout),
    }


def changed_counts(runtime: Any, ctx: Any) -> dict[str, int]:
    """How many values differ from their defaults: per stage id, ``build`` (the seed and the
    pinned year) and ``layout`` (the pinned map version); ``{}`` when the parameters cannot be
    read."""
    try:
        rows = recipe_view(runtime, ctx)["rows"]
    except Exception:  # noqa: BLE001 - the state must answer even when params.json is broken
        return {}
    out: dict[str, int] = {}
    for r in rows:
        if r["differs"]:
            out[r["group"]] = out.get(r["group"], 0) + 1
    return out


# ── the exports ───────────────────────────────────────────────────────────────


@lru_cache(maxsize=8)
def _catalogue_file(path: str, mtime: float) -> dict[str, str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)}


def catalogue(static_dir: Path, language: str) -> dict[str, str]:
    """The interface's catalogue of *language* over the English one (the labels' words)."""
    out: dict[str, str] = {}
    for lang in ("en", language):
        path = static_dir / "i18n" / f"{lang}.json"
        if path.is_file():
            out.update(_catalogue_file(str(path), path.stat().st_mtime))
    return out


class _Words:
    def __init__(self, words: Mapping[str, str]) -> None:
        self.words = words

    def __call__(self, key: str, fallback: str = "", **params: Any) -> str:
        text = self.words.get(key, fallback)
        for k, v in params.items():
            text = text.replace("{" + k + "}", str(v))
        return text

    def label(self, row: Mapping[str, Any]) -> str:
        return self(f"param.label.{row['group']}.{row['name']}", row["name"])

    def group(self, group: str, names: Mapping[str, str]) -> str:
        if group in ("build", "layout"):
            return self(f"recipe.group.{group}", group)
        return self(f"stage.{group}", names.get(group, group))

    def origin(self, row: Mapping[str, Any]) -> str:
        if row["from"] == "rule":
            return self("settings.origin.rule_named", "by a rule: {rule}",
                        rule=row.get("rule_description") or row.get("rule") or "")  # fmt: skip
        if row["from"] == "version":
            return self("recipe.origin.version", "map version {version}", version=row["version"])
        return self(f"settings.origin.{row['from']}", row["from"])


def _shown(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(_shown(v) for v in value)
    if isinstance(value, dict):
        return "; ".join(f"{k}: {_shown(v)}" for k, v in value.items())
    return str(value)


def recipe_markdown(view: Mapping[str, Any], words: Mapping[str, str], made: str) -> str:
    """The recipe as Markdown: one table per stage, the changed values marked."""
    w = _Words(words)
    out = [f"# {w('recipe.export.title', 'Recipe of {project}', project=view['project'])}", "",
           w("recipe.export.lead", "{made}. Values that differ from their defaults are marked *.",
             made=made), ""]  # fmt: skip
    head = [w("recipe.col.parameter", "parameter"), w("recipe.col.code", "code"),
            w("recipe.col.value", "value"), w("recipe.col.default", "default"),
            w("recipe.col.origin", "origin")]  # fmt: skip
    last = None
    for row in view["rows"]:
        if row["group"] != last:
            if last is not None:
                out.append("")
            last = row["group"]
            title = w.group(last, view["stages"])
            code = f" (`{last}`)" if last not in ("build", "layout") else ""
            out += [f"## {title}{code}", "", "| " + " | ".join(head) + " |",
                    "|" + "---|" * len(head)]  # fmt: skip
        cells = [w.label(row) + (" *" if row["differs"] else ""), f"`{row['name']}`",
                 _shown(row["value"]), _shown(row["default_value"]), w.origin(row)]  # fmt: skip
        out.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    return "\n".join(out) + "\n"


def recipe_csv(view: Mapping[str, Any], words: Mapping[str, str]) -> str:
    """The recipe as CSV, one row per parameter; lists and objects as JSON."""
    w = _Words(words)
    buf = io.StringIO()
    out = csv.writer(buf)
    out.writerow(["step", "parameter", "label", "value", "default", "origin", "differs", "tier"])

    def cell(v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, (list, dict, bool)):
            return json.dumps(v, ensure_ascii=False)
        return str(v)

    for row in view["rows"]:
        out.writerow([row["group"], row["name"], w.label(row), cell(row["value"]),
                      cell(row["default_value"]), w.origin(row),
                      "yes" if row["differs"] else "no", row["tier"]])  # fmt: skip
    return buf.getvalue()
