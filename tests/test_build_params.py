# SPDX-License-Identifier: MIT
"""Parameters as decisions: declarations, validation when read, rules, cross-checks, the registry."""

from __future__ import annotations

import json

import pytest
from _build_fakes import YEAR, Controls, make_project, make_registry

from cartolex.build import (
    STAGES,
    CostModel,
    ParamsError,
    ParamSpec,
    ProjectSizes,
    Registry,
    Stage,
    load_params,
    theme_depth,
    theme_level_sizes,
)
from cartolex.build.params import check_params, resolve_params
from cartolex.project import STAGE_IDS
from cartolex.project.models import ParamsFile

# ── the rules the build ships ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("keywords", "units", "depth"),
    [
        (50, 10_000, 1),  # clamped up to 1
        (999, 10_000, 1),
        (1_000, 10_000, 2),
        (5_000, 800, 2),
        (5_000, 99, 1),  # few units cap the depth
        (10_000, 10_000, 3),
        (99_999, 10**6, 3),
        (100_000, 10**6, 4),
        (10**9, 10**9, 4),  # clamped down to 4
        (1, 1, 1),
    ],
)
def test_theme_depth(keywords, units, depth):
    assert theme_depth(keywords, units) == depth


def test_theme_levels_grow_geometrically_from_the_top():
    assert theme_level_sizes(500, 1, 15, 20) == (15,)
    assert theme_level_sizes(5_000, 2, 15, 20) == (15, 250)
    assert theme_level_sizes(50_000, 3, 15, 20) == (15, 194, 2500)
    four = theme_level_sizes(500_000, 4, 15, 20)
    assert four[0] == 15 and four[-1] == 25_000
    ratios = [b / a for a, b in zip(four, four[1:], strict=False)]
    assert max(ratios) / min(ratios) < 1.01  # geometric
    assert theme_level_sizes(5_000, 2, 10, 50) == (10, 100)  # both tunable


def test_cartolex_stages_declare_every_stage_in_order():
    assert STAGES.ids == STAGE_IDS
    triage = STAGES["keywords.triage"]
    assert triage.opt_in and triage.network and triage.paid and triage.needs_consent
    assert [s.id for s in STAGES if s.needs_consent] == ["keywords.triage"]
    assert STAGES["themes.group"].param("depth").rule == "theme_depth"
    assert STAGES["themes.group"].param("top_groups").default == 15
    assert STAGES["themes.group"].param("keywords_per_group").default == 20
    assert STAGES.upstream_of("map.layout") >= {"corpus.assemble", "themes.apply"}
    assert STAGES.downstream_of("map.layout") == {"map.trajectories", "overlays.position"}
    for stage in STAGES:
        for spec in stage.params:
            assert spec.description


def test_the_parameter_set_stays_small():
    """Every parameter earns its place: a new one is a decision, not a habit."""
    count = sum(len(s.params) for s in STAGES)
    assert count <= 16, f"{count} parameters: justify each new one"


def test_a_registry_refuses_what_does_not_fit():
    run = STAGES["keywords.extract"]
    with pytest.raises(ValueError, match="earlier stage"):
        Registry([run])  # its upstream is missing
    with pytest.raises(ValueError, match="order"):
        Registry([STAGES["keywords.extract"], STAGES["corpus.assemble"]])
    with pytest.raises(ValueError, match="unknown stage id"):
        Stage("keywords.guess", "guess")
    with pytest.raises(ValueError, match="global"):
        Stage("corpus.assemble", "x", params=(ParamSpec("seed", "int", "s", default=1),))
    with pytest.raises(ValueError, match="declared twice"):
        Stage(
            "corpus.assemble",
            "x",
            params=(ParamSpec("a", "int", "a", default=1), ParamSpec("a", "int", "a", default=2)),
        )
    with pytest.raises(ValueError, match="says what it sends"):
        Stage("keywords.triage", "x", network=True)
    with pytest.raises(ValueError, match="decisions/"):
        Stage("corpus.assemble", "x", decisions=("sources/tables/texts.parquet",))
    with pytest.raises(ValueError, match="no upstream stage reports"):
        Registry(
            [
                Stage(
                    "themes.group",
                    "x",
                    params=(ParamSpec("depth", "int", "d", rule="theme_depth"),),
                )
            ]
        )
    with pytest.raises(ValueError, match="its default"):
        ParamSpec("n", "int", "n", default=0, minimum=1)
    with pytest.raises(ValueError, match="unknown cost driver"):
        CostModel("pages", 1, 1, 1, 1)


def test_an_opt_in_stage_gets_a_switch():
    triage = STAGES["keywords.triage"]
    assert triage.param("enabled").default is False
    config = make_config()
    assert "switched off" in triage.skip_reason(config, ParamsFile())
    on = ParamsFile(stages={"keywords.triage": {"enabled": True}})
    assert triage.skip_reason(config, on) is None
    assert STAGES["overlays.position"].skip_reason(config, ParamsFile()) == (
        "the project has no overlay"
    )


def make_config():
    from cartolex.project.models import ProjectFile

    return ProjectFile.model_validate(
        {
            "name": "x",
            "identity": {"domain_title": "Coastal systems"},
            "languages": {"corpus": ["en"]},
            "created": {"at": "2026-09-28T10:00:00Z", "by": "test"},
            "app": {"id": "test", "version": "0"},
        }
    )


# ── reading params.json ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("stages", "message"),
    [
        ({"keywords.extract": {"min_df": 3}}, "unknown parameter 'min_df'"),
        ({"keywords.extract": {"min_people": "3"}}, "is not a whole number"),
        ({"keywords.extract": {"min_people": True}}, "is not a whole number"),
        ({"keywords.extract": {"min_people": 0}}, "below the minimum 1"),
        ({"keywords.extract": {"max_share": 1.5}}, "above the maximum 1"),
        ({"keywords.extract": {"counting_unit": "word"}}, "not one of"),
        ({"corpus.assemble": {"parts": ["title", "summary"]}}, "not among"),
        ({"corpus.assemble": {"parts": []}}, "fewer than 1"),
        ({"corpus.assemble": {"parts": ["title", "title"]}}, "repeats"),
        ({"keywords.triage": {"enabled": "yes"}}, "not true or false"),
        ({"themes.group": {"depth": 5}}, "above the maximum 4"),
        ({"themes.apply": {"anything": 1}}, "(known: none)"),
    ],
)
def test_params_are_refused_with_their_reason(stages, message):
    problems = check_params(ParamsFile(stages=stages), STAGES)
    assert len(problems) == 1 and message in problems[0]


def test_every_problem_is_reported_at_once(tmp_path):
    project = make_project(tmp_path / "p")
    registry = make_registry(Controls(log=tmp_path / "log"))
    raw = {
        "format": "cartolex-params/1",
        "seed": 1,
        "stages": {"keywords.extract": {"min_df": 2, "min_people": -1}},
    }
    project.layout.params_json.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ParamsError) as info:
        load_params(project, registry)
    assert len(info.value.problems) == 2
    assert "decisions/params.json" in str(info.value)
    project.layout.params_json.write_text("{not json", encoding="utf-8")
    with pytest.raises(ParamsError, match="not valid JSON"):
        load_params(project, registry)
    project.close()


def test_effective_values_say_where_they_come_from():
    stage = STAGES["themes.group"]
    sizes = ProjectSizes(kept_keywords=5_000, mapped_units=800)
    params = ParamsFile(seed=11, stages={"themes.group": {"top_groups": 12}})
    resolved = resolve_params(stage, params, sizes, year=YEAR)
    values = {k: (v.value, v.source, v.rule) for k, v in resolved.values.items()}
    assert values == {
        "seed": (11, "params.json", None),
        "depth": (2, "rule", "theme_depth"),
        "top_groups": (12, "params.json", None),
        "keywords_per_group": (20, "default", None),
    }
    pinned = resolve_params(
        STAGES["map.trajectories"], ParamsFile(pinned_year=2020), sizes, year=YEAR
    )
    assert pinned.values["year"].value == 2020 and pinned.values["year"].source == "params.json"
    today = resolve_params(STAGES["map.trajectories"], ParamsFile(), sizes, year=YEAR)
    assert today.values["year"].value == YEAR and today.values["year"].source == "default"
    assert today.values["window_years"].value == 3


def test_a_rule_waits_for_its_sizes():
    resolved = resolve_params(STAGES["themes.group"], ParamsFile(), ProjectSizes(), year=YEAR)
    assert resolved.unknown == {"depth": ("kept_keywords", "mapped_units")}
    assert resolved.values["depth"].value is None
    overridden = ParamsFile(stages={"themes.group": {"depth": 3}})
    resolved = resolve_params(STAGES["themes.group"], overridden, ProjectSizes(), year=YEAR)
    assert resolved.unknown == {} and resolved.values["depth"].value == 3


@pytest.mark.parametrize(
    ("stage", "set_", "sizes", "message"),
    [
        (
            "themes.group",
            {"top_groups": 40},
            ProjectSizes(kept_keywords=40, mapped_units=500),
            "only 40 keyword(s)",
        ),
        (
            "themes.group",
            {"depth": 3, "keywords_per_group": 200},
            ProjectSizes(kept_keywords=2_000, mapped_units=500),
            "would not grow",
        ),
        (
            "themes.space",
            {"dimensions": 50},
            ProjectSizes(kept_keywords=900, people=30),
            "allows fewer than 30",
        ),
        ("keywords.extract", {"min_people": 9}, ProjectSizes(people=4), "only 4 people"),
    ],
)
def test_cross_checks_refuse_impossible_values(stage, set_, sizes, message):
    params = ParamsFile(stages={stage: set_})
    assert check_params(params, STAGES) == []  # each value is fine on its own
    resolved = resolve_params(STAGES[stage], params, sizes, year=YEAR)
    problems = resolved.problems(STAGES[stage], sizes, make_config())
    assert len(problems) == 1 and message in problems[0]


def test_cross_checks_wait_for_unknown_sizes():
    stage = STAGES["themes.group"]
    resolved = resolve_params(stage, ParamsFile(), ProjectSizes(), year=YEAR)
    assert resolved.problems(stage, ProjectSizes(), make_config()) == []


def test_the_default_parameters_pass_their_cross_checks_on_rule_sizes():
    for k in (1_000, 5_000, 20_000, 200_000):
        for u in (10, 100, 5_000, 10**5):
            sizes = ProjectSizes(people=u, kept_keywords=k, mapped_units=u)
            stage = STAGES["themes.group"]
            resolved = resolve_params(stage, ParamsFile(), sizes, year=YEAR)
            assert resolved.problems(stage, sizes, make_config()) == [], (k, u)


def test_ai_clean_up_needs_a_provider():
    stage = STAGES["keywords.triage"]
    resolved = resolve_params(stage, ParamsFile(), ProjectSizes(), year=YEAR)
    problems = resolved.problems(stage, ProjectSizes(), make_config())
    assert problems and "identity.ai" in problems[0]
