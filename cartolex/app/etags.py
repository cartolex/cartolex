# SPDX-License-Identifier: MIT
"""Versions of decision files as HTTP entity tags (4f: concurrent edits).

A decision file's version is its fingerprint (``sha256:<hex>``), the one the
guarded writer checks (:func:`cartolex.project.write_decision`); a file that
does not exist yet has the version ``none``. A read answers with the version
in ``ETag``; a write must send the version it read in ``If-Match``. When the
file changed in between, the write is refused with **412** and the current
version, so the interface can offer « reload and merge ». A write without
``If-Match`` gets **428**.
"""

from __future__ import annotations

from fastapi import Request

from .errors import ApiError

__all__ = ["ABSENT", "etag_of", "expected_version", "version_of"]

#: The version of a decision file that does not exist yet.
ABSENT = "none"


def version_of(fingerprint: str | None) -> str:
    """The version of a file: its fingerprint, or :data:`ABSENT`."""
    return fingerprint or ABSENT


def etag_of(fingerprint: str | None) -> str:
    """The ``ETag`` header value of a version (a strong tag)."""
    return f'"{version_of(fingerprint)}"'


def expected_version(request: Request, *, header: str = "If-Match") -> str | None:
    """The fingerprint the client read, from ``If-Match`` (``None``: it read no file).

    Raises 428 when the header is missing or is ``*``, and 400 when it names
    more than one version.
    """
    raw = (request.headers.get(header) or "").strip()
    if not raw or raw == "*":
        raise ApiError(
            428,
            "version_required",
            "this change needs the version you read: send it in If-Match (the ETag of the read)",
            next_action="reload",
        )
    if "," in raw:
        raise ApiError(400, "invalid", "If-Match names one version", next_action="reload")
    value = raw.removeprefix("W/").strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    return None if value == ABSENT else value
