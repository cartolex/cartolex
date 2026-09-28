# SPDX-License-Identifier: MIT
"""Demo worlds with bodies (full texts): same world, long repetitive bodies added."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from cartolex.demo import generate
from cartolex.demo.bodies import BODY_TEMPLATES, HEADINGS, SECTIONS
from cartolex.demo.cli import main as cli_main
from cartolex.demo.writers import world_files


@pytest.fixture(scope="module")
def pair():
    return generate(size="XS", seed=0), generate(size="XS", seed=0, bodies=True)


def test_bodies_leave_the_rest_of_the_world_unchanged(pair) -> None:
    plain, full = pair
    a, b = world_files(plain), world_files(full)
    for name in ("people.csv", "groups.csv", "works.csv", "authorships.csv"):
        assert a[name] == b[name], name
    for x, y in zip(plain.works, full.works, strict=True):
        assert (x.title, x.abstract, x.body) == (y.title, y.abstract, "")
        assert y.text.startswith(x.text) and y.body
        assert y.text == f"{x.text}\n{y.body}\n"


def test_a_body_is_long_repetitive_and_in_the_language_of_its_work(pair) -> None:
    _, full = pair
    for work in full.works:
        paragraphs = work.body.split("\n\n")
        headings = [p for p in paragraphs if p in HEADINGS[work.language].values()]
        assert headings == [HEADINGS[work.language][s] for s in SECTIONS]
        assert 350 <= len(work.body.split()) <= 1600
        assert "{" not in work.body
    sentences = Counter(
        s.strip() for w in full.works for s in w.body.replace("\n\n", " ").split(". ")
    )
    assert sentences.most_common(1)[0][1] > 5  # generic filler comes back
    assert set(BODY_TEMPLATES) == {"en", "fr", "pt"}


def test_trilingual_bodies() -> None:
    world = generate(size="XS", seed=0, languages="en,fr,pt", bodies=True)
    pt = [w for w in world.works if w.language == "pt"]
    assert pt and all(HEADINGS["pt"]["results"] in w.body for w in pt)


def test_manifest_and_truth_of_a_world_with_bodies(tmp_path: Path, pair) -> None:
    plain, full = pair
    manifest = full.write(tmp_path / "w")
    assert manifest["bodies"] is True and "languages" not in manifest
    assert manifest["counts"]["words"] > 3 * plain.counts()["words"]
    truth = json.loads((tmp_path / "w" / "truth.json").read_text(encoding="utf-8"))
    assert truth["bodies"] is True and truth["languages"] == ["en", "fr"]
    templates = {r["text"] for r in truth["lexicon"] if r["kind"] == "template"}
    assert "Material and methods" in templates
    assert "The data set was checked for quality before the analysis." in templates


def test_command_line(tmp_path: Path, capsys) -> None:
    assert cli_main(["create", "--size", "XS", "--out", str(tmp_path / "w"), "--bodies"]) == 0
    manifest = json.loads((tmp_path / "w" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["bodies"] is True
