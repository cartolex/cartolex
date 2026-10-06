# SPDX-License-Identifier: MIT
"""The collection notices a person acknowledged (« Don't show this again »), and their reset."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from ..notices import NOTICE_VERSION
from ..routing import Routes, principal_of, runtime_of

routes = Routes(tags=["me"])


def _view(request: Request) -> dict[str, Any]:
    kinds = runtime_of(request).notices.read(principal_of(request).id)
    return {
        "version": NOTICE_VERSION,
        "acknowledged": [{"kind": k, "at": v.get("at")} for k, v in sorted(kinds.items())],
    }


@routes.get("/api/me/notices", action="me.read", resource="app")
def get_notices(request: Request) -> dict[str, Any]:
    """The kinds of collection whose notice this person acknowledged, and when: their notice
    is brief until its content changes."""
    return _view(request)


@routes.delete("/api/me/notices", action="me.write", resource="app")
def reset_notices(request: Request) -> dict[str, Any]:
    """Forget every acknowledgement: each kind of collection shows its full notice again."""
    runtime_of(request).notices.reset(principal_of(request).id)
    return _view(request)
