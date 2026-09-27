# SPDX-License-Identifier: MIT
"""Stop-word lists: the packaged lists, and the profile of one run.

The packaged lists live in ``cartolex/_data/stopwords/core.json`` and are read
through :mod:`importlib.resources` (a checkout, a wheel or a zip all work),
once, on first use. They are immutable: :class:`StopwordLists` holds frozen
sets and tuples, and a run's additions and removals produce a new object
(:meth:`StopwordLists.with_overrides`) instead of editing shared ones.

A :class:`StopwordProfile` is what a run carries in its context: the packaged
lists and the run's additions and removals (the ``add`` / ``remove`` blocks of
the workspace override file, see
:func:`cartolex.lexicon.config_loader.load_overrides`). Extraction and
triage read :attr:`StopwordProfile.packaged`; consolidation reads
:attr:`StopwordProfile.adjusted` and :attr:`StopwordProfile.consolidation_blacklist`
— which is where the additions and removals have always taken effect.
"""

from __future__ import annotations

import copy
import functools
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from importlib.resources import files
from types import MappingProxyType
from typing import Any

from .config_loader import apply_overrides

#: Where the packaged lists live inside the package.
_DATA_PACKAGE = "cartolex._data"
_CORE_FILE = ("stopwords", "core.json")


def _as_set(values: Iterable[str] | None) -> frozenset[str]:
    if not values:
        return frozenset()
    return frozenset(str(v).strip() for v in values if isinstance(v, str) and str(v).strip())


def _as_tuple(values: Iterable[str] | None) -> tuple[str, ...]:
    if not values:
        return ()
    return tuple(str(v).strip() for v in values if isinstance(v, str) and str(v).strip())


def _as_merge_map(values: Mapping | None) -> dict[str, str]:
    if not values:
        return {}
    merged: dict[str, str] = {}
    for key, value in values.items():
        k = str(key).strip().lower()
        v = str(value).strip().lower()
        if k and v:
            merged[k] = v
    return merged


def _union_lang_sets(data: Mapping[str, Any], prefix: str, suffix: str = "") -> frozenset[str]:
    """Union of every ``<prefix><lang><suffix>`` list in *data*.

    Language-agnostic: adding a ``midwords_<lang>`` / ``blacklist_<lang>_base``
    block to ``stopwords/core.json`` is enough to include a new language — no
    code change needed.
    """
    out: set[str] = set()
    for key, val in data.items():
        if key.startswith(prefix) and key.endswith(suffix) and len(key) > len(prefix) + len(suffix):
            out |= _as_set(val)
    return frozenset(out)


def load_packaged_data() -> dict[str, Any]:
    """Read the packaged stop-word file (a fresh dict on every call)."""
    resource = files(_DATA_PACKAGE)
    for part in _CORE_FILE:
        resource = resource / part
    try:
        data = json.loads(resource.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Packaged stop-word lists not found: {resource} (the package is incomplete)"
        ) from exc
    except ValueError as exc:
        raise ValueError(f"Packaged stop-word lists are not valid JSON: {resource}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Packaged stop-word lists must be a JSON object: {resource}")
    return data


@dataclass(frozen=True)
class StopwordLists:
    """One immutable set of stop-word lists (all sets frozen, patterns in tuples)."""

    midwords: frozenset[str]
    basic_blacklist: frozenset[str]
    admin_lead_tokens: frozenset[str]
    bare_generic_nouns: frozenset[str]
    header_noise_lead: frozenset[str]
    connectives: frozenset[str]
    plural_suffixes: tuple[str, ...]
    admin_tokens: frozenset[str]
    admin_patterns: tuple[str, ...]
    junk_patterns: tuple[str, ...]
    geo_terms: frozenset[str]
    org_acronyms: frozenset[str]
    merge_map: Mapping[str, str]
    person_names: frozenset[str]
    single_blacklist: frozenset[str]
    #: Every list of the source file, by its key (``"midwords_pt"``, …).
    blocks: Mapping[str, frozenset[str]] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> StopwordLists:
        """Build the lists from the content of a stop-word file."""
        geo = _as_set(data.get("geo_terms"))
        for key in (
            "geo_france",
            "geo_countries",
            "geo_us_cities",
            "geo_de_cities",
            "geo_uk_cities",
            "eu_capitals",
        ):
            geo |= _as_set(data.get(key))
        return cls(
            midwords=_union_lang_sets(data, "midwords_"),
            basic_blacklist=_union_lang_sets(data, "blacklist_", "_base"),
            admin_lead_tokens=_union_lang_sets(data, "admin_lead_tokens_"),
            bare_generic_nouns=_union_lang_sets(data, "bare_generic_nouns_"),
            header_noise_lead=_union_lang_sets(data, "header_noise_lead_"),
            connectives=_union_lang_sets(data, "connectives_"),
            plural_suffixes=tuple(sorted(_union_lang_sets(data, "plural_suffixes_"))),
            admin_tokens=_as_set(data.get("admin_tokens")),
            admin_patterns=_as_tuple(data.get("admin_patterns")),
            junk_patterns=_as_tuple(data.get("junk_patterns")),
            geo_terms=geo,
            org_acronyms=_as_set(data.get("org_acronyms")),
            merge_map=MappingProxyType(_as_merge_map(data.get("merge_map"))),
            person_names=_as_set(data.get("person_names")),
            single_blacklist=_as_set(data.get("single_blacklist")),
            blocks=MappingProxyType(
                {k: _as_set(v) for k, v in data.items() if isinstance(v, list)}
            ),
        )

    def block(self, key: str) -> frozenset[str]:
        """One list of the source file by its key (empty when absent), e.g. ``"midwords_pt"``."""
        return self.blocks.get(key, frozenset())

    def with_overrides(self, overrides: Mapping[str, Any]) -> StopwordLists:
        """These lists with the ``add`` / ``remove`` blocks of *overrides* applied.

        The same rules as always (see
        :func:`~cartolex.lexicon.config_loader.apply_overrides`):
        tokens are lower-cased and stripped, patterns stripped, and the merge
        map gains or loses entries. Lists the override file cannot name
        (connectives, plural suffixes, …) are unchanged. ``self`` is not
        modified.
        """
        lists = {
            "midwords": set(self.midwords),
            "basic_blacklist": set(self.basic_blacklist),
            "admin_tokens": set(self.admin_tokens),
            "admin_patterns": list(self.admin_patterns),
            "junk_patterns": list(self.junk_patterns),
            "geo_terms": set(self.geo_terms),
            "org_acronyms": set(self.org_acronyms),
            "person_names": set(self.person_names),
            "merge_map": dict(self.merge_map),
            "single_blacklist": set(self.single_blacklist),
        }
        apply_overrides(overrides=dict(overrides), **lists)
        return StopwordLists(
            midwords=frozenset(lists["midwords"]),
            basic_blacklist=frozenset(lists["basic_blacklist"]),
            admin_lead_tokens=self.admin_lead_tokens,
            bare_generic_nouns=self.bare_generic_nouns,
            header_noise_lead=self.header_noise_lead,
            connectives=self.connectives,
            plural_suffixes=self.plural_suffixes,
            admin_tokens=frozenset(lists["admin_tokens"]),
            admin_patterns=tuple(lists["admin_patterns"]),
            junk_patterns=tuple(lists["junk_patterns"]),
            geo_terms=frozenset(lists["geo_terms"]),
            org_acronyms=frozenset(lists["org_acronyms"]),
            merge_map=MappingProxyType(lists["merge_map"]),
            person_names=frozenset(lists["person_names"]),
            single_blacklist=frozenset(lists["single_blacklist"]),
            blocks=self.blocks,
        )

    @functools.cached_property
    def admin_pattern_res(self) -> tuple[re.Pattern[str], ...]:
        """The administrative patterns, compiled (case-insensitive)."""
        return tuple(re.compile(p, re.IGNORECASE) for p in self.admin_patterns)

    @functools.cached_property
    def junk_pattern_res(self) -> tuple[re.Pattern[str], ...]:
        """The junk patterns, compiled (case-insensitive)."""
        return tuple(re.compile(p, re.IGNORECASE) for p in self.junk_patterns)


@functools.lru_cache(maxsize=1)
def packaged_lists() -> StopwordLists:
    """The packaged lists, read once (immutable, so sharing them is safe)."""
    return StopwordLists.from_data(load_packaged_data())


@dataclass(frozen=True)
class StopwordProfile:
    """The stop words of one run: the packaged lists and the run's additions and removals.

    ``overrides`` is the content of the workspace override file (empty when
    there is none). Extraction and triage use :attr:`packaged`; consolidation
    uses :attr:`adjusted` and :attr:`consolidation_blacklist`.
    """

    packaged: StopwordLists
    overrides: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def default(cls) -> StopwordProfile:
        """The packaged lists with no additions or removals."""
        return cls(packaged=packaged_lists())

    def with_overrides(self, overrides: Mapping[str, Any] | None) -> StopwordProfile:
        """This profile with *overrides* as the run's additions and removals (copied)."""
        frozen = MappingProxyType(copy.deepcopy(dict(overrides or {})))
        return StopwordProfile(packaged=self.packaged, overrides=frozen)

    @functools.cached_property
    def adjusted(self) -> StopwordLists:
        """The packaged lists with the run's additions and removals applied."""
        if not self.overrides:
            return self.packaged
        return self.packaged.with_overrides(self.overrides)

    @property
    def consolidation_blacklist(self) -> frozenset[str]:
        """The global blacklist consolidation applies.

        The union of the adjusted base blacklist, administrative tokens,
        geographic terms, acronyms, common names and single-word blacklist —
        but only when the run has an override file: without one, consolidation
        has always applied no global blacklist of its own (extraction already
        filtered with the packaged lists).
        """
        if not self.overrides:
            return frozenset()
        lists = self.adjusted
        return (
            lists.basic_blacklist
            | lists.admin_tokens
            | lists.geo_terms
            | lists.org_acronyms
            | lists.person_names
            | lists.single_blacklist
        )
