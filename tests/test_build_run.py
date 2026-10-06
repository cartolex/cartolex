# SPDX-License-Identifier: MIT
"""Running a build: the dry run, consent, the memory budget, progress, cancel, kills and resumes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from _build_fakes import (
    KILLED,
    YEAR,
    Controls,
    make_project,
    make_registry,
    results,
    set_texts,
)

from cartolex.build import (
    STAGES,
    BuildBusy,
    CostModel,
    Progress,
    ProjectSizes,
    StageState,
    available_memory_mb,
    build,
    plan,
    status,
)
from cartolex.build.machine import resident_memory_mb
from cartolex.project import Project
from cartolex.project.models import AIIdentity, Measures, RunRecord
from cartolex.project.tables import write_decision_csv

FAKES = Path(__file__).with_name("_build_fakes.py")


@pytest.fixture(autouse=True)
def _no_fsync(monkeypatch):
    """A killed process loses nothing a disk flush would keep: skip the flushes, stay fast."""
    monkeypatch.setattr(os, "fsync", lambda fd: None)


class Env:
    def __init__(self, root: Path, log: Path) -> None:
        self.project = make_project(root)
        self.layout = self.project.layout
        self.controls = Controls(log=log)
        self.registry = make_registry(self.controls)

    def plan(self, **kw):
        kw.setdefault("year", YEAR)
        kw.setdefault("budget_mb", 1e9)
        return plan(self.project, registry=self.registry, **kw)

    def build(self, **kw):
        kw.setdefault("year", YEAR)
        kw.setdefault("budget_mb", 1e9)
        return build(self.project, registry=self.registry, **kw)

    def calls(self) -> list[str]:
        text = self.controls.log.read_text() if self.controls.log.exists() else ""
        self.controls.log.unlink(missing_ok=True)
        return text.splitlines()

    def states(self) -> dict[str, StageState]:
        return {k: v.state for k, v in status(self.project, self.registry, year=YEAR).items()}

    def enable_triage(self) -> None:
        config = self.project.config
        identity = config.identity.model_copy(update={"ai": AIIdentity(provider="f", model="m")})
        self.project.save_config(config.model_copy(update={"identity": identity}), action="t")
        params, fp = self.project.read_params()
        stages = {**params.stages, "keywords.triage": {"enabled": True}}
        self.project.save_params(
            params.model_copy(update={"stages": stages}), expected=fp, action="t"
        )


@pytest.fixture()
def env(tmp_path):
    e = Env(tmp_path / "proj", tmp_path / "calls.log")
    yield e
    e.project.close()


def _stages_called(calls: list[str]) -> list[str]:
    return [c for c in calls if ":chunk:" not in c]


# ── the dry run ──────────────────────────────────────────────────────────────


def test_the_dry_run_and_the_run_agree(env):
    def check(everything: bool = True, consent=None, **kw):
        planned = env.plan(**kw)
        result = env.build(consent=consent, **kw)
        assert result.ran_ids == planned.to_run
        assert _stages_called(env.calls()) == list(planned.to_run)
        assert result.plan.to_run == planned.to_run
        assert result.plan.to_keep == planned.to_keep and result.plan.to_skip == planned.to_skip
        if everything:
            assert set(env.states().values()) <= {StageState.UP_TO_DATE, StageState.SKIPPED}
        return planned

    first = check()
    assert first.to_run == ("corpus.assemble", "keywords.extract", "keywords.build", "themes.group")
    assert first.to_skip == ("keywords.triage", "overlays.position")
    assert check().to_run == ()
    write_decision_csv(env.layout.keywords_csv, "keywords", [{"term": "tide", "decision": "keep"}])
    assert check().to_run == ("keywords.build", "themes.group")
    assert check(force=["keywords.extract"]).to_run == (
        "keywords.extract",
        "keywords.build",
        "themes.group",
    )
    env.layout.stopwords_json.write_text('{"format": "cartolex-stopwords/1"}', encoding="utf-8")
    targeted = check(False, targets=["keywords.build"])
    assert targeted.to_run == ("keywords.extract", "keywords.build")
    assert [i.stage for i in targeted.items] == [
        "corpus.assemble",
        "keywords.extract",
        "keywords.triage",
        "keywords.build",
    ]
    assert env.plan().to_run == ("themes.group",)  # left behind by the targeted build
    check()
    env.enable_triage()
    triage = check(consent=lambda request: True)
    assert triage.to_run == ("keywords.triage", "keywords.build", "themes.group")
    assert triage.item("keywords.triage").needs_consent


def test_the_plan_says_why_in_words(env):
    env.build()
    set_texts(env.project, 6)
    text = env.plan().describe()
    assert text.startswith("4 stage(s) to run, 0 up to date, 2 skipped")
    assert "sources/tables/texts.parquet changed" in text
    assert "corpus.assemble needs an update" in text
    item = env.plan().item("corpus.assemble")
    assert item.estimate is not None and item.estimate.basis.startswith("the last run")
    assert item.parameters == {"year": YEAR, "parts": ["title"]}


# ── consent and budget ───────────────────────────────────────────────────────


def test_consent_is_asked_before_anything_runs_and_a_refusal_skips_the_stage(env):
    env.enable_triage()
    asked = []

    def refuse(request):
        asked.append((request, env.controls.log.exists()))
        return False

    result = env.build(consent=refuse)
    ((request, anything_ran),) = asked
    assert not anything_ran
    assert request.stage == "keywords.triage" and request.paid and request.network
    assert "keyword strings" in str(request)
    # the opt-in AI clean-up is skipped, as when switched off: the later stages run
    assert result.outcome == "succeeded" and result.refused == {}
    assert result.ran_ids == (
        "corpus.assemble", "keywords.extract", "keywords.build", "themes.group",
    )  # fmt: skip
    assert "keywords.triage" in result.plan.to_skip
    assert "no consent" in result.plan.item("keywords.triage").reasons[0]
    assert env.states()["keywords.triage"] is StageState.NEVER_BUILT  # asked again next time
    again = env.build()  # no callback: no consent, skipped again, nothing else to run
    assert again.ran_ids == () and "keywords.triage" in again.plan.to_skip


def test_a_stage_over_the_memory_budget_is_refused_unless_allowed(env):
    # corpus.assemble: 10 MB + 1 MB per text, 5 texts: about 15 MB.
    planned = env.plan(budget_mb=12)
    assert planned.item("corpus.assemble").over_budget
    assert "the budget is 12 MB" in planned.item("corpus.assemble").blocked
    assert planned.item("keywords.extract").blocked.startswith("depends on corpus.assemble")
    assert planned.to_run == ()
    assert "CANNOT RUN" in planned.describe()
    refused = env.build(budget_mb=12)
    assert refused.ran == () and set(refused.refused) == set(planned.blocked)
    assert not env.layout.stage("corpus.assemble").exists()
    allowed = env.build(budget_mb=12, allow_over_budget=["corpus.assemble"])
    assert allowed.ran_ids == (
        "corpus.assemble",
        "keywords.extract",
        "keywords.build",
        "themes.group",
    )


def test_the_budget_defaults_to_the_available_memory(env):
    available = available_memory_mb()
    if sys.platform.startswith("linux"):
        assert available and available > 0
    budget = plan(env.project, registry=env.registry, year=YEAR).budget_mb
    if available is None:
        assert budget is None
    else:
        assert budget == pytest.approx(available + resident_memory_mb(), rel=0.5)
        if sys.platform.startswith("linux"):
            assert resident_memory_mb() > 0


def test_an_impossible_parameter_blocks_its_stage_with_the_reason(env):
    env.controls.sizes["kept_keywords"] = 10
    env.build()
    params, fp = env.project.read_params()
    stages = {"themes.group": {"top_groups": 10}}
    env.project.save_params(params.model_copy(update={"stages": stages}), expected=fp, action="t")
    planned = env.plan()
    assert "top_groups is 10 for 10 keywords" in planned.item("themes.group").blocked
    result = env.build()
    assert result.ran == () and "themes.group" in result.refused


def test_a_cross_check_waits_for_sizes_an_upstream_stage_will_report_again(env):
    env.controls.sizes["kept_keywords"] = 10
    env.build()
    params, fp = env.project.read_params()
    stages = {"themes.group": {"top_groups": 12}}
    env.project.save_params(params.model_copy(update={"stages": stages}), expected=fp, action="t")
    assert env.plan().item("themes.group").blocked  # 12 groups for 10 keywords
    env.controls.sizes["kept_keywords"] = 500  # the next vocabulary is larger
    planned = env.plan(force=["keywords.build"])
    assert planned.item("themes.group").blocked is None  # judged when keywords.build has run
    result = env.build(force=["keywords.build"])
    assert result.ran_ids == ("keywords.build", "themes.group")
    env.controls.sizes["kept_keywords"] = 8
    result = env.build(force=["keywords.build"])
    assert result.outcome == "failed" and result.ran_ids == ("keywords.build",)
    assert "top_groups is 12 for 8 keywords" in result.failed[1]
    attempt = status(env.project, env.registry, year=YEAR)["themes.group"].attempt
    assert attempt.outcome == "failed" and "top_groups is 12" in attempt.error


def test_estimates_scale_the_last_run():
    model = CostModel("texts", 1.0, 0.5, 100.0, 2.0)
    assert model.estimate(ProjectSizes(texts=10), None).seconds == 6.0
    last = RunRecord.model_validate(
        {
            "stage": "corpus.assemble",
            "run_id": "20260928T100000Z-aaaa",
            "outcome": "succeeded",
            "started_at": "2026-09-28T10:00:00Z",
            "code": {"version": "1", "fingerprint": "sha256:" + "0" * 64},
        }
    )
    last.measures = Measures(seconds=21.0, peak_memory_mb=300.0, counts={"texts": 100})
    scaled = model.estimate(ProjectSizes(texts=200), last)
    assert scaled.seconds == pytest.approx(41.0) and scaled.peak_memory_mb == pytest.approx(500.0)
    assert "scaled by texts" in str(scaled)
    assert model.estimate(ProjectSizes(), None).seconds is None
    assert STAGES["themes.group"].cost.memory_exponent == 2.0
    stand_in = CostModel("kept_keywords", 1.0, 0.01, 100.0, 1.0, fallback=("people", 10.0))
    assert stand_in.estimate(ProjectSizes(people=20), None).seconds == pytest.approx(3.0)
    assert stand_in.estimate(ProjectSizes(kept_keywords=100), None).seconds == pytest.approx(2.0)
    with pytest.raises(ValueError, match="unknown cost driver"):
        CostModel("texts", 1, 1, 1, 1, fallback=("pages", 1.0))


def test_a_bounded_stage_is_estimated_within_its_budget_or_at_its_own_process():
    stage = STAGES["keywords.extract"]  # sizes its workers to the job's budget
    assert stage.bounded
    last = RunRecord.model_validate(
        {
            "stage": stage.id,
            "run_id": "20260928T100000Z-aaaa",
            "outcome": "succeeded",
            "started_at": "2026-09-28T10:00:00Z",
            "code": {"version": "1", "fingerprint": "sha256:" + "0" * 64},
        }
    )
    sizes = {"characters": 8_000_000_000, "texts": 6_000_000}
    now = ProjectSizes(**sizes)
    last.measures = Measures(seconds=3500.0, peak_memory_mb=15_000.0, counts=sizes)
    assert stage.estimate(now, last, memory_mb=20_000).peak_memory_mb == pytest.approx(15_000)
    assert stage.estimate(now, last, memory_mb=6_000).peak_memory_mb == 6_000
    # its own process held 7 GB: a 6 GB budget does not make it fit in 6 GB
    last.measures = Measures(
        seconds=3500.0, peak_memory_mb=15_000.0, own_memory_mb=7_000.0, counts=sizes
    )
    over = stage.estimate(now, last, memory_mb=6_000)
    assert over.peak_memory_mb == pytest.approx(7_000) and "own process" in over.basis
    assert stage.estimate(now, last, memory_mb=10_000).peak_memory_mb == 10_000


# ── progress and cancel ──────────────────────────────────────────────────────


def test_progress_never_goes_back_and_beats_while_a_stage_is_silent(env):
    env.controls.progress = [0.2, 0.7, 0.4, 0.9]  # 0.4 goes back: never shown
    started, release = threading.Event(), threading.Event()
    env.controls.hold["keywords.build"] = (started, release)
    threading.Timer(0.4, release.set).start()
    events: list[tuple[float, Progress]] = []
    result = env.build(progress=lambda e: events.append((time.monotonic(), e)), heartbeat_s=0.05)
    assert result.outcome == "succeeded"
    progress = [e for _, e in events]
    assert [e.fraction for e in progress] == sorted(e.fraction for e in progress)
    assert [e.phase for e in progress] == sorted(e.phase for e in progress)
    for phase in {e.phase for e in progress}:
        within = [e.stage_fraction for e in progress if e.phase == phase]
        assert within == sorted(within)
    assert {e.phases for e in progress} == {4}
    assert progress[-1].fraction == 1.0 and progress[-1].stage_fraction == 1.0
    assert any(e.heartbeat and e.stage == "keywords.build" for e in progress)
    gaps = [b[0] - a[0] for a, b in zip(events, events[1:], strict=False)]
    assert max(gaps) < 0.3  # the silent stage held 0.4 s
    assert str(progress[0]).startswith("phase 1 of 4: gather the texts")
    with pytest.raises(ValueError, match="heartbeat"):
        env.build(heartbeat_s=11)


def test_a_cancel_before_the_build_changes_nothing(env):
    cancel = threading.Event()
    cancel.set()
    result = env.build(cancel=cancel)
    assert result.outcome == "cancelled" and result.summary() == "cancelled: nothing changed"
    assert not any(env.layout.stage(s).exists() for s in env.registry.ids)


def test_a_cancel_inside_a_chunk_keeps_what_finished_before(env):
    env.build()
    before = results(env.layout.root)
    set_texts(env.project, 8)
    cancel = threading.Event()
    env.controls.cancel_at = ("keywords.extract", 1, cancel)
    env.calls()
    result = env.build(cancel=cancel)
    assert result.outcome == "cancelled"
    assert result.summary() == (
        "cancelled: finished before the cancel: corpus.assemble; nothing else changed"
    )
    assert env.calls() == [
        "corpus.assemble",
        "keywords.extract",
        "keywords.extract:chunk:0",
        "keywords.extract:chunk:1",
    ]
    after = results(env.layout.root)
    assert after["corpus.assemble"] != before["corpus.assemble"]
    assert {k: v for k, v in after.items() if k != "corpus.assemble"} == {
        k: v for k, v in before.items() if k != "corpus.assemble"
    }
    extract = status(env.project, env.registry, year=YEAR)["keywords.extract"]
    assert extract.state is StageState.FAILED and extract.attempt.outcome == "cancelled"
    assert list(env.layout.staging_root.iterdir()) == []  # a cancel keeps no checkpoint
    env.controls.cancel_at = None
    assert env.build().ran_ids == ("keywords.extract", "keywords.build", "themes.group")
    assert "keywords.extract:chunk:0" in env.calls()  # started over


def test_a_second_build_waits_for_the_running_one(env):
    started, release = threading.Event(), threading.Event()
    env.controls.hold["keywords.extract"] = (started, release)
    job = threading.Thread(target=env.build)
    job.start()
    try:
        assert started.wait(10)
        with pytest.raises(BuildBusy, match="keywords.extract"):
            env.plan()
    finally:
        release.set()
        job.join()


def test_the_job_log_holds_stage_names_counts_and_times(env):
    result = env.build()
    lines = [
        json.loads(x) for x in (env.layout.jobs / f"{result.job_id}.jsonl").read_text().splitlines()
    ]
    assert [x["event"] for x in lines] == [
        "start",
        "phase",
        "stage-end",
        "phase",
        "stage-end",
        "phase",
        "stage-end",
        "phase",
        "stage-end",
        "end",
    ]
    assert lines[-1]["outcome"] == "succeeded"
    assert "Survey" not in (env.layout.jobs / f"{result.job_id}.jsonl").read_text()


def test_a_job_runner_names_the_log_the_build_appends_to(env):
    log = env.layout.jobs / "job-7.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('{"event": "submitted"}\n', encoding="utf-8")
    result = env.build(job_id="job-7")
    assert result.job_id == "job-7"
    events = [json.loads(x)["event"] for x in log.read_text().splitlines()]
    assert events[0] == "submitted" and events[1] == "start" and events[-1] == "end"
    with pytest.raises(ValueError, match="job id"):
        env.build(job_id="../elsewhere")


def test_a_failed_stage_leaves_no_staging_and_changes_nothing_else(env):
    env.controls.fail.add("keywords.extract")
    result = env.build()
    assert result.outcome == "failed" and result.ran_ids == ("corpus.assemble",)
    assert result.not_run == ("keywords.build", "themes.group")
    assert "keywords.extract failed (RuntimeError: keywords.extract broke on purpose)" in (
        result.summary()
    )
    assert list(env.layout.staging_root.iterdir()) == []
    assert not env.layout.stage("keywords.extract").exists()
    record = json.loads(env.layout.attempt("keywords.extract").read_text())
    assert record["outcome"] == "failed" and "broke on purpose" in record["error"]


# ── kills ────────────────────────────────────────────────────────────────────


def _kill(root: Path, log: Path, step: str) -> None:
    """Build in another process that dies at *step*, as a crash would."""
    proc = subprocess.run(
        [sys.executable, str(FAKES), str(root), str(log), step],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == KILLED, f"{step}: exit {proc.returncode}\n{proc.stderr}"


def _reopen(root: Path) -> Project:
    """Open after a kill: the lock left behind is taken over, then repairs run."""
    assert (root / ".lock").exists()
    return Project.open(root, write=True)


def _whole_generations(project: Project) -> None:
    """Every stage folder holds one whole generation; nothing of a swap is left."""
    layout = project.layout
    assert not layout.journal.exists()
    for stage in ("corpus.assemble", "keywords.extract", "keywords.build", "themes.group"):
        folder = layout.stage(stage)
        if folder.exists():
            record = RunRecord.model_validate_json((folder / "run.json").read_bytes())
            assert record.stage == stage and record.outcome == "succeeded"
            assert not (folder / ".staging.json").exists() and not (folder / ".chunks").exists()


@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    """The results of an uninterrupted build and rebuild."""
    root = tmp_path_factory.mktemp("reference") / "proj"
    project = make_project(root)
    registry = make_registry(Controls(log=root.parent / "calls.log"))
    build(project, registry=registry, year=YEAR, budget_mb=1e9)
    first = results(root)
    set_texts(project, 7)
    build(project, registry=registry, year=YEAR, budget_mb=1e9)
    second = results(root)
    project.close()
    return first, second


#: Where a build process dies: before the first stage, at each point of a stage's life, at a
#: boundary between two stages, after the last. Every step of the swap itself is covered, in
#: process, by tests/test_generations.py.
KILL_STEPS = [
    "stage:start:corpus.assemble",
    "stage:staged:keywords.extract",
    "inside:keywords.extract:1",
    "stage:ran:keywords.extract",
    "swap:journal#2",
    "swap:placed#2",
    "stage:done:keywords.extract",
    "stage:done:themes.group",
]


@pytest.mark.parametrize("step", KILL_STEPS)
def test_a_build_killed_anywhere_loses_nothing(tmp_path, reference, step):
    first, second = reference
    root, log = tmp_path / "proj", tmp_path / "calls.log"
    project = make_project(root)
    registry = make_registry(Controls(log=log))
    build(project, registry=registry, year=YEAR, budget_mb=1e9)
    set_texts(project, 7)  # every stage needs a new generation
    project.close()
    _kill(root, log, step)

    project = _reopen(root)
    _whole_generations(project)
    now = results(root)
    for stage, text in now.items():
        assert text in (first[stage], second[stage]), stage  # the old or the new, whole
    build(project, registry=registry, year=YEAR, budget_mb=1e9)
    assert results(root) == second
    assert {s.state for s in status(project, registry, year=YEAR).values()} == {
        StageState.UP_TO_DATE,
        StageState.SKIPPED,
    }
    assert list(project.layout.staging_root.iterdir()) == []
    project.close()


def test_a_killed_run_resumes_from_its_last_chunk(tmp_path, reference):
    root, log = tmp_path / "proj", tmp_path / "calls.log"
    make_project(root).close()
    _kill(root, log, "inside:keywords.extract:2")
    assert log.read_text().splitlines()[-1] == "keywords.extract:chunk:2"
    log.unlink()

    project = _reopen(root)
    env_status = status(project, make_registry(Controls(log=log)), year=YEAR)
    extract = env_status["keywords.extract"]
    assert extract.state is StageState.FAILED and extract.interrupted is not None
    controls = Controls(log=log)
    registry = make_registry(controls)
    planned = plan(project, registry=registry, year=YEAR, budget_mb=1e9)
    assert planned.item("keywords.extract").resume == "resumes a killed run: 2 of 4 chunks are done"
    build(project, registry=registry, year=YEAR, budget_mb=1e9)
    assert [c for c in log.read_text().splitlines() if c.startswith("keywords.extract")] == [
        "keywords.extract",
        "keywords.extract:chunk:2",
        "keywords.extract:chunk:3",
    ]
    record = json.loads(project.layout.run_json("keywords.extract").read_text())
    assert record["warnings"] == ["resumed a killed run: 2 of 4 chunks were already done"]
    assert results(root) == reference[0]
    project.close()


def test_a_killed_run_starts_over_when_its_inputs_changed(tmp_path, reference):
    root, log = tmp_path / "proj", tmp_path / "calls.log"
    make_project(root).close()
    _kill(root, log, "inside:keywords.extract:2")
    log.unlink()
    project = _reopen(root)
    project.layout.stopwords_json.write_text('{"format": "cartolex-stopwords/1"}', encoding="utf-8")
    registry = make_registry(Controls(log=log))
    assert (
        plan(project, registry=registry, year=YEAR, budget_mb=1e9).item("keywords.extract").resume
        is None
    )
    build(project, registry=registry, year=YEAR, budget_mb=1e9)
    chunks = [c for c in log.read_text().splitlines() if c.startswith("keywords.extract:chunk")]
    assert chunks == [f"keywords.extract:chunk:{i}" for i in range(4)]
    project.close()


def _hold(mb: int, seconds: float) -> None:
    block = bytearray(mb * 1024 * 1024)
    block[::4096] = b"x" * len(block[::4096])  # touched: resident
    time.sleep(seconds)


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="the process tree is read in /proc"
)
def test_a_stage_peak_counts_its_worker_processes_together():
    import multiprocessing

    from cartolex.build.machine import PeakMemory

    context = multiprocessing.get_context("spawn")
    with PeakMemory() as peak:
        parent = resident_memory_mb()
        workers = [context.Process(target=_hold, args=(300, 2.0)) for _ in range(2)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
    # Two workers of 300 MB at once: counted together (the largest one alone adds 310).
    assert peak.peak_mb is not None and peak.peak_mb - parent >= 450, (peak.peak_mb, parent)
