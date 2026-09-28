# SPDX-License-Identifier: MIT
"""A host's prompt folder and function words reach the engine's run context of every stage."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from cartolex.build.engine import EngineOptions, _with_options, engine_registry, run_context
from cartolex.project import Project
from cartolex.project.files import atomic_write_bytes, json_bytes


def _ctx(project: Project, tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        stage=SimpleNamespace(id="keywords.extract"),
        upstream={},
        out=tmp_path / "out",
        layout=project.layout,
        project=project,
        params={},
        record=lambda stage_id: None,
        cancel_requested=False,
        progress=lambda fraction, message: None,
    )


def test_the_host_overlay_and_prompts_reach_the_run_context(tmp_path):
    project = Project.init(tmp_path / "p", name="n", domain_title="t")
    atomic_write_bytes(
        project.layout.stopwords_json,
        json_bytes(
            {"format": "cartolex-stopwords/1", "add": {"en": ["mine"]}, "remove": {"en": ["shared"]}}
        ),
    )
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    options = EngineOptions(
        prompt_dir=prompts,
        stopword_overlay={"add": {"en": ["hostword", "shared"]}, "remove": {"fr": ["mine"]}},
    )
    seen = {}

    def runner(ctx):
        rctx = run_context(ctx, settings=None)  # type: ignore[arg-type]
        seen["prompt_dir"] = rctx.prompt_dir
        seen["overrides"] = dict(rctx.stopwords.overrides)

    _with_options(runner, options)(_ctx(project, tmp_path))
    assert seen["prompt_dir"] == prompts
    blocks = {k: v["basic_blacklist"] for k, v in seen["overrides"].items()}
    # the project's own decisions win over the host's overlay
    assert blocks == {"add": ["hostword", "mine"], "remove": ["shared"]}
    # without options, the project's decisions alone
    run_context_plain = {}

    def plain(ctx):
        run_context_plain["o"] = dict(run_context(ctx, settings=None).stopwords.overrides)  # type: ignore[arg-type]

    plain(_ctx(project, tmp_path))
    assert {k: v["basic_blacklist"] for k, v in run_context_plain["o"].items()} == {
        "add": ["mine"],
        "remove": ["shared"],
    }
    project.close()


def test_the_registry_wraps_every_runner_only_when_options_are_given():
    plain = engine_registry(None)
    same = engine_registry(None, EngineOptions())
    assert [s.run for s in plain][0] is [s.run for s in same][0]
    wrapped = engine_registry(None, EngineOptions(stopword_overlay={"add": {"en": ["x"]}}))
    assert all(s.run.__name__ == p.run.__name__ for s, p in zip(wrapped, plain, strict=True))
    assert [s.run for s in wrapped][0] is not [s.run for s in plain][0]
