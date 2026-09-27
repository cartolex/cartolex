# SPDX-License-Identifier: MIT
"""Typed single-pass LLM triage (replacement for the v2 two-step pipeline).

Three layers:

1. ``prefilter_terms`` — deterministic, no API call. Removes structural,
   lexical and admin junk that the LLM consistently leaks through.
2. ``run_typed_triage`` — single LLM pass with a short prompt anchored on
   the domain's ``reference_keywords`` (when a domain catalog lists them). Emits a typed verdict
   per term: ``C`` (concept), ``M`` (method), ``O`` (object of study) for
   accepts; ``N`` / ``K`` / ``G`` / ``F`` for rejects.
3. ``post_check_typed`` — deterministic guard against the most common
   LLM failure mode (reordering / inserting connectives when emitting
   the canonical English form).

Output dict is backward-compatible with the v2 consumer in
``cartolex.lexicon.consolidation`` (keys ``accepted``, ``rejected``,
``canonical_map``, ``translation_map``, ``term_lang``), plus two new
keys: ``typed`` (term → {verdict, lang, canonical_en}) and
``reject_reasons`` (term → letter code).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import unicodedata
from collections.abc import Callable, Collection
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .llm_filter import TermDecisionCache
from .mistral_client import (
    DEFAULT_TIMEOUT_S,
    AdaptiveThrottle,
    LLMCache,
    LLMCancelled,
    MistralClient,
    _cache_key,
)
from .prompt_store import PromptTemplate, load_prompt
from .stopwords_config import StopwordLists, packaged_lists
from .text_utils import tokenize

if TYPE_CHECKING:
    from .llm_usage import UsageRecorder
    from .prompt_store import PromptDir

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, str], None] | None

ACCEPT_CODES = frozenset({"C", "M", "O"})
REJECT_CODES = frozenset({"N", "K", "G", "F"})

# Admin-verb leads, bare generic nouns and header-noise adjectives are
# externalized as language-keyed blocks in cartolex/_data/stopwords/core.json
# (StopwordLists.admin_lead_tokens / bare_generic_nouns / header_noise_lead),
# so a new language is added by editing that JSON, not this module. Every
# check below reads the stop-word lists it is given (default: the packaged ones).


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _norm(s: str) -> str:
    return _strip_accents(s.lower())


def _norm_person_whitelist(person_whitelist: Collection[str] | None) -> frozenset[str]:
    """Accent-stripped lowercase whitelist forms, for case-insensitive matching."""
    if not person_whitelist:
        return frozenset()
    return frozenset(_norm(n.strip()) for n in person_whitelist if n and n.strip())


# Citation artefacts: a name followed by an editor/director marker as found in
# bibliographies — "vellmoor (ed.)", "smith (eds.)", "durand (dir.)". Matched on
# the accent-stripped form so "(éd.)" / "(éds.)" are caught too.
_CITATION_ARTIFACT_RE = re.compile(r"\((?:ed|eds|dir|dirs)\.?\)\s*$", re.IGNORECASE)


def is_citation_artifact(term: str) -> bool:
    """Return True when *term* ends with a bibliographic editor/director marker.

    Pure deterministic check for citation residue leaking out of reference
    lists ("vellmoor (ed.)", "martin (dirs.)", "lefebvre (éd.)"). Such strings
    are never valid keywords — they name a cited person, not a research object.
    """
    return bool(_CITATION_ARTIFACT_RE.search(_strip_accents(term)))


def _is_structural_junk(term: str) -> bool:
    """Garbled forms: markdown bold, trailing/leading hyphen, length extremes."""
    if not term or len(term) > 80:
        return True
    if term.endswith("-") or term.startswith("-"):
        return True
    if "**" in term or "__" in term:
        return True
    # Ligatures and stray spacing artefacts
    if "\ufb01" in term or "\ufb02" in term:  # fi, fl ligatures
        return True
    # All digits or pure punctuation
    stripped = re.sub(r"[\s\-]", "", term)
    if not stripped:
        return True
    if stripped.isdigit():
        return True
    # Single char or two-char unless an established acronym (kept by uppercase test)
    if len(stripped) < 3 and not term.isupper():
        return True
    return False


def _is_admin_or_junk_pattern(term: str, sw: StopwordLists) -> bool:
    return any(p.search(term) for p in sw.admin_pattern_res) or any(
        p.search(term) for p in sw.junk_pattern_res
    )


def _is_name_or_geo(term: str, tokens: list[str], sw: StopwordLists) -> bool:
    """Single name/geo token, or a bigram entirely composed of names."""
    if not tokens:
        return False
    if len(tokens) == 1:
        t = tokens[0]
        return t in sw.person_names or t in sw.geo_terms
    if len(tokens) <= 3:
        # Pure name string ("ada quillane", "ilse brevik zelmork")
        if all(t in sw.person_names for t in tokens):
            return True
        # Lab/institution geo + name
        if all(t in sw.person_names or t in sw.geo_terms for t in tokens):
            return True
    return False


def _is_function_word_phrase(tokens: list[str], sw: StopwordLists) -> bool:
    """All tokens are midwords/articles/prepositions/single-blacklist."""
    if not tokens:
        return True
    return all(
        t in sw.midwords or t in sw.single_blacklist or t in sw.basic_blacklist for t in tokens
    )


def _is_admin_lead(tokens: list[str], sw: StopwordLists) -> bool:
    """Starts with an admin / CV verb (`comprendre`, `develop`, ...)."""
    if not tokens:
        return False
    lead = _norm(tokens[0])
    return lead in sw.admin_lead_tokens


def _is_header_fragment(tokens: list[str], sw: StopwordLists) -> bool:
    """Heuristic for plural-noun list fragments ("polymères gels cristaux").

    Requires 3+ tokens, no connective / midword, no obvious adjective
    suffix, AND every token plural-looking (ends with 's'). That last
    constraint is what distinguishes real ≥3-word science phrases
    ("atomic force microscopy", "nuclear magnetic resonance") from
    CV / TOC list dumps where every entry is a plural topic label.
    Borderline cases stay for the LLM stage to judge.
    """
    if len(tokens) < 3:
        return False
    if any(t in sw.midwords for t in tokens):
        return False
    if any(t in {"vs", "and", "or", "et", "ou"} for t in tokens):
        return False
    if any(_norm(t).endswith(sw.plural_suffixes) for t in tokens):
        return False
    # All tokens must look plural (FR or EN): end in 's' or 'x' (French
    # plurals like "cristaux", "biomatériaux"). Length > 2 so we don't
    # trip on bare "is" / "as". "atomic force microscopy" -> none qualify
    # -> not a header. "polymères gels cristaux" -> all qualify.
    if not all(len(t) > 2 and t[-1] in ("s", "x") for t in tokens):
        return False
    return True


def _is_generic_lead(tokens: list[str], sw: StopwordLists) -> bool:
    """Phrase starts with a generic discourse adjective ("nouvelle thématique")."""
    if not tokens:
        return False
    return _norm(tokens[0]) in sw.header_noise_lead


def _is_bare_generic_noun(tokens: list[str], sw: StopwordLists) -> bool:
    if len(tokens) != 1:
        return False
    return _norm(tokens[0]) in sw.bare_generic_nouns


@dataclass(frozen=True)
class PrefilterReason:
    """Why the deterministic prefilter rejected a term."""

    code: str  # one of: structural, name_geo, function_word, admin, header, generic, citation
    detail: str = ""


def prefilter_terms(
    terms: list[str],
    person_whitelist: Collection[str] | None = None,
    *,
    stopwords: StopwordLists | None = None,
) -> tuple[list[str], dict[str, PrefilterReason]]:
    """Apply deterministic filters before any LLM call.

    Returns ``(survivors, rejected)`` where ``rejected`` maps term ->
    reason.  Order of survivors preserves input order; duplicates are
    deduped.  Terms matching *person_whitelist* (case- and
    accent-insensitively) are operator-approved research objects: they
    bypass every deterministic rejection — most importantly the
    person-name (``name_geo``) one. *stopwords* defaults to the packaged lists.
    """
    sw = stopwords if stopwords is not None else packaged_lists()
    wl_norm = _norm_person_whitelist(person_whitelist)
    seen: set[str] = set()
    survivors: list[str] = []
    rejected: dict[str, PrefilterReason] = {}

    for term in terms:
        t = term.strip()
        if not t or t in seen or t in rejected:
            continue

        if wl_norm and _norm(t) in wl_norm:
            survivors.append(t)
            seen.add(t)
            continue

        if _is_structural_junk(t):
            rejected[t] = PrefilterReason("structural")
            continue
        if _is_admin_or_junk_pattern(t, sw):
            rejected[t] = PrefilterReason("admin")
            continue
        if is_citation_artifact(t):
            rejected[t] = PrefilterReason("citation", "editor/director marker")
            continue

        tokens = tokenize(t)
        if not tokens:
            rejected[t] = PrefilterReason("structural", "empty token list")
            continue
        if _is_name_or_geo(t, tokens, sw):
            rejected[t] = PrefilterReason("name_geo")
            continue
        if _is_function_word_phrase(tokens, sw):
            rejected[t] = PrefilterReason("function_word")
            continue
        if _is_admin_lead(tokens, sw):
            rejected[t] = PrefilterReason("admin", "verb-led")
            continue
        if _is_generic_lead(tokens, sw):
            rejected[t] = PrefilterReason("generic", "discourse adjective")
            continue
        if _is_bare_generic_noun(tokens, sw):
            rejected[t] = PrefilterReason("generic", "bare noun")
            continue
        if _is_header_fragment(tokens, sw):
            rejected[t] = PrefilterReason("header")
            continue

        survivors.append(t)
        seen.add(t)

    return survivors, rejected


# ── LLM stage ─────────────────────────────────────────────────────────────────


@dataclass
class TypedDecision:
    verdict: str  # one of ACCEPT_CODES | REJECT_CODES
    lang: str  # "fr" | "en"
    canonical_en: str  # meaningful only when verdict ∈ ACCEPT_CODES


#: Name of the packaged typed-triage system template (``<prompt_dir>/<name>.txt``).
TYPED_PROMPT_NAME = "triage_typed_system"
#: Placeholders the packaged template must carry.
TYPED_PLACEHOLDERS = (
    "{domain_title}",
    "{subfields_block}",
    "{reference_language_name}",
    "{person_whitelist_block}",
)


def _format_subfields_block(reference_keywords: list[str]) -> str:
    if not reference_keywords:
        return "  (no reference subfields listed for this domain)"
    return "\n".join(f"  - {kw}" for kw in reference_keywords)


def _format_person_whitelist_block(person_whitelist: Collection[str]) -> str:
    """Render the operator person whitelist for the triage system prompt.

    Empty whitelist -> empty string, leaving the prompt byte-identical to the
    no-whitelist behaviour.
    """
    names = sorted({n.strip() for n in person_whitelist if n and n.strip()})
    if not names:
        return ""
    listed = ", ".join(f'"{n}"' for n in names)
    return (
        "\nWHITELIST: names on the provided whitelist are accepted as research"
        "\nobjects — classify them O with their usual canonical form, even as a"
        "\nbare surname or short form; never reject them with N."
        f"\nWhitelist: {listed}."
    )


# Workspace-level override (the context's ``paths.triage_prompt_override_txt``):
# when present and carrying the two placeholders {domain_title} and
# {subfields_block}, it replaces the packaged template.
TYPED_PROMPT_OVERRIDE_NAME = "triage_typed.txt"


def load_typed_template(
    override_path: Path | None = None,
    *,
    prompt_dir: PromptDir | None = None,
) -> PromptTemplate:
    """Return the typed-triage system template, honouring a workspace override.

    *override_path* (a workspace file) wins when it exists and carries the
    placeholders ``{domain_title}`` and ``{subfields_block}``; otherwise the
    template ``triage_typed_system.txt`` of *prompt_dir* (default: the
    packaged prompts) is loaded and checked — a missing or incomplete one
    raises :class:`~cartolex.lexicon.prompt_store.PromptTemplateError`.
    """
    if override_path is not None and Path(override_path).exists():
        try:
            txt = Path(override_path).read_text(encoding="utf-8")
            if "{domain_title}" in txt and "{subfields_block}" in txt:
                return PromptTemplate(name=TYPED_PROMPT_NAME, source=str(override_path), text=txt)
        except Exception:
            pass
    return load_prompt(
        TYPED_PROMPT_NAME, required_placeholders=TYPED_PLACEHOLDERS, prompt_dir=prompt_dir
    )


def build_typed_prompt(
    terms: list[str],
    domain_title: str,
    reference_keywords: list[str],
    *,
    template: PromptTemplate | None = None,
    reference_language: str = "en",
    person_whitelist: Collection[str] = (),
) -> tuple[str, str]:
    """Return ``(system_prompt, user_content)`` for the typed triage.

    *template* is the system template (default: the packaged one, see
    :func:`load_typed_template`). ``reference_language`` is the
    canonical-form language the LLM is asked to normalise terms into (default
    English, reproducing the historical behaviour).

    ``person_whitelist`` holds operator-approved person names surfaced to the
    LLM as force-accepted research objects via the ``{person_whitelist_block}``
    placeholder. Workspace overrides lacking the placeholder simply omit the
    block — the deterministic force-accept in :func:`post_check_typed` still
    guarantees acceptance.
    """
    from cartolex.lexicon.lang_utils import language_name

    if template is None:
        template = load_typed_template()
    system = template.render(
        domain_title=domain_title,
        subfields_block=_format_subfields_block(reference_keywords),
        reference_language_name=language_name(reference_language),
        person_whitelist_block=_format_person_whitelist_block(person_whitelist),
    )
    user = json.dumps(terms, ensure_ascii=False)
    return system, user


# Parser ----------------------------------------------------------------------

# ACCEPT lines: "C en term=canonical"  (lang mandatory). <lang> is the term's
# source-language ISO code — any two letters (generalised from the original
# en|fr) so corpora in other languages are not dropped to the "F" fallback.
_ACCEPT_RE = re.compile(r"^([CMO])\s+([a-z]{2})\s+(.+)$")
# REJECT lines: "N term" (no lang, no canonical)
_REJECT_RE = re.compile(r"^([NKGF])\s+(.+)$")


def _parse_typed_lines(text: str, input_terms: list[str]) -> dict[str, TypedDecision]:
    results: dict[str, TypedDecision] = {}
    lookup = {t.lower(): t for t in input_terms}

    for raw in text.strip().splitlines():
        line = raw.strip()
        if not line:
            continue

        m = _ACCEPT_RE.match(line)
        if m:
            code, lang, rest = m.groups()
            if "=" in rest:
                term, canonical = rest.split("=", 1)
                term = term.strip()
                canonical = canonical.strip()
            else:
                term = rest.strip()
                canonical = term
            original = lookup.get(term.lower(), term)
            results[original] = TypedDecision(code, lang, canonical)
            continue

        m = _REJECT_RE.match(line)
        if m:
            code, term = m.groups()
            term = term.strip()
            original = lookup.get(term.lower(), term)
            results[original] = TypedDecision(code, "en", original)
            continue

    # Missing => conservative reject as "F" (fragment / unparseable line).
    for t in input_terms:
        if t not in results:
            results[t] = TypedDecision("F", "en", t)
    return results


# Post-check ------------------------------------------------------------------


def _tokens_norm(s: str) -> list[str]:
    return [_norm(tok) for tok in tokenize(s)]


# The connectives are externalized (connectives_<lang> in the core stop-word
# list): StopwordLists.connectives.


def post_check_typed(
    term: str,
    decision: TypedDecision,
    person_whitelist: Collection[str] | None = None,
    *,
    stopwords: StopwordLists | None = None,
) -> TypedDecision:
    """Demote accepts whose canonical_en reorders tokens or inserts a connective.

    The LLM's most reliable failure mode is "rescuing" bag-of-words
    bigrams by emitting a fluent English form that injects ``of`` /
    ``and`` / ``interface`` or reorders nouns. We catch this cheaply by
    comparing token multisets and order.

    Terms matching *person_whitelist* (case- and accent-insensitively) are
    operator-approved research objects and are force-accepted instead: an
    LLM reject becomes an ``O`` (object of study) with the term itself as
    canonical, and no demotion applies. *stopwords* (default: the packaged
    lists) provides the connectives.
    """
    wl_norm = _norm_person_whitelist(person_whitelist)
    if wl_norm and _norm(term) in wl_norm:
        if decision.verdict in ACCEPT_CODES and decision.canonical_en.strip():
            return decision
        return TypedDecision("O", decision.lang, term)

    if decision.verdict not in ACCEPT_CODES:
        return decision

    # Defense-in-depth for the person-name policy: a citation artefact
    # ("vellmoor (ed.)") is never a valid keyword even if the LLM accepted it.
    if is_citation_artifact(term) or is_citation_artifact(decision.canonical_en):
        return TypedDecision("N", decision.lang, term)

    canon = decision.canonical_en.strip()
    if not canon:
        return TypedDecision("F", decision.lang, term)

    in_tokens = _tokens_norm(term)
    out_tokens = _tokens_norm(canon)
    if not in_tokens or not out_tokens:
        return decision

    # If lengths differ by more than one (allow plural->singular drop), reject.
    if abs(len(in_tokens) - len(out_tokens)) > 1:
        return TypedDecision("F", decision.lang, term)

    # Any new connective in the canonical that wasn't in the input is a rescue.
    extra_out = set(out_tokens) - set(in_tokens)
    connectives = (stopwords if stopwords is not None else packaged_lists()).connectives
    if extra_out & connectives:
        return TypedDecision("F", decision.lang, term)

    return decision


# Runner ----------------------------------------------------------------------


def _chunk(lst: list[Any], n: int) -> list[list[Any]]:
    return [lst[i : i + n] for i in range(0, len(lst), n)]


_TYPED_CACHE_PHASE = "typed_v3"


def run_typed_triage(
    global_terms: list[str],
    domain_title: str,
    reference_keywords: list[str],
    *,
    api_key: str,
    cache_path: Path,
    term_cache_path: Path,
    model: str = "mistral-small-latest",
    api_url: str = "https://api.mistral.ai",
    batch_size: int = 100,
    temperature: float = 0.1,
    progress: ProgressCallback = None,
    max_concurrent: int = 4,
    template: PromptTemplate | None = None,
    person_whitelist: Collection[str] = (),
    stopwords: StopwordLists | None = None,
    reference_language: str = "en",
    timeout_s: float = DEFAULT_TIMEOUT_S,
    should_cancel: Callable[[], bool] | None = None,
    usage: UsageRecorder | None = None,
) -> dict[str, Any]:
    """Run the typed single-pass triage.

    Returns a dict backward-compatible with v2 plus two new keys::

        typed           term -> {verdict, lang, canonical_en}
        reject_reasons  term -> short reason code
                        (one of: prefilter_<reason>, N, K, G, F)

    ``cache_path`` / ``term_cache_path`` are the batch and per-term answer
    caches. *template* is the system template (default: the packaged one);
    *stopwords* the lists of the deterministic prefilter and post-check
    (default: the packaged ones); live calls report their tokens to *usage*.
    Names in *person_whitelist* are force-accepted: they bypass the
    deterministic prefilter and post-check person-name rejections and are
    surfaced to the LLM in the prompt.

    ``timeout_s`` bounds every individual API call, and ``should_cancel`` — polled
    before each call and during every backoff wait — lets a GUI stop the run: an
    unattended batch of hundreds of calls must never be able to hang or to become
    unstoppable.  A batch that fails for a non-retryable reason (a refused key, an
    empty wallet) aborts the whole run at once, raising
    :class:`~cartolex.lexicon.mistral_client.LLMError` with the cause named.
    """
    cache = LLMCache(cache_path)
    term_cache = TermDecisionCache(term_cache_path)
    sw = stopwords if stopwords is not None else packaged_lists()
    if template is None:
        template = load_typed_template()

    if progress:
        progress(0, f"Prefilter: {len(global_terms)} input terms")

    survivors, pre_rejected = prefilter_terms(
        global_terms, person_whitelist=person_whitelist, stopwords=sw
    )
    if progress:
        progress(
            5,
            f"Prefilter: {len(survivors)} survived, {len(pre_rejected)} dropped deterministically",
        )

    # One throttle for the whole run: a 429 seen by any worker parks the fleet
    # and halves the ceiling, instead of each thread queueing another refusal.
    throttle = AdaptiveThrottle(max_concurrent)
    # A hard failure in one batch condemns the run (see the runner below); the
    # flag lets the calls already in flight give up at their next wait rather
    # than finish a ladder of retries nobody will read.
    abort = threading.Event()

    def _stop() -> bool:
        return abort.is_set() or bool(should_cancel and should_cancel())

    client = MistralClient(
        api_key=api_key,
        model=model,
        api_url=api_url,
        temperature=temperature,
        cache=cache,
        timeout_s=timeout_s,
        throttle=throttle,
        should_cancel=_stop,
        usage=usage,
    )

    # Per-term cache lookup
    cached: dict[str, TypedDecision] = {}
    uncached: list[str] = []
    for t in survivors:
        hit = term_cache.get(_TYPED_CACHE_PHASE, t, domain_title, model)
        if hit is not None:
            cached[t] = TypedDecision(**hit)
        else:
            uncached.append(t)
    if progress and cached:
        progress(
            8,
            f"Cache: {len(cached)} terms reused, {len(uncached)} need classification",
        )

    decisions: dict[str, TypedDecision] = dict(cached)

    if uncached:
        chunks = _chunk(uncached, batch_size)
        total = len(chunks)
        lock = threading.Lock()
        done = 0

        def _process(chunk: list[str]) -> dict[str, TypedDecision]:
            if _stop():  # abandoned run: don't open a call nobody will read
                raise LLMCancelled()
            system, user = build_typed_prompt(
                chunk,
                domain_title,
                reference_keywords,
                template=template,
                reference_language=reference_language,
                person_whitelist=person_whitelist,
            )
            ckey = _cache_key(_TYPED_CACHE_PHASE, chunk, domain_title, model)
            raw = client.chat_text(system, user, cache_key=ckey)
            return _parse_typed_lines(raw, chunk)

        def _persist(batch: dict[str, TypedDecision]) -> None:
            for t, d in batch.items():
                term_cache.put(
                    _TYPED_CACHE_PHASE,
                    t,
                    domain_title,
                    model,
                    {"verdict": d.verdict, "lang": d.lang, "canonical_en": d.canonical_en},
                )
            term_cache.flush()

        if progress:
            progress(
                10,
                f"LLM: {total} batches of ≤{batch_size} terms, "
                f"{min(max_concurrent, total)} at a time — first answers within a few minutes",
            )

        if max_concurrent <= 1:
            for i, chunk in enumerate(chunks):
                if progress:
                    pct = 10 + int(80 * i / total)
                    progress(pct, f"LLM batch {i + 1}/{total} ({len(chunk)} terms)")
                batch = _process(chunk)
                decisions.update(batch)
                _persist(batch)
        else:
            # NOT a `with` block: its __exit__ waits for every queued task, so a
            # first failure used to surface only once the other 400+ batches had
            # each walked their own retry ladder — hours of a job frozen on the
            # last progress line it ever printed. Here the first hard error
            # cancels what has not started and tells the rest to give up.
            executor = ThreadPoolExecutor(max_workers=max_concurrent)
            futures: dict = {}
            try:
                futures = {executor.submit(_process, c): c for c in chunks}
                for fut in as_completed(futures):
                    try:
                        batch = fut.result()
                    except BaseException:
                        abort.set()
                        for pending in futures:
                            pending.cancel()
                        raise
                    with lock:
                        decisions.update(batch)
                        _persist(batch)
                        done += 1
                        if progress:
                            pct = 10 + int(80 * done / total)
                            progress(pct, f"LLM {done}/{total} batches done")
            finally:
                executor.shutdown(wait=True, cancel_futures=True)
                cache.flush()
                term_cache.flush()

    cache.flush()
    term_cache.flush()

    # Post-check
    for t, d in list(decisions.items()):
        decisions[t] = post_check_typed(t, d, person_whitelist=person_whitelist, stopwords=sw)

    # Assemble output
    accepted = sorted(t for t, d in decisions.items() if d.verdict in ACCEPT_CODES)
    rejected_llm = [t for t, d in decisions.items() if d.verdict in REJECT_CODES]
    rejected_all = sorted(list(pre_rejected.keys()) + rejected_llm)

    canonical_map: dict[str, str] = {}
    translation_map: dict[str, str] = {}
    term_lang: dict[str, str] = {}
    typed_out: dict[str, dict[str, str]] = {}
    reject_reasons: dict[str, str] = {}

    for t, d in decisions.items():
        term_lang[t] = d.lang
        typed_out[t] = {
            "verdict": d.verdict,
            "lang": d.lang,
            "canonical_en": d.canonical_en,
        }
        if d.verdict in ACCEPT_CODES:
            canonical_map[t] = d.canonical_en
            if d.canonical_en != t:
                translation_map[t] = d.canonical_en
        else:
            reject_reasons[t] = d.verdict

    for t, r in pre_rejected.items():
        reject_reasons[t] = f"prefilter_{r.code}"

    for t in accepted:
        canonical_map.setdefault(t, t)

    if progress:
        n_c = sum(1 for d in decisions.values() if d.verdict == "C")
        n_m = sum(1 for d in decisions.values() if d.verdict == "M")
        n_o = sum(1 for d in decisions.values() if d.verdict == "O")
        progress(
            100,
            f"Done. Accepted {len(accepted)} (C={n_c} M={n_m} O={n_o}); "
            f"rejected {len(rejected_all)} "
            f"(prefilter={len(pre_rejected)}, llm={len(rejected_llm)}).",
        )

    return {
        "typed": typed_out,
        "reject_reasons": reject_reasons,
        "canonical_map": canonical_map,
        "translation_map": translation_map,
        "term_lang": term_lang,
        "accepted": accepted,
        "rejected": rejected_all,
        "clusters": [],  # kept for backward compat
    }
