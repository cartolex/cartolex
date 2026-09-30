# SPDX-License-Identifier: MIT
"""Every path of a project folder, named once (see ``docs/format/index.md``).

Nothing else in cartolex joins a project's path parts: code asks a
:class:`ProjectLayout` for the file it reads or writes. Building a layout
touches nothing on disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import STAGE_IDS

__all__ = ["SOURCE_TABLES", "ProjectLayout"]

#: The data-model tables of ``sources/tables/`` (see ``docs/format/sources.md``).
SOURCE_TABLES = (
    "texts",
    "text_parts",
    "people",
    "organisations",
    "affiliations",
    "authorships",
)


@dataclass(frozen=True)
class ProjectLayout:
    """The paths of the project rooted at :attr:`root`."""

    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root))

    # ── top level ──
    @property
    def project_json(self) -> Path:
        return self.root / "project.json"

    @property
    def lock(self) -> Path:
        return self.root / ".lock"

    @property
    def sources(self) -> Path:
        return self.root / "sources"

    @property
    def decisions(self) -> Path:
        return self.root / "decisions"

    @property
    def derived(self) -> Path:
        return self.root / "derived"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def outputs(self) -> Path:
        return self.root / "outputs"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    # ── sources ──
    @property
    def tables(self) -> Path:
        return self.sources / "tables"

    def table(self, name: str) -> Path:
        """The Parquet file of the data-model table *name*."""
        if name not in SOURCE_TABLES:
            raise KeyError(f"unknown source table {name!r}; known: {list(SOURCE_TABLES)}")
        return self.tables / f"{name}.parquet"

    def slot(self, slot_id: str) -> Path:
        """A slot's own folder: its raw material."""
        return self.sources / slot_id

    def base(self, base_id: str) -> Path:
        """The copy of a base's map bundle."""
        return self.sources / "bases" / base_id

    # ── decisions ──
    @property
    def people_csv(self) -> Path:
        return self.decisions / "people.csv"

    @property
    def organisations_csv(self) -> Path:
        return self.decisions / "organisations.csv"

    @property
    def affiliations_csv(self) -> Path:
        return self.decisions / "affiliations.csv"

    @property
    def params_json(self) -> Path:
        return self.decisions / "params.json"

    @property
    def keywords_csv(self) -> Path:
        return self.decisions / "keywords.csv"

    @property
    def themes_json(self) -> Path:
        return self.decisions / "themes.json"

    @property
    def maps_json(self) -> Path:
        return self.decisions / "maps.json"

    @property
    def snowball_csv(self) -> Path:
        return self.decisions / "snowball.csv"

    @property
    def stopwords_json(self) -> Path:
        return self.decisions / "stopwords.json"

    @property
    def prompts(self) -> Path:
        return self.decisions / "prompts"

    @property
    def curation_notes_md(self) -> Path:
        """The curator's notes and standing rules for the AI copilot (Markdown)."""
        return self.decisions / "curation-notes.md"

    @property
    def history(self) -> Path:
        return self.decisions / "history"

    def history_of(self, path: Path) -> Path:
        """The history folder of a decision file (``project.json`` included)."""
        rel = Path(path).resolve().relative_to(self.root.resolve())
        parts = rel.parts[1:] if rel.parts[0] == "decisions" else rel.parts
        return self.history.joinpath(*parts)

    # ── derived ──
    def stage(self, stage_id: str) -> Path:
        """The current results of a stage."""
        return self.derived / _known(stage_id)

    @property
    def staging_root(self) -> Path:
        """The folder of every staging folder."""
        return self.derived / ".staging"

    def staging(self, stage_id: str, run_id: str) -> Path:
        """Where a running stage writes, until it succeeds."""
        return self.staging_root / f"{_known(stage_id)}.{run_id}"

    def previous(self, stage_id: str) -> Path:
        """The generation a successful run replaced, kept to put back."""
        return self.derived / ".previous" / _known(stage_id)

    def attempt(self, stage_id: str) -> Path:
        """The record of a stage's last failed or cancelled attempt."""
        return self.derived / ".attempts" / f"{_known(stage_id)}.json"

    @property
    def journal(self) -> Path:
        """The record of a swap in progress."""
        return self.derived / ".journal.json"

    def run_json(self, stage_id: str) -> Path:
        return self.stage(stage_id) / "run.json"

    # ── cache, outputs, logs ──
    @property
    def cache_ai(self) -> Path:
        return self.cache / "ai"

    @property
    def cache_parse(self) -> Path:
        return self.cache / "parse"

    @property
    def cache_http(self) -> Path:
        return self.cache / "http"

    @property
    def jobs(self) -> Path:
        return self.logs / "jobs"

    def skeleton(self) -> tuple[Path, ...]:
        """The folders a new project starts with."""
        return (
            self.tables,
            self.decisions,
            self.prompts,
            self.history,
            self.derived,
            self.cache_ai,
            self.cache_parse,
            self.cache_http,
            self.outputs,
            self.jobs,
        )


def _known(stage_id: str) -> str:
    if stage_id not in STAGE_IDS:
        raise KeyError(f"unknown stage id {stage_id!r}; known: {list(STAGE_IDS)}")
    return stage_id
