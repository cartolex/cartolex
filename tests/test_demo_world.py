# SPDX-License-Identifier: MIT
"""The demo world generator: determinism, sizes, identifiers, truth and texts."""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import pytest

from cartolex.demo import FORMAT, SIZES, generate
from cartolex.demo.identifiers import (
    DOI_RE,
    IDHAL_RE,
    OPENALEX_RE,
    ORCID_RE,
    is_valid_orcid,
    orcid_check_character,
)
from cartolex.demo.model import CAREER_STAGES, FIRST_YEAR, NOW_YEAR, Person
from cartolex.demo.names import fold
from cartolex.demo.texts import MAX_WORDS, MIN_WORDS
from cartolex.demo.writers import world_files


@pytest.fixture(scope="module")
def world_xs():
    return generate("XS", 0)


@pytest.fixture(scope="module")
def world_s():
    return generate("S", 0)


# -- determinism ---------------------------------------------------------------


def test_same_size_and_seed_give_byte_identical_files(tmp_path):
    first = generate("S", 0).write(tmp_path / "a")
    second = generate("S", 0).write(tmp_path / "b")
    assert first == second
    names = sorted(p.relative_to(tmp_path / "a") for p in (tmp_path / "a").rglob("*"))
    assert names == sorted(p.relative_to(tmp_path / "b") for p in (tmp_path / "b").rglob("*"))
    for rel in names:
        a, b = tmp_path / "a" / rel, tmp_path / "b" / rel
        if a.is_file():
            assert a.read_bytes() == b.read_bytes(), rel


def test_output_does_not_depend_on_string_hashing(tmp_path):
    """Two processes with different hash seeds write the same files (no set-order leaks)."""
    code = (
        "import json, sys\n"
        "from cartolex.demo import generate\n"
        "manifest = generate('XS', 0).write(sys.argv[1])\n"
        "print(json.dumps(manifest['files'], sort_keys=True))\n"
    )
    outputs = []
    for i, hash_seed in enumerate(("1", "2")):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed}
        done = subprocess.run(
            [sys.executable, "-c", code, str(tmp_path / str(i))],
            env=env,
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parent.parent,
        )
        outputs.append(done.stdout)
    assert outputs[0] == outputs[1]


def test_another_seed_gives_another_world():
    a = world_files(generate("XS", 0))
    b = world_files(generate("XS", 1))
    assert a["people.csv"] != b["people.csv"]
    assert a["works.csv"] != b["works.csv"]


def test_generation_uses_no_global_random_state():
    import random

    random.seed(123)
    before = random.random()
    random.seed(123)
    generate("XS", 0)
    assert random.random() == before


def test_xs_generation_is_fast():
    started = time.perf_counter()
    generate("XS", 3)
    assert time.perf_counter() - started < 3.0


def test_unknown_size_is_refused():
    with pytest.raises(ValueError):
        generate("XXL", 0)


# -- sizes and mix -------------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "works"),
    [("XS", (40, 150)), ("S", (150, 450))],
)
def test_counts_are_within_the_size_ranges(size, works):
    for seed in (0, 1):
        world = generate(size, seed)
        counts = world.counts()
        spec = SIZES[size]
        assert counts["cohort"] == spec.people
        assert counts["applicants"] == spec.applicants
        assert counts["groups"] == spec.groups + spec.external_groups
        assert works[0] <= counts["works"] <= works[1]
        assert counts["authorships"] >= counts["works"]


def test_language_mix_is_mostly_english_with_some_french():
    for seed in (0, 1, 2):
        counts = generate("S", seed).counts()
        share = counts["works_fr"] / counts["works"]
        assert 0.1 <= share <= 0.35, share


def test_some_groups_write_more_french_than_others(world_s):
    shares = [g.french_share for g in world_s.groups]
    assert max(shares) >= 0.3 and min(shares) <= 0.2


def test_coverage_variety(world_xs, world_s):
    for world in (world_xs, world_s):
        coverage = Counter(p.coverage for p in world.cohort)
        assert coverage["no_data"] >= 1 and coverage["thin"] >= 1 and coverage["good"] > 0
        for p in world.people:
            n = len(world.works_of(p.person_id))
            if p.coverage == "no_data":
                assert n == 0
            elif p.coverage == "thin":
                assert 1 <= n <= 2
            else:
                assert n >= 1


def test_sources_cover_all_services(world_s):
    seen = Counter(s for w in world_s.works for s in w.sources)
    assert set(seen) == {"openalex", "hal", "orcid"}
    assert all(w.sources for w in world_s.works)


# -- people, groups, identifiers -----------------------------------------------


def test_full_names_are_unique_even_without_accents(world_s):
    keys = [(fold(p.first_name), fold(p.last_name)) for p in world_s.people]
    assert len(keys) == len(set(keys))


def test_there_is_no_gender_attribute_anywhere(tmp_path, world_xs):
    fields = {f.name for f in dataclasses.fields(Person)}
    manifest = world_xs.write(tmp_path)
    header = (tmp_path / "people.csv").read_text(encoding="utf-8").splitlines()[0]
    truth_keys = set(json.loads((tmp_path / "truth.json").read_text(encoding="utf-8")))
    for word in ("gender", "sex", "civility", "title"):
        assert word not in fields
        assert word not in header
        assert word not in truth_keys
        assert word not in manifest["counts"]


def test_identifiers_have_the_synthetic_formats(world_s):
    orcids = [p.orcid for p in world_s.people if p.orcid]
    assert orcids and len(set(orcids)) == len(orcids)
    for orcid in orcids:
        assert ORCID_RE.match(orcid), orcid
        assert is_valid_orcid(orcid), orcid
    openalex = [p.openalex_id for p in world_s.people if p.openalex_id]
    assert openalex and len(set(openalex)) == len(openalex)
    assert all(OPENALEX_RE.match(x) for x in openalex)
    idhal = [p.idhal for p in world_s.people if p.idhal]
    assert idhal and all(IDHAL_RE.match(x) for x in idhal)
    dois = [w.doi for w in world_s.works if w.doi]
    assert dois and len(set(dois)) == len(dois)
    assert all(DOI_RE.match(d) and d.startswith("10.5555/cartolex-demo.s.") for d in dois)
    assert any(not w.doi for w in world_s.works), "some works have no DOI"


def test_orcid_check_character():
    assert orcid_check_character("000000001234567") == "2"
    assert orcid_check_character("000000000000001") == "X"
    assert is_valid_orcid("0000-0000-0123-4562") is False
    assert is_valid_orcid("0000-0000-0000-001X") is True


def test_groups_sit_at_fictional_sites(world_s):
    acronyms = [g.acronym for g in world_s.groups]
    assert len(set(acronyms)) == len(acronyms)
    for g in world_s.groups:
        assert -90 <= g.lat <= 90 and -180 <= g.lon <= 180
        assert g.site in g.institution
        assert abs(sum(g.themes.values()) - 1.0) < 1e-9


# -- works and truth -----------------------------------------------------------


def test_works_are_consistent(world_s):
    people = {p.person_id: p for p in world_s.people}
    for w in world_s.works:
        assert FIRST_YEAR <= w.year <= NOW_YEAR
        assert 1 <= len(w.authors) <= 4
        assert len(set(w.authors)) == len(w.authors)
        assert all(a in people for a in w.authors)
        roles = {people[a].role for a in w.authors}
        assert len(roles) == 1, "projected sets never co-author with the cohort"
        assert w.language in ("en", "fr")
        if w.doc_type == "thesis":
            assert len(w.authors) == 1 and people[w.authors[0]].career_stage == "phd"
        assert w.themes and all(t in {th.id for th in world_s.themes} for t in w.themes)


def test_abstracts_have_the_right_length(world_s):
    for w in world_s.works:
        n = len(w.abstract.split())
        assert MIN_WORDS <= n <= MAX_WORDS, (w.work_id, n)
        assert w.title and "\n" not in w.title
        assert w.text == f"{w.title}\n\n{w.abstract}\n"


def test_texts_are_detected_in_their_language(world_s):
    from cartolex.lexicon.lang_utils import detect_language_text

    wrong = [
        w.work_id
        for w in world_s.works
        if detect_language_text(w.abstract, allowed=("fr", "en")) != w.language
    ]
    assert len(wrong) <= 0.02 * len(world_s.works), wrong


def test_texts_use_the_themes_of_the_work(world_s):
    themes = {t.id: t for t in world_s.themes}
    for w in world_s.works[:60]:
        forms = set()
        for tid in w.themes:
            for term in themes[tid].terms:
                forms.add(term.en if w.language == "en" else term.fr)
        assert any(f in w.text for f in forms), w.work_id


def test_truth_is_consistent(tmp_path, world_s):
    world_s.write(tmp_path)
    truth = json.loads((tmp_path / "truth.json").read_text(encoding="utf-8"))
    theme_ids = {t["id"] for t in truth["themes"]}
    assert len(theme_ids) == 12
    for theme in truth["themes"]:
        assert theme["name_en"] and theme["name_fr"]
        assert all(t["en"] and t["fr"] for t in theme["terms"])
    person_ids = {p.person_id for p in world_s.people}
    assert set(truth["people"]) == person_ids == set(truth["coverage"])
    for mixture in truth["people"].values():
        assert 1 <= len(mixture) <= 3 and set(mixture) <= theme_ids
        assert abs(sum(mixture.values()) - 1.0) < 1e-9
    assert set(truth["works"]) == {w.work_id for w in world_s.works}
    assert all(set(t) <= theme_ids for t in truth["works"].values())
    assert set(truth["coverage"].values()) <= {"good", "thin", "no_data"}


# -- the neutral files ---------------------------------------------------------


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_written_files_match_the_format(tmp_path, world_xs):
    manifest = world_xs.write(tmp_path)
    assert manifest["format"] == FORMAT
    assert (manifest["size"], manifest["seed"]) == ("XS", 0)
    assert manifest["generator"].startswith("cartolex.demo ")
    on_disk = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk == manifest
    for rel, digest in manifest["files"].items():
        assert hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest() == digest
    people = _rows(tmp_path / "people.csv")
    assert list(people[0]) == [
        "person_id",
        "last_name",
        "first_name",
        "group",
        "institution",
        "site",
        "career_stage",
        "orcid",
        "openalex_id",
        "idhal",
        "role",
    ]
    assert {p["career_stage"] for p in people} <= set(CAREER_STAGES)
    assert {p["role"] for p in people} == {"cohort", "overlay:applicants"}
    groups = {g["group_id"] for g in _rows(tmp_path / "groups.csv")}
    assert {p["group"] for p in people} <= groups
    works = _rows(tmp_path / "works.csv")
    assert len(works) == manifest["counts"]["works"]
    for w in works:
        assert (tmp_path / w["text_path"]).is_file()
        assert set(w["sources"].split(";")) <= {"openalex", "hal", "orcid"}
    authorships = _rows(tmp_path / "authorships.csv")
    assert len(authorships) == manifest["counts"]["authorships"]
    assert {a["work_id"] for a in authorships} == {w["work_id"] for w in works}


def test_write_refuses_an_existing_world_unless_asked(tmp_path, world_xs):
    world_xs.write(tmp_path)
    with pytest.raises(FileExistsError):
        world_xs.write(tmp_path)
    stale = tmp_path / "texts" / "w99999.txt"
    stale.write_text("stale", encoding="utf-8")
    world_xs.write(tmp_path, overwrite=True)
    assert not stale.exists()
