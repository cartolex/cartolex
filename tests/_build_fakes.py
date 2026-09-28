# SPDX-License-Identifier: MIT
"""Fake stages for the build tests: small, fast, deterministic, and easy to break on purpose.

The registry uses real stage ids with fake runners. Every runner writes a result
that depends only on what it read, and appends a line to a call log kept outside
the project, so a test can see what ran, what resumed, and compare the results
of an interrupted build with those of an uninterrupted one.

Run as a script, it opens a project and builds it, killing its own process
(``os._exit``) at a named step, as a crash would::

    python tests/_build_fakes.py PROJECT CALL_LOG STEP [--engine]

With ``--engine`` the build runs cartolex's own stages instead of the fakes.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from cartolex.build import CostModel, CrossCheck, ParamSpec, Registry, Stage
from cartolex.build.execution import StageContext
from cartolex.project import Project
from cartolex.project.tables import SOURCE_SCHEMAS, write_decision_csv, write_source_table

NOW = datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc)
YEAR = 2026
CHUNKS = 4

#: Exit status of a process killed on purpose.
KILLED = 77


@dataclass
class Controls:
    """What a test makes the fake stages do."""

    log: Path
    fail: set[str] = field(default_factory=set)
    #: stage → (started, release): the stage waits for *release* after setting *started*.
    hold: dict[str, tuple[threading.Event, threading.Event]] = field(default_factory=dict)
    #: set this event when a stage reaches a chunk: (stage id, chunk index, event).
    cancel_at: tuple[str, int, threading.Event] | None = None
    #: "stage:chunk" → os._exit inside that chunk, after writing part of it.
    kill_inside: str | None = None
    #: counts a stage reports on top of its own (to drive the size rules).
    sizes: dict[str, int] = field(default_factory=dict)
    #: progress values a stage reports, in order (to test that progress never goes back).
    progress: list[float] = field(default_factory=list)


def _log(controls: Controls, line: str) -> None:
    with open(controls.log, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _digest(*parts: object) -> str:
    return hashlib.sha256("\n".join(map(str, parts)).encode()).hexdigest()[:16]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else "-"


def _common(ctx: StageContext, controls: Controls) -> None:
    stage = ctx.stage.id
    _log(controls, stage)
    if stage in controls.hold:
        started, release = controls.hold[stage]
        started.set()
        release.wait(10)
    if stage in controls.fail:
        raise RuntimeError(f"{stage} broke on purpose")
    for value in controls.progress:
        ctx.progress(value, "working")


def _assemble(controls: Controls):
    def run(ctx: StageContext) -> Mapping[str, int]:
        _common(ctx, controls)
        texts = pq.read_table(ctx.layout.table("texts"), columns=["text_id", "title"])
        with open(ctx.layout.people_csv, encoding="utf-8", newline="") as fh:
            people = list(csv.DictReader(fh))
        mapped = [p for p in people if p["role"] == "mapped"]
        lines = [f"{t} {title}" for t, title in zip(*texts.to_pydict().values(), strict=True)]
        body = "\n".join(lines) + f"\nparts={ctx.params['parts']} year={ctx.params['year']}\n"
        (ctx.out / "corpus.txt").write_text(body, encoding="utf-8")
        counts = {
            "people": len(people),
            "mapped_units": len(mapped),
            "texts": texts.num_rows,
            "characters": sum(len(x) for x in lines),
        }
        return {**counts, **{k: v for k, v in controls.sizes.items() if k in counts}}

    return run


def _extract(controls: Controls):
    def run(ctx: StageContext) -> Mapping[str, int]:
        _common(ctx, controls)
        corpus = _read(ctx.folder("corpus.assemble") / "corpus.txt")
        stop = _read(ctx.layout.stopwords_json)
        for i in ctx.chunks(CHUNKS):
            _log(controls, f"keywords.extract:chunk:{i}")
            part = ctx.chunk_dir(i) / "part.txt"
            if controls.cancel_at and controls.cancel_at[:2] == (ctx.stage.id, i):
                controls.cancel_at[2].set()
            if controls.kill_inside == f"keywords.extract:{i}":
                part.write_text("half a chunk", encoding="utf-8")
                os._exit(KILLED)
            part.write_text(_digest(corpus, stop, ctx.params["min_people"], i), encoding="utf-8")
        parts = [(ctx.chunk_dir(i) / "part.txt").read_text() for i in range(CHUNKS)]
        (ctx.out / "candidates.txt").write_text("\n".join(parts) + "\n", encoding="utf-8")
        return {"candidates": len(parts)}

    return run


def _write_from(stage: str, name: str, *upstream: tuple[str, str], decision: str | None = None):
    def factory(controls: Controls):
        def run(ctx: StageContext) -> Mapping[str, int]:
            _common(ctx, controls)
            read = [_read(ctx.folder(up) / f) if up in ctx.upstream else "-" for up, f in upstream]
            if decision:
                read.append(_read(ctx.layout.root / decision))
            params = json.dumps(ctx.params, sort_keys=True)
            (ctx.out / name).write_text(_digest(*read, params) + "\n", encoding="utf-8")
            counts = {"items": 1}
            if stage == "keywords.build":
                counts["kept_keywords"] = controls.sizes.get("kept_keywords", 1500)
            return counts

        return run

    return factory


def _enough_groups(values, sizes, _config):
    if values["top_groups"] >= (sizes.kept_keywords or 0):
        return f"top_groups is {values['top_groups']} for {sizes.kept_keywords} keywords"
    return None


def make_registry(controls: Controls) -> Registry:
    """The fake stages, wired to *controls*."""
    return Registry(
        [
            Stage(
                "corpus.assemble",
                "gather the texts",
                decisions=("decisions/people.csv",),
                sources=("texts",),
                project=("languages",),
                params=(
                    ParamSpec(
                        "parts",
                        "list",
                        "parts read",
                        default=["title"],
                        choices=("title", "abstract"),
                    ),
                ),
                uses=("year",),
                provides=("people", "texts", "characters", "mapped_units"),
                cost=CostModel("texts", 0.1, 0.01, 10.0, 1.0),
                run=_assemble(controls),
            ),
            Stage(
                "keywords.extract",
                "find keyword candidates",
                upstream=("corpus.assemble",),
                decisions=("decisions/stopwords.json",),
                chunked=True,
                params=(ParamSpec("min_people", "int", "threshold", default=3, minimum=1),),
                cost=CostModel("characters", 0.1, 0.001, 20.0, 0.1),
                run=_extract(controls),
            ),
            Stage(
                "keywords.triage",
                "AI clean-up",
                upstream=("keywords.extract",),
                opt_in=True,
                network=True,
                paid=True,
                consent_note="sends keyword strings to a fake provider",
                run=_write_from(
                    "keywords.triage", "answers.txt", ("keywords.extract", "candidates.txt")
                )(controls),
            ),
            Stage(
                "keywords.build",
                "build the vocabulary",
                upstream=("keywords.extract", "keywords.triage"),
                decisions=("decisions/keywords.csv",),
                provides=("kept_keywords",),
                cost=CostModel("characters", 0.1, 0.001, 30.0, 0.1),
                run=_write_from(
                    "keywords.build",
                    "vocabulary.txt",
                    ("keywords.extract", "candidates.txt"),
                    ("keywords.triage", "answers.txt"),
                    decision="decisions/keywords.csv",
                )(controls),
            ),
            Stage(
                "themes.group",
                "group keywords into topics and themes",
                upstream=("keywords.build",),
                params=(
                    ParamSpec("depth", "int", "levels", rule="theme_depth", minimum=1, maximum=4),
                    ParamSpec("top_groups", "int", "top", default=15, minimum=2),
                ),
                checks=(
                    CrossCheck(
                        "fewer groups than keywords",
                        ("top_groups",),
                        ("kept_keywords",),
                        _enough_groups,
                    ),
                ),
                uses=("seed",),
                cost=CostModel("kept_keywords", 0.1, 0.001, 40.0, 0.01),
                run=_write_from("themes.group", "groups.txt", ("keywords.build", "vocabulary.txt"))(
                    controls
                ),
            ),
            Stage(
                "overlays.position",
                "place projected people",
                upstream=("themes.group",),
                project=("overlays",),
                applies=lambda config, _: None if config.overlays else "the project has no overlay",
                run=_write_from(
                    "overlays.position", "positions.txt", ("themes.group", "groups.txt")
                )(controls),
            ),
        ]
    )


RESULT_FILES = {
    "corpus.assemble": "corpus.txt",
    "keywords.extract": "candidates.txt",
    "keywords.triage": "answers.txt",
    "keywords.build": "vocabulary.txt",
    "themes.group": "groups.txt",
    "overlays.position": "positions.txt",
}


def results(project_root: Path) -> dict[str, str]:
    """The result file of every stage that has one."""
    derived = Path(project_root) / "derived"
    return {
        stage: (derived / stage / name).read_text(encoding="utf-8")
        for stage, name in RESULT_FILES.items()
        if (derived / stage / name).exists()
    }


def _texts_table(n: int) -> pa.Table:
    rows = [
        {
            "text_id": f"t{i:04d}",
            "slot": "collected",
            "position": i,
            "year": 2020 + i % 5,
            "date": None,
            "doc_type": "article",
            "title": f"Survey of tidal channel sample {i}",
            "doi": None,
            "ids": [],
            "version_of": None,
            "n_authors": 2,
            "source": "import",
            "retrieved_at": NOW,
        }
        for i in range(n)
    ]
    schema = SOURCE_SCHEMAS["texts"]
    return pa.table({f.name: [r[f.name] for r in rows] for f in schema}, schema=schema)


def make_project(root: Path, *, n_texts: int = 5, n_people: int = 4) -> Project:
    """A new project, open for writing, with a few texts and people."""
    project = Project.init(
        root,
        name="Build test",
        domain_title="Coastal and marine systems",
        corpus_languages=("en",),
        seed=7,
        now=NOW,
    )
    write_source_table(project.layout.table("texts"), "texts", _texts_table(n_texts))
    write_decision_csv(
        project.layout.people_csv,
        "people",
        [{"person_id": f"p{i:03d}", "role": "mapped"} for i in range(n_people)],
    )
    return project


def set_texts(project: Project, n: int) -> None:
    """Replace the texts table with *n* texts."""
    write_source_table(project.layout.table("texts"), "texts", _texts_table(n))


def main(argv: list[str]) -> int:
    """Build the project in ``argv[0]``, dying at step ``argv[2]`` (``name`` or ``name#n``)."""
    root, log, step = Path(argv[0]), Path(argv[1]), argv[2]
    from cartolex.build import build

    os.fsync = lambda fd: None  # a killed process loses nothing a flush would keep
    controls = Controls(log=log)
    if step.startswith("inside:"):
        controls.kill_inside = step.removeprefix("inside:")
    name, _, nth = step.partition("#")
    seen = {"n": 0}

    def probe(current: str) -> None:
        if current == name:
            seen["n"] += 1
            if seen["n"] == int(nth or 1):
                os._exit(KILLED)

    registry = make_registry(controls)
    if "--engine" in argv:  # cartolex's own stages, running the engine
        from cartolex.build import STAGES

        registry = STAGES
    project = Project.open(root, write=True)
    build(project, registry=registry, year=YEAR, probe=probe, budget_mb=1e9)
    project.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
