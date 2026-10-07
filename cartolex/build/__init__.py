# SPDX-License-Identifier: MIT
"""The build: stages, their parameters, run records, validity, safe re-runs and the dry run.

A build runs the stages of a project (``docs/format/derived.md``) that need it,
in order, each in a staging folder swapped into place when it succeeds::

    from cartolex.build import build, plan, status

    for s in status(project).values():      # the six states, with their reasons
        print(s.describe())
    print(plan(project).describe())         # the dry run: what runs, what is kept, why
    result = build(project, consent=ask, progress=show, cancel=stop_event)
    print(result.summary())

:data:`STAGES` declares cartolex's stages; ``docs/dev/build.md`` explains how a
stage is declared and how the machinery works. The engine packages never import
this package.
"""

from .execution import (
    BuildResult,
    Cancelled,
    ConsentRequest,
    Progress,
    StageContext,
    StageRefused,
    build,
)
from .fingerprints import code_fingerprint, table_fingerprint
from .machine import PeakMemory, available_memory_mb
from .params import (
    RULES,
    CrossCheck,
    ParamsError,
    ParamSpec,
    ProjectSizes,
    Rule,
    fitting_depth,
    space_dimensions,
    theme_depth,
    theme_level_sizes,
)
from .planning import BuildBusy, BuildPlan, PlanItem, plan
from .records import new_run_id
from .stages import STAGES, CostModel, Estimate, Registry, Stage, StageNotConnected
from .validity import Reason, StageState, StageStatus, load_params, status

__all__ = [
    "RULES",
    "STAGES",
    "BuildBusy",
    "BuildPlan",
    "BuildResult",
    "Cancelled",
    "ConsentRequest",
    "CostModel",
    "CrossCheck",
    "Estimate",
    "ParamSpec",
    "ParamsError",
    "PeakMemory",
    "PlanItem",
    "Progress",
    "ProjectSizes",
    "Reason",
    "Registry",
    "Rule",
    "Stage",
    "StageContext",
    "StageNotConnected",
    "StageRefused",
    "StageState",
    "StageStatus",
    "available_memory_mb",
    "build",
    "code_fingerprint",
    "load_params",
    "new_run_id",
    "plan",
    "status",
    "table_fingerprint",
    "fitting_depth",
    "space_dimensions",
    "theme_depth",
    "theme_level_sizes",
]
