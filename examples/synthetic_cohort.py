# SPDX-License-Identifier: MIT
"""End-to-end cartolex walkthrough on a synthetic cohort — no network needed.

Builds a fake sociology-flavoured workspace (12 researchers x 3 documents in
the ``manual`` corpus slot), then runs the full offline pipeline:

    extraction (TF-IDF) -> consolidation -> roster -> SVD -> concept
    clustering -> UMAP 2-D atlas -> static plots

The optional LLM stages (keyword triage, label translation) are not run here —
see INTEGRATION.md.

Usage::

    python examples/synthetic_cohort.py [workspace_dir]

Outputs land in ``<workspace_dir>`` (default: ``./example_workspace``):
``automatic_data/keywords_*.csv``, ``lexical_analysis/umap_individuals.csv``,
``lexical_analysis/proto_subfields.json``, ``lexical_analysis/*.png`` …
"""

from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

from cartolex.atlas import driver
from cartolex.context import RunContext
from cartolex.lexicon import KeywordsConfig, run_pipeline_stage_1, run_pipeline_stage_3
from cartolex.lexicon.io_helpers import build_researcher_index

# ── 1. Synthetic cohort ────────────────────────────────────────────────────

THEMES = {
    "travail": [
        "sociologie du travail",
        "précarité de l'emploi",
        "plateformes numériques",
        "syndicalisme",
        "conditions de travail",
        "gig economy",
        "labour market segmentation",
        "télétravail",
        "autonomie professionnelle",
        "management algorithmique",
    ],
    "education": [
        "inégalités scolaires",
        "reproduction sociale",
        "orientation scolaire",
        "capital culturel",
        "démocratisation de l'enseignement",
        "educational attainment",
        "tracking and streaming",
        "rapport au savoir",
        "décrochage scolaire",
        "mixité sociale",
    ],
    "migration": [
        "parcours migratoires",
        "politiques d'asile",
        "transnationalisme",
        "intégration économique",
        "discriminations ethno-raciales",
        "border regimes",
        "remittances",
        "diaspora networks",
        "regroupement familial",
        "naturalisation",
    ],
}

FILLER_FR = (
    "Cette recherche mobilise des méthodes mixtes, combinant entretiens semi-directifs, "
    "analyse statistique des enquêtes longitudinales et observation ethnographique. "
    "Les résultats éclairent les dynamiques de {kw1} et leur articulation avec {kw2}, "
    "en portant une attention particulière à {kw3} dans les contextes urbains et ruraux. "
)
FILLER_EN = (
    "This project develops a comparative analysis of {kw1}, drawing on panel survey data "
    "and archival sources. We examine how {kw2} interacts with {kw3} across welfare "
    "regimes, and discuss implications for social stratification research. "
)

FIRST_NAMES = [
    "Ada",
    "Bruno",
    "Chloé",
    "David",
    "Emma",
    "Farid",
    "Gaëlle",
    "Hugo",
    "Inès",
    "Jules",
    "Karim",
    "Léa",
]
UNITS = ["LAB-SOC-A", "LAB-SOC-B", "LAB-SOC-C"]


def build_workspace(ws: Path) -> None:
    """Write the synthetic manual-slot corpus + index into *ws*."""
    random.seed(42)
    corpus = ws / "automatic_data" / "corpus_manual"
    corpus.mkdir(parents=True, exist_ok=True)
    (ws / "manual_data").mkdir(exist_ok=True)

    theme_names = list(THEMES)
    rows: list[dict[str, str]] = []
    for i, first in enumerate(FIRST_NAMES):
        theme_kws = THEMES[theme_names[i % 3]]
        last, unit = f"Socio{i:02d}", UNITS[i % 3]
        for d in range(3):
            picks = random.sample(theme_kws, 5)
            template = FILLER_FR if d % 2 == 0 else FILLER_EN
            text = " ".join(
                template.format(kw1=picks[j % 5], kw2=picks[(j + 1) % 5], kw3=picks[(j + 2) % 5])
                for j in range(6)
            )
            fname = f"{last}_{first}_{unit}_doc{d}.txt"
            (corpus / fname).write_text(text, encoding="utf-8")
            rows.append(
                {
                    "last_name": last,
                    "first_name": first,
                    "unit": unit,
                    # Optional doc_year enables recency windows + trajectories.
                    # Mind KeywordsConfig.kw_recency_years (default 5): dated
                    # documents older than the window are excluded from keyword
                    # construction. Keep the years recent (or set the window to 0).
                    "doc_year": str(2024 + d),
                    "txt_path": str(Path("automatic_data/corpus_manual") / fname),
                }
            )

    with (ws / "manual_index.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["last_name", "first_name", "unit", "doc_year", "txt_path"]
        )
        writer.writeheader()
        writer.writerows(rows)
    print(f"[workspace] {len(rows)} documents, {len(FIRST_NAMES)} researchers -> {ws}")


# ── 2. Pipeline ────────────────────────────────────────────────────────────


def main() -> None:
    ws = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("example_workspace")
    ws = ws.resolve()
    build_workspace(ws)

    cfg = KeywordsConfig(
        # corpus_slots defaults to the one "manual" slot this workspace fills
        min_df=2,  # tiny corpus — relax the document-frequency floor
        domain_title="sociologie (cohorte synthétique)",  # domain title for prompts
    )
    # One explicit context per run: the workspace's paths, the settings, the
    # stop words, the prompts, the current year, …
    ctx = RunContext.for_workspace(ws, cfg)

    print("[stage 1] TF-IDF extraction …")
    run_pipeline_stage_1(ctx)

    # Optional stage 2 (LLM keyword triage) would run here — see INTEGRATION.md.

    print("[stage 3] consolidation + scoring …")
    run_pipeline_stage_3(ctx)

    print("[roster] researcher index …")
    build_researcher_index(ctx)

    print("[lexical] SVD -> concepts -> UMAP -> plots …")
    driver.run_svd(ctx)
    driver.run_clustering(ctx)
    driver.run_umap(ctx)
    driver.run_lexical_plots(ctx)

    print("\nDone. Key outputs:")
    paths = ctx.paths
    for path in (
        paths.refined_terms_csv,
        paths.person_terms_csv,
        paths.layout_persons_csv,
        paths.clusters_csv,
        paths.proto_subfields_json,
        paths.persons_groups_png,
        paths.svd_model_json,
        paths.layout_model_json,
    ):
        mark = "ok" if path.exists() else "MISSING"
        print(f"  [{mark}] {path.relative_to(ws)}")


if __name__ == "__main__":
    main()
