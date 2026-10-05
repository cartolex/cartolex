# SPDX-License-Identifier: MIT
"""Merging works found by several finders: one work, one text.

The same work often comes from several finders (OpenAlex, HAL, SciELO, ORCID,
an import). When the source tables are rebuilt, the texts of a slot are merged
by three rules, tried in this order:

1. **same DOI** (lower case, no ``https://doi.org/``); the rule is named
   ``hal_doi`` when one side's DOI is the link a HAL deposit states;
2. **a source link**: the same identifier under the same scheme (HAL, arXiv,
   PubMed, PMC, SciELO, OpenAlex) on both sides;
3. **title and year, per person**: two texts of the same person with the same
   normalised title (at least three words) and years at most one apart.

A merge is refused when the two sides carry different DOIs, or (for the third
rule) document types that cannot be the same work (a thesis and an article).
A **preprint** and a **published version** (article, review, communication,
proceedings, chapter or report) that meet by the third rule, or that a
provider links (the published DOI arXiv or bioRxiv gives), are **not merged**:
they stay two texts, the preprint's ``version_of`` naming the published one,
and the build reads only the published version (see
``docs/format/sources.md``). A preprint that meets several published texts by
the third rule (an article and its conference version) is linked to the
version of record among them (:data:`~cartolex.project.corpus.VERSION_RANK`:
an article before a review, a chapter, a conference version); it stays
unlinked when two of them rank the same.

Fields are filled one by one: each takes the value of the highest-priority
finder that has one (:data:`FINDER_PRIORITY`, a parameter), ties going to
the latest record, then to the smallest value. A value that another finder
gives differently is kept as a **conflict** in the merge log; nothing is
overwritten silently. The surviving id is the smallest id of the group, so a
text keeps the id it had first.

:func:`merge_works` is a pure function over :class:`WorkRecord` rows: the same
records in any order give the same result, and merging its result again
changes nothing. :func:`merge_texts` applies it to a
:class:`~cartolex.collect.tables.SourceBuilder` (texts, parts, authorships),
and :func:`write_merge_log` writes ``sources/merges.json``.
"""

from __future__ import annotations

import bisect
import hashlib
import itertools
import json
import os
import tempfile
from array import array
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from cartolex.project.corpus import version_rank
from cartolex.project.files import atomic_write_bytes, replace_path
from cartolex.project.layout import ProjectLayout

from .finders import normalise_doi, normalise_title

__all__ = [
    "FINDER_PRIORITY",
    "LINK_SCHEMES",
    "MERGE_LOG_FORMAT",
    "Conflict",
    "Merge",
    "MergeLog",
    "MergeResult",
    "Refusal",
    "VersionLink",
    "WorkRecord",
    "merge_texts",
    "merge_works",
    "write_merge_log",
]

#: Which finder's value wins, field by field, earlier first; finders not listed come after,
#: in name order. A journal platform (SciELO) states the version of record, so it comes
#: before an open archive (HAL), whose dates are sometimes the deposit's.
FINDER_PRIORITY: tuple[str, ...] = (
    "import",
    "folder",
    "corpus",
    "openalex",
    "scielo",
    "hal",
    "orcid",
)
#: Identifier schemes that name one work: the same value on two texts makes them one.
LINK_SCHEMES: tuple[str, ...] = ("hal", "arxiv", "pmid", "pmcid", "scielo", "openalex")
MERGE_LOG_FORMAT = "cartolex-merges/1"
#: A title shorter than this (in words) never merges two texts on its own.
MIN_TITLE_WORDS = 3
MAX_YEAR_GAP = 1
#: Types a preprint can become: a preprint meeting one of these is its earlier version.
PUBLISHED_TYPES = frozenset(
    {"article", "review", "communication", "proceedings", "chapter", "report"}
)
#: Types that can name the same work although they differ (a paper typed as an article
#: by one finder and as a communication by another).
_COMPATIBLE = (frozenset({"article", "communication", "chapter", "other"}),)
_FIELDS = ("title", "doc_type", "year", "date", "doi", "n_authors")
_RULE_RANK = {"doi": 0, "hal_doi": 0, "link": 1, "title_year": 2}


@dataclass(frozen=True)
class WorkRecord:
    """One finder's record of a text, as the merge sees it."""

    text_id: str
    slot: str
    source: str
    title: str
    doc_type: str
    retrieved_at: datetime
    year: int | None = None
    date: str | None = None
    doi: str | None = None
    ids: Mapping[str, str] = field(default_factory=dict)
    version_of: str | None = None
    n_authors: int = 0
    keys: tuple[str, ...] = ()
    people: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Merge:
    """Texts joined into one: the id kept, the ids merged into it, the rule and its evidence."""

    slot: str
    kept: str
    merged: tuple[str, ...]
    rule: str
    evidence: str


@dataclass(frozen=True)
class VersionLink:
    """A preprint and the published version it became: two texts, one ``version_of``."""

    slot: str
    preprint: str
    published: str
    rule: str
    evidence: str


@dataclass(frozen=True)
class Refusal:
    """Two texts a rule matched but that were kept apart, and why."""

    slot: str
    texts: tuple[str, str]
    rule: str
    reason: str


@dataclass(frozen=True)
class Conflict:
    """A field two finders give differently: the value kept, and the one set aside."""

    text_id: str
    field: str
    kept: Any
    kept_from: str
    other: Any
    other_from: str


@dataclass
class MergeResult:
    """What merging gave: the texts' fields, where each came from, and the log."""

    texts: dict[str, dict[str, Any]] = field(default_factory=dict)
    provenance: dict[str, dict[str, str]] = field(default_factory=dict)
    merged_into: dict[str, str] = field(default_factory=dict)
    merges: list[Merge] = field(default_factory=list)
    versions: list[VersionLink] = field(default_factory=list)
    refused: list[Refusal] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    fills: list[dict[str, str]] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        """Merges per rule, version links, refusals and conflicts."""
        out: dict[str, int] = {}
        for m in self.merges:
            # Records a finder key had already joined into one text are counted apart.
            what = f"merged by {m.rule}" if m.merged else "joined by a shared key"
            out[what] = out.get(what, 0) + max(1, len(m.merged))
        out["version links"] = len(self.versions)
        out["refused"] = len(self.refused)
        out["conflicts"] = len(self.conflicts)
        return out


def _rank(priority: Sequence[str]):
    order = {name: i for i, name in enumerate(priority)}
    return lambda source: (order.get(source, len(order)), source)


def _stamp(ts: datetime) -> float:
    return ts.timestamp() if ts.tzinfo else ts.replace(tzinfo=timezone.utc).timestamp()


def _content(record: WorkRecord) -> str:
    """A record's values, whatever text id it carries (the last tie-break)."""
    return repr(replace(record, text_id=""))


def _same(fld: str, a: Any, b: Any) -> bool:
    if fld == "title":
        return normalise_title(a) == normalise_title(b)
    if fld == "doi":
        return normalise_doi(a) == normalise_doi(b)
    return a == b


def _choose(records: Sequence[WorkRecord], rank) -> tuple[dict[str, Any], dict[str, str], list]:
    """Fields of one text from its records: per field, the best-ranked record with a value."""
    fields: dict[str, Any] = {}
    prov: dict[str, str] = {}
    conflicts: list[tuple[str, Any, str, Any, str]] = []
    ordered = sorted(records, key=lambda r: (rank(r.source), -_stamp(r.retrieved_at), _content(r)))
    for fld in _FIELDS:
        with_value = [r for r in ordered if getattr(r, fld) not in (None, "", 0)]
        if not with_value:
            fields[fld] = 0 if fld == "n_authors" else None
            continue
        best = with_value[0]
        fields[fld] = getattr(best, fld)
        prov[fld] = best.source
        seen: set[str] = set()
        for other in with_value[1:]:
            value = getattr(other, fld)
            if other.source == best.source or _same(fld, value, fields[fld]):
                continue
            if other.source in seen:
                continue
            seen.add(other.source)
            conflicts.append((fld, fields[fld], best.source, value, other.source))
    ids: dict[str, str] = {}
    for r in ordered:
        for scheme, value in sorted(r.ids.items()):
            if scheme not in ids:
                ids[scheme] = value
                prov.setdefault(f"ids.{scheme}", r.source)
            elif ids[scheme] != value and prov.get(f"ids.{scheme}") != r.source:
                conflicts.append(
                    (f"ids.{scheme}", ids[scheme], prov[f"ids.{scheme}"], value, r.source)
                )
    fields["ids"] = ids
    fields["doi"] = normalise_doi(fields["doi"])
    stated = sorted({r.version_of for r in records if r.version_of})
    fields["version_of"] = stated[0] if stated else None
    best = ordered[0]
    fields["source"] = best.source
    fields["retrieved_at"] = max(r.retrieved_at for r in records)
    fields["slot"] = best.slot
    return fields, prov, conflicts


class _Groups:
    """Union-find over text ids, with each group's DOIs, types and members; the smallest id
    of a group names it."""

    def __init__(self, by_id: Mapping[str, Sequence[WorkRecord]]) -> None:
        self.parent = {i: i for i in by_id}
        self.dois = {i: _dois(recs) for i, recs in by_id.items()}
        self.types = {i: {r.doc_type for r in recs} for i, recs in by_id.items()}
        self.members = {i: [i] for i in by_id}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> tuple[str, list[str]]:
        """Join the groups of *a* and *b*; returns the id kept and the ids joined to it."""
        ra, rb = self.find(a), self.find(b)
        keep, drop = (ra, rb) if ra <= rb else (rb, ra)
        self.parent[drop] = keep
        self.dois[keep] |= self.dois.pop(drop)
        self.types[keep] |= self.types.pop(drop)
        joined = self.members.pop(drop)
        self.members[keep] = sorted(self.members[keep] + joined)
        return keep, joined


def _compatible(a: set[str], b: set[str]) -> bool:
    if a & b:
        return True
    return any(a <= group and b <= group for group in _COMPATIBLE)


def merge_works(
    records: Iterable[WorkRecord],
    *,
    priority: Sequence[str] = FINDER_PRIORITY,
    stated_versions: Iterable[tuple[str, str]] = (),
) -> MergeResult:
    """Merge *records* into texts by the three rules (see the module docstring).

    *stated_versions* are ``(text id, DOI)`` pairs a provider gave: the text is a
    preprint whose published version has that DOI. Records of one text id were
    joined already (they share a finder's key). The result does not depend on
    the order of *records*, and merging it again changes nothing.
    """
    rank = _rank(priority)
    by_id: dict[str, list[WorkRecord]] = defaultdict(list)
    for rec in records:
        by_id[rec.text_id].append(rec)
    result = MergeResult()
    units: dict[str, dict[str, Any]] = {}
    people: dict[str, set[str]] = defaultdict(set)
    for tid in sorted(by_id):
        recs = by_id[tid]
        units[tid] = _choose(recs, rank)[0]
        for r in recs:
            people[tid] |= r.people
        sources = sorted({r.source for r in recs}, key=rank)
        if len(sources) > 1:
            shared = set(recs[0].keys).intersection(*(set(r.keys) for r in recs[1:]))
            result.merges.append(
                Merge(
                    units[tid]["slot"],
                    tid,
                    (),
                    "doi" if any(k.startswith("doi:") for k in shared) else "key",
                    f"{' + '.join(sources)}: " + (", ".join(sorted(shared)) or "same key"),
                )
            )
    by_slot: dict[str, list[str]] = defaultdict(list)
    for tid in sorted(units):
        by_slot[units[tid]["slot"]].append(tid)
    groups = _Groups(by_id)
    version_edges: list[tuple[str, str, str, str]] = []
    for slot, tids in sorted(by_slot.items()):
        for rule, a, b, evidence in _edges(tids, units, by_id, people):
            ra, rb = groups.find(a), groups.find(b)
            if ra == rb:
                continue
            types_a, types_b = groups.types[ra], groups.types[rb]
            if rule == "title_year":
                pre_a, pre_b = types_a == {"preprint"}, types_b == {"preprint"}
                if pre_a != pre_b and (types_b if pre_a else types_a) & PUBLISHED_TYPES:
                    pre, pub = (a, b) if pre_a else (b, a)
                    version_edges.append((pre, pub, rule, evidence))
                    continue
                if not _compatible(types_a, types_b):
                    reason = f"types {sorted(types_a)} and {sorted(types_b)}"
                    result.refused.append(Refusal(slot, (a, b), rule, reason))
                    continue
            dois_a, dois_b = groups.dois[ra], groups.dois[rb]
            if dois_a and dois_b and not dois_a & dois_b:
                reason = f"different DOIs {sorted(dois_a)} and {sorted(dois_b)}"
                result.refused.append(Refusal(slot, (a, b), rule, reason))
                continue
            keep, joined = groups.union(a, b)
            result.merges.append(Merge(slot, keep, tuple(joined), rule, evidence))
    roots = {t: groups.find(t) for t in units}
    result.merged_into = dict(sorted(roots.items()))
    members: dict[str, list[str]] = defaultdict(list)
    for t, root in result.merged_into.items():
        members[root].append(t)
    for root, tids in sorted(members.items()):
        recs = [r for t in tids for r in by_id[t]]
        fields, prov, conflicts = _choose(recs, rank)
        fields["text_id"] = root
        fields["version_of"] = roots.get(fields["version_of"], fields["version_of"])
        result.texts[root] = fields
        result.provenance[root] = prov
        for fld, kept, kept_from, other, other_from in conflicts:
            result.conflicts.append(Conflict(root, fld, kept, kept_from, other, other_from))
    _versions(result, roots, stated_versions, version_edges)
    result.merges.sort(key=lambda m: (m.slot, m.kept, m.merged, m.rule))
    result.refused.sort(key=lambda r: (r.slot, r.texts, r.rule))
    result.conflicts.sort(key=lambda c: (c.text_id, c.field, c.other_from, repr(c.other)))
    return result


def _versions(
    result: MergeResult,
    roots: Mapping[str, str],
    stated_versions: Iterable[tuple[str, str]],
    version_edges: Sequence[tuple[str, str, str, str]],
) -> None:
    """Link preprints to their published version: a provider's statement first, then the
    title rule; a preprint that meets several published texts is left unlinked."""
    texts = result.texts
    by_doi: dict[tuple[str, str], str] = {}
    for root, fields in sorted(texts.items()):
        if fields["doi"]:
            by_doi.setdefault((fields["slot"], fields["doi"]), root)
    chosen: dict[str, tuple[str, str, str]] = {}
    said: dict[str, dict[str, str]] = defaultdict(dict)
    for tid, doi in sorted(set(stated_versions)):
        d = normalise_doi(doi)
        pre = roots.get(tid)
        if pre is None or not d or texts[pre]["doi"] == d:
            continue
        pub = by_doi.get((texts[pre]["slot"], d))
        if pub and pub != pre:
            said[pre].setdefault(pub, d)
    for pre, pubs in sorted(said.items()):
        if len(pubs) == 1:
            ((pub, d),) = pubs.items()
            chosen[pre] = (pub, "stated", d)
        else:
            reason = f"providers name several published versions {sorted(pubs)}"
            result.refused.append(Refusal(texts[pre]["slot"], (pre, min(pubs)), "stated", reason))
    candidates: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)
    for pre, pub, rule, evidence in version_edges:
        rp, rb = roots[pre], roots[pub]
        if rp != rb:
            candidates[rp].setdefault(rb, (rule, evidence))
    for pre, pubs in sorted(candidates.items()):
        if pre in chosen:
            continue
        best = _version_of_record(pubs, texts)
        if best is not None:
            rule, evidence = pubs[best]
            chosen[pre] = (best, rule, evidence)
        else:
            slot = texts[pre]["slot"]
            result.refused.append(
                Refusal(
                    slot,
                    (pre, min(pubs)),
                    "title_year",
                    f"several published versions {sorted(pubs)}",
                )
            )
    for pre, (pub, rule, evidence) in sorted(chosen.items()):
        if texts[pub].get("version_of") == pre:
            continue  # never a loop
        texts[pre]["version_of"] = pub
        result.versions.append(VersionLink(texts[pre]["slot"], pre, pub, rule, evidence))


def _version_of_record(
    pubs: Mapping[str, Any], texts: Mapping[str, Mapping[str, Any]]
) -> str | None:
    """The one published text of *pubs* that ranks first as the version of record, or ``None``
    when two rank the same (see :data:`~cartolex.project.corpus.VERSION_RANK`)."""
    if len(pubs) == 1:
        return next(iter(pubs))
    ranked = sorted((version_rank(texts[p]["doc_type"]), p) for p in pubs)
    if ranked[0][0] == ranked[1][0]:
        return None
    return ranked[0][1]


def _dois(records: Iterable[WorkRecord]) -> set[str]:
    return {d for d in (normalise_doi(r.doi) for r in records) if d}


def _edges(
    tids: Sequence[str],
    units: Mapping[str, Mapping[str, Any]],
    by_id: Mapping[str, Sequence[WorkRecord]],
    people: Mapping[str, set[str]],
) -> list[tuple[str, str, str, str]]:
    """Every pair a rule matches, as ``(rule, a, b, evidence)`` in a fixed order.

    Every record of a text counts (its DOI, its identifiers, its own title, year
    and people), not only the values the text kept: a merged text then matches
    exactly what its records matched, and merging again changes nothing.
    """
    edges: list[tuple[str, str, str, str]] = []
    by_doi: dict[str, list[str]] = defaultdict(list)
    for t in tids:
        for d in sorted(_dois(by_id[t])):
            by_doi[d].append(t)
    for doi, group in sorted(by_doi.items()):
        for other in group[1:]:
            sources = {r.source for t in (group[0], other) for r in by_id[t]
                       if normalise_doi(r.doi) == doi}  # fmt: skip
            rule = "hal_doi" if "hal" in sources else "doi"
            edges.append((rule, group[0], other, doi))
    for scheme in LINK_SCHEMES:
        by_value: dict[str, list[str]] = defaultdict(list)
        for t in tids:
            values = {r.ids.get(scheme) for r in by_id[t]} - {None}
            for v in values:
                by_value[str(v)].append(t)
        for value, group in sorted(by_value.items()):
            group = sorted(set(group))
            for other in group[1:]:
                edges.append((f"link:{scheme}", group[0], other, f"{scheme} {value}"))
    by_person_title: dict[tuple[str, str], set[tuple[str, int]]] = defaultdict(set)
    for t in tids:
        for r in by_id[t]:
            title = normalise_title(r.title)
            if len(title.split()) < MIN_TITLE_WORDS or r.year is None:
                continue
            for pid in r.people:
                by_person_title[(pid, title)].add((t, r.year))
    pairs: dict[tuple[str, str], str] = {}
    for (pid, title), found in sorted(by_person_title.items()):
        ordered = sorted(found)
        for i, (a, ya) in enumerate(ordered):
            for b, yb in ordered[i + 1 :]:
                if a != b and abs(ya - yb) <= MAX_YEAR_GAP:
                    pairs.setdefault((min(a, b), max(a, b)), f"{pid}: {title[:60]}")
    edges += [("title_year", a, b, ev) for (a, b), ev in sorted(pairs.items())]
    edges.sort(key=lambda e: (_RULE_RANK.get(e[0].split(":")[0], 9), e[1], e[2], e[0]))
    return edges


# ── applying to a source builder ─────────────────────────────────────────────


def _sortable(*parts: str | Sequence[str]) -> str:
    """A string that sorts as the tuple of *parts* (strings, or tuples of strings) sorts."""
    out = []
    for part in parts:
        if isinstance(part, str):
            out.append(part + "\x00")
        else:
            out.append("".join(p + "\x01" for p in part) + "\x00")
    return "".join(out)


class MergeLog:
    """What merging the texts of a rebuild gave, kept in the scratch database: each text's
    fields (written to the texts table), the log's entries (``sources/merges.json``) and
    the counts per rule."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self._counts: dict[str, int] = {}
        #: Text id → the id it was merged into (texts that disappear).
        self.moved: dict[str, str] = {}
        #: Every version link, for the abstracts published versions take from their preprint.
        self.versions: list[VersionLink] = []
        self._seq = 0

    def counts(self) -> dict[str, int]:
        """Merges per rule, version links, refusals and conflicts."""
        out = dict(self._counts)
        for what in ("version links", "refused", "conflicts"):
            out.setdefault(what, 0)
        return out

    def _entry(self, kind: str, sortkey: str, doc: Mapping[str, Any]) -> None:
        self._seq += 1
        text = json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True)
        self.store.execute(
            "INSERT INTO merge_log (kind, sortkey, seq, doc) VALUES (?, ?, ?, ?)",
            (kind, sortkey, self._seq, text),
        )

    def conflict(self, c: Conflict) -> None:
        doc = {**asdict(c), "kept": _plain(c.kept), "other": _plain(c.other)}
        self._entry("conflicts", _sortable(c.text_id, c.field, c.other_from, repr(c.other)), doc)
        self._counts["conflicts"] = self._counts.get("conflicts", 0) + 1

    def fill(self, fill: Mapping[str, str]) -> None:
        self._entry("fills", f"{self._seq:012d}", fill)

    def add(self, result: MergeResult) -> None:
        """Keep one group's result: its texts' fields, the moves, the log's entries."""
        for tid, fields in result.texts.items():
            data = json.dumps(_plain(fields), ensure_ascii=False, separators=(",", ":"))
            self.store.execute(
                "INSERT INTO texts_out VALUES (?, ?, ?, ?, ?)",
                (tid, fields["slot"], fields["year"], fields["date"], data),
            )
        self.moved.update({old: new for old, new in result.merged_into.items() if old != new})
        for what, n in result.counts().items():
            if what != "conflicts":
                self._counts[what] = self._counts.get(what, 0) + n
        for m in result.merges:
            self._entry("merges", _sortable(m.slot, m.kept, m.merged, m.rule), asdict(m))
        for v in result.versions:
            self.versions.append(v)
            self._entry("versions", _sortable(v.preprint), asdict(v))
        for x in result.refused:
            self._entry("refused", _sortable(x.slot, x.texts, x.rule), asdict(x))
        for c in result.conflicts:
            self.conflict(c)

    def write_log(self, layout: ProjectLayout) -> None:
        """Write ``sources/merges.json`` (as :func:`write_merge_log`), an entry at a time."""
        path = layout.sources / "merges.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
                out.write("{\n")
                names = ("conflicts", "fills", "format", "merges", "refused", "versions")
                for i, name in enumerate(names):
                    end = ",\n" if i < len(names) - 1 else "\n"
                    if name == "format":
                        out.write(f' "format": {json.dumps(MERGE_LOG_FORMAT)}{end}')
                        continue
                    docs = self.store.rows(
                        "SELECT doc FROM merge_log WHERE kind = ? ORDER BY sortkey, seq", (name,)
                    )
                    first = True
                    for (doc,) in docs:
                        out.write(f' "{name}": [\n' if first else ",\n")
                        out.write("\n".join("  " + line for line in doc.split("\n")))
                        first = False
                    out.write(f' "{name}": []{end}' if first else f"\n ]{end}")
                out.write("}\n")
                out.flush()
                os.fsync(out.fileno())
            replace_path(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def _hash(key: str) -> int:
    return int.from_bytes(hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest(), "little")


def _candidate_keys(records: Sequence[WorkRecord], stated: Iterable[str]) -> set[str]:
    """Every key by which a rule could join a text to another (more than the rules join:
    a group of candidates is merged by :func:`merge_works`, which applies the rules)."""
    keys: set[str] = set()
    for r in records:
        doi = normalise_doi(r.doi)
        if doi:
            keys.add(f"doi\x00{doi}")
        for scheme in LINK_SCHEMES:
            value = r.ids.get(scheme)
            if value is not None:
                keys.add(f"link\x00{scheme}\x00{value}")
        title = normalise_title(r.title)
        if r.year is not None and len(title.split()) >= MIN_TITLE_WORDS:
            for pid in r.people:
                keys.add(f"title\x00{pid}\x00{title}")
    for doi in stated:  # a preprint meets the texts of its published version's DOI
        d = normalise_doi(doi)
        if d:
            keys.add(f"doi\x00{d}")
    return keys


def _work_record(tid: str, n_authors: int | None, data: str, people: frozenset[str]) -> WorkRecord:
    d = json.loads(data)
    at = datetime.fromisoformat(d["retrieved_at"])
    return WorkRecord(
        text_id=tid,
        slot=d["slot"],
        source=d["source"],
        title=d["title"],
        doc_type=d["doc_type"],
        retrieved_at=at if at.tzinfo else at.replace(tzinfo=timezone.utc),
        year=d["year"],
        date=d["date"],
        doi=d["doi"],
        ids=d["ids"],
        version_of=d["version_of"],
        n_authors=n_authors or 0,
        keys=tuple(d["keys"]),
        people=people,
    )


def _texts_records(store: Any, *, selected: bool = False) -> Iterator[tuple[str, list[WorkRecord]]]:
    """Every text's records, text by text in id order, each with the people of the text
    (only the texts in the ``selected`` table with *selected*)."""
    where = " WHERE tid IN (SELECT tid FROM selected)" if selected else ""
    authors = itertools.groupby(
        store.rows(f"SELECT tid, pid FROM authorships{where} ORDER BY tid, pid"),
        key=lambda r: r[0],
    )
    pending = next(authors, None)
    records = store.rows(f"SELECT tid, n_authors, data FROM records{where} ORDER BY tid, seq")
    for tid, rows in itertools.groupby(records, key=lambda r: r[0]):
        while pending is not None and pending[0] < tid:
            pending = next(authors, None)
        people = frozenset()
        if pending is not None and pending[0] == tid:
            people = frozenset(pid for _t, pid in pending[1])
        yield tid, [_work_record(tid, n, data, people) for _t, n, data in rows]


def _groups(store: Any, stated: Mapping[str, list[str]]) -> tuple[list[str], np.ndarray]:
    """The texts in id order, and for each, its group: the texts some candidate key joins."""
    tids: list[str] = []
    hashes, owners = array("Q"), array("q")
    named: list[tuple[int, str]] = []  # a record naming the text it is a version of
    for i, (tid, records) in enumerate(_texts_records(store)):
        tids.append(tid)
        for key in _candidate_keys(records, stated.get(tid, ())):
            hashes.append(_hash(key))
            owners.append(i)
        named += [(i, r.version_of) for r in records if r.version_of]
    n = len(tids)
    a = b = np.zeros(0, dtype=np.int64)
    if hashes:
        h = np.frombuffer(hashes, dtype=np.uint64)
        o = np.frombuffer(owners, dtype=np.int64)
        order = np.lexsort((o, h))
        h, o = h[order], o[order]
        same = np.flatnonzero(h[1:] == h[:-1])
        a, b = o[same], o[same + 1]
    if named:  # the texts are in id order: the one named is found by bisection
        pairs = [
            (i, j) for i, t in named if (j := bisect.bisect_left(tids, t)) < n and tids[j] == t
        ]
        if pairs:
            a = np.concatenate([a, np.array([p[0] for p in pairs], dtype=np.int64)])
            b = np.concatenate([b, np.array([p[1] for p in pairs], dtype=np.int64)])
    if not len(a):
        return tids, np.arange(n)
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    graph = coo_matrix((np.ones(len(a), dtype=np.int8), (a, b)), shape=(n, n))
    _count, labels = connected_components(graph, directed=False)
    return tids, labels


def merge_texts(builder: Any, *, priority: Sequence[str] = FINDER_PRIORITY) -> MergeLog:
    """Merge the texts a :class:`~cartolex.collect.tables.SourceBuilder` holds, in its
    scratch database.

    Texts are first put in groups: the texts some key could join (a DOI, a source link,
    a person's title). Each group is merged by :func:`merge_works`, the texts of most
    groups alone, so memory holds one group at a time. The texts kept go to the scratch
    database's ``texts_out``; the texts merged into another disappear, their parts and
    authorships moving to the text kept (a clash keeps the most recent part, the smallest
    position). A published version without an abstract gets the abstract parts of its
    preprint. Returns the log of the merges.
    """
    store = builder.store
    log = MergeLog(store)
    rank = _rank(priority)
    stated: dict[str, list[str]] = defaultdict(list)
    for tid, relation, value in builder.text_links:
        if relation == "version_of_doi":
            stated[tid].append(value)
    tids, labels = _groups(store, stated)
    sizes = np.bincount(labels, minlength=1) if len(labels) else np.zeros(0, dtype=np.int64)
    members: dict[int, list[int]] = defaultdict(list)
    for i in np.flatnonzero(sizes[labels] > 1) if len(labels) else ():
        members[int(labels[i])].append(int(i))
    for i, (tid, records) in enumerate(_texts_records(store)):
        if tids[i] != tid:
            raise RuntimeError("the texts' records changed while they were merged")
        group = members.get(int(labels[i]))
        if group is None:
            alone = _alone(tid, records, rank)
            if alone is None:
                alone = merge_works(
                    records, priority=priority, stated_versions=_stated([tid], stated)
                )
            log.add(alone)
            continue
        if group[0] != i:
            continue  # merged with the group's first text
        ids = [tids[j] for j in group]
        store.execute("DELETE FROM selected")
        store.executemany("INSERT INTO selected VALUES (?)", [(t,) for t in ids])
        everything = [r for _t, recs in _texts_records(store, selected=True) for r in recs]
        log.add(merge_works(everything, priority=priority, stated_versions=_stated(ids, stated)))
    _move_rows(builder, log)
    _drop_orphans(builder)
    _fill_abstracts(store, log)
    return log


def _alone(tid: str, records: Sequence[WorkRecord], rank: Any) -> MergeResult | None:
    """What :func:`merge_works` gives for a text no key joins to another, when one finder
    gave all its records (most texts): its fields, nothing to log. ``None`` otherwise."""
    if len({r.source for r in records}) != 1:
        return None
    fields, prov, _conflicts = _choose(records, rank)  # one finder: no conflict
    fields["text_id"] = tid
    return MergeResult(texts={tid: fields}, provenance={tid: prov}, merged_into={tid: tid})


def _stated(tids: Iterable[str], stated: Mapping[str, list[str]]) -> list[tuple[str, str]]:
    return [(t, doi) for t in tids for doi in stated.get(t, ())]


def _move_rows(builder: Any, log: MergeLog) -> None:
    """The parts and authorships of texts merged into another move to the text kept."""
    from .tables import unpack_text

    store = builder.store
    origin: dict[tuple[str, str, str, str], str] = {}
    for old in sorted(log.moved):
        new = log.moved[old]
        rows = store.execute(
            "SELECT part, language, provider, format, content, retrieved_at FROM parts"
            " WHERE tid = ? ORDER BY part, language, provider",
            (old,),
        ).fetchall()
        for part, language, provider, fmt, content, at in rows:
            key = (new, part, language, provider)
            kept = store.execute(
                "SELECT content, retrieved_at FROM parts"
                " WHERE tid = ? AND part = ? AND language = ? AND provider = ?",
                key,
            ).fetchone()
            if kept is not None:
                mine, theirs = unpack_text(content), unpack_text(kept[0])
                newer = (_time(at), mine) > (_time(kept[1]), theirs)
                if mine != theirs:
                    winner, loser = (
                        (old, origin.get(key, new)) if newer else (origin.get(key, new), old)
                    )
                    log.conflict(
                        Conflict(new, f"part {part}/{language}/{provider}", f"from {winner}", "",
                                 f"from {loser}", "")
                    )  # fmt: skip
                if not newer:
                    continue
            store.execute(
                "INSERT OR REPLACE INTO parts VALUES (?, ?, ?, ?, ?, ?, ?)",
                (*key, fmt, content, at),
            )
            origin[key] = old
        store.execute("DELETE FROM parts WHERE tid = ?", (old,))
    for old in sorted(log.moved):
        new = log.moved[old]
        rows = store.execute(
            "SELECT pid, position, orgs, last, corresponding FROM authorships"
            " WHERE tid = ? ORDER BY pid",
            (old,),
        ).fetchall()
        for pid, position, orgs, last, corresponding in rows:
            kept = store.execute(
                "SELECT position, orgs, last, corresponding FROM authorships"
                " WHERE tid = ? AND pid = ?",
                (new, pid),
            ).fetchone()
            if kept is not None:
                position = min(kept[0], position)
                orgs = json.dumps(sorted(set(json.loads(kept[1])) | set(json.loads(orgs))))
                last = kept[2] if kept[2] is not None else last
                corresponding = kept[3] if kept[3] is not None else corresponding
            store.execute(
                "INSERT OR REPLACE INTO authorships VALUES (?, ?, ?, ?, ?, ?)",
                (new, pid, position, orgs, last, corresponding),
            )
        store.execute("DELETE FROM authorships WHERE tid = ?", (old,))


def _drop_orphans(builder: Any) -> None:
    """Parts and authorships of a text that no longer exists (its raw run was removed) go."""
    store = builder.store
    store.execute("DELETE FROM kept_texts")
    store.executemany("INSERT INTO kept_texts VALUES (?)", [(t,) for t in builder._taken["texts"]])
    for table in ("parts", "authorships"):
        cur = store.execute(
            f"DELETE FROM {table} WHERE tid NOT IN (SELECT tid FROM texts_out)"
            " AND tid NOT IN (SELECT tid FROM kept_texts)"
        )
        if cur.rowcount > 0:
            builder.count("merge: rows of a missing text dropped", cur.rowcount)


def _fill_abstracts(store: Any, log: MergeLog) -> None:
    """A published version without an abstract reads its preprint's."""
    for link in sorted(log.versions, key=lambda v: v.preprint):
        has = store.execute(
            "SELECT 1 FROM parts WHERE tid = ? AND part = 'abstract' LIMIT 1", (link.published,)
        ).fetchone()
        if has:
            continue
        rows = store.execute(
            "SELECT language, provider, format, content, retrieved_at FROM parts"
            " WHERE tid = ? AND part = 'abstract' ORDER BY language, provider",
            (link.preprint,),
        ).fetchall()
        for language, provider, fmt, content, at in rows:
            store.execute(
                "INSERT OR REPLACE INTO parts VALUES (?, 'abstract', ?, ?, ?, ?, ?)",
                (link.published, language, provider, fmt, content, at),
            )
            log.fill(
                {
                    "text_id": link.published,
                    "part": f"abstract/{language}/{provider}",
                    "from": link.preprint,
                }
            )


def _time(text: str | None) -> datetime:
    if not text:
        return datetime.min.replace(tzinfo=timezone.utc)
    ts = datetime.fromisoformat(text)
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def write_merge_log(layout: ProjectLayout, results: Sequence[MergeResult] | MergeResult) -> None:
    """Write ``sources/merges.json``: every merge with its rule, version links, refusals,
    conflicts and fills. Rebuilt with the tables; the same records give the same bytes."""
    if isinstance(results, MergeResult):
        results = [results]
    doc = {
        "format": MERGE_LOG_FORMAT,
        "merges": [asdict(m) for r in results for m in r.merges],
        "versions": [asdict(v) for r in results for v in r.versions],
        "refused": [asdict(x) for r in results for x in r.refused],
        "conflicts": [
            {**asdict(c), "kept": _plain(c.kept), "other": _plain(c.other)}
            for r in results
            for c in r.conflicts
        ],
        "fills": [f for r in results for f in r.fills],
    }
    text = json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    atomic_write_bytes(layout.sources / "merges.json", text.encode("utf-8"))


def _plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    return value
