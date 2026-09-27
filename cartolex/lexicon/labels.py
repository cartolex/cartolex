# SPDX-License-Identifier: MIT
"""Keyword-language relabel layer.

Language is a *presentation* concern: the canonical ``concept`` stays the stable
internal key everywhere (TF-IDF / SVD / UMAP geometry never changes); only the
labels rendered to the user change. This module is the single place that maps a
canonical concept to its display label in the chosen language — and the single
place that FILLS the sides consolidation could not attest from the corpus
(:func:`fill_missing_label_sides`, an LLM translation pass).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    from cartolex.lexicon.prompt_store import PromptDir

logger = logging.getLogger(__name__)

# Shipped prompt template (cartolex/_data/prompts/labels_translate_system.txt) for the
# translation pass; single placeholder = the target language's human name.
TRANSLATE_PROMPT_NAME = "labels_translate_system"


def load_label_map(pairs_csv: Path, lang: str) -> dict[str, str]:
    """Map each canonical concept to its display label in ``lang``.

    Built from the refined-pairs CSV, whose display-language columns are
    ``term_<lang>`` (``term_fr``, ``term_en``, ``term_pt``, …). Falls back to
    the concept string when the requested language column is absent or blank.
    Returns ``{}`` if the pairs file is absent.
    """
    side = f"term_{lang}"
    if not pairs_csv.exists():
        return {}
    df = pd.read_csv(pairs_csv)
    if "concept" not in df.columns:
        return {}
    out: dict[str, str] = {}
    for _, row in df.iterrows():
        concept = str(row["concept"])
        label = str(row[side]).strip() if side in df.columns and pd.notna(row.get(side)) else ""
        out[concept] = label or concept
    return out


def relabel_terms(df: pd.DataFrame, term_col: str, label_map: dict[str, str]) -> pd.DataFrame:
    """Return a copy of ``df`` with ``term_col`` mapped through ``label_map``.

    Terms absent from the map are left unchanged. The input frame is not mutated.
    """
    out = df.copy()
    out[term_col] = out[term_col].map(lambda t: label_map.get(t, t))
    return out


def _fallback_mask(df: pd.DataFrame, lang: str) -> pd.Series:
    """Boolean mask of the rows whose ``lang`` side still needs a translation.

    Consolidation never translates: it SELECTS the best raw extracted term per
    display language, and when a concept has no raw term in ``lang`` it echoes
    the concept string with ``score_<lang> = 0.0``.  That score is therefore
    the fallback marker: ``score_<lang> == 0.0`` means "label not attested in
    this language's corpus stream".  A missing/blank ``term_<lang>`` cell is
    treated the same way (defensive: hand-edited or legacy files), and a
    language with no columns at all needs every side (nothing was extracted
    for it, so nothing can be attested).
    """
    score_col, term_col = f"score_{lang}", f"term_{lang}"
    if score_col in df.columns:
        score = pd.to_numeric(df[score_col], errors="coerce").fillna(0.0)
        score_zero = score == 0.0
    else:
        score_zero = pd.Series(True, index=df.index)
    if term_col in df.columns:
        term = df[term_col]
        # pandas 3: NaN survives astype(str) as float — fillna("") first.
        blank = term.isna() | (term.fillna("").astype(str).str.strip() == "")
    else:
        blank = pd.Series(True, index=df.index)
    return score_zero | blank


def _translation_schema(batch: Sequence[str]) -> dict[str, Any]:
    """Strict JSON schema: one required string property per input concept."""
    return {
        "type": "object",
        "properties": {concept: {"type": "string"} for concept in batch},
        "required": list(batch),
        "additionalProperties": False,
    }


def fill_missing_label_sides(
    pairs_csv: Path,
    *,
    languages: Sequence[str],
    client: Any,
    cache_dir: Path | None = None,
    batch_size: int = 60,
    prompt_dir: PromptDir | None = None,
) -> dict[str, dict[str, int]]:
    """Fill the non-corpus-attested display-label sides of the pairs CSV via the LLM.

    For each language in *languages*, the rows needing translation are those
    with ``score_<lang> == 0.0`` or a missing/blank ``term_<lang>`` — the
    consolidation fallback marker (see :func:`_fallback_mask`).  Their
    ``concept`` strings are translated into the target language by *client*
    (any object with the :meth:`MistralClient.chat_json` contract) and written
    into ``term_<lang>``.  ``score_<lang>`` is deliberately left at ``0.0``:
    after this pass a zero score means "label translated (or fallback), not
    corpus-attested", so downstream consumers can still tell attested labels
    apart.  Corpus-attested rows are never sent to the LLM nor modified.

    Calls are batched (*batch_size* concepts per call, strict JSON-mapping
    response) and cached/audited on disk under *cache_dir* with the subfield
    stage's convention (:func:`cartolex.lexicon.subfields._cached_chat_json`), so a
    re-run is resumable for free.  A batch that fails (transient API error,
    malformed response) is logged and skipped: its rows keep their fallback
    label and are counted in the report.  The CSV is rewritten atomically
    (tmp + replace), only when at least one label changed. The prompt template
    is read from *prompt_dir* (a run's ``ctx.prompt_dir``; default: the
    packaged prompts) and checked before any call.

    Returns ``{"filled": {lang: n}, "untranslated": {lang: n}}``.
    """
    if not pairs_csv.exists():
        raise FileNotFoundError(
            f"Pairs file not found: {pairs_csv} — run the keyword consolidation stage first."
        )
    df = pd.read_csv(pairs_csv)
    if "concept" not in df.columns:
        raise ValueError(f"{pairs_csv} has no 'concept' column — not a refined-pairs file.")

    from cartolex.lexicon.lang_utils import language_name
    from cartolex.lexicon.prompt_store import load_prompt

    # The subfield stage's cache+audit helper; imported lazily so plain label lookups
    # keep this module free of the subfields import chain (numpy/sklearn).
    from cartolex.lexicon.subfields import _cached_chat_json

    template = load_prompt(
        TRANSLATE_PROMPT_NAME, required_placeholders=("{language_name}",), prompt_dir=prompt_dir
    )
    model = str(getattr(client, "model", ""))
    temperature = float(getattr(client, "temperature", 0.1))

    # Plan all batches first so the [N%] progress markers span the whole pass.
    plans: list[tuple[str, pd.Series, list[list[str]], str]] = []
    for raw_lang in languages:
        lang = str(raw_lang).strip().lower()
        if not lang:
            continue
        mask = _fallback_mask(df, lang)
        seen: set[str] = set()
        todo = [
            c
            for c in df.loc[mask, "concept"].tolist()
            if isinstance(c, str) and c.strip() and not (c in seen or seen.add(c))
        ]
        batches = [todo[i : i + batch_size] for i in range(0, len(todo), batch_size)]
        plans.append((lang, mask, batches, template.render(language_name=language_name(lang))))

    total_batches = sum(len(batches) for _, _, batches, _ in plans)
    filled: dict[str, int] = {}
    untranslated: dict[str, int] = {}
    changed = False
    done = 0
    for lang, mask, batches, system_prompt in plans:
        translations: dict[str, str] = {}
        for i, batch in enumerate(batches):
            logger.info(
                "[%d%%] labels-translate %s: batch %d/%d (%d concepts)",
                int(95 * done / max(total_batches, 1)),
                lang,
                i + 1,
                len(batches),
                len(batch),
            )
            done += 1
            try:
                resp = _cached_chat_json(
                    client,
                    system_prompt,
                    json.dumps(batch, ensure_ascii=False),
                    response_schema=_translation_schema(batch),
                    model=model,
                    temperature=temperature,
                    cache_dir=cache_dir,
                    label=f"labels-translate-{lang}",
                )
            except Exception as exc:
                # Partial-failure tolerance: the pass must complete; failed
                # rows keep their fallback label and are reported untranslated.
                logger.warning(
                    "labels-translate %s: batch %d/%d failed (%s); rows left as-is.",
                    lang,
                    i + 1,
                    len(batches),
                    exc,
                )
                continue
            if isinstance(resp, dict):
                for concept in batch:
                    value = resp.get(concept)
                    if isinstance(value, str) and value.strip():
                        translations[concept] = value.strip()

        term_col, score_col = f"term_{lang}", f"score_{lang}"
        if term_col not in df.columns:
            df[term_col] = df["concept"]
            changed = True
        if score_col not in df.columns:
            df[score_col] = 0.0
            changed = True
        sel = mask & df["concept"].isin(translations)
        if sel.any():
            df.loc[sel, term_col] = df.loc[sel, "concept"].map(translations)
            changed = True
        filled[lang] = int(sel.sum())
        untranslated[lang] = int(mask.sum()) - int(sel.sum())
        logger.info(
            "labels-translate %s: %d filled, %d left untranslated.",
            lang,
            filled[lang],
            untranslated[lang],
        )

    if changed:
        tmp = pairs_csv.with_name(pairs_csv.name + ".tmp")
        df.to_csv(tmp, index=False)
        tmp.replace(pairs_csv)
    logger.info("[100%%] labels-translate: done.")
    return {"filled": filled, "untranslated": untranslated}
