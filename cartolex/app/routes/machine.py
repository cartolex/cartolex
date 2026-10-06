# SPDX-License-Identifier: MIT
"""This computer: the keys and the OpenAlex snapshot folder saved on it (never in a project),
and its limits."""

from __future__ import annotations

import os
from typing import Annotated, Any, Literal

from fastapi import Request
from pydantic import BaseModel, Field

from ..errors import ApiError
from ..machine import KEY_SERVICES, BudgetRefused, SnapshotRefused
from ..routing import Routes, runtime_of

routes = Routes(tags=["machine"])

#: What OpenAlex allows a day, with and without a free key (its policy, checked 2026-09-28).
OPENALEX_BUDGET = {
    "without_key_usd": 0.10,
    "with_key_usd": 1.00,
    "list_per_1000_usd": 0.10,
    "search_per_1000_usd": 1.00,
    "lookup_usd": 0.0,
    "checked": "2026-09-28",
    "get_key": "https://openalex.org/settings/api",
}


def _view(runtime: Any) -> dict[str, Any]:
    from cartolex.build.machine import available_memory_mb, resident_memory_mb

    available = available_memory_mb()
    budget = runtime.settings.build_budget_mb
    return {
        "hosted": runtime.settings.hosted,
        "keys": {name: runtime.keys.status(name) for name in KEY_SERVICES},
        "keys_saved_in": "this computer" if runtime.keys.path is not None else "memory",
        "ai_api": runtime.ai_access() is not None,
        "openalex": OPENALEX_BUDGET,
        "snapshot": None if runtime.settings.hosted else runtime.snapshot.status(),
        "build_budget": None if runtime.settings.hosted else runtime.budget.status(),
        "limits": {
            "cpus": os.cpu_count(),
            "available_memory_mb": None if available is None else round(available),
            "budget_mb": round(budget)
            if budget is not None
            else None
            if available is None
            else round(available + resident_memory_mb()),
            "budget_from": "launch" if budget is not None else "available memory",
            "max_upload_mb": runtime.settings.max_upload_mb,
        },
    }


@routes.get("/api/machine", action="machine.read", resource="app")
def machine(request: Request) -> dict[str, Any]:
    """The keys saved on this computer (whether set, where from, their last four characters),
    whether the AI clean-up can run by API, OpenAlex's daily budget, the OpenAlex snapshot
    folder saved here (its state, release, sizes and read speed), what the builds may use
    of this computer (``build_budget``) and its limits."""
    return _view(runtime_of(request))


class KeyBody(BaseModel):
    """A key to save on this computer for *service*; ``null`` removes it."""

    service: Literal["mistral", "openalex"]
    key: Annotated[str | None, Field(min_length=8, max_length=500, pattern=r"^\S+$")] = None


@routes.put("/api/machine/keys", action="machine.write", resource="app")
def save_key(request: Request, body: KeyBody) -> dict[str, Any]:
    """Save or remove a key on this computer (never in a project); refused when hosted."""
    runtime = runtime_of(request)
    if runtime.settings.hosted:
        raise ApiError.of("keys_hosted")
    runtime.keys.save(body.service, body.key)
    return _view(runtime)


class SnapshotBody(BaseModel):
    """The folder of a downloaded OpenAlex snapshot, its full path; ``null`` removes it."""

    folder: Annotated[str | None, Field(min_length=1, max_length=4096)] = None


@routes.put("/api/machine/snapshot", action="machine.write", resource="app")
def save_snapshot(request: Request, body: SnapshotBody) -> dict[str, Any]:
    """Save or remove the OpenAlex snapshot folder of this computer (never in a project);
    refused when the folder holds no snapshot, and when hosted."""
    runtime = runtime_of(request)
    if runtime.settings.hosted:
        raise ApiError.of("snapshot_hosted")
    try:
        runtime.snapshot.save(body.folder)
    except SnapshotRefused as exc:
        raise ApiError.of("snapshot_invalid", folder=body.folder or "", reason=exc.reason) from None
    return _view(runtime)


class BudgetBody(BaseModel):
    """What the builds may use of this computer; ``null``: the default."""

    memory_mb: Annotated[int | None, Field(ge=1, le=10_000_000)] = None
    workers: Annotated[int | None, Field(ge=1, le=4096)] = None
    scratch: Annotated[str | None, Field(min_length=1, max_length=4096)] = None


@routes.put("/api/machine/budget", action="machine.write", resource="app")
def save_budget(request: Request, body: BudgetBody) -> dict[str, Any]:
    """Save what the builds the app starts may use of this computer: the memory their
    stages size their work to, their worker processes, a scratch folder on a fast disk
    (never in a project); refused when hosted."""
    runtime = runtime_of(request)
    if runtime.settings.hosted:
        raise ApiError.of("budget_hosted")
    try:
        runtime.budget.save(memory_mb=body.memory_mb, workers=body.workers, scratch=body.scratch)
    except BudgetRefused as exc:
        value = getattr(body, exc.field)
        raise ApiError.of(
            "budget_invalid", field=exc.field, value=str(value), reason=exc.reason
        ) from None
    return _view(runtime)
