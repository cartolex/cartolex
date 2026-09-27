# SPDX-License-Identifier: MIT
"""The project format, ``cartolex-project/1``: create, open, read and write a project folder.

The format is described in ``docs/format/``. :class:`Project` opens a folder;
:class:`ProjectLayout` names every path in it; :mod:`cartolex.project.models`
validates its JSON files and :mod:`cartolex.project.tables` its tables.
"""

from .files import (
    StaleWrite,
    atomic_write_bytes,
    fingerprint,
    json_bytes,
    read_model,
    write_decision,
)
from .layout import SOURCE_TABLES, ProjectLayout
from .lock import LockHeld, ProjectLock, StaleLock, remove_stale_lock
from .models import LANGUAGES, STAGE_IDS
from .project import FORMAT, NotAProject, Project, UnsupportedFormat

__all__ = [
    "FORMAT",
    "LANGUAGES",
    "SOURCE_TABLES",
    "STAGE_IDS",
    "LockHeld",
    "NotAProject",
    "Project",
    "ProjectLayout",
    "ProjectLock",
    "StaleLock",
    "StaleWrite",
    "UnsupportedFormat",
    "atomic_write_bytes",
    "fingerprint",
    "json_bytes",
    "read_model",
    "remove_stale_lock",
    "write_decision",
]
