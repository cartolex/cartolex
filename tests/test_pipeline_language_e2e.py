# SPDX-License-Identifier: MIT
"""End-to-end language proofs (offline, no LLM).

- A Portuguese corpus flows through extraction + consolidation + the atlas and
  produces non-empty Portuguese keyword tables and a rendered UMAP — the
  concrete proof that Portuguese is no longer silently dropped.
- The default configuration still yields the historical FR/EN pairs shape.
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

import pandas as pd
import pytest

from cartolex.context import RunContext
from cartolex.lexicon import KeywordsConfig, run_pipeline_stage_1, run_pipeline_stage_3

PT_THEMES = [
    [
        "sociologia do trabalho",
        "precariedade laboral",
        "plataformas digitais",
        "sindicalismo",
        "condições de trabalho",
        "teletrabalho",
    ],
    [
        "desigualdades escolares",
        "reprodução social",
        "capital cultural",
        "abandono escolar",
        "mobilidade social",
        "ensino superior",
    ],
    [
        "percursos migratórios",
        "políticas de asilo",
        "transnacionalismo",
        "integração económica",
        "redes de diáspora",
        "naturalização",
    ],
]

FILLER_PT = (
    "Esta pesquisa mobiliza métodos mistos, combinando entrevistas semiestruturadas "
    "e análise estatística de inquéritos longitudinais. Os resultados esclarecem as "
    "dinâmicas de {kw1} e a sua articulação com {kw2}, com atenção particular a {kw3} "
    "em contextos urbanos e rurais. "
)
FILLER_EN = (
    "This project develops a comparative analysis of {kw1}, drawing on panel survey data. "
    "We examine how {kw2} interacts with {kw3} across welfare regimes. "
)


def _build_pt_workspace(ws: Path) -> None:
    random.seed(42)
    corpus = ws / "automatic_data" / "corpus_manual"
    corpus.mkdir(parents=True, exist_ok=True)
    (ws / "manual_data").mkdir(exist_ok=True)

    rows = []
    for i in range(9):
        kws = PT_THEMES[i % 3]
        last, unit = f"Socio{i:02d}", f"LAB-{i % 3}"
        for d in range(3):
            picks = random.sample(kws, 4)
            # Mostly Portuguese, with occasional English documents (multilingual corpus).
            template = FILLER_EN if d == 2 else FILLER_PT
            text = " ".join(
                template.format(kw1=picks[j % 4], kw2=picks[(j + 1) % 4], kw3=picks[(j + 2) % 4])
                for j in range(6)
            )
            fname = f"{last}_{unit}_doc{d}.txt"
            (corpus / fname).write_text(text, encoding="utf-8")
            rows.append(
                {
                    "last_name": last,
                    "first_name": f"R{i:02d}",
                    "unit": unit,
                    "txt_path": str(Path("automatic_data/corpus_manual") / fname),
                }
            )
    with (ws / "manual_index.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["last_name", "first_name", "unit", "txt_path"])
        w.writeheader()
        w.writerows(rows)


@pytest.mark.models("pt", "en")
def test_portuguese_corpus_end_to_end(tmp_path: Path) -> None:
    ws = tmp_path / "pt_ws"
    ws.mkdir()
    _build_pt_workspace(ws)

    cfg = KeywordsConfig(
        reference_language="pt",
        corpus_languages=("pt", "en"),
        display_languages=("pt", "en"),
        min_df=2,
        kw_recency_years=0,
        use_llm=False,
        domain_title="sociologia (coorte sintética)",
    )
    ctx = RunContext.for_workspace(ws, cfg)

    run_pipeline_stage_1(ctx)
    auto = ws / "automatic_data"
    pt_raw = pd.read_csv(auto / "raw_keywords_pt.csv")
    assert len(pt_raw) > 0, "Portuguese stream produced no keywords (silently dropped?)"
    # A distinctive Portuguese term survived extraction.
    assert any("trabalho" in t or "escolares" in t or "migratórios" in t for t in pt_raw["term"])

    run_pipeline_stage_3(ctx)
    pairs = pd.read_csv(auto / "keywords_global_refined_pairs.csv")
    assert "term_pt" in pairs.columns
    assert "term_en" in pairs.columns
    assert len(pairs) > 0
    # The per-display-language single-column list is written for Portuguese.
    assert (auto / "keywords_global_refined_pt.csv").exists()

    # The atlas geometry is language-agnostic; confirm it renders on PT input.
    from cartolex.atlas import driver

    driver.run_svd(ctx)
    driver.run_clustering(ctx)
    driver.run_umap(ctx)
    umap = pd.read_csv(ws / "lexical_analysis" / "umap_individuals.csv")
    assert len(umap) > 0


@pytest.mark.models("fr", "en")
def test_default_config_produces_fr_en_pairs(tmp_path: Path) -> None:
    """Back-compat: the default configuration still writes term_fr/term_en pairs."""
    ws = tmp_path / "fr_ws"
    corpus = ws / "automatic_data" / "corpus_manual"
    corpus.mkdir(parents=True, exist_ok=True)
    (ws / "manual_data").mkdir(parents=True, exist_ok=True)

    fr = "apprentissage automatique et réseaux de neurones pour la classification des images"
    en = "machine learning and neural networks for the classification of images in practice"
    rows = []
    for i in range(6):
        text = (fr + " ") * 3 if i % 2 == 0 else (en + " ") * 3
        fname = f"r{i}.txt"
        (corpus / fname).write_text(text, encoding="utf-8")
        rows.append(
            {
                "last_name": f"N{i}",
                "first_name": f"P{i}",
                "unit": "LAB",
                "txt_path": str(Path("automatic_data/corpus_manual") / fname),
            }
        )
    with (ws / "manual_index.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["last_name", "first_name", "unit", "txt_path"])
        w.writeheader()
        w.writerows(rows)

    cfg = KeywordsConfig(
        min_df=1,
        kw_recency_years=0,
        use_llm=False,
    )
    ctx = RunContext.for_workspace(ws, cfg)
    run_pipeline_stage_1(ctx)
    run_pipeline_stage_3(ctx)
    pairs = pd.read_csv(ws / "automatic_data" / "keywords_global_refined_pairs.csv")
    assert {"term_fr", "term_en"} <= set(pairs.columns)


@pytest.mark.models("en")
def test_a_corpus_language_without_text_is_skipped(tmp_path: Path, caplog) -> None:
    """An English-only corpus with the default two corpus languages runs through.

    The French stream has no text: its extraction is skipped with a warning
    (instead of the vectorizer's "empty vocabulary" error) and its raw table is
    written empty; consolidation then works from the English stream alone.
    """
    import logging

    ws = tmp_path / "en_ws"
    corpus = ws / "automatic_data" / "corpus_manual"
    corpus.mkdir(parents=True)
    (ws / "manual_data").mkdir()
    texts = [
        "machine learning and neural networks for the classification of images in practice",
        "coastal sediment transport under storm waves measured by acoustic profilers",
        "protein folding kinetics studied with molecular dynamics and fluorescence probes",
    ]
    rows = []
    for i in range(6):
        fname = f"r{i}.txt"
        (corpus / fname).write_text((texts[i % 3] + " ") * 3, encoding="utf-8")
        rows.append(
            {
                "last_name": f"N{i}",
                "first_name": f"P{i}",
                "unit": "LAB",
                "txt_path": str(Path("automatic_data/corpus_manual") / fname),
            }
        )
    with (ws / "manual_index.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["last_name", "first_name", "unit", "txt_path"])
        w.writeheader()
        w.writerows(rows)

    ctx = RunContext.for_workspace(ws, KeywordsConfig(min_df=1, kw_recency_years=0, use_llm=False))
    assert ctx.settings.corpus_languages == ("fr", "en")
    with caplog.at_level(logging.WARNING, logger="cartolex.lexicon.extract_raw"):
        run_pipeline_stage_1(ctx)
    assert "No text in corpus language 'fr'" in caplog.text
    auto = ws / "automatic_data"
    assert pd.read_csv(auto / "raw_keywords_fr.csv").empty
    assert len(pd.read_csv(auto / "raw_keywords_en.csv")) > 0
    run_pipeline_stage_3(ctx)
    refined = pd.read_csv(auto / "keywords_global_refined.csv")
    assert len(refined) > 0 and set(refined["lang"]) == {"en"}
