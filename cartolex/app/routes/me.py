# SPDX-License-Identifier: MIT
"""The person's own preferences: interface language, theme, the jobs they dismissed.

The interface keeps them here, per principal, so they outlive a browser's own
storage (a new address, a cleared cache) and, hosted, follow a person from one
browser to another; the browser keeps a copy only to paint the theme before the
first request. They live in the app's own folder (``<data dir>/users/<digest of
the principal's id>.json``, never in a project), or in memory when the app has
no folder.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Request
from pydantic import BaseModel, Field

from ..routing import Routes, principal_of, runtime_of

routes = Routes(tags=["me"])

FORMAT = "cartolex-preferences/1"
Key = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")]
Scalar = Annotated[str, Field(max_length=200)] | int | float | bool | None
JobId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")]
#: Dismissed jobs kept (the latest ones): the Activity list never holds more.
MAX_DISMISSED = 200


class Preferences(BaseModel):
    """What a person chose: the interface language, the theme, the finished jobs they
    dismissed from the Activity list, and a few other settings."""

    locale: Annotated[str, Field(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")] | None = None
    theme: Literal["system", "light", "dark"] | None = None
    dismissed_jobs: Annotated[list[JobId], Field(max_length=MAX_DISMISSED)] = []
    #: When the interface saved them (milliseconds since 1970, the browser's clock): between
    #: the browser's copy and these, the newer wins.
    saved_at: Annotated[int, Field(ge=0)] | None = None
    other: Annotated[dict[Key, Scalar], Field(max_length=50)] = {}


def _file(request: Request) -> Path | None:
    settings = runtime_of(request).settings
    if settings.data_dir is None:
        return None
    digest = hashlib.sha256(principal_of(request).id.encode("utf-8")).hexdigest()[:32]
    return Path(settings.data_dir) / "users" / f"{digest}.json"


def _read(request: Request) -> tuple[Preferences, bool]:
    runtime = runtime_of(request)
    path = _file(request)
    if path is None:
        stored = runtime.preferences.get(principal_of(request).id)
        return (stored or Preferences()), stored is not None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return Preferences.model_validate(doc.get("preferences", {})), True
    except (OSError, ValueError):
        return Preferences(), False


def preference(request: Request, key: str) -> Any:
    """One of the other settings of the person who sends *request* (``None`` when unset)."""
    prefs, _ = _read(request)
    return prefs.other.get(key)


def _view(request: Request, prefs: Preferences, stored: bool) -> dict[str, Any]:
    return {
        "preferences": prefs.model_dump(mode="json"),
        "stored": stored,
        "locales": list(runtime_of(request).settings.locales),
    }


@routes.get("/api/me/preferences", action="me.read", resource="app")
def get_preferences(request: Request) -> dict[str, Any]:
    """This person's preferences (empty until they save some)."""
    prefs, stored = _read(request)
    return _view(request, prefs, stored)


@routes.put("/api/me/preferences", action="me.write", resource="app")
def put_preferences(request: Request, body: Preferences) -> dict[str, Any]:
    """Replace this person's preferences."""
    from cartolex.project.files import atomic_write_bytes, json_bytes

    from ..errors import ApiError

    runtime = runtime_of(request)
    if body.locale is not None and body.locale not in runtime.settings.locales:
        raise ApiError.of(
            "unknown_locale", locale=body.locale, locales=list(runtime.settings.locales)
        )
    path = _file(request)
    with runtime.preferences_lock:
        if path is None:
            runtime.preferences[principal_of(request).id] = body
        else:
            atomic_write_bytes(
                path, json_bytes({"format": FORMAT, "preferences": body.model_dump(mode="json")})
            )
    return _view(request, body, True)
