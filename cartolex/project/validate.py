# SPDX-License-Identifier: MIT
"""Check a whole project folder against the format; list every problem found.

:func:`validate_project` reads every file the format defines that exists —
``project.json``, the JSON decision files, the source tables, the CSV
decisions — and reports what breaks its rules, one line each, without stopping
at the first. It also checks that references hold across files: people in
``people.csv`` exist in the sources, a projected person's set is declared, a
pinned map version's base exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .files import read_model
from .layout import SOURCE_TABLES, ProjectLayout
from .models import LANGUAGES, MapsFile, ParamsFile, ProjectFile, StopwordsFile, ThemesFile
from .project import NotAProject, UnsupportedFormat, _check_format
from .tables import DECISION_TABLES, read_decision_csv, read_source_table

__all__ = ["Problem", "validate_project"]


@dataclass(frozen=True)
class Problem:
    path: str  # relative to the project
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def validate_project(root: Path) -> list[Problem]:
    """Every problem found in the project at *root* (empty when it is valid)."""
    layout = ProjectLayout(Path(root))
    problems: list[Problem] = []

    def rel(p: Path) -> str:
        try:
            return p.relative_to(layout.root).as_posix()
        except ValueError:
            return str(p)

    def note(p: Path, exc: Exception) -> None:
        text = str(exc)
        prefix = f"{p}: "
        problems.append(Problem(rel(p), text[len(prefix) :] if text.startswith(prefix) else text))

    try:
        _check_format(layout.project_json)
        config = read_model(layout.project_json, ProjectFile)
    except FileNotFoundError:
        return [Problem("project.json", "missing: this folder is not a cartolex project")]
    except (NotAProject, UnsupportedFormat, ValueError) as exc:
        return [Problem("project.json", str(exc))]
    assert isinstance(config, ProjectFile)
    for lang in config.languages.corpus:
        if lang not in LANGUAGES:
            problems.append(
                Problem(
                    "project.json",
                    f"no language pack for {lang!r}: cartolex extracts {', '.join(LANGUAGES)}",
                )
            )

    for path, model in (
        (layout.params_json, ParamsFile),
        (layout.themes_json, ThemesFile),
        (layout.maps_json, MapsFile),
        (layout.stopwords_json, StopwordsFile),
    ):
        if path.exists():
            try:
                read_model(path, model)
            except ValueError as exc:
                note(path, exc)
    if layout.maps_json.exists():
        try:
            maps = read_model(layout.maps_json, MapsFile)
            bases = {b.id for b in config.bases}
            for v in maps.versions:  # type: ignore[attr-defined]
                if v.base is not None and v.base not in bases:
                    problems.append(
                        Problem(
                            rel(layout.maps_json),
                            f"version {v.id!r} names an unknown base {v.base!r}",
                        )
                    )
        except ValueError:
            pass

    person_ids: set[str] | None = None
    for name in SOURCE_TABLES:
        path = layout.table(name)
        if not path.exists():
            continue
        try:
            table = read_source_table(path, name)
        except Exception as exc:  # TableError or an unreadable Parquet file
            note(path, exc)
            continue
        if name == "people":
            person_ids = set(table["person_id"].to_pylist())
        if name == "texts":
            slots = {s.id for s in config.slots}
            unknown = sorted(set(table["slot"].to_pylist()) - slots)
            if unknown:
                problems.append(Problem(rel(path), f"texts name undeclared slot(s) {unknown}"))

    overlays = {o.id for o in config.overlays}
    for name in DECISION_TABLES:
        path = getattr(layout, f"{name}_csv")
        if not path.exists():
            continue
        try:
            rows = read_decision_csv(path, name)
        except ValueError as exc:
            note(path, exc)
            continue
        if name == "people":
            for row in rows:
                if person_ids is not None and row["person_id"] not in person_ids:
                    problems.append(Problem(rel(path), f"unknown person {row['person_id']!r}"))
                if row["role"] == "projected" and row["set"] not in overlays:
                    problems.append(
                        Problem(
                            rel(path),
                            f"{row['person_id']!r} is projected into an undeclared set {row['set']!r}",
                        )
                    )
    return problems
