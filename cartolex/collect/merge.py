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
A **preprint** and a **published version** (article, communication, chapter
or report) that meet by the third rule, or that a provider links (the
published DOI arXiv or bioRxiv gives), are **not merged**: they stay two
texts, the preprint's ``version_of`` naming the published one, and the build
reads only the published version (see ``docs/format/sources.md``).

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

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from cartolex.project.files import atomic_write_bytes
from cartolex.project.layout import ProjectLayout

from .finders import normalise_doi, normalise_title

__all__ = [
    "FINDER_PRIORITY",
    "LINK_SCHEMES",
    "MERGE_LOG_FORMAT",
    "Conflict",
    "Merge",
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
PUBLISHED_TYPES = frozenset({"article", "communication", "chapter", "report"})
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
        if len(pubs) == 1:
            ((pub, (rule, evidence)),) = pubs.items()
            chosen[pre] = (pub, rule, evidence)
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


def merge_texts(builder: Any, *, priority: Sequence[str] = FINDER_PRIORITY) -> MergeResult:
    """Merge the texts a :class:`~cartolex.collect.tables.SourceBuilder` holds, in place.

    Texts merged into another disappear; their parts and authorships move to
    the text kept (a clash keeps the most recent part, the smallest position).
    A published version without an abstract gets the abstract parts of its
    preprint. Returns the merge result, for the log.
    """
    authors: dict[str, set[str]] = defaultdict(set)
    for tid, pid in builder.authorships:
        authors[tid].add(pid)
    records: list[WorkRecord] = []
    for tid, recs in builder.text_records.items():
        for rec in recs:
            records.append(
                WorkRecord(
                    text_id=tid,
                    slot=rec["slot"],
                    source=rec["source"],
                    title=rec["title"],
                    doc_type=rec["doc_type"],
                    retrieved_at=rec["retrieved_at"],
                    year=rec["year"],
                    date=rec["date"],
                    doi=rec["doi"],
                    ids=dict(rec["ids"]),
                    version_of=rec["version_of"],
                    n_authors=rec["n_authors"] or 0,
                    keys=tuple(rec.get("keys", ())),
                    people=frozenset(authors.get(tid, ())),
                )
            )
    stated = [
        (tid, value) for tid, relation, value in builder.text_links if relation == "version_of_doi"
    ]
    result = merge_works(records, priority=priority, stated_versions=stated)
    moved = {old: new for old, new in result.merged_into.items() if old != new}
    for tid, fields in result.texts.items():
        row = builder.texts.get(tid)
        if row is None:
            continue
        row.update({k: v for k, v in fields.items() if k in row or k == "version_of"})
    for old in moved:
        builder.texts.pop(old, None)
    if moved:
        parts: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        origin: dict[tuple[str, str, str, str], str] = {}
        for key in sorted(builder.parts, key=lambda k: (k[0] in moved, k)):
            row = builder.parts[key]
            new = (moved.get(key[0], key[0]), *key[1:])
            if new in parts:
                kept = parts[new]
                newer = (row["retrieved_at"], row["content"]) > (
                    kept["retrieved_at"],
                    kept["content"],
                )
                if row["content"] != kept["content"]:
                    winner, loser = (key[0], origin[new]) if newer else (origin[new], key[0])
                    result.conflicts.append(
                        Conflict(
                            new[0],
                            f"part {'/'.join(new[1:])}",
                            f"from {winner}",
                            "",
                            f"from {loser}",
                            "",
                        )
                    )
                if not newer:
                    continue
            parts[new] = {**row, "text_id": new[0]}
            origin[new] = key[0]
        builder.parts = parts
        authorships = {}
        for key in sorted(builder.authorships, key=lambda k: (k[0] in moved, k)):
            row = builder.authorships[key]
            new = (moved.get(key[0], key[0]), key[1])
            if new in authorships:
                kept = authorships[new]
                kept["position"] = min(kept["position"], row["position"])
                kept["orgs"] = sorted(set(kept["orgs"]) | set(row["orgs"]))
                for flag in ("last", "corresponding"):
                    if kept[flag] is None:
                        kept[flag] = row[flag]
                continue
            authorships[new] = {**row, "text_id": new[0]}
        builder.authorships = authorships
    # Parts and authorships of a text that no longer exists (its raw run was removed) go.
    kept_ids = set()
    if "texts" in builder.existing:
        given = builder.registry.given("texts")
        kept_ids = {t for t in builder.existing["texts"]["text_id"].to_pylist() if t not in given}
    known = set(builder.texts) | kept_ids
    for store in (builder.parts, builder.authorships):
        for key in [k for k in store if k[0] not in known]:
            del store[key]
            builder.count("merge: rows of a missing text dropped")
    # A published version without an abstract reads its preprint's.
    for link in result.versions:
        has = any(k[0] == link.published and k[1] == "abstract" for k in builder.parts)
        if has:
            continue
        for key, row in sorted(builder.parts.items()):
            if key[0] == link.preprint and key[1] == "abstract":
                builder.parts[(link.published, *key[1:])] = {**row, "text_id": link.published}
                result.fills.append(
                    {
                        "text_id": link.published,
                        "part": f"abstract/{key[2]}/{key[3]}",
                        "from": link.preprint,
                    }
                )
    return result


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
