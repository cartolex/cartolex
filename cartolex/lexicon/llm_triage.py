# SPDX-License-Identifier: MIT
"""Stage 2 (LLM): Mistral-powered keyword triage.

This module:
  1. Loads the hard-filtered global keyword list (``ctx.paths.global_terms_csv``)
  2. Runs the typed single-pass Mistral triage (deterministic prefilter →
     one typed LLM classification pass → deterministic post-check), with the
     domain's title and the project owner's description of it as context.
  3. Saves decisions (``ctx.paths.triage_decisions_json``)
  4. Updates the backward-compatible translation cache
     (``ctx.paths.translation_cache_json``)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from .lexical_filters import is_malformed_term
from .llm_filter import save_decisions
from .mistral_client import load_api_key
from .stopwords_config import packaged_lists
from .text_utils import tokenize
from .triage_typed import build_typed_prompt, load_typed_template, run_typed_triage
from .whitelist import load_person_whitelist

if TYPE_CHECKING:
    from cartolex.context import RunContext

logger = logging.getLogger(__name__)


def _load_global_terms(
    path: Path,
    min_score: float = 0.0,
) -> tuple[list[str], pd.DataFrame]:
    """Load the term list of the merged candidate table, with a safety net.

    Parameters
    ----------
    min_score : float
        Drop terms whose ``score_len`` is strictly below this value.
        Default 0.0 keeps everything.

    The safety net drops what is never a term whatever the extraction:
    blank cells, numbers and malformed strings (web addresses, encoding
    garbage). It applies no stop-word list: the extraction's candidates are
    noun phrases, and the packaged lists of the n-gram extraction blocked real
    terms (a term containing a word of one or two letters, common nouns).

    Returns
    -------
    (terms, df) : tuple[list[str], DataFrame]
        ``terms`` is the filtered list; ``df`` has columns [term, score_len]
        so callers can inspect score distributions.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Global keywords CSV not found: {path} — run the extraction stage first."
        )
    df = pd.read_csv(path)
    if "term" not in df.columns:
        raise ValueError(f"No 'term' column in {path}: not a global keywords table.")
    # Drop missing terms first: under pandas >= 3 the ``term`` column is the string dtype
    # whose NA sentinel is a float ``nan`` that ``astype(str)`` leaves in place, so the
    # ``!= ""`` filter alone would not remove a blank cell from an older CSV.
    df = df[df["term"].notna()].copy()
    df["term"] = df["term"].astype(str).str.strip()
    df = df[df["term"] != ""].copy()
    n_raw = len(df)

    # Safety net: numbers and malformed strings are never terms.
    keep = ~df["term"].str.fullmatch(r"[\d\s]+") & ~df["term"].apply(is_malformed_term)
    df = df[keep].copy()
    n_after = len(df)
    if n_after < n_raw:
        logger.info("Safety-net filter removed %d terms (%d → %d)", n_raw - n_after, n_raw, n_after)

    # Score-based cutoff
    if min_score > 0 and "score_len" in df.columns:
        n_before = len(df)
        df = df[df["score_len"] >= min_score].copy()
        n_cut = n_before - len(df)
        if n_cut:
            logger.info(
                "Score cutoff (≥%.4f) removed %d terms (%d → %d)",
                min_score,
                n_cut,
                n_before,
                len(df),
            )

    return df["term"].tolist(), df[["term", "score_len"]] if "score_len" in df.columns else df[
        ["term"]
    ]


def _strip_leading_trailing_midwords(
    terms: list[str], midwords: frozenset[str] | set[str] | None = None
) -> list[str]:
    """Strip leading/trailing midwords (la, le, les, des, de, of, the, ...).

    E.g. 'la physique' → 'physique', 'des matériaux composites' → 'matériaux composites'.
    Single-token results that become empty are dropped. *midwords* defaults to
    the packaged list.
    """
    if midwords is None:
        midwords = packaged_lists().midwords
    cleaned: list[str] = []
    for term in terms:
        tokens = tokenize(term)
        if not tokens:
            continue
        # Strip from left
        while len(tokens) > 1 and tokens[0] in midwords:
            tokens = tokens[1:]
        # Strip from right
        while len(tokens) > 1 and tokens[-1] in midwords:
            tokens = tokens[:-1]
        result = " ".join(tokens)
        if result:
            cleaned.append(result)
    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for t in cleaned:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    return deduped


def run_pipeline_stage_2_llm(
    ctx: RunContext,
    *,
    progress_callback=None,
    api_key: str | None = None,
    domain_title: str | None = None,
    dry_run: bool = False,
    should_cancel=None,
) -> dict | None:
    """Run the LLM triage stage (Stage 2) of a run.

    Parameters
    ----------
    ctx : RunContext
        The run: its paths, settings (model, batch size, …), stop words,
        prompt directory and usage recorder.
    progress_callback : callable, optional
        ``(percent: int, message: str) -> None``
    api_key : str, optional
        Mistral API key override.
    domain_title : str, optional
        Domain title override (default: ``ctx.settings.domain_title``).
    dry_run : bool
        If True, build prompts and print preview but don't call the API.
    should_cancel : callable, optional
        ``() -> bool``, polled before every API call and during every backoff
        wait.  Lets a GUI stop a run of hundreds of calls; the stage then raises
        ``LLMCancelled``. Without one, the context's ``cancel`` is polled.

    Returns
    -------
    dict or None
        The full LLM decisions dict, or None if dry_run.
    """
    with ctx.threads.applied():
        return _triage(
            ctx,
            progress_callback=ctx.percent_reporter(progress_callback),
            api_key=api_key,
            domain_title=domain_title,
            dry_run=dry_run,
            should_cancel=should_cancel or ctx.cancel,
        )


def _triage(
    ctx: RunContext,
    *,
    progress_callback,
    api_key: str | None,
    domain_title: str | None,
    dry_run: bool,
    should_cancel,
) -> dict | None:
    cfg = ctx.settings
    paths = ctx.paths
    lists = ctx.stopwords.packaged
    title = domain_title or cfg.domain_title

    def log(p, m):
        if progress_callback:
            progress_callback(p, m)
        logger.info("[%d%%] %s", p, m)

    # 1. Load terms (with safety-net filtering + score cutoff)
    log(0, "Loading global keyword candidates...")
    terms, _terms_df = _load_global_terms(paths.global_terms_csv, min_score=cfg.llm_min_score)
    log(
        1,
        f"Loaded {len(terms)} terms after safety-net filtering"
        + (f" (score cutoff ≥{cfg.llm_min_score:.4f})" if cfg.llm_min_score > 0 else ""),
    )

    # 1b. Strip leading/trailing midwords (la/le/les/des/de/of/the...)
    terms = _strip_leading_trailing_midwords(terms, lists.midwords)
    log(2, f"After midword stripping: {len(terms)} unique terms")

    def prompt_inputs():
        # The system template (a workspace override, else the context's prompt
        # directory) and the person whitelist, read and checked when used.
        template = load_typed_template(paths.triage_prompt_override_txt, prompt_dir=ctx.prompt_dir)
        return template, load_person_whitelist(paths.person_whitelist_csv)

    # 2. Preview mode
    if dry_run:
        log(5, "=== DRY RUN: showing prompts only ===")
        template, person_whitelist = prompt_inputs()
        sys_typed, usr_typed = build_typed_prompt(
            terms[:5],
            title,
            domain_description=cfg.domain_description,
            template=template,
            reference_language=cfg.reference_language,
            person_whitelist=person_whitelist,
        )
        logger.info("TYPED SYSTEM PROMPT:\n%s", sys_typed)
        logger.info("TYPED USER (first 5 of %d):\n%s", len(terms), usr_typed)
        log(100, "Dry run complete. No API calls made.")
        return None

    # 3. Resolve API key
    key = api_key or load_api_key(paths.api_key_json, interactive=False)
    if not key:
        log(
            100,
            "No Mistral API key found. Set MISTRAL_API_KEY env var "
            f"or add it to {paths.api_key_json}",
        )
        return None

    # 4. Run typed single-pass triage
    template, person_whitelist = prompt_inputs()
    log(
        5,
        f"Starting typed single-pass LLM triage: "
        f"model={cfg.llm_model}, batch_size={cfg.llm_batch_size}, "
        f"max_concurrent={cfg.llm_max_concurrent}, "
        f'domain="{title}"' + (", with its description" if cfg.domain_description else ""),
    )
    result = run_typed_triage(
        global_terms=terms,
        domain_title=title,
        domain_description=cfg.domain_description,
        api_key=key,
        model=cfg.llm_model,
        api_url=cfg.llm_api_url,
        batch_size=cfg.llm_batch_size,
        temperature=cfg.llm_temperature,
        cache_path=paths.triage_batch_cache_json,
        term_cache_path=paths.triage_term_cache_json,
        progress=log,
        max_concurrent=cfg.llm_max_concurrent,
        template=template,
        person_whitelist=person_whitelist,
        stopwords=lists,
        reference_language=cfg.reference_language,
        timeout_s=cfg.llm_timeout_s,
        should_cancel=should_cancel,
        usage=ctx.usage,
        client_factory=ctx.ai_client,
    )

    # 5. Save decisions
    log(95, "Saving LLM decisions...")
    save_decisions(result, paths.triage_decisions_json)

    # 6. Update translation_cache.json for backward compatibility
    translation_map = result.get("translation_map", {})
    if translation_map:
        existing = {}
        if paths.translation_cache_json.exists():
            try:
                existing = json.loads(paths.translation_cache_json.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing.update(translation_map)
        paths.translation_cache_json.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        log(97, f"Updated translation_cache.json with {len(translation_map)} entries")

    # 7. Summary
    n_accepted = len(result.get("accepted", []))
    n_rejected = len(result.get("rejected", []))
    log(100, f"Done. {n_accepted} accepted, {n_rejected} rejected.")

    return result


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point for the standalone LLM triage stage."""
    import argparse

    from cartolex.context import RunContext

    from .config import KeywordsConfig

    parser = argparse.ArgumentParser(description="Stage 2 (LLM): Mistral-powered keyword triage")
    parser.add_argument("--workspace", required=True, type=Path, help="workspace folder")
    parser.add_argument(
        "--domain-title", default=None, help="Domain title used in prompts (default: from config)"
    )
    parser.add_argument(
        "--domain-description",
        default=None,
        help="Short description of the domain, given to the model as context",
    )
    parser.add_argument(
        "--model",
        default="mistral-small-latest",
        help="Mistral model ID for the typed triage pass",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview prompts without making API calls"
    )
    parser.add_argument("--batch-size", type=int, default=100, help="Terms per API request")
    args = parser.parse_args(argv)

    cfg = KeywordsConfig(
        llm_model=args.model,
        llm_batch_size=args.batch_size,
    )
    if args.domain_title:
        cfg.domain_title = args.domain_title
    if args.domain_description:
        cfg.domain_description = args.domain_description
    ctx = RunContext.for_workspace(args.workspace, cfg)

    if not args.dry_run:
        # Confirm before sending
        terms, _ = _load_global_terms(ctx.paths.global_terms_csv)
        logger.info("Ready to send %d terms to Mistral API (typed triage):", len(terms))
        logger.info("  Model: %s", ctx.settings.llm_model)
        logger.info("  Domain: %s", ctx.settings.domain_title)
        confirm = input("\nProceed? [y/N] ").strip().lower()
        if confirm != "y":
            logger.info("Aborted.")
            return

    run_pipeline_stage_2_llm(ctx, dry_run=args.dry_run)
