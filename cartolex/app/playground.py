# SPDX-License-Identifier: MIT
"""The themes playground: the grouping regrouped with other settings, nothing saved.

The theme editor's « Playground » tab lets the curator try the grouping's
settings (the levels, the keywords per topic, the top themes or each level's
size, the comb and its θ, the space's unit) and see the tree before adopting
it. :func:`group_preview` runs the build's own runners — ``themes.group``'s,
and ``themes.space``'s when the space's settings differ from the stored
space's — on the project's current results, with the settings laid over
``params.json``, into a scratch folder that is removed afterwards: a preview is
what a build with those settings writes as its proposal (``themes_draft.json``),
apart from the run's id in ``based_on.run``.

Nothing reaches the project: ``params.json`` is not written, and no stage's
results change. Adopting a preview is the build's business (the settings saved
through ``PUT /api/params``, then ``themes.group`` built), followed by
:func:`cartolex.project.themes_carry.carry_curation` in the editor.

Per-node stability (the copilot kit's: the grouping redone on the space refitted
without a share of the people, three times) is not measured here: it costs
three refits of the space and three groupings, many times a preview's time.
"""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

#: The settings a playground may change, per stage.
PLAYGROUND_PARAMS: dict[str, tuple[str, ...]] = {
    "themes.space": ("space_unit",),
    "themes.group": (
        "depth",
        "top_groups",
        "keywords_per_group",
        "level_sizes",
        "comb",
        "comb_theta",
    ),
}

#: The stages a preview may run, in the build's order.
PREVIEW_STAGES = ("themes.space", "themes.group")


class PreviewRefused(ValueError):
    """The settings cannot be previewed: *problems* says why (each a sentence)."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


class PreviewNeedsBuild(RuntimeError):
    """A stage the preview reads has no results yet (*stage*)."""

    def __init__(self, stage: str) -> None:
        self.stage = stage
        super().__init__(f"{stage} has no results")


def overlaid(params: Any, settings: Mapping[str, Mapping[str, Any]]) -> Any:
    """*params* (a :class:`~cartolex.project.models.ParamsFile`) with *settings* laid over it.

    Only :data:`PLAYGROUND_PARAMS` may be given; a value ``None`` removes the
    parameter from ``params.json`` (its default or its rule applies).
    """
    unknown = [
        f"{stage}.{name}"
        for stage, values in settings.items()
        for name in values
        if name not in PLAYGROUND_PARAMS.get(stage, ())
    ]
    if unknown:
        raise PreviewRefused([f"{u}: not a setting of the playground" for u in sorted(unknown)])
    stages = {k: dict(v) for k, v in params.stages.items()}
    for stage, values in settings.items():
        target = stages.setdefault(stage, {})
        for name, value in values.items():
            if value is None:
                target.pop(name, None)
            else:
                target[name] = value
        if not target:
            stages.pop(stage)
    return params.model_copy(update={"stages": stages})


def _view(project: Any, registry: Any, settings: Mapping[str, Mapping[str, Any]], year: int | None):
    """The build's view of *project* with *settings* laid over its parameters, checked."""
    from cartolex.build.params import check_params
    from cartolex.build.validity import _View

    view = _View.read(project, registry, year)
    view.params = overlaid(view.params, settings)
    problems = check_params(view.params, registry)
    for sid in PREVIEW_STAGES:
        problems += view.resolve(registry[sid]).problems(registry[sid], view.sizes, project.config)
    if problems:
        raise PreviewRefused(problems)
    return view


def _space_settings(view: Any, registry: Any) -> dict[str, Any]:
    return view.resolve(registry["themes.space"]).plain()


def needs_space(view: Any, registry: Any) -> bool:
    """Whether the space must be refitted: a setting of ``themes.space`` differs from the
    stored space's (its run record)."""
    record = view.records.get("themes.space")
    if record is None:
        return True
    used = {name: pv.value for name, pv in record.parameters.items()}
    return any(used.get(k) != v for k, v in _space_settings(view, registry).items())


def preview_settings(
    project: Any, registry: Any, settings: Mapping[str, Mapping[str, Any]], year: int | None = None
) -> dict[str, Any]:
    """What a preview with *settings* is computed from: each stage's effective values and the
    runs it reads (the cache key and the answer's ``settings``)."""
    view = _view(project, registry, settings, year)
    for sid in ("keywords.build", "themes.space"):
        if view.records.get(sid) is None:
            raise PreviewNeedsBuild(sid)
    refit = needs_space(view, registry)
    return {
        "stages": {sid: view.resolve(registry[sid]).plain() for sid in PREVIEW_STAGES},
        "space_refit": refit,
        "runs": {
            sid: view.records[sid].run_id  # type: ignore[union-attr]
            for sid in ("keywords.build", "themes.space")
        },
    }


def cache_key(project_id: str, effective: Mapping[str, Any]) -> tuple:
    """The key a preview is kept under: the project, the runs read and the effective values."""
    text = json.dumps(
        {"stages": effective["stages"], "runs": effective["runs"]}, sort_keys=True, default=str
    )
    return ("playground", project_id, text)


def _stage_context(
    project: Any,
    view: Any,
    registry: Any,
    stage_id: str,
    out: Path,
    upstream: dict[str, Path],
    report: Callable[[float, str], None],
    cancel: Any,
) -> Any:
    from cartolex.build.execution import StageContext
    from cartolex.build.planning import run_inputs

    stage = registry[stage_id]
    inputs = run_inputs(view, stage)
    readable = {u: path for u, path in upstream.items() if u in registry.upstream_of(stage_id)}
    out.mkdir(parents=True, exist_ok=True)
    return StageContext(
        project=project,
        stage=stage,
        run_id=f"preview-{stage_id}",
        out=out,
        params=inputs.resolved.plain(),
        sizes=view.sizes,
        identity=inputs.identity,
        upstream=readable,
        report=report,
        cancel=cancel,
        probe=lambda _name: None,
        records={u: view.records[u] for u in readable if view.records.get(u) is not None},
    )


def group_preview(
    project: Any,
    registry: Any,
    settings: Mapping[str, Mapping[str, Any]],
    *,
    year: int | None = None,
    progress: Callable[[float, str], None] | None = None,
    cancel: Any = None,
    scratch: Path | None = None,
) -> dict[str, Any]:
    """The grouping's proposal with *settings*, from the project's current results.

    Runs ``themes.space`` (when its settings differ from the stored space's)
    and ``themes.group`` as the build does, into a scratch folder removed
    afterwards. Answers ``tree`` (the proposal, a ``cartolex-themes/1``
    document whose ``based_on.run`` is ``preview``), ``settings`` (each
    stage's effective values), ``space_refit``, ``theta`` (the comb's θ: the
    pinned one, else the calibrated one; ``None`` without the comb) and
    ``seconds`` (``space``, ``group``, ``total``).
    """
    from cartolex.build.execution import Cancelled

    started = time.perf_counter()
    report = progress or (lambda _f, _m: None)
    view = _view(project, registry, settings, year)
    layout = project.layout
    for sid in ("keywords.build", "themes.space"):
        if view.records.get(sid) is None:
            raise PreviewNeedsBuild(sid)
    refit = needs_space(view, registry)
    upstream = {
        sid: layout.stage(sid)
        for sid in registry.ids
        if view.records.get(sid) is not None and sid not in ("themes.group",)
    }
    seconds: dict[str, float] = {}
    parts = (0.0, 0.5, 1.0) if refit else (0.0, 0.0, 1.0)

    def scaled(lo: float, hi: float, what: str) -> Callable[[float, str], None]:
        def forward(fraction: float, message: str = "") -> None:
            report(lo + (hi - lo) * float(fraction), message or what)

        return forward

    with tempfile.TemporaryDirectory(prefix="cartolex-playground-", dir=scratch) as tmp:
        folder = Path(tmp)
        if refit:
            t0 = time.perf_counter()
            ctx = _stage_context(
                project,
                view,
                registry,
                "themes.space",
                folder / "themes.space",
                upstream,
                scaled(parts[0], parts[1], "space"),
                cancel,
            )
            registry["themes.space"].run(ctx)
            upstream["themes.space"] = folder / "themes.space"
            seconds["space"] = round(time.perf_counter() - t0, 2)
        if cancel is not None and cancel.is_set():
            raise Cancelled("the preview was cancelled")
        t0 = time.perf_counter()
        out = folder / "themes.group"
        ctx = _stage_context(
            project,
            view,
            registry,
            "themes.group",
            out,
            upstream,
            scaled(parts[1], parts[2], "grouping"),
            cancel,
        )
        registry["themes.group"].run(ctx)
        seconds["group"] = round(time.perf_counter() - t0, 2)
        tree = json.loads((out / "themes_draft.json").read_text(encoding="utf-8"))
        theta = _theta(
            ctx.params, int(view.sizes.kept_keywords or 0), upstream["themes.space"], out
        )
    tree.setdefault("based_on", {})["run"] = "preview"
    seconds["total"] = round(time.perf_counter() - started, 2)
    return {
        "tree": tree,
        "settings": {sid: view.resolve(registry[sid]).plain() for sid in PREVIEW_STAGES},
        "space_refit": refit,
        "theta": theta,
        "seconds": seconds,
    }


def _theta(params: Mapping[str, Any], kept: int, space: Path, group: Path) -> float | None:
    """The θ the comb kept: the pinned one, else the one its calibration chose."""
    if not params.get("comb"):
        return None
    if params.get("comb_theta") is not None:
        return float(params["comb_theta"])
    from cartolex.build.engine import comb_options, theme_levels, ward_options
    from cartolex.lexicon.theme_tree import TEXT_KEYWORDS, comb_calibration

    models = space / "models"
    found = comb_calibration(
        lexical_data_json=models / "lexical_data.json",
        embeddings_json=models / "embeddings.json",
        term_clusters_csv=group / "umap_terms_clustered.csv",
        text_keywords_npz=group / TEXT_KEYWORDS,
        level_sizes=theme_levels(params, kept),
        comb_options=comb_options(params),
        ward=ward_options(params),
    )
    return None if found is None else float(found["theta"])
