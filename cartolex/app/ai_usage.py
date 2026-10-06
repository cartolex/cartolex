# SPDX-License-Identifier: MIT
"""What the AI clean-up by API costs in tokens: the estimate before a run, the usage after.

:func:`triage_estimate` counts, before a run of ``keywords.triage``, the candidates
an AI judges, those whose answer is already paid for (``cache/ai/``), the calls
and the tokens sent and received (an upper bound). :func:`recorded_usage` reads
what the provider reported: the last run's tokens (its run counts) and the
project's total (``cache/ai/usage.json``). The provider bills the calls; cartolex
counts tokens only.
"""

from __future__ import annotations

from typing import Any

__all__ = ["TRIAGE_STAGE", "recorded_usage", "themes_copilot_tokens", "triage_estimate"]

TRIAGE_STAGE = "keywords.triage"
#: Tokens of the instructions when the project's own prompt cannot be read.
FALLBACK_SYSTEM_TOKENS = 1500
#: Tokens an answer spends per term judged (its verdict and category).
TOKENS_OUT_PER_TERM = 10


def triage_estimate(runtime: Any, ctx: Any) -> dict[str, Any] | None:
    """The estimate of a run of the AI clean-up by API on the current extraction, or
    ``None`` before an extraction: ``terms`` judged (every candidate but those rejected
    automatically, one per distinct term), ``answered`` (their answer already paid for),
    ``new``, ``rejected``, ``calls``, ``tokens_in``, ``tokens_out``, ``upper_bound``."""
    from cartolex.lexicon.llm_filter import TermDecisionCache
    from cartolex.lexicon.scoring import AI_BANDS
    from cartolex.lexicon.triage_typed import _TYPED_CACHE_PHASE, load_typed_template
    from cartolex.project.handoff import tokens

    from .routes.build import AI_BATCH
    from .routes.keywords import extracted

    rows, run_id = extracted(runtime, ctx)
    if run_id is None:
        return None
    terms = sorted({r["term"] for r in rows if r["band"] in AI_BANDS})
    config = ctx.project.config
    known = 0
    if config.identity.ai is not None:
        cache = TermDecisionCache(ctx.layout.cache_ai / "triage_term_cache.json")
        title, model = config.identity.domain_title, config.identity.ai.model
        known = sum(1 for t in terms if cache.get(_TYPED_CACHE_PHASE, t, title, model))
    new_terms = len(terms) - known
    calls = -(-new_terms // AI_BATCH)
    try:
        template = load_typed_template(
            ctx.layout.root / "decisions/prompts/triage_typed_system.txt"
        )
        system = tokens(template.text)
    except Exception:  # an unreadable override: the build says why; the estimate goes on
        system = FALLBACK_SYSTEM_TOKENS
    share = new_terms / max(len(terms), 1)
    return {
        "terms": len(terms),
        "new": new_terms,
        "answered": known,
        "rejected": sum(1 for r in rows if r["band"] == "rejected"),
        "calls": calls,
        "tokens_in": calls * system + int(share * sum(tokens(t) + 4 for t in terms)),
        "tokens_out": new_terms * TOKENS_OUT_PER_TERM,
        "upper_bound": True,
    }


#: A themes bundle read by an assistant: its guide and talk, then per node (its name, its
#: counts, its notes) and per keyword (the term and its node), as the kit lists them; its
#: answer: a fixed part, then per node (a rename, a move, a reason). Rough, said « about ».
THEMES_GUIDE_TOKENS = 12_000
THEMES_TOKENS_PER_NODE = 30
THEMES_TOKENS_PER_KEYWORD = 8
THEMES_ANSWER_TOKENS = 3_000
THEMES_ANSWER_PER_NODE = 25


def themes_copilot_tokens(nodes: int, keywords: int) -> dict[str, int]:
    """About how many tokens an assistant reads (``in``) and writes (``out``) for a themes
    bundle of *nodes* nodes and *keywords* keywords."""
    return {
        "in": THEMES_GUIDE_TOKENS
        + THEMES_TOKENS_PER_NODE * int(nodes)
        + THEMES_TOKENS_PER_KEYWORD * int(keywords),
        "out": THEMES_ANSWER_TOKENS + THEMES_ANSWER_PER_NODE * int(nodes),
    }


def recorded_usage(layout: Any) -> dict[str, Any] | None:
    """The tokens the provider reported: ``last`` (the current run of the clean-up: ``run``,
    ``at``, ``tokens_in``, ``tokens_out``; ``None`` when it never ran by API or ran before
    tokens were counted) and ``total`` (every run of this project: ``tokens_in``,
    ``tokens_out``, ``updated_at``; ``None`` before the first); ``None`` when both are."""
    from cartolex.build.records import read_record
    from cartolex.lexicon.llm_usage import read_persisted

    last = None
    record = read_record(layout, TRIAGE_STAGE)
    counts = record.measures.counts if record is not None else {}
    if "tokens_in" in counts:
        last = {
            "run": record.run_id,
            "at": record.finished_at.isoformat() if record.finished_at else None,
            "tokens_in": int(counts["tokens_in"]),
            "tokens_out": int(counts.get("tokens_out", 0)),
        }
    path = layout.cache_ai / "usage.json"
    total = None
    if path.is_file():
        used = read_persisted(path)
        total = {
            "tokens_in": used.prompt_tokens,
            "tokens_out": used.completion_tokens,
            "updated_at": _updated_at(path),
        }
    if last is None and total is None:
        return None
    return {"last": last, "total": total}


def _updated_at(path: Any) -> str | None:
    import json

    try:
        return json.loads(path.read_text(encoding="utf-8")).get("updated_at")
    except (OSError, ValueError):
        return None
