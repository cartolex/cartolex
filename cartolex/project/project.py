# SPDX-License-Identifier: MIT
"""Create and open a project folder (see ``docs/format/index.md``)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .files import atomic_write_bytes, fingerprint, json_bytes, read_model, write_decision
from .layout import ProjectLayout
from .lock import ProjectLock
from .models import (
    AppStamp,
    Created,
    Identity,
    Languages,
    ParamsFile,
    ProjectFile,
    Slot,
)

__all__ = [
    "FORMAT",
    "FORMAT_MAJOR",
    "NotAProject",
    "Project",
    "UnsupportedFormat",
    "cartolex_version",
]

FORMAT = "cartolex-project/1"
FORMAT_MAJOR = 1


class NotAProject(FileNotFoundError):
    """The folder holds no ``project.json``."""


class UnsupportedFormat(ValueError):
    """The project was written in a format this cartolex does not read."""

    def __init__(self, path: Path, found: str) -> None:
        self.path, self.found = path, found
        super().__init__(
            f"{path} is in format {found!r}; this cartolex reads {FORMAT!r}"
            + (
                "; run `cartolex project upgrade` with a cartolex that knows both"
                if found.startswith("cartolex-project/")
                else ""
            )
        )


def cartolex_version() -> str:
    try:
        return version("cartolex")
    except PackageNotFoundError:  # pragma: no cover - running from a bare checkout
        return "0+unknown"


def _check_format(path: Path) -> None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: not valid JSON ({exc})") from exc
    found = raw.get("format") if isinstance(raw, dict) else None
    if not isinstance(found, str) or not found.startswith("cartolex-project/"):
        raise NotAProject(f"{path} is not a cartolex project file (format {found!r})")
    if found != FORMAT:
        raise UnsupportedFormat(path, found)


class Project:
    """An open project: its layout, its ``project.json``, and the lock when writing."""

    def __init__(self, layout: ProjectLayout, config: ProjectFile, lock: ProjectLock | None):
        self.layout = layout
        self.config = config
        self._lock = lock
        self._config_fp = fingerprint(layout.project_json)

    # ── creating ──
    @classmethod
    def init(
        cls,
        root: Path,
        *,
        name: str,
        domain_title: str,
        domain_description: str = "",
        corpus_languages: tuple[str, ...] = ("en",),
        reference_language: str = "en",
        display_languages: tuple[str, ...] | None = None,
        slots: tuple[Slot, ...] = (),
        seed: int = 0,
        app: str = "cartolex",
        now: datetime | None = None,
    ) -> Project:
        """Create a new project in *root* (an empty or missing folder) and open it for writing."""
        root = Path(root)
        layout = ProjectLayout(root)
        if layout.project_json.exists():
            raise FileExistsError(f"{root} already holds a project")
        if root.exists() and any(root.iterdir()):
            raise FileExistsError(f"{root} is not empty; a project starts in an empty folder")
        now = now or datetime.now(timezone.utc)
        version_ = cartolex_version()
        config = ProjectFile(
            name=name,
            identity=Identity(domain_title=domain_title, domain_description=domain_description),
            languages=Languages(
                corpus=list(corpus_languages),
                reference=reference_language,
                display=list(display_languages or corpus_languages),
            ),
            slots=list(slots),
            created=Created(at=now, by=f"cartolex {version_}"),
            app=AppStamp(id=app, version=version_),
        )
        for folder in layout.skeleton():
            folder.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(layout.project_json, json_bytes(config))
        atomic_write_bytes(layout.params_json, json_bytes(ParamsFile(seed=seed)))
        return cls.open(root, write=True, app=app)

    # ── opening ──
    @classmethod
    def open(cls, root: Path, *, write: bool = False, app: str = "cartolex") -> Project:
        """Open the project in *root*; take its lock when *write* is true."""
        layout = ProjectLayout(Path(root))
        if not layout.project_json.exists():
            raise NotAProject(f"{root} holds no project.json")
        _check_format(layout.project_json)
        config = read_model(layout.project_json, ProjectFile)
        lock = ProjectLock(layout, app).acquire() if write else None
        return cls(layout, config, lock)  # type: ignore[arg-type]

    def close(self) -> None:
        if self._lock is not None:
            self._lock.release()
            self._lock = None

    def __enter__(self) -> Project:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def writable(self) -> bool:
        return self._lock is not None

    # ── project.json and params.json ──
    def save_config(self, config: ProjectFile, *, action: str) -> None:
        """Replace ``project.json`` (guarded: refused if it changed since it was read)."""
        self._require_write()
        config = config.model_copy(
            update={"app": AppStamp(id=self.config.app.id, version=cartolex_version())}
        )
        self._config_fp = write_decision(
            self.layout,
            self.layout.project_json,
            json_bytes(config),
            expected=self._config_fp,
            action=action,
        )
        self.config = config

    def read_params(self) -> tuple[ParamsFile, str | None]:
        """``decisions/params.json`` and its fingerprint (defaults when the file is missing)."""
        path = self.layout.params_json
        if not path.exists():
            return ParamsFile(), None
        return read_model(path, ParamsFile), fingerprint(path)  # type: ignore[return-value]

    def save_params(self, params: ParamsFile, *, expected: str | None, action: str) -> str:
        self._require_write()
        return write_decision(
            self.layout,
            self.layout.params_json,
            json_bytes(params),
            expected=expected,
            action=action,
        )

    def _require_write(self) -> None:
        if self._lock is None:
            raise PermissionError("the project is open read-only; open it with write=True")
