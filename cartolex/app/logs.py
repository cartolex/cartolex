# SPDX-License-Identifier: MIT
"""Structured logs: one JSON object per line, with the request id, no personal data (4h).

The app logs through the standard ``logging`` module under ``cartolex.app``;
it never configures logging itself (importing or creating an app changes no
global setting). The server (``cartolex app``, ``cartolex api``) installs
:class:`JsonFormatter` with :func:`configure_logging`.

A request line holds the request id, the method, the route's **template**
(``/api/jobs/{job_id}``, never the path with its values, nor the query), the
status and the time taken. Names, texts, file paths and keys never enter a
line: the fields a line may carry are listed in :data:`FIELDS`.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import IO, Any

__all__ = ["FIELDS", "JsonFormatter", "configure_logging"]

#: The fields a log line may carry besides the time, level, logger and message.
FIELDS = (
    "event",
    "request_id",
    "method",
    "route",
    "status",
    "ms",
    "job_id",
    "kind",
    "state",
    "error",
    "mode",
    "port",
)


class JsonFormatter(logging.Formatter):
    """Formats a record as one JSON object: time, level, logger, message and :data:`FIELDS`."""

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "at": datetime.fromtimestamp(record.created, timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            ),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in FIELDS:
            value = getattr(record, name, None)
            if value is not None:
                line[name] = value
        return json.dumps(line, ensure_ascii=False)


def configure_logging(stream: IO[str] | None = None, level: int = logging.INFO) -> logging.Handler:
    """Send the app's logs (``cartolex.app``) and the server's to *stream* as JSON lines."""
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    for name in ("cartolex.app", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.setLevel(level)
        logger.propagate = False
    # The server's own access log would print raw paths and queries: the app logs requests.
    logging.getLogger("uvicorn.access").disabled = True
    return handler
