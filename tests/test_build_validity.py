# SPDX-License-Identifier: MIT
"""The six states of a stage, their transitions and reasons, fingerprints and run records."""

from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from _build_fakes import YEAR, Controls, make_project, make_registry, set_texts

from cartolex.build import (
    Registry,
    StageState,
    build,
    code_fingerprint,
    new_run_id,
    status,
    table_fingerprint,
)
from cartolex.build import fingerprints as fp_module
from cartolex.project import Project
from cartolex.project.models import RUN_ID_PATTERN, AIIdentity, ParamsFile, RunRecord
from cartolex.project.tables import write_decision_csv

NEVER, OK, STALE = StageState.NEVER_BUILT, StageState.UP_TO_DATE, StageState.NEEDS_UPDATE
RUNNING, FAILED, SKIPPED = StageState.RUNNING, StageState.FAILED, StageState.SKIPPED


@pytest.fixture(autouse=True)
def _no_fsync(monkeypatch):
    """A killed process loses nothing a disk flush would keep: skip the flushes, stay fast."""
    monkeypatch.setattr(os, "fsync", lambda fd: None)


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.project = make_project(tmp_path / "proj")
        self.layout = self.project.layout
        self.controls = Controls(log=tmp_path / "calls.log")
        self.registry: Registry = make_registry(self.controls)

    def states(self, year: int = YEAR) -> dict[str, StageState]:
        return {k: v.state for k, v in status(self.project, self.registry, year=year).items()}

    def reasons(self, stage: str, year: int = YEAR) -> list[str]:
        return [str(r) for r in status(self.project, self.registry, year=year)[stage].reasons]

    def build(self, **kw):
        kw.setdefault("year", YEAR)
        kw.setdefault("budget_mb", 1e9)
        return build(self.project, registry=self.registry, **kw)

    def calls(self) -> list[str]:
        text = self.controls.log.read_text() if self.controls.log.exists() else ""
        self.controls.log.unlink(missing_ok=True)
        return [line for line in text.splitlines() if ":chunk:" not in line]

    def set_params(self, **stages) -> None:
        params, fp = self.project.read_params()
        params = params.model_copy(update={"stages": {**params.stages, **stages}})
        self.project.save_params(params, expected=fp, action="test")


@pytest.fixture()
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.project.close()


def test_the_six_states_and_their_transitions(env):
    assert env.states() == {
        "corpus.assemble": NEVER,
        "keywords.extract": NEVER,
        "keywords.triage": SKIPPED,
        "keywords.build": NEVER,
        "themes.group": NEVER,
        "overlays.position": SKIPPED,
    }
    assert env.build().outcome == "succeeded"
    assert set(env.states().values()) == {OK, SKIPPED}
    env.calls()

    # A decision changes: its stage and everything after it need an update.
    write_decision_csv(
        env.layout.keywords_csv,
        "keywords",
        [{"term": "tidal channel", "language": "en", "decision": "keep"}],
    )
    states = env.states()
    assert states["keywords.extract"] is OK and states["keywords.build"] is STALE
    assert env.reasons("keywords.build") == ["decisions/keywords.csv was added"]
    assert env.reasons("themes.group") == ["keywords.build needs an update"]
    env.build()
    assert env.calls() == ["keywords.build", "themes.group"]
    assert set(env.states().values()) == {OK, SKIPPED}

    # Running: a job holds the stage now.
    started, release = threading.Event(), threading.Event()
    env.controls.hold["themes.group"] = (started, release)
    job = threading.Thread(target=env.build, kwargs={"force": ["themes.group"]})
    job.start()
    assert started.wait(10)
    try:
        running = status(env.project, env.registry, year=YEAR)["themes.group"]
        assert running.state is RUNNING and running.running is not None
        reader = Project.open(env.layout.root)  # another reader sees it too
        assert status(reader, env.registry, year=YEAR)["themes.group"].state is RUNNING
    finally:
        release.set()
        job.join()
    del env.controls.hold["themes.group"]
    assert env.states()["themes.group"] is OK

    # Failed: the previous results stay in place and usable.
    before = (env.layout.stage("keywords.build") / "vocabulary.txt").read_text()
    env.controls.fail.add("keywords.build")
    result = env.build(force=["keywords.build"])
    assert result.outcome == "failed" and result.failed[0] == "keywords.build"
    failed = status(env.project, env.registry, year=YEAR)["keywords.build"]
    assert failed.state is FAILED and failed.has_results
    assert "broke on purpose" in failed.attempt.error
    assert (env.layout.stage("keywords.build") / "vocabulary.txt").read_text() == before
    assert env.reasons("themes.group") == ["keywords.build failed and will run again"]
    env.controls.fail.clear()
    env.build()
    assert set(env.states().values()) == {OK, SKIPPED}
    assert not env.layout.attempt("keywords.build").exists()

    # Skipped: an opt-in stage switched on, then off again.
    env.project.save_config(
        env.project.config.model_copy(
            update={
                "identity": env.project.config.identity.model_copy(
                    update={"ai": AIIdentity(provider="fake", model="fake-small")}
                )
            }
        ),
        action="test",
    )
    env.set_params(**{"keywords.triage": {"enabled": True}})
    states = env.states()
    assert states["keywords.triage"] is NEVER and states["keywords.build"] is STALE
    assert env.reasons("keywords.build") == ["keywords.triage has no results"]
    env.build(consent=lambda request: True)
    assert env.states()["keywords.triage"] is OK
    env.set_params(**{"keywords.triage": {"enabled": False}})
    assert env.states()["keywords.triage"] is SKIPPED
    assert env.reasons("keywords.build") == ["keywords.triage is now skipped"]


def test_needs_update_says_what_changed(env):
    env.controls.sizes.update(mapped_units=1_000, kept_keywords=1_500)
    env.build()
    assert set(env.states().values()) == {OK, SKIPPED}

    env.set_params(**{"keywords.extract": {"min_people": 2}})
    assert env.reasons("keywords.extract") == ["parameter min_people: 3 → 2 (from params.json)"]
    env.build()

    params, fp = env.project.read_params()
    env.project.save_params(
        params.model_copy(update={"pinned_year": 2020}), expected=fp, action="t"
    )
    assert env.reasons("corpus.assemble") == ["parameter year: 2026 → 2020 (from params.json)"]
    params, fp = env.project.read_params()
    env.project.save_params(
        params.model_copy(update={"pinned_year": None}), expected=fp, action="t"
    )
    assert env.states()["corpus.assemble"] is OK
    assert env.reasons("corpus.assemble", year=YEAR + 1) == [
        "parameter year: 2026 → 2027 (default)"
    ]

    set_texts(env.project, 6)
    assert env.reasons("corpus.assemble") == ["sources/tables/texts.parquet changed"]
    env.build()

    config = env.project.config
    env.project.save_config(
        config.model_copy(
            update={"languages": config.languages.model_copy(update={"display": ["en", "fr"]})}
        ),
        action="test",
    )
    assert env.reasons("corpus.assemble") == ["project.json: languages changed"]
    env.build()

    # A size a rule depends on changes: the rule's value moves, and the reason names it.
    env.controls.sizes["kept_keywords"] = 15_000
    env.build(force=["keywords.build"])  # themes.group follows its upstream
    group = status(env.project, env.registry, year=YEAR)["themes.group"]
    assert group.state is OK
    assert group.record.parameters["depth"].value == 3
    assert group.record.parameters["depth"].rule == "theme_depth"

    env.layout.stopwords_json.write_text('{"format": "cartolex-stopwords/1"}', encoding="utf-8")
    assert env.reasons("keywords.extract") == ["decisions/stopwords.json was added"]
    env.build()
    env.layout.stopwords_json.unlink()
    assert env.reasons("keywords.extract") == ["decisions/stopwords.json was removed"]


def test_file_dates_never_decide(env, tmp_path):
    env.build()
    for path in env.layout.root.rglob("*"):
        if path.is_file():
            os.utime(path, (1_000_000_000, 1_000_000_000))
    assert set(env.states().values()) == {OK, SKIPPED}
    env.project.close()
    copy = tmp_path / "copy"
    shutil.copytree(env.layout.root, copy, copy_function=shutil.copyfile)  # new dates
    moved = Project.open(copy)
    assert {s.state for s in status(moved, env.registry, year=YEAR).values()} == {OK, SKIPPED}
    env.project = Project.open(env.layout.root, write=True)


def test_run_records_hold_what_the_run_read(env):
    env.build()
    raw = json.loads(env.layout.run_json("keywords.extract").read_text())
    record = RunRecord.model_validate(raw)
    assert RUN_ID_PATTERN.match(record.run_id) and record.outcome == "succeeded"
    assert "error" not in raw
    assert record.parameters["min_people"].value == 3
    assert record.parameters["min_people"].source == "default"
    upstream = json.loads(env.layout.run_json("corpus.assemble").read_text())
    stage_inputs = [i for i in record.inputs if i.kind == "stage"]
    assert [(i.stage, i.run_id) for i in stage_inputs] == [("corpus.assemble", upstream["run_id"])]
    assert record.code.fingerprint == code_fingerprint()
    assert record.measures.seconds is not None and record.measures.seconds >= 0
    assert record.measures.peak_memory_mb and record.measures.peak_memory_mb > 0
    assert record.measures.counts["candidates"] == 4
    assert record.measures.counts["characters"] > 0  # its cost driver, for later estimates
    corpus = RunRecord.model_validate(upstream)
    files = {i.path: i.fingerprint for i in corpus.inputs if i.kind != "stage"}
    assert set(files) == {"sources/tables/texts.parquet", "decisions/people.csv"}
    assert corpus.identity == {"languages": env.project.config.languages.model_dump(mode="json")}
    assert corpus.parameters["year"].value == YEAR
    assert not any(p.name == "run.json" for p in env.layout.staging_root.glob("*"))


def test_run_ids_sort_by_time():
    first, second = new_run_id(), new_run_id()
    assert RUN_ID_PATTERN.match(first) and first[:16] <= second[:16]
    assert first != second


def test_large_tables_are_fingerprinted_by_their_footer(tmp_path, monkeypatch):
    path = tmp_path / "t.parquet"
    table = pa.table({"id": [f"t{i}" for i in range(1000)], "n": list(range(1000))})
    pq.write_table(table, path, row_group_size=100)
    whole = table_fingerprint(path)
    monkeypatch.setattr(fp_module, "TABLE_FULL_HASH_LIMIT", 0)
    footer = table_fingerprint(path)
    assert footer != whole and footer.startswith("sha256:")
    pq.write_table(table, path, row_group_size=100)
    assert table_fingerprint(path) == footer  # the same table written again
    changed = table.set_column(1, "n", pa.array([*range(999), 5000]))
    pq.write_table(changed, path, row_group_size=100)
    assert table_fingerprint(path) != footer  # a value moved a row group's statistics
    assert table_fingerprint(tmp_path / "missing.parquet") is None
    (tmp_path / "bad.parquet").write_bytes(b"not a table at all")
    assert table_fingerprint(tmp_path / "bad.parquet") is not None


def test_the_code_fingerprint_is_computed_once():
    assert code_fingerprint() is code_fingerprint()
    assert code_fingerprint().startswith("sha256:")


def test_an_invalid_params_file_is_refused_with_its_reason(env):
    params = ParamsFile(stages={"keywords.extract": {"min_df": 2}})
    env.layout.params_json.write_text(params.model_dump_json(), encoding="utf-8")
    from cartolex.build import ParamsError

    with pytest.raises(ParamsError, match="unknown parameter 'min_df'"):
        status(env.project, env.registry)
    with pytest.raises(ParamsError):
        env.build()


def test_sizes_come_from_the_records_else_from_the_sources(env):
    from datetime import datetime, timezone

    from cartolex.build.validity import current_sizes
    from cartolex.project.tables import SOURCE_SCHEMAS, write_source_table

    sizes = current_sizes(env.project, env.registry, {})
    assert (sizes.people, sizes.texts, sizes.mapped_units) == (4, 5, 4)
    assert sizes.characters is None and sizes.kept_keywords is None
    write_decision_csv(
        env.layout.people_csv,
        "people",
        [
            {"person_id": "p1", "role": "mapped"},
            {"person_id": "p2", "role": "context"},
            {"person_id": "p3", "role": "projected"},
        ],
    )
    content = ["Tidal flow over a sand bar.", "Salt marsh growth in a small estuary."]
    schema = SOURCE_SCHEMAS["text_parts"]
    parts = pa.table(
        {
            "text_id": ["t0000", "t0001"],
            "part": ["abstract", "abstract"],
            "language": ["en", "en"],
            "provider": ["import", "import"],
            "format": ["plain", "plain"],
            "content": content,
            "retrieved_at": [datetime(2026, 9, 1, tzinfo=timezone.utc)] * 2,
        },
        schema=schema,
    )
    write_source_table(env.layout.table("text_parts"), "text_parts", parts)
    sizes = current_sizes(env.project, env.registry, {})
    assert (sizes.people, sizes.mapped_units) == (2, 1)
    assert sizes.characters and sizes.characters >= sum(len(c) for c in content)
    env.controls.sizes.update(kept_keywords=1_234)
    env.build()
    records = {s: v.record for s, v in status(env.project, env.registry, year=YEAR).items()}
    sizes = current_sizes(env.project, env.registry, records)
    assert sizes.kept_keywords == 1_234 and sizes.people == 3  # the stages' own counts


def test_a_new_stage_version_needs_an_update(env):
    env.build()
    record = json.loads(env.layout.run_json("keywords.extract").read_text())
    before = env.registry["keywords.extract"].version
    assert record["code"]["stage_version"] == before
    env.registry = env.registry.replace("keywords.extract", version=before + 1)
    assert env.reasons("keywords.extract") == [
        f"cartolex changed how this stage works (version {before} → {before + 1})"
    ]
    assert env.reasons("keywords.build") == ["keywords.extract needs an update"]
    assert env.states()["corpus.assemble"] is OK
    env.build()
    assert set(env.states().values()) == {OK, SKIPPED}
    record = json.loads(env.layout.run_json("keywords.extract").read_text())
    assert record["code"]["stage_version"] == before + 1
    with pytest.raises(ValueError, match="whole number"):
        env.registry.replace("keywords.extract", version=0)
