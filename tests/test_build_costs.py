# SPDX-License-Identifier: MIT
"""The stages' cost models against measured builds: every estimate within a factor of 2.25."""

from __future__ import annotations

import pytest

from cartolex.build import STAGES, ProjectSizes

#: Measured builds: each world's sizes, and each stage's seconds and peak MB (the stage's
#: processes together) in a fresh process (tools/scale_study.py, 6 October 2026, 20 cores,
#: a 12 GB budget; docs/sizes.md). "light": one work per person, titles only.
MEASURES = {
    "S": (
        {
            "people": 39,
            "texts": 226,
            "characters": 301626,
            "kept_keywords": 331,
            "mapped_units": 39,
        },
        {
            "corpus.assemble": (0.4, 171),
            "keywords.extract": (18.5, 811),
            "keywords.build": (3.4, 231),
            "themes.space": (2.3, 241),
            "themes.group": (2.3, 237),
            "themes.apply": (1.7, 211),
            "map.layout": (31.7, 490),
            "map.trajectories": (2.2, 240),
            "overlays.position": (2.0, 225),
        },
    ),
    "L": (
        {
            "people": 343,
            "texts": 2137,
            "characters": 2853283,
            "kept_keywords": 2198,
            "mapped_units": 343,
        },
        {
            "corpus.assemble": (0.3, 226),
            "keywords.extract": (39.9, 2523),
            "keywords.build": (13.4, 274),
            "themes.space": (4.3, 482),
            "themes.group": (5.3, 482),
            "themes.apply": (1.6, 238),
            "map.layout": (29.6, 641),
            "map.trajectories": (6.7, 486),
            "overlays.position": (2.5, 236),
        },
    ),
    "1k": (
        {
            "people": 965,
            "texts": 6205,
            "characters": 8280370,
            "kept_keywords": 5016,
            "mapped_units": 965,
        },
        {
            "corpus.assemble": (0.8, 304),
            "keywords.extract": (75.7, 5741),
            "keywords.build": (31.1, 536),
            "themes.space": (7.8, 749),
            "themes.group": (7.9, 744),
            "themes.apply": (2.1, 329),
            "map.layout": (32.1, 732),
            "map.trajectories": (13.2, 637),
            "overlays.position": (2.9, 240),
        },
    ),
    "10k": (
        {
            "people": 9698,
            "texts": 60687,
            "characters": 80948223,
            "kept_keywords": 9705,
            "mapped_units": 9698,
        },
        {
            "corpus.assemble": (3.9, 606),
            "keywords.extract": (248.6, 7306),
            "keywords.build": (52.6, 1549),
            "themes.space": (17.6, 2098),
            "themes.group": (15.8, 2084),
            "themes.apply": (3.2, 536),
            "map.layout": (56.4, 714),
            "map.trajectories": (33.5, 3603),
            "overlays.position": (15.5, 486),
        },
    ),
    "light10k": (
        {
            "people": 9698,
            "texts": 9698,
            "characters": 739692,
            "kept_keywords": 1657,
            "mapped_units": 9698,
        },
        {
            "corpus.assemble": (1.4, 344),
            "keywords.extract": (43.5, 4700),
            "keywords.build": (13.3, 546),
            "themes.space": (5.7, 763),
            "themes.group": (3.6, 806),
            "themes.apply": (1.5, 435),
            "map.layout": (43.8, 644),
            "map.trajectories": (12.9, 1290),
            "overlays.position": (3.6, 392),
        },
    ),
    "light100k": (
        {
            "people": 97052,
            "texts": 97052,
            "characters": 7383513,
            "kept_keywords": 9217,
            "mapped_units": 97052,
        },
        {
            "corpus.assemble": (8.7, 801),
            "keywords.extract": (121.9, 6139),
            "keywords.build": (78.9, 1172),
            "themes.space": (40.0, 1675),
            "themes.group": (12.7, 1613),
            "themes.apply": (14.8, 1003),
            "map.layout": (189.3, 1236),
            "map.trajectories": (280.2, 3702),
            "overlays.position": (461.7, 2680),
        },
    ),
}

#: How far an estimate may be from its measure, either way (the models' forms fit no
#: closer on these six worlds: times under a second, light worlds beside full ones).
FACTOR = 2.25


@pytest.mark.parametrize("world", sorted(MEASURES))
def test_every_estimate_is_within_a_factor_of_2_25(world):
    sizes, stages = MEASURES[world]
    for stage_id, (seconds, peak_mb) in stages.items():
        est = STAGES[stage_id].estimate(ProjectSizes(**sizes), None)
        assert 1 / FACTOR <= est.seconds / seconds <= FACTOR, (world, stage_id, est, seconds)
        assert 1 / FACTOR <= est.peak_memory_mb / peak_mb <= FACTOR, (world, stage_id, est, peak_mb)


def test_the_dry_run_refuses_a_stage_that_will_not_fit():
    # A million people: the extraction alone needs more memory than a 64 GB machine has.
    sizes = ProjectSizes(
        people=970_000,
        texts=6_111_000,
        characters=8_152_074_000,
        kept_keywords=10_000,
        mapped_units=970_000,
    )
    est = STAGES["keywords.extract"].estimate(sizes, None)
    assert est.peak_memory_mb > 64 * 1024
    # From the people alone, before a vocabulary: at most 10 000 keywords.
    early = STAGES["themes.group"].estimate(ProjectSizes(people=10**6), None)
    assert early.peak_memory_mb == pytest.approx(213.8 + 1.766 * 10_000**0.72)


def test_a_bounded_stage_never_needs_more_than_the_jobs_memory_budget():
    huge = ProjectSizes(people=169_287, texts=6_000_000, characters=6 * 10**9, mapped_units=169_287)
    for stage in STAGES:
        est = stage.estimate(huge, None, memory_mb=12_288)
        free = stage.estimate(huge, None)
        if stage.bounded:
            assert est.peak_memory_mb is None or est.peak_memory_mb <= 12_288, stage.id
        else:
            assert est.peak_memory_mb == free.peak_memory_mb, stage.id
    bounded = {s.id for s in STAGES if s.bounded}
    assert bounded == {"keywords.extract", "keywords.build", "themes.space", "map.trajectories"}
    extract = STAGES["keywords.extract"].estimate(huge, None, memory_mb=12_288)
    assert extract.peak_memory_mb == 12_288 and "at most the job's budget" in extract.basis
