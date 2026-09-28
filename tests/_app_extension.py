# SPDX-License-Identifier: MIT
"""A generic extension, as a host application would write one: a page, a slot, a stage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from cartolex.app import Branding, Extension, NavEntry, StatusArea
from cartolex.app.deps import ProjectContext, project_context
from cartolex.build import Stage
from cartolex.project.models import Overlay, Slot


def _static(folder: Path) -> Path:
    (folder / "pages").mkdir(parents=True, exist_ok=True)
    (folder / "index.js").write_text(
        "export function register(api) { api.pages.add({ id: 'reports', route: '/reports' }); }\n",
        encoding="utf-8",
    )
    (folder / "pages" / "reports.js").write_text(
        "export function mount(ctx) { return () => {}; }\n", encoding="utf-8"
    )
    (folder / "logo.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"></svg>\n', encoding="utf-8"
    )
    return folder


def _router() -> APIRouter:
    router = APIRouter()

    @router.get("/hello")
    def hello(ctx: ProjectContext = Depends(project_context)) -> dict[str, Any]:  # noqa: B008
        return {"hello": ctx.project.config.name}

    return router


def make_extension(static_dir: Path, *, stage_runner=None, opened: list | None = None) -> Extension:
    """The extension: a « reports » page, a « reports » folder slot, its own stage declaration.

    The stage replaces the declaration of ``overlays.position`` with a runner of the host's
    (the project format lists the build's stages: a host declares one of them).
    """
    from _build_fakes import make_registry  # noqa: F401 - the fakes' registry shapes the stage

    stages: tuple[Stage, ...] = ()
    if stage_runner is not None:
        stages = (
            Stage(
                "overlays.position",
                "place projected people (the host's way)",
                upstream=("themes.group",),
                project=("overlays",),
                run=stage_runner,
            ),
        )
    return Extension(
        id="reports",
        routers=(_router(),),
        static_dir=_static(static_dir),
        modules=("index.js",),
        i18n={"en": {"nav.reports": "Reports"}, "fr": {"nav.reports": "Rapports"}},
        branding=Branding(name="Example host", logo="logo.svg", accent="#2b47a8"),
        nav=(NavEntry("reports", "nav.reports", "/reports", "pages/reports.js", order=70),),
        stages=stages,
        stage_patches={"themes.group": {"defaults": {"top_groups": 7}}},
        corpus_slots=(Slot(id="reports", kind="folder", fit=True, trajectory=False),),
        overlay_sets=(Overlay(id="applicants"),),
        identity_provider=lambda new: {
            "domain_description": f"described by the host for {new.name}"
        },
        status_keys=(
            StatusArea(
                "reports",
                "area.reports",
                probe=lambda project: [{"id": "report", "state": "never_built", "label": "report"}],
            ),
        ),
        capabilities={"reports": True},
        settings_dir_name="example-host",
        on_project_open=(lambda project: opened.append(project.config.name))
        if opened is not None
        else None,
    )
