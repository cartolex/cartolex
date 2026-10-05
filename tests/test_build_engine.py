# SPDX-License-Identifier: MIT
"""The engine on a project: the ownership table, an XS build end to end, edits, cancel, kill, CLI."""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cartolex.build import STAGES, StageState, build, plan, status
from cartolex.build.engine import AIAccess, engine_registry, keywords_settings
from cartolex.build.enginefiles import (
    ENGINE_FILES,
    UNAVAILABLE,
    FromProject,
    NotProvided,
    Owned,
    OwnFolder,
    engine_paths,
    files_of,
)
from cartolex.cli import main as cli
from cartolex.context import EnginePaths
from cartolex.demo import generate
from cartolex.demo.project import write_project
from cartolex.project import Project
from cartolex.project.models import AIIdentity, StopwordsFile
from cartolex.project.tables import decision_csv_bytes, read_decision_csv

YEAR = 2026
FAKES = Path(__file__).with_name("_build_fakes.py")
OK, SKIPPED = StageState.UP_TO_DATE, StageState.SKIPPED

#: Files a stage writes besides the engine's (the corpus contract's texts and people maps,
#: the placed sets), as prefixes of paths relative to the stage's folder.
EXTRA_FILES = {
    "corpus.assemble": (
        "manual/texts.parquet",
        "manual/pairs.parquet",
        "manual/people.csv",
        "overlays/",
    ),
    "overlays.position": ("applicants/positions.json",),
}


# ── the ownership table ──────────────────────────────────────────────────────


def test_every_engine_path_has_one_place():
    fields = {f.name for f in dataclasses.fields(EnginePaths)}
    assert set(ENGINE_FILES) == fields
    for name, place in ENGINE_FILES.items():
        assert isinstance(place, Owned | FromProject | OwnFolder | NotProvided), name
        if isinstance(place, Owned):
            assert place.stage in STAGES, name
            for later in place.amended_by:
                assert place.stage in STAGES.upstream_of(later), (name, later)
        if isinstance(place, FromProject):
            assert place.kind in ("decision", "cache")
            assert place.rel.startswith("decisions/" if place.kind == "decision" else "cache/")
    owned = [(p.stage, p.rel) for p in ENGINE_FILES.values() if isinstance(p, Owned)]
    assert len(owned) == len(set(owned)), "two fields share a file"


def test_a_stage_writes_its_own_files_and_reads_the_latest_writers(tmp_path):
    folders = {s: tmp_path / "derived" / s for s in STAGES.ids}
    for stage in STAGES:
        seen = {s: folders[s] for s in STAGES.upstream_of(stage.id)}
        seen[stage.id] = tmp_path / "staging" / stage.id
        paths = engine_paths(stage.id, seen, tmp_path)
        for name, place in ENGINE_FILES.items():
            value = getattr(paths, name)
            path = value.folder if hasattr(value, "folder") else value
            path = path[0] if isinstance(path, tuple) else path
            text = str(path)
            if isinstance(place, Owned) and stage.id in place.writers:
                assert text.startswith(str(seen[stage.id])), (stage.id, name)
            elif isinstance(place, Owned):
                available = [w for w in place.writers if w in seen]
                if available:
                    assert text.startswith(str(folders[available[-1]])), (stage.id, name)
                else:
                    assert UNAVAILABLE in text, (stage.id, name)
            elif isinstance(place, NotProvided):
                assert UNAVAILABLE in text, (stage.id, name)


def test_the_embeddings_the_layout_amends_are_read_downstream_from_it(tmp_path):
    seen = {s: tmp_path / s for s in STAGES.upstream_of("map.trajectories")}
    seen["map.trajectories"] = tmp_path / "staging"
    paths = engine_paths("map.trajectories", seen, tmp_path)
    assert paths.embeddings_json.parent.parent == tmp_path / "map.layout"
    assert paths.lexical_data_json.parent.parent == tmp_path / "themes.space"
    seen = {s: tmp_path / s for s in STAGES.upstream_of("themes.apply")}
    seen["themes.apply"] = tmp_path / "staging"
    paths = engine_paths("themes.apply", seen, tmp_path)
    assert paths.embeddings_json.parent.parent == tmp_path / "themes.space"


# ── an XS project, built end to end ──────────────────────────────────────────


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """An XS demo world written as a project and built by cartolex's stages."""
    root = tmp_path_factory.mktemp("xs") / "project"
    project = write_project(generate("XS", 0), root)
    result = build(project, year=YEAR, budget_mb=1e9)
    project.close()
    assert result.outcome == "succeeded", result.summary()
    return root


def _copy(built: Path, tmp_path: Path) -> Project:
    root = tmp_path / "project"
    shutil.copytree(built, root)
    return Project.open(root, write=True)


def _states(project: Project) -> dict[str, StageState]:
    return {k: v.state for k, v in status(project, year=YEAR).items()}


def _reasons(project: Project, stage: str) -> list[str]:
    return [str(r) for r in status(project, year=YEAR)[stage].reasons]


@pytest.mark.models("en", "fr")
def test_an_xs_project_builds_end_to_end(built):
    project = Project.open(built)
    states = _states(project)
    assert states.pop("keywords.triage") is SKIPPED
    assert set(states.values()) == {OK}
    for stage in STAGES.ids:
        folder = project.layout.stage(stage)
        if not folder.is_dir():
            continue
        written = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()} - {
            "run.json"
        }
        claimed = files_of(stage)
        stray = [
            f
            for f in written
            if not any(f == c or (("{}" in c) and _matches(c, f)) for c in claimed)
            and not any(f.startswith(x) for x in EXTRA_FILES.get(stage, ()))
        ]
        assert not stray, (stage, stray)
    record = json.loads(project.layout.run_json("themes.group").read_text())
    assert record["measures"]["counts"]["topics"] >= 1
    assert record["parameters"]["depth"]["from"] == "rule"
    assert record["measures"]["counts"]["depth"] == record["parameters"]["depth"]["value"] == 1
    maps = json.loads(project.layout.maps_json.read_text())
    assert maps["pinned"] == "v1" and maps["versions"][0]["layout"]["method"] == "umap"
    corpus = json.loads(project.layout.run_json("corpus.assemble").read_text())
    assert corpus["measures"]["counts"]["people"] == 11
    assert not project.config.identity.frozen  # no AI answer, no curation decision yet
    positions = json.loads(
        (project.layout.stage("overlays.position") / "applicants" / "positions.json").read_text()
    )
    assert positions["items"] and {"person_id", "x", "y", "levels"} <= set(positions["items"][0])
    assert "themes" not in positions["items"][0]  # the two-level weights exist at depth 2 only


def _matches(pattern: str, path: str) -> bool:
    head, _, tail = pattern.partition("{}")
    return path.startswith(head) and path.endswith(tail)


@pytest.mark.models("en", "fr")
def test_the_settings_come_from_the_project(built):
    project = Project.open(built)
    settings = keywords_settings(project.config, recency_years=0, min_people=2)
    assert [s.id for s in settings.corpus_slots] == [s.id for s in project.config.slots]
    assert settings.corpus_languages == tuple(project.config.languages.corpus)
    assert settings.domain_title == project.config.identity.domain_title
    assert (settings.kw_recency_years, settings.min_df) == (0, 2)


@pytest.mark.models("en", "fr")
def test_edited_decisions_make_the_right_stages_need_an_update(built, tmp_path):
    project = _copy(built, tmp_path)
    layout = project.layout

    layout.stopwords_json.write_bytes(
        StopwordsFile(add={"en": ["survey"]}).model_dump_json().encode()
    )
    assert _reasons(project, "keywords.extract") == ["decisions/stopwords.json was added"]
    assert _states(project)["corpus.assemble"] is OK
    layout.stopwords_json.unlink()

    params, fp = project.read_params()
    stages = {**params.stages, "map.trajectories": {"window_years": 4}}
    project.save_params(params.model_copy(update={"stages": stages}), expected=fp, action="t")
    assert _reasons(project, "map.trajectories") == [
        "parameter window_years: 3 → 4 (from params.json)"
    ]
    assert _states(project)["map.layout"] is OK

    rows = read_decision_csv(layout.people_csv, "people")
    rows[0] = {**rows[0], "role": "excluded"}
    layout.people_csv.write_bytes(decision_csv_bytes("people", rows))
    assert _reasons(project, "corpus.assemble") == ["decisions/people.csv changed"]
    planned = plan(project, year=YEAR, budget_mb=1e9)
    assert planned.to_run[0] == "corpus.assemble" and "overlays.position" in planned.to_run
    project.close()


@pytest.mark.models("en", "fr")
def test_a_curated_theme_tree_is_applied(built, tmp_path):
    from cartolex.build.engine import _vocabulary
    from cartolex.project.themes import rename_node
    from cartolex.project.themes_curated import from_curated
    from cartolex.project.themes_versions import read_themes, save_themes

    project = _copy(built, tmp_path)
    params, fp = project.read_params()
    stages = {**params.stages, "themes.group": {"level_sizes": [3, 8]}}  # two levels: both forms
    project.save_params(params.model_copy(update={"stages": stages}), expected=fp, action="t")
    assert build(project, year=YEAR, budget_mb=1e9).outcome == "succeeded"
    terms, _ = _vocabulary(project.layout.stage("themes.space"))
    draft = json.loads((project.layout.stage("themes.group") / "subfields_draft.json").read_text())
    tree = from_curated(draft, terms).tree
    first = tree.nodes[0].id
    tree = rename_node(tree, first, {"en": "Renamed theme"}).tree
    save_themes(project, tree, expected=None, action="import the draft")
    assert _reasons(project, "themes.apply") == ["decisions/themes.json was added"]
    result = build(project, year=YEAR, budget_mb=1e9)
    assert result.ran_ids == ("themes.apply", "map.layout", "map.trajectories", "overlays.position")
    applied = json.loads((project.layout.stage("map.layout") / "subfields.json").read_text())
    assert "Renamed theme" in [s["label"] for s in applied["subfields"]]
    generic = json.loads((project.layout.stage("map.layout") / "themes_applied.json").read_text())
    assert generic["source"] == "decisions"
    assert {"id": first, "names": {"en": "Renamed theme"}}.items() <= {
        "id": generic["nodes"][0]["id"],
        "names": {"en": generic["nodes"][0]["names"]["en"]},
    }.items()
    assert (project.layout.stage("themes.apply") / "curated.json").exists()
    rebased, _ = read_themes(project)
    assert rebased.based_on.vocabulary is not None  # rebased before the stage ran
    assert project.config.identity.frozen  # a curation decision froze it
    assert set(_states(project).values()) == {OK, SKIPPED}
    project.close()


class _FakeModel:
    """Answers the typed triage prompt: every term kept as itself (``C en term=term``)."""

    calls = 0

    def __init__(self, **_: Any) -> None:
        self.chat = self

    def complete(self, *, model: str, messages: list[dict], temperature: float, **_: Any) -> Any:
        _FakeModel.calls += 1
        terms = json.loads(messages[-1]["content"])
        content = "\n".join(f"C en {t}={t}" for t in terms)
        message = SimpleNamespace(content=content, model_dump=lambda: {"content": content})
        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


@pytest.mark.models("en", "fr")
def test_the_ai_clean_up_runs_with_an_injected_client(built, tmp_path):
    project = _copy(built, tmp_path)
    config = project.config
    identity = config.identity.model_copy(update={"ai": AIIdentity(provider="mistral", model="m")})
    project.save_config(config.model_copy(update={"identity": identity}), action="t")
    params, fp = project.read_params()
    stages = {**params.stages, "keywords.triage": {"enabled": True}}
    project.save_params(params.model_copy(update={"stages": stages}), expected=fp, action="t")

    no_key = build(project, ["keywords.triage"], year=YEAR, budget_mb=1e9, consent=lambda r: True)
    assert no_key.outcome == "failed" and "no AI key" in no_key.failed[1]

    _FakeModel.calls = 0
    registry = engine_registry(AIAccess(client_factory=_FakeModel, max_concurrent=1))
    result = build(
        project,
        ["keywords.build"],
        registry=registry,
        year=YEAR,
        budget_mb=1e9,
        consent=lambda r: True,
    )
    assert result.ran_ids == ("keywords.triage", "keywords.build"), result.summary()
    assert _FakeModel.calls > 0
    assert (project.layout.cache_ai / "triage_term_cache.json").exists()
    counts = json.loads(project.layout.run_json("keywords.triage").read_text())["measures"]
    assert counts["counts"]["accepted"] > 0
    assert project.config.identity.frozen  # the first AI answers froze it
    _FakeModel.calls = 0
    again = build(
        project,
        ["keywords.triage"],
        registry=registry,
        force=["keywords.triage"],
        year=YEAR,
        budget_mb=1e9,
        consent=lambda r: True,
    )
    assert again.outcome == "succeeded" and _FakeModel.calls == 0  # answered from cache/ai/
    project.close()


class _NeverSingleWords(_FakeModel):
    """Answers every single word « never a keyword » (G), every phrase a concept."""

    def complete(self, *, model: str, messages: list[dict], temperature: float, **_):
        terms = json.loads(messages[-1]["content"])
        content = "\n".join(f"C en {t}={t}" if " " in t else f"G {t}" for t in terms)
        message = SimpleNamespace(content=content, model_dump=lambda: {"content": content})
        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def _raw(project: Project, band: str) -> dict[str, str]:
    import pandas as pd

    raw = pd.read_csv(project.layout.stage("keywords.extract") / "raw_keywords_en.csv")
    rows = raw[raw["band"] == band]
    return dict(zip(rows["term"].str.lower(), rows["reason"], strict=True))


@pytest.mark.models("en", "fr")
def test_never_answers_reject_the_candidates_of_another_project(built, tmp_path):
    from cartolex.build.engine import EngineOptions
    from cartolex.lexicon.rejects import MachineRejects

    machine = MachineRejects(tmp_path / "rejects")
    registry = engine_registry(
        AIAccess(client_factory=_NeverSingleWords, max_concurrent=1),
        EngineOptions(rejects_folder=machine.folder),
    )
    first = _copy(built, tmp_path / "a")
    identity = first.config.identity.model_copy(
        update={"ai": AIIdentity(provider="mistral", model="m")}
    )
    first.save_config(first.config.model_copy(update={"identity": identity}), action="t")
    params, fp = first.read_params()
    stages = {**params.stages, "keywords.triage": {"enabled": True}}
    first.save_params(params.model_copy(update={"stages": stages}), expected=fp, action="t")
    run = {"registry": registry, "year": YEAR, "budget_mb": 1e9, "consent": lambda r: True}
    assert build(first, ["keywords.triage"], **run).outcome == "succeeded"
    cached = machine.terms("en")
    assert cached and all(" " not in t for t in cached)
    # A project's own answers never reject its own candidates.
    build(first, ["keywords.extract"], force=["keywords.extract"], **run)
    assert not _raw(first, "rejected")
    first.close()

    second = _copy(built, tmp_path / "b")
    second.save_config(second.config.model_copy(update={"name": "Another map"}), action="t")
    assert build(second, ["keywords.extract"], force=["keywords.extract"], **run).outcome == (
        "succeeded"
    )
    rejected = _raw(second, "rejected")
    assert rejected and set(rejected) <= cached
    assert set(rejected.values()) == {"rejected-earlier"}
    counts = json.loads(second.layout.run_json("keywords.extract").read_text())["measures"]
    assert counts["counts"]["rejected"] >= len(rejected)
    second.close()


@pytest.mark.models("en", "fr")
def test_a_cancel_stops_a_real_stage_and_changes_nothing_in_it(built, tmp_path):
    project = _copy(built, tmp_path)
    before = (project.layout.stage("keywords.extract") / "keywords_global.csv").read_bytes()
    cancel = threading.Event()

    def progress(event):
        if event.stage == "keywords.extract" and event.stage_fraction > 0:
            cancel.set()

    result = build(
        project,
        year=YEAR,
        budget_mb=1e9,
        force=["keywords.extract"],
        cancel=cancel,
        progress=progress,
    )
    assert result.outcome == "cancelled" and result.ran == ()
    assert result.summary() == "cancelled: nothing changed"
    after = (project.layout.stage("keywords.extract") / "keywords_global.csv").read_bytes()
    assert after == before
    assert status(project, year=YEAR)["keywords.extract"].attempt.outcome == "cancelled"
    assert list(project.layout.staging_root.iterdir()) == []
    project.close()


@pytest.mark.models("en", "fr")
def test_the_space_is_fitted_on_the_texts_by_a_rule_and_can_be_on_the_people(built, tmp_path):
    project = _copy(built, tmp_path)
    space = json.loads(project.layout.run_json("themes.space").read_text())["parameters"]
    assert space["space_unit"] == {"value": "text", "from": "rule", "rule": "space_unit_texts"}
    group = json.loads(project.layout.run_json("themes.group").read_text())["parameters"]
    assert group["comb_sideways"]["value"] == "within_parent"
    assert group["comb_grid"]["rule"] == "comb_grid_by_space"
    before = (project.layout.stage("themes.space") / "models" / "svd.npz").read_bytes()
    params, fp = project.read_params()
    stages = {**params.stages, "themes.space": {"space_unit": "person"}}
    project.save_params(params.model_copy(update={"stages": stages}), expected=fp, action="t")
    result = build(project, ["themes.group"], year=YEAR, budget_mb=1e9)
    assert result.outcome == "succeeded" and result.ran_ids == ("themes.space", "themes.group")
    record = json.loads(project.layout.run_json("themes.space").read_text())
    assert record["parameters"]["space_unit"]["value"] == "person"
    group = json.loads(project.layout.run_json("themes.group").read_text())["parameters"]
    assert group["comb_sideways"]["value"] == "anywhere"
    assert group["comb_grid"]["value"] == [
        0.025,
        0.05,
        0.075,
        0.1,
        0.125,
        0.15,
        0.175,
        0.2,
        0.225,
        0.25,
    ]
    after = (project.layout.stage("themes.space") / "models" / "svd.npz").read_bytes()
    assert after != before
    project.close()


@pytest.mark.models("en", "fr")
def test_a_killed_build_of_real_stages_loses_nothing(built, tmp_path):
    project = _copy(built, tmp_path)
    params, fp = project.read_params()
    stages = {**params.stages, "themes.space": {"dimensions": 8}}
    project.save_params(params.model_copy(update={"stages": stages}), expected=fp, action="t")
    old_run = json.loads(project.layout.run_json("themes.space").read_text())["run_id"]
    root = project.layout.root
    project.close()
    proc = subprocess.run(
        [
            sys.executable,
            str(FAKES),
            str(root),
            str(tmp_path / "calls.log"),
            "stage:ran:themes.space",
            "--engine",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 77, proc.stderr[-2000:]
    assert (root / ".lock").exists()  # left by the killed process, taken over
    project = Project.open(root, write=True)
    kept = json.loads(project.layout.run_json("themes.space").read_text())
    assert kept["run_id"] == old_run  # the previous results are still in place
    assert status(project, year=YEAR)["themes.space"].state is StageState.FAILED
    result = build(project, year=YEAR, budget_mb=1e9)
    assert result.outcome == "succeeded" and result.ran_ids[0] == "themes.space"
    assert set(_states(project).values()) == {OK, SKIPPED}
    record = json.loads(project.layout.run_json("themes.space").read_text())
    assert record["parameters"]["dimensions"]["value"] == 8
    project.close()


# ── the command line ─────────────────────────────────────────────────────────


@pytest.mark.models("en", "fr")
def test_the_command_line_verbs(built, tmp_path, capsys):
    project = _copy(built, tmp_path)
    root = str(project.layout.root)
    project.close()

    assert cli(["status", root]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == len(STAGES.ids) and "up to date" in out

    assert cli(["params", root]) == 0
    out = capsys.readouterr().out
    assert "depth = " in out and "(rule theme_depth)" in out

    assert cli(["params", root, "--set", "keywords.extract.min_df=2"]) == 1
    assert "unknown parameter 'min_df'" in capsys.readouterr().err
    assert cli(["params", root, "--set", "map.trajectories.window_years=4", "seed=5"]) == 0
    out = capsys.readouterr().out
    assert "window_years = 4  (params.json)" in out

    assert cli(["build", root, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "run   map.trajectories" in out and "parameter window_years: 3 → 4" in out

    assert cli(["versions", root, "--try-another", "--seed", "9"]) == 0
    assert "added v2" in capsys.readouterr().out
    assert cli(["versions", root, "--pin", "v2"]) == 0
    out = capsys.readouterr().out
    assert "* v2  umap seed 9" in out
    assert cli(["versions", root, "--pin", "v9"]) == 1

    assert cli(["build", root, "--only", "map.trajectories", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "phase 1 of 2: draw the map" in out and "built: map.layout, map.trajectories" in out
    assert cli(["status", root]) == 0
    assert "map.trajectories (change over time): up to date" in capsys.readouterr().out


def test_the_ai_key_never_shows(monkeypatch):
    assert "secret" not in repr(AIAccess(api_key="secret-key"))
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    assert os.environ.get("MISTRAL_API_KEY") is None


@pytest.mark.models("en", "fr")
def test_a_projected_set_in_its_own_folder_is_gathered_placed_and_followed(built, tmp_path):
    import pyarrow as pa
    import pyarrow.compute as pc

    from cartolex.project import SOURCE_TABLES
    from cartolex.project.models import Overlay
    from cartolex.project.tables import read_source_table, write_source_table

    project = _copy(built, tmp_path)
    layout = project.layout
    rows = read_decision_csv(layout.people_csv, "people")
    members = [
        r["person_id"] for r in rows if r["role"] == "projected" and r["set"] == "applicants"
    ]
    assert members
    root = tmp_path / "outside" / "applicants"  # a host application's folder, beside the project
    for name in SOURCE_TABLES:
        table = read_source_table(layout.table(name), name)
        if "person_id" in table.column_names:
            table = table.filter(pc.is_in(table["person_id"], value_set=pa.array(members)))
        write_source_table(root / "tables" / f"{name}.parquet", name, table)
    layout.people_csv.write_bytes(
        decision_csv_bytes("people", [r for r in rows if r["person_id"] not in members])
    )
    config = project.config.model_copy(
        update={"overlays": [Overlay(id="applicants", root=str(root))]}
    )
    project.save_config(config, action="the projected set moves to its own folder")
    assert _states(project)["corpus.assemble"] is not OK

    result = build(project, year=YEAR, budget_mb=1e9)
    assert result.outcome == "succeeded", result.summary()
    positions = json.loads(
        (layout.stage("overlays.position") / "applicants" / "positions.json").read_text()
    )
    assert {it["person_id"] for it in positions["items"]} <= set(members) and positions["items"]

    people = read_source_table(root / "tables" / "people.parquet", "people")
    write_source_table(root / "tables" / "people.parquet", "people", people.slice(1))
    assert any("overlay" in r or "applicants" in r for r in _reasons(project, "corpus.assemble"))
    project.close()
