# SPDX-License-Identifier: MIT
"""The explicit run context: no side effect at import, nothing shared between runs.

* importing every module of ``cartolex`` in a fresh interpreter, from an empty
  folder, creates no file or folder anywhere;
* two runs with different stop-word settings in one process do not affect
  each other;
* the packaged data (stop words, prompts) loads from a built wheel installed in
  a clean virtual environment;
* a broken prompt template raises a clear error, and only when it is used.

All data here is synthetic.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

import cartolex
from cartolex.context import EnginePaths, PathPattern, RunContext, ThreadLimits
from cartolex.lexicon.config import KeywordsConfig
from cartolex.lexicon.prompt_store import PromptTemplateError
from cartolex.lexicon.stopwords_config import StopwordProfile, packaged_lists

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = Path(cartolex.__file__).resolve().parent

_IMPORT_ALL = """
import importlib, pkgutil, sys
import cartolex
for info in pkgutil.walk_packages(cartolex.__path__, "cartolex."):
    if not info.name.endswith("__main__"):
        importlib.import_module(info.name)
print("imported", sum(1 for m in sys.modules if m.startswith("cartolex")))
"""


def _tree(root: Path) -> set[str]:
    # Bytecode caches are left out: the checked interpreter runs with bytecode
    # writing disabled, and other interpreters (e.g. a test run on another Python
    # version at the same time) may write theirs into the package meanwhile.
    return {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if "__pycache__" not in p.relative_to(root).parts
    }


# ── (a) nothing happens at import ────────────────────────────────────────────


def test_importing_every_module_creates_nothing(tmp_path: Path) -> None:
    """A fresh interpreter imports all of cartolex without writing a file anywhere.

    The interpreter runs from an empty folder, with a fresh home, temporary and
    cache folders, and without bytecode caching; afterwards all of them are
    still empty and the package tree is unchanged.
    """
    folders = {name: tmp_path / name for name in ("cwd", "home", "tmp", "cache", "config")}
    for folder in folders.values():
        folder.mkdir()
    env = {
        **os.environ,
        "HOME": str(folders["home"]),
        "TMPDIR": str(folders["tmp"]),
        "XDG_CACHE_HOME": str(folders["cache"]),
        "XDG_CONFIG_HOME": str(folders["config"]),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.pop("MPLCONFIGDIR", None)
    before = _tree(PACKAGE_ROOT)
    proc = subprocess.run(
        [sys.executable, "-B", "-c", _IMPORT_ALL],
        cwd=folders["cwd"],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.startswith("imported")
    created = {
        name: sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))
        for name, folder in folders.items()
    }
    assert created == {name: [] for name in folders}
    assert _tree(PACKAGE_ROOT) == before


def test_building_a_context_touches_no_folder(tmp_path: Path) -> None:
    base = tmp_path / "not-yet"
    ctx = RunContext.for_workspace(base, KeywordsConfig())
    assert not base.exists()
    assert ctx.paths.root == base
    # Every named path lies inside the workspace.
    for field in ctx.paths.__dataclass_fields__:
        value = getattr(ctx.paths, field)
        paths = value if isinstance(value, tuple) else (value,)
        for path in paths:
            if isinstance(path, PathPattern):
                path = path("x")
            assert base in (path, *path.parents), (field, path)


# ── (b) runs do not share stop-word settings ────────────────────────────────


def _consolidation_workspace(ws: Path, overrides: dict | None) -> RunContext:
    """A workspace ready for consolidation: an index, two texts and raw keyword tables."""
    corpus = ws / "automatic_data" / "corpus_manual"
    corpus.mkdir(parents=True)
    texts = {
        "a.txt": "classification of photons and proteins; classification again",
        "b.txt": "photons in a classification of proteins",
    }
    rows = ["last_name,first_name,unit,txt_path"]
    for i, (name, text) in enumerate(texts.items()):
        (corpus / name).write_text(text, encoding="utf-8")
        rows.append(f"Person{i},First{i},GROUP_A,automatic_data/corpus_manual/{name}")
    (ws / "manual_index.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    if overrides is not None:
        (ws / "manual_data").mkdir()
    settings = KeywordsConfig(kw_recency_years=0)
    paths = EnginePaths.for_workspace(ws)
    if overrides is not None:
        paths.overrides_json.write_text(json.dumps(overrides), encoding="utf-8")
    ctx = RunContext.for_workspace(ws, settings, now_year=2026)
    raw = {
        "en": [
            {"term": "classification", "score": 5.0, "len": 1, "score_len": 5.0},
            {"term": "photons", "score": 4.0, "len": 1, "score_len": 4.0},
            {"term": "proteins", "score": 3.0, "len": 1, "score_len": 3.0},
        ],
        "fr": [{"term": "photons", "score": 1.0, "len": 1, "score_len": 1.0}],
    }
    for lang, rows_ in raw.items():
        pd.DataFrame(rows_).to_csv(ctx.paths.raw_terms_csv(lang), index=False)
    return ctx


def _refined_terms(ctx: RunContext) -> list[str]:
    from cartolex.lexicon import run_pipeline_stage_3

    run_pipeline_stage_3(ctx)
    return sorted(pd.read_csv(ctx.paths.refined_terms_csv)["term"].astype(str))


def test_two_runs_with_different_stop_words_do_not_affect_each_other(tmp_path: Path) -> None:
    packaged_before = packaged_lists()
    plain = _consolidation_workspace(tmp_path / "plain", overrides=None)
    blocked = _consolidation_workspace(
        tmp_path / "blocked", overrides={"add": {"basic_blacklist": ["classification"]}}
    )

    first = _refined_terms(plain)
    with_overrides = _refined_terms(blocked)
    again = _refined_terms(plain)

    assert "classification" in first
    assert "classification" not in with_overrides
    assert again == first  # the other run's additions did not leak
    assert "classification" in blocked.stopwords.adjusted.basic_blacklist
    assert "classification" not in plain.stopwords.adjusted.basic_blacklist
    # The packaged lists are shared and immutable: unchanged by either run.
    assert packaged_lists() is packaged_before
    assert "classification" not in packaged_before.basic_blacklist
    assert StopwordProfile.default().overrides == {}


def test_overrides_produce_a_new_profile(tmp_path: Path) -> None:
    base = StopwordProfile.default()
    changed = base.with_overrides(
        {"add": {"midwords": ["Zzz"], "merge_map": {"Foo": "Bar"}}, "remove": {"midwords": ["de"]}}
    )
    assert "zzz" in changed.adjusted.midwords and "de" not in changed.adjusted.midwords
    assert changed.adjusted.merge_map["foo"] == "bar"
    assert "zzz" not in base.adjusted.midwords and "de" in base.adjusted.midwords
    assert changed.consolidation_blacklist and not base.consolidation_blacklist
    with pytest.raises(AttributeError):
        base.packaged.midwords.add("x")  # frozen sets


# ── (c) packaged data from an installed wheel ───────────────────────────────

_WHEEL_CHECK = """
import sys
from pathlib import Path
import cartolex
from cartolex.lexicon.prompt_store import load_prompt
from cartolex.lexicon.stopwords_config import packaged_lists
from cartolex.lexicon.triage_typed import TYPED_PLACEHOLDERS, TYPED_PROMPT_NAME

root = Path(cartolex.__file__).resolve().parent
assert "site-packages" in root.parts, root
lists = packaged_lists()
assert lists.midwords and lists.block("midwords_pt") and lists.plural_suffixes
prompt = load_prompt(TYPED_PROMPT_NAME, required_placeholders=TYPED_PLACEHOLDERS)
assert Path(prompt.source).resolve().is_relative_to(root), prompt.source
assert len(load_prompt("labels_translate_system").text) > 100
leftovers = [n for n in ("config", "prompts") if (Path(sys.prefix) / n).exists()]
assert not leftovers, leftovers
print("wheel data ok")
"""


def test_packaged_data_loads_from_an_installed_wheel(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is needed to build and install the wheel")
    src = tmp_path / "src"
    src.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(REPO_ROOT / name, src / name)
    shutil.copytree(
        REPO_ROOT / "cartolex",
        src / "cartolex",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    wheels = tmp_path / "wheels"
    venv = tmp_path / "venv"
    # Offline: the build backend and the dependencies come from uv's cache.
    for cmd in (
        [uv, "build", "--quiet", "--offline", "--wheel", "--out-dir", str(wheels), str(src)],
        [uv, "venv", "--quiet", "--python", sys.executable, str(venv)],
    ):
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
    (wheel,) = wheels.glob("cartolex-*.whl")
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    install = subprocess.run(
        [uv, "pip", "install", "--quiet", "--offline", "-p", str(python), str(wheel)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert install.returncode == 0, install.stderr
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    proc = subprocess.run(
        [str(python), "-I", "-c", _WHEEL_CHECK],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "wheel data ok" in proc.stdout


# ── (d) prompt templates are checked when used ──────────────────────────────


def _triage_workspace(ws: Path, prompt_dir: Path) -> RunContext:
    ctx = RunContext.for_workspace(ws, KeywordsConfig(), prompt_dir=prompt_dir)
    ctx.paths.global_terms_csv.parent.mkdir(parents=True)
    ctx.paths.global_terms_csv.write_text("term,score_len\nsoft matter,1.0\n", encoding="utf-8")
    return ctx


def test_a_broken_prompt_raises_a_clear_error_only_when_used(tmp_path, monkeypatch) -> None:
    from cartolex.lexicon.llm_triage import run_pipeline_stage_2_llm

    prompts = tmp_path / "prompts"
    prompts.mkdir()
    # The system template without one of its placeholders.
    (prompts / "triage_typed_system.txt").write_text(
        "Classify for {domain_title} in {reference_language_name}.",
        encoding="utf-8",
    )
    # Building the context and importing the stage raise nothing.
    ctx = _triage_workspace(tmp_path / "ws", prompts)
    # Without an API key the stage stops before it needs the template.
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    assert run_pipeline_stage_2_llm(ctx) is None
    # Using it raises, naming the file and the missing placeholder.
    with pytest.raises(PromptTemplateError) as excinfo:
        run_pipeline_stage_2_llm(ctx, dry_run=True)
    message = str(excinfo.value)
    assert "triage_typed_system.txt" in message and "{domain_description}" in message


def test_a_template_with_the_removed_catalogue_anchor_is_refused(tmp_path) -> None:
    """A prompt written for the removed domain catalogue says what replaces it."""
    from cartolex.lexicon.llm_triage import run_pipeline_stage_2_llm

    old_template = (
        "Domain {domain_title}; subfields:\n{subfields_block}\n"
        "{reference_language_name} {person_whitelist_block}"
    )
    # A workspace override ...
    ctx = RunContext.for_workspace(tmp_path / "ws", KeywordsConfig())
    ctx.paths.global_terms_csv.parent.mkdir(parents=True)
    ctx.paths.global_terms_csv.write_text("term,score_len\nsoft matter,1.0\n", encoding="utf-8")
    override = ctx.paths.triage_prompt_override_txt
    override.parent.mkdir(parents=True)
    override.write_text(old_template, encoding="utf-8")
    with pytest.raises(PromptTemplateError) as excinfo:
        run_pipeline_stage_2_llm(ctx, dry_run=True)
    message = str(excinfo.value)
    assert str(override) in message
    assert "{subfields_block}" in message and "{domain_description}" in message
    # ... or a prompt folder of the run.
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "triage_typed_system.txt").write_text(old_template, encoding="utf-8")
    ctx = _triage_workspace(tmp_path / "ws2", prompts)
    with pytest.raises(PromptTemplateError, match=r"\{subfields_block\}, which no longer exists"):
        run_pipeline_stage_2_llm(ctx, dry_run=True)


def test_the_domain_description_reaches_the_prompt(tmp_path, caplog) -> None:
    import logging

    from cartolex.lexicon.llm_triage import run_pipeline_stage_2_llm

    settings = KeywordsConfig(domain_title="Coastal systems", domain_description="Dunes and tides.")
    ctx = RunContext.for_workspace(tmp_path, settings)
    ctx.paths.global_terms_csv.parent.mkdir(parents=True)
    ctx.paths.global_terms_csv.write_text("term,score_len\nsoft matter,1.0\n", encoding="utf-8")
    with caplog.at_level(logging.INFO, logger="cartolex.lexicon.llm_triage"):
        run_pipeline_stage_2_llm(ctx, dry_run=True)
    assert "describes the domain as follows" in caplog.text
    assert "Dunes and tides." in caplog.text


def test_an_unknown_placeholder_is_named_when_rendered(tmp_path) -> None:
    from cartolex.lexicon.llm_triage import run_pipeline_stage_2_llm

    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "triage_typed_system.txt").write_text(
        "{domain_title} {domain_description} {reference_language_name} "
        "{person_whitelist_block} {typo}",
        encoding="utf-8",
    )
    ctx = _triage_workspace(tmp_path / "ws", prompts)
    with pytest.raises(PromptTemplateError, match=r"triage_typed_system\.txt.*\{typo\}"):
        run_pipeline_stage_2_llm(ctx, dry_run=True)


# ── the other context fields ────────────────────────────────────────────────


def test_staleness_hooks_run_before_an_atlas_stage(tmp_path: Path) -> None:
    from cartolex.atlas import driver

    calls: list[tuple] = []

    class Stop(Exception):
        pass

    def guard(stage, root, *, confirmed):
        calls.append(("guard", stage, root, confirmed))
        raise Stop

    ctx = RunContext.for_workspace(
        tmp_path, KeywordsConfig(), staleness_guard=guard, staleness_erase=lambda *a: None
    )
    for run in (driver.run_svd, driver.run_lexical_plots, driver.run_trajectories):
        with pytest.raises(Stop):
            run(ctx, force=True)
    assert [c[1] for c in calls] == ["svd", "plots", "trajectories"]
    assert all(c[2] == tmp_path and c[3] is True for c in calls)


def test_each_context_owns_its_usage_recorder_and_limits(tmp_path: Path) -> None:
    a = RunContext.for_workspace(tmp_path / "a", KeywordsConfig())
    b = RunContext.for_workspace(tmp_path / "b", KeywordsConfig())
    assert a.usage is not b.usage
    a.usage.record(10, 5)
    assert a.usage.cumulative().total_tokens == 15
    assert b.usage.cumulative().total_tokens == 0
    assert ThreadLimits(processes=2).workers(8) == 2
    assert ThreadLimits().workers(8) == 8
    with ThreadLimits(numeric=1).applied():
        pass


def test_live_calls_report_to_the_run_recorder(monkeypatch) -> None:
    import types

    from cartolex.lexicon.llm_usage import UsageRecorder
    from cartolex.lexicon.mistral_client import MistralClient

    class _Sdk:
        def __init__(self, **_):
            self.chat = self

        def complete(self, **_):
            message = types.SimpleNamespace(content="ok", model_dump=lambda: {})
            usage = types.SimpleNamespace(prompt_tokens=7, completion_tokens=3)
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=message)], usage=usage
            )

    fake = types.ModuleType("mistralai")
    fake.Mistral = _Sdk  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mistralai", fake)
    mine, other = UsageRecorder(), UsageRecorder()
    MistralClient(api_key="k", usage=mine).chat_text("s", "u")
    assert mine.cumulative().total_tokens == 10
    assert other.cumulative().total_tokens == 0


def test_the_current_year_comes_from_the_context() -> None:
    from cartolex.lexicon.io_helpers import _filter_window

    assert _filter_window(3, 2020) == 2018
    assert _filter_window(0, None) is None
    with pytest.raises(ValueError, match="now_year"):
        _filter_window(3, None)
    ctx = RunContext.for_workspace(Path("unused"), KeywordsConfig(), now_year=1999)
    assert ctx.now_year == 1999


def test_frozen_atlas_parameters_are_read_per_run(tmp_path: Path) -> None:
    from cartolex.atlas.driver import DEFAULTS, atlas_defaults

    ctx = RunContext.for_workspace(tmp_path, KeywordsConfig())
    assert atlas_defaults(ctx) == DEFAULTS
    ctx.paths.atlas_params_json.parent.mkdir(parents=True)
    ctx.paths.atlas_params_json.write_text(
        json.dumps({"UMAP_N_NEIGHBORS": 7, "N_COMPONENTS_SVD": "bad"}), encoding="utf-8"
    )
    frozen = atlas_defaults(ctx)
    assert frozen.umap_n_neighbors == 7
    assert frozen.n_components_svd == DEFAULTS.n_components_svd
    assert DEFAULTS.umap_n_neighbors == 25  # the engine's defaults are untouched


def test_the_domain_label_defaults_from_the_workspace(tmp_path: Path) -> None:
    """Without a label in the settings, the context reads the workspace's override file."""
    paths = EnginePaths.for_workspace(tmp_path)
    paths.overrides_json.parent.mkdir(parents=True)
    doc = {"domain_title": "Tidal flats"}
    paths.overrides_json.write_text(json.dumps(doc), "utf-8")
    ctx = RunContext.for_workspace(tmp_path, KeywordsConfig())
    assert ctx.settings.domain_title == "Tidal flats"
    named = RunContext.for_workspace(
        tmp_path,
        KeywordsConfig(domain_title="Reefs"),
    )
    assert named.settings.domain_title == "Reefs"
    fallback = RunContext.for_workspace(tmp_path / "empty")
    assert fallback.settings.domain_title
