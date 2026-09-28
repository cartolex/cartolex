# SPDX-License-Identifier: MIT
"""Theme recovery: run a variant's lexicon through the engine and compare with the demo truth.

For a demo world, the variant's candidate tables and a judgement (the kept
band, and the to-check terms the oracle accepts, under their canonical
English form) are written into a workspace; the engine's consolidation, SVD
and term clustering then run as in a real project. Two measures compare the
result with the world's truth:

- **ARI** of the term groups with the true themes, for the kept terms that
  belong to one theme, at the topic level (concept clusters) and at the theme
  level (proto-subfields);
- **person mix**: each person's theme mix estimated from their row of the
  lexical matrix (each term counting for its true themes), compared with the
  truth: mean cosine similarity over people, and Pearson correlation over all
  person × theme cells.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd
from analyses import ParsedCorpus
from measures import Gold, Matcher, adjusted_rand

from cartolex.context import RunContext
from cartolex.lexicon import KeywordsConfig, run_pipeline_stage_3
from cartolex.lexicon.io_helpers import build_researcher_index
from cartolex.lexicon.utils import make_researcher_id


class Workspace:
    """A demo world's corpus contract, written once, reused by every variant."""

    def __init__(self, parsed: ParsedCorpus) -> None:
        self.parsed = parsed
        self.base = Path(tempfile.mkdtemp(prefix="lexicon-lab-"))
        parsed.corpus.world.write_corpus(self.base / "corpus")

    def fresh(self) -> Path:
        ws = self.base / "run"
        if ws.exists():
            shutil.rmtree(ws)
        (ws / "automatic_data").mkdir(parents=True)
        shutil.copy(self.base / "corpus" / "manual_index.csv", ws / "manual_index.csv")
        os.symlink(
            self.base / "corpus" / "automatic_data" / "corpus_manual",
            ws / "automatic_data" / "corpus_manual",
        )
        return ws

    def close(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)


def theme_recovery(
    ws_owner: Workspace,
    tables: dict[str, pd.DataFrame],
    matchers: dict[str, Matcher],
    golds: dict[str, Gold],
    *,
    length_bonus_alpha: float = 2.0,
) -> dict:
    """ARI and person-mix agreement of one variant (see the module docstring)."""
    from cartolex.atlas import driver
    from cartolex.atlas.model_files import load_lexical_data

    t0 = time.perf_counter()
    corpus = ws_owner.parsed.corpus
    ws = ws_owner.fresh()
    langs = tuple(tables)
    cfg = KeywordsConfig(
        kw_recency_years=0,
        corpus_languages=langs,
        display_languages=langs,
        length_bonus_alpha=length_bonus_alpha,
    )
    ctx = RunContext.for_workspace(ws, cfg, now_year=2026)
    accepted, rejected, canonical, term_lang = [], [], {}, {}
    for lang, table in tables.items():
        table.to_csv(ctx.paths.raw_terms_csv(lang), index=False)
        m, g = matchers[lang], golds[lang]
        for term, band in zip(table["term"], table["band"], strict=True):
            k = m.key(term)
            if band == "kept" or (band == "check" and k in g.all):
                accepted.append(term)
                canonical[term] = g.canonical.get(k, term.lower())
                term_lang[term] = lang
            else:
                rejected.append(term)
    decisions = {
        "accepted": sorted(set(accepted)),
        "rejected": sorted(set(rejected) - set(accepted)),
        "canonical_map": canonical,
        "translation_map": {},
        "term_lang": term_lang,
    }
    ctx.paths.triage_decisions_json.write_text(json.dumps(decisions, ensure_ascii=False))
    pd.concat(
        [t[["term", "score", "len", "score_len"]].assign(lang=lang) for lang, t in tables.items()]
    ).rename(columns={"score": "score_raw"}).to_csv(ctx.paths.global_terms_csv, index=False)
    run_pipeline_stage_3(ctx)
    build_researcher_index(ctx)
    driver.run_svd(ctx)
    driver.run_clustering(ctx)

    themes_of = {}
    for g in golds.values():
        themes_of.update(g.themes)
    clustered = pd.read_csv(ctx.paths.terms_clustered_csv)
    proto = json.loads(ctx.paths.proto_subfields_json.read_text(encoding="utf-8"))
    concept_subfield = {int(k): int(v) for k, v in proto["concept_subfield"].items()}
    single = [
        (row.cluster, themes_of[row.term][0])
        for row in clustered.itertuples()
        if len(themes_of.get(row.term, ())) == 1
    ]
    ari_topics = adjusted_rand([c for c, _ in single], [t for _, t in single])
    ari_themes = adjusted_rand(
        [concept_subfield.get(int(c), -1) for c, _ in single], [t for _, t in single]
    )

    data = load_lexical_data(ctx.paths.lexical_data_json)
    X = data.X.tocsr()
    terms = [str(t) for t in data.terms]
    theme_ids = sorted({th for ths in themes_of.values() for th in ths})
    col = {th: i for i, th in enumerate(theme_ids)}
    T = np.zeros((len(terms), len(theme_ids)))
    for i, t in enumerate(terms):
        ths = themes_of.get(t, ())
        for th in ths:
            T[i, col[th]] = 1.0 / len(ths)
    est = np.asarray(X @ T)
    world = corpus.world
    groups = {g.group_id: g.acronym for g in world.groups}
    ids = {
        make_researcher_id(p.last_name, p.first_name, groups[p.group]): p.person_id
        for p in world.cohort
    }
    person_index = {pid: i for i, pid in enumerate(corpus.people)}
    cos, a_cells, b_cells = [], [], []
    for row, ind in enumerate(data.individuals):
        pid = ids.get(str(ind))
        if pid is None or pid not in person_index:
            continue
        truth = np.zeros(len(theme_ids))
        for th, w in corpus.person_mix[person_index[pid]].items():
            if th in col:
                truth[col[th]] = w
        e = est[row]
        if e.sum() <= 0:
            continue
        e = e / e.sum()
        cos.append(float(e @ truth / (np.linalg.norm(e) * np.linalg.norm(truth))))
        a_cells += list(e)
        b_cells += list(truth)
    return {
        "ari_topics": ari_topics,
        "ari_themes": ari_themes,
        "mix_cosine": float(np.mean(cos)) if cos else float("nan"),
        "mix_pearson": float(np.corrcoef(a_cells, b_cells)[0, 1]) if a_cells else float("nan"),
        "vocabulary": len(terms),
        "seconds": time.perf_counter() - t0,
    }
