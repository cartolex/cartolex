# SPDX-License-Identifier: MIT
"""Centralized configuration for the keyword extraction pipeline."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Default domain title, used when neither the settings nor the workspace's
# override files name one (see default_domain_title). Kept domain-neutral. The
# value enters the AI cache keys (see tests/test_ai_cache_keys.py): changing it
# would make runs without a title pay again for answers they already have.
_DEFAULT_DOMAIN_TITLE = "recherche scientifique"


def default_domain_title(candidates: Iterable[Path]) -> str:
    """The domain title of the first of *candidates* (JSON files) that names one.

    A run context calls this with the workspace's override file and its
    template (``paths.overrides_json``, ``paths.overrides_template_json``)
    when the settings name no domain; falls back to a neutral default.
    """
    for p in candidates:
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                title = str(data.get("domain_title", "")).strip()
                if title:
                    return title
            except Exception:
                continue
    return _DEFAULT_DOMAIN_TITLE


class SettingsError(ValueError):
    """Invalid engine settings (a language list, a corpus slot, …)."""


_SLOT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True)
class CorpusSlot:
    """One source of corpus documents: an index CSV and the text files it lists.

    ``id`` names the slot; the workspace layout derives the slot's index
    (``<workspace>/<id>_index.csv``) and text folder
    (``<workspace>/automatic_data/corpus_<id>/``) from it (see
    :meth:`cartolex.context.EnginePaths.for_workspace`). ``fit``: the slot's
    documents build the map (extraction, consolidation and the roster).
    ``trajectory``: the trajectory stage reads the slot's per-document index.
    ``doc_types``: only documents of these types (the index's ``doc_type``
    column, case-insensitive) are read; a document without a type always
    passes; ``None`` (or an empty list) reads every document.
    """

    id: str
    fit: bool = True
    trajectory: bool = True
    doc_types: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        slot_id = str(self.id).strip()
        if not _SLOT_ID.match(slot_id):
            raise SettingsError(
                f"Corpus slot id {self.id!r} must start with a letter or digit and hold only "
                "letters, digits, '.', '_' or '-' (it names the slot's files)."
            )
        object.__setattr__(self, "id", slot_id)
        if not (self.fit or self.trajectory):
            raise SettingsError(
                f"Corpus slot {slot_id!r} is read by no stage (fit and trajectory are both off)."
            )
        if self.doc_types is not None:
            if isinstance(self.doc_types, str):
                raise SettingsError(
                    f"Corpus slot {slot_id!r}: doc_types must be a list of types, not a string."
                )
            types = tuple(
                dict.fromkeys(str(t).strip().lower() for t in self.doc_types if str(t).strip())
            )
            object.__setattr__(self, "doc_types", types or None)

    @classmethod
    def coerce(cls, value: CorpusSlot | str | Mapping[str, Any]) -> CorpusSlot:
        """A slot from a :class:`CorpusSlot`, an id, or a mapping of its fields."""
        if isinstance(value, CorpusSlot):
            return value
        if isinstance(value, str):
            return cls(value)
        if isinstance(value, Mapping):
            fields = dict(value)
            if fields.get("doc_types") is not None:
                fields["doc_types"] = tuple(fields["doc_types"])
            return cls(**fields)
        raise SettingsError(f"Not a corpus slot: {value!r}")

    def as_dict(self) -> dict[str, Any]:
        """The slot as plain JSON values (for the settings snapshot)."""
        return {
            "id": self.id,
            "fit": bool(self.fit),
            "trajectory": bool(self.trajectory),
            "doc_types": sorted(self.doc_types) if self.doc_types is not None else None,
        }


def _default_slots() -> tuple[CorpusSlot, ...]:
    return (CorpusSlot("manual"),)


@dataclass
class KeywordsConfig:
    """All tuneable hyperparameters for the keyword pipeline.

    A run carries one inside its :class:`~cartolex.context.RunContext`
    (``ctx.settings``); the workspace location is not a setting — it lives in
    the context's paths.
    """

    # ── The corpus ─────────────────────────────────────────────
    # The ordered registry of corpus slots (see CorpusSlot). Slot order is
    # document order: it fixes the order in which each person's documents are
    # concatenated, hence the rows of the TF-IDF matrix. Default: one slot,
    # "manual".
    corpus_slots: tuple[CorpusSlot, ...] = field(default_factory=_default_slots)

    # Recency window applied to every dated document of the fitted slots,
    # counted back from the run's current year: only documents of the last
    # kw_recency_years years build the keywords (0 = the whole history).
    # Documents without a year always pass.
    kw_recency_years: int = 5

    # Worker processes of Stage 1: the per-paragraph language split and the
    # parsing of new texts (each parsing worker loads its own language model,
    # a few hundred MB). The output is identical whatever the number.
    # 1 = a single process.
    extraction_n_jobs: int = 1

    # N-gram range of the attribution vectorizer (consolidation), widened
    # there to the longest candidate; the candidates themselves are noun
    # phrases of at most cartolex.lexicon.noun_phrases.MAX_UNITS word units.
    ngram_range: tuple[int, int] = (1, 4)
    # Window of the candidates: used by at least min_df people and at most
    # max_df of them (a share); max_features keeps the most frequent.
    min_df: int = 3  # absolute count — must be int, NOT float
    max_df: float = 0.6  # share of people
    max_features: int = 1_000_000
    # What a TF-IDF document is when candidates are scored: "person" (a
    # person's texts together, each person weighs the same), "text" (each
    # text weighs the same; a text two people wrote counts once) or
    # "organisation" (each organisation weighs the same; a text counts once
    # for an organisation). See cartolex.lexicon.scoring.
    counting_unit: str = "person"

    # Scoring
    length_bonus_alpha: float = 2.0
    # Basis of the FINAL quantity weights (atlas shares, pies, wordclouds):
    #   "tf"    — plain term-frequency shares ("fraction of activity", default)
    #   "tfidf" — legacy length-boosted TF-IDF (favours distinctive terms)
    # Ranking and the lexical geometry (SVD/UMAP/clusters) always stay on the
    # boosted TF-IDF score; switching basis only needs the subfields applied again.
    weights_basis: str = "tf"

    # Consolidation / filtering
    global_top_n: int = 10_000
    nested_threshold: float = 1.3

    # Per-entity top-N
    top_n_researcher: int = 30
    top_n_unit: int = 50
    top_n_domain: int = 200

    # ── LLM triage (Mistral) ─────────────────────────────────
    use_llm: bool = True
    llm_model: str = "mistral-small-latest"  # model for the typed single-pass triage
    llm_api_url: str = "https://api.mistral.ai"
    llm_batch_size: int = 150
    llm_temperature: float = 0.1
    # Parallel API calls. Deliberately modest: a fresh provider account is
    # rate-limited long before it is fast, and a fleet that trips 429s spends
    # its time in backoff. AdaptiveThrottle lowers this further on its own when
    # the provider pushes back, and raises it again once calls flow.
    llm_max_concurrent: int = 4
    # Ceiling on ONE API call, seconds. Never None: the SDK's own default is to
    # wait forever, which froze a whole triage behind a filtering proxy.
    llm_timeout_s: float = 180.0
    llm_min_score: float = 0.0  # score_len cutoff — terms below are skipped
    refined_top_n: int = 0  # post-triage cutoff: keep only top-N concepts (0 = all)
    # The title of the mapped domain, used in the triage prompt and in the AI
    # cache keys. Empty: the run context reads it from the workspace override
    # files.
    domain_title: str = ""
    # A short description of the mapped domain, written by the project's
    # owner: the AI triage receives it as context ({domain_description} in
    # the triage prompt). It never enters the AI cache keys. Empty: the
    # prompt names the domain by its title alone.
    domain_description: str = ""

    # ── Language model ───────────────────────────────────────
    # Generalizes the former hardwired FR/EN split + hidden English pivot into
    # two orthogonal axes plus a configurable pivot.  Defaults reproduce the
    # historical FR/EN + English-pivot behaviour exactly (zero migration).
    #   reference_language : the canonical concept-key language (the pivot).
    #       Triage canonicalizes every accepted term INTO this language; it is
    #       the stable join key everywhere downstream.  It need NOT appear in
    #       corpus_languages — an all-Portuguese corpus keyed to an English
    #       pivot (reference_language="en") is the "keep English pivot" model
    #       as a special case.
    #   corpus_languages   : accepted ingest streams, auto-detected per
    #       paragraph; paragraphs in other languages are dropped at extraction.
    #       Any subset of the languages with a language model (en, fr, pt).
    #   display_languages  : translation skins rendered at display surfaces
    #       through the single relabel choke point (labels.py).
    reference_language: str = "en"
    corpus_languages: tuple[str, ...] = ("fr", "en")
    display_languages: tuple[str, ...] = ("fr", "en")

    def __post_init__(self):
        self._normalise_slots()
        self._normalise_languages()
        from .scoring import COUNTING_UNITS

        if self.counting_unit not in COUNTING_UNITS:
            raise SettingsError(
                f"KeywordsConfig.counting_unit {self.counting_unit!r} is not one of "
                f"{', '.join(COUNTING_UNITS)}."
            )

    @property
    def fit_slots(self) -> tuple[CorpusSlot, ...]:
        """The slots whose documents build the map, in order."""
        return tuple(s for s in self.corpus_slots if s.fit)

    @property
    def trajectory_slots(self) -> tuple[CorpusSlot, ...]:
        """The slots the trajectory stage reads, in order."""
        return tuple(s for s in self.corpus_slots if s.trajectory)

    def _normalise_slots(self) -> None:
        """Coerce the slot registry and fail loudly on an unusable one."""
        if isinstance(self.corpus_slots, (str, CorpusSlot, Mapping)):
            raise SettingsError("KeywordsConfig.corpus_slots must be a sequence of corpus slots.")
        slots = tuple(CorpusSlot.coerce(s) for s in self.corpus_slots)
        ids = [s.id for s in slots]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise SettingsError(f"KeywordsConfig.corpus_slots repeats the slot id(s) {duplicates}.")
        if not any(s.fit for s in slots):
            raise SettingsError(
                "KeywordsConfig.corpus_slots must hold at least one slot with fit=True."
            )
        self.corpus_slots = slots

    def _normalise_languages(self) -> None:
        """Lower-case, strip and de-duplicate the language tuples; fail loudly
        on an empty required set."""
        ref = (self.reference_language or "").strip().lower()
        if not ref:
            raise SettingsError(
                "KeywordsConfig.reference_language must be a non-empty language code."
            )
        self.reference_language = ref

        corpus = tuple(dict.fromkeys(c.strip().lower() for c in self.corpus_languages if c.strip()))
        if not corpus:
            raise SettingsError(
                "KeywordsConfig.corpus_languages must list at least one language code."
            )
        # The extraction parses each corpus language with its own language model.
        from .language_models import supported_languages

        unsupported = [c for c in corpus if c not in supported_languages()]
        if unsupported:
            raise SettingsError(
                f"KeywordsConfig.corpus_languages holds {', '.join(map(repr, unsupported))}: "
                f"the keyword extraction supports {', '.join(supported_languages())}."
            )
        self.corpus_languages = corpus

        display = tuple(
            dict.fromkeys(d.strip().lower() for d in self.display_languages if d.strip())
        )
        if not display:
            raise SettingsError(
                "KeywordsConfig.display_languages must list at least one language code."
            )
        self.display_languages = display
