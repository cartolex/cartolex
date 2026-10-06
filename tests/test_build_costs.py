# SPDX-License-Identifier: MIT
"""The stages' cost models against measured builds: every estimate within a factor of two."""

from __future__ import annotations

import pytest

from cartolex.build import STAGES, ProjectSizes

#: Measured builds: each world's sizes, and each stage's seconds and peak MB in a fresh
#: process (tools/scale_study.py; docs/sizes.md). "light": one work per person, titles only.
MEASURES = {
    "S": (
        {
            "people": 39,
            "texts": 226,
            "characters": 301626,
            "kept_keywords": 454,
            "mapped_units": 39,
        },
        {
            "corpus.assemble": (0.2, 158),
            "keywords.extract": (9.7, 782),
            "keywords.build": (4.1, 212),
            "themes.space": (1.3, 214),
            "themes.group": (1.3, 211),
            "themes.apply": (1.6, 207),
            "map.layout": (21.7, 498),
            "map.trajectories": (1.5, 233),
            "overlays.position": (1.5, 207),
        },
    ),
    "L": (
        {
            "people": 343,
            "texts": 2137,
            "characters": 2853283,
            "kept_keywords": 2955,
            "mapped_units": 343,
        },
        {
            "corpus.assemble": (0.6, 209),
            "keywords.extract": (36.9, 834),
            "keywords.build": (18.6, 250),
            "themes.space": (1.5, 222),
            "themes.group": (1.4, 268),
            "themes.apply": (1.2, 242),
            "map.layout": (15.9, 706),
            "map.trajectories": (6.5, 396),
            "overlays.position": (1.9, 220),
        },
    ),
    "1k": (
        {
            "people": 965,
            "texts": 6205,
            "characters": 8280370,
            "kept_keywords": 5677,
            "mapped_units": 965,
        },
        {
            "corpus.assemble": (0.6, 272),
            "keywords.extract": (88.8, 848),
            "keywords.build": (31.9, 319),
            "themes.space": (1.6, 252),
            "themes.group": (3.0, 454),
            "themes.apply": (1.3, 343),
            "map.layout": (21.1, 741),
            "map.trajectories": (16.1, 768),
            "overlays.position": (2.5, 244),
        },
    ),
    "10k": (
        {
            "people": 9698,
            "texts": 60691,
            "characters": 80953270,
            "kept_keywords": 9703,
            "mapped_units": 9698,
        },
        {
            "corpus.assemble": (8.0, 431),
            "keywords.extract": (870.3, 1667),
            "keywords.build": (132.4, 871),
            "themes.space": (5.0, 330),
            "themes.group": (6.2, 944),
            "themes.apply": (5.3, 553),
            "map.layout": (44.3, 958),
            "map.trajectories": (205.5, 1351),
            "overlays.position": (23.3, 699),
        },
    ),
    "light10k": (
        {
            "people": 9698,
            "texts": 9698,
            "characters": 739692,
            "kept_keywords": 4108,
            "mapped_units": 9698,
        },
        {
            "corpus.assemble": (1.4, 365),
            "keywords.extract": (47.9, 710),
            "keywords.build": (24.4, 287),
            "themes.space": (3.7, 269),
            "themes.group": (2.5, 344),
            "themes.apply": (3.1, 490),
            "map.layout": (43.5, 900),
            "map.trajectories": (26.7, 496),
            "overlays.position": (9.9, 422),
        },
    ),
    "light100k": (
        {
            "people": 97052,
            "texts": 97052,
            "characters": 7383513,
            "kept_keywords": 10000,
            "mapped_units": 97052,
        },
        {
            "corpus.assemble": (10.8, 655),
            "keywords.extract": (495.3, 900),
            "keywords.build": (154.0, 865),
            "themes.space": (34.0, 748),
            "themes.group": (7.0, 1114),
            "themes.apply": (26.3, 773),
            "map.layout": (212.9, 1166),
            "map.trajectories": (2395.7, 1028),
            "overlays.position": (501.6, 1150),
        },
    ),
}


@pytest.mark.parametrize("world", sorted(MEASURES))
def test_every_estimate_is_within_a_factor_of_two(world):
    sizes, stages = MEASURES[world]
    for stage_id, (seconds, peak_mb) in stages.items():
        est = STAGES[stage_id].estimate(ProjectSizes(**sizes), None)
        assert 0.5 <= est.seconds / seconds <= 2.0, (world, stage_id, est, seconds)
        assert 0.5 <= est.peak_memory_mb / peak_mb <= 2.0, (world, stage_id, est, peak_mb)


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
    assert early.peak_memory_mb == pytest.approx(203.0 + 8.31e-6 * 10_000**2)


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
