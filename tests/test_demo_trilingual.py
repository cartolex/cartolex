# SPDX-License-Identifier: MIT
"""The trilingual demo variant (English, French, Portuguese) and the untouched default."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections import Counter
from pathlib import Path

import pytest

from cartolex.demo import LANGUAGE_SETS, generate, lexicon_truth
from cartolex.demo import vocabulary as v
from cartolex.demo.cli import main as cli_main
from cartolex.demo.texts import LEADINS, TEMPLATES, render, slot_names
from cartolex.demo.writers import world_files

#: sha256 of the manifest of default worlds (English and French). The stored
#: numeric reference is made on these worlds: adding a language must leave them
#: byte-identical (the manifest holds the sha256 of every other file).
DEFAULT_MANIFESTS = {
    ("XS", 0): "a6d488213670489d8dd400531735d05687560d712345ffc39d807b3dcfa8cecd",
    ("S", 0): "a6c3386c2643f368080a79db21ef73e7b2a769a2af72a3b6703f21a4f9ea6cef",
    ("S", 1): "4931e2c84c6e224bd8ad6b732317f61fe8456100a53b4034841cfd1b4706fdf6",
    ("L", 0): "c1a82662b7aa67547b1279016c88937c19817b99a4f2a5bbb5a2146c70851818",
}


@pytest.mark.parametrize(("size", "seed"), sorted(DEFAULT_MANIFESTS))
def test_default_worlds_are_unchanged(tmp_path: Path, size: str, seed: int) -> None:
    manifest = generate(size=size, seed=seed).write(tmp_path / "w")
    assert "languages" not in manifest and "works_pt" not in manifest["counts"]
    digest = hashlib.sha256((tmp_path / "w" / "manifest.json").read_bytes()).hexdigest()
    assert digest == DEFAULT_MANIFESTS[(size, seed)]


@pytest.fixture(scope="module")
def pair():
    return generate(size="S", seed=0), generate(size="S", seed=0, languages="en,fr,pt")


def _table(data: bytes) -> list[dict]:
    return list(csv.DictReader(io.StringIO(data.decode("utf-8"))))


def test_a_trilingual_world_keeps_the_people_and_the_bibliography(pair) -> None:
    default, tri = pair
    a, b = world_files(default), world_files(tri)
    for name in ("people.csv", "groups.csv", "authorships.csv"):
        assert a[name] == b[name], name
    works_a, works_b = _table(a["works.csv"]), _table(b["works.csv"])
    changed = 0
    for x, y in zip(works_a, works_b, strict=True):
        for col in ("work_id", "year", "doc_type", "doi", "sources", "text_path"):
            assert x[col] == y[col]
        if x["language"] != y["language"]:
            # Only English works become Portuguese; French works stay French.
            assert (x["language"], y["language"]) == ("en", "pt")
            changed += 1
    counts = tri.counts()
    assert changed == counts["works_pt"]
    assert counts["works_fr"] == default.counts()["works_fr"]
    assert 0.08 <= counts["works_pt"] / counts["works"] <= 0.35


def test_portuguese_is_written_by_some_groups_more_than_others(pair) -> None:
    _, tri = pair
    shares = sorted(g.portuguese_share for g in tri.groups)
    assert shares[0] < 0.15 < 0.3 <= shares[-1]


def test_portuguese_texts_are_detected_as_portuguese(pair) -> None:
    from cartolex.lexicon.lang_utils import detect_language_text

    _, tri = pair
    works = [w for w in tri.works if w.language == "pt"]
    assert works
    langs = Counter(
        detect_language_text(w.abstract, allowed=("en", "fr", "pt")) for w in works[:30]
    )
    assert langs["pt"] >= 0.9 * sum(langs.values())
    text = " ".join(w.abstract for w in works)
    assert "{" not in text and "}" not in text
    # Contractions of the templates' prepositions with the terms' articles.
    assert re.search(r"\b(do|da|dos|das) ", text) and re.search(r"\b(ao|à|aos|às) ", text)


def test_the_manifest_and_truth_of_a_trilingual_world(tmp_path: Path, pair) -> None:
    _, tri = pair
    manifest = tri.write(tmp_path / "w")
    assert manifest["languages"] == ["en", "fr", "pt"]
    assert list(manifest["counts"])[7:10] == ["works_en", "works_fr", "works_pt"]
    truth = json.loads((tmp_path / "w" / "truth.json").read_text(encoding="utf-8"))
    assert truth["languages"] == ["en", "fr", "pt"]
    theme = truth["themes"][0]
    assert theme["name_pt"] and {"pt", "pt_article"} <= set(theme["terms"][0])
    records = truth["lexicon"]
    kinds = Counter((r["kind"], r["lang"]) for r in records)
    n_terms = len(v.all_terms())
    for lang in ("en", "fr", "pt"):
        assert kinds[("theme", lang)] == n_terms
        assert kinds[("method", lang)] == len(v.METHODS)
        assert kinds[("template", lang)] > 100
    assert {r["field"] for r in records if r["kind"] in ("theme", "method")} == {True}
    assert {r["field"] for r in records if r["kind"] in ("driver", "setting", "template")} == {
        False
    }
    by_text = {(r["text"], r["lang"]): r for r in records if r["kind"] == "theme"}
    shoreline = by_text[("variação da linha de costa", "pt")]
    assert shoreline["canonical"] == "shoreline change"
    assert shoreline["themes"] == ["coastal-geomorphology"]


def test_lexicon_truth_follows_the_languages() -> None:
    records = lexicon_truth(("en", "fr"))
    assert {r["lang"] for r in records} == {"en", "fr"}
    assert records == sorted(records, key=lambda r: r["kind"] != "theme")  # themes first


def test_languages_are_checked(tmp_path: Path, capsys) -> None:
    assert LANGUAGE_SETS == (("en", "fr"), ("en", "fr", "pt"))
    assert generate(size="XS", languages=["pt", "fr", "en"]).languages == ("en", "fr", "pt")
    with pytest.raises(ValueError, match="unsupported languages"):
        generate(size="XS", languages="en,de")
    with pytest.raises(SystemExit):
        cli_main(["create", "--size", "XS", "--out", str(tmp_path / "x"), "--languages", "de"])
    rc = cli_main(
        ["create", "--size", "XS", "--out", str(tmp_path / "w"), "--languages", "en,fr,pt"]
    )
    assert rc == 0 and "in Portuguese" in capsys.readouterr().out


# ── the Portuguese vocabulary ───────────────────────────────────────────────


def test_every_phrase_has_a_portuguese_form() -> None:
    for term in v.all_terms():
        assert term.pt and term.pt_article in v.PT_ARTICLES, term.en
    for method in v.METHODS:
        assert method.term.pt, method.term.en
    assert all(d.pt for d in v.DRIVERS)
    assert all(s.pt for s in v.SETTINGS)
    assert all(t.name_pt for t in v.THEMES)


def test_each_english_form_has_one_portuguese_form() -> None:
    terms = list(v.all_terms()) + [m.term for m in v.METHODS] + list(v.DRIVERS)
    by_pt: dict[str, set[str]] = {}
    for t in terms:
        by_pt.setdefault(t.pt_def, set()).add(t.en)
    assert not {k: s for k, s in by_pt.items() if len(s) > 1}


def test_portuguese_article_forms() -> None:
    o, a, os_, as_ = v.parse_terms(
        """
        sediment budget | le bilan sédimentaire | o balanço sedimentar
        longshore drift | la dérive littorale | a deriva litorânea
        tide gauge records | les enregistrements marégraphiques | os registros maregráficos
        rip currents | les courants d'arrachement | as correntes de retorno
        """
    )
    assert (o.pt_def, o.pt_de, o.pt_em, o.pt_a, o.pt_por) == (
        "o balanço sedimentar",
        "do balanço sedimentar",
        "no balanço sedimentar",
        "ao balanço sedimentar",
        "pelo balanço sedimentar",
    )
    assert (a.pt_de, a.pt_a, a.pt_por) == (
        "da deriva litorânea",
        "à deriva litorânea",
        "pela deriva litorânea",
    )
    assert (os_.pt_em, as_.pt_a) == ("nos registros maregráficos", "às correntes de retorno")
    with pytest.raises(ValueError, match="Portuguese form needs a definite article"):
        v.parse_terms("sediment budget | le bilan sédimentaire | balanço sedimentar")
    (compound,) = v.compose_terms(
        """
        biomass | la biomasse | a biomassa
        --
        hake | le merlu | a merluza
        """
    )
    assert compound.pt_def == "a biomassa da merluza"


@pytest.mark.parametrize("kind", ["natural", "social"])
def test_portuguese_templates_render(kind: str) -> None:
    term = v.THEMES[0].terms[0]
    method = v.METHODS[0].term
    slots = {
        "T": term,
        "T2": v.THEMES[1].terms[0],
        "M": method,
        "M2": v.METHODS[1].term,
        "D": v.DRIVERS[0],
        "S": v.SETTINGS[0],
        "N": "12",
        "P": "40",
        "Y1": "2010",
        "Y2": "2020",
    }
    roles = TEMPLATES[("pt", kind)]
    assert set(roles) == set(TEMPLATES[("fr", kind)])
    for role, templates in roles.items():
        assert len(templates) >= len(TEMPLATES[("fr", kind)][role]) - 1, role
        for template in templates:
            text = render(template, {k: slots[k] for k in slot_names(template)}, "pt")
            assert "{" not in text and "}" not in text
    assert set(LEADINS["pt"]) == set(LEADINS["fr"])
