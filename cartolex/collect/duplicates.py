# SPDX-License-Identifier: MIT
"""People who may be one person: pairs scored on their evidence, and the clear ones.

Two rows of ``people.parquet`` may be one person: a list imported twice with
different spellings, a person found by an institution and by a list, a name
that changed. :func:`duplicate_pairs` proposes pairs and weighs, for each, what
the project knows of both, from its own tables only (nothing is asked of a
service):

* a shared identifier (the same ORCID, OpenAlex record, idHAL…, in the tables
  or among the records decided in ``people.csv``) counts for; two different
  ORCIDs count strongly against;
* the names: the same name once case, accents, hyphens and particles are set
  aside, letters such as ``ø`` or ``ł`` read as ``o`` or ``l`` (a name form among
  the aliases counts); the same words split another way between surname and given
  names, or in another order; a first name given as an initial, or more given
  names on one side; one surname part of the other;
* an organisation both belonged to; co-authors in the project both wrote with;
* texts both are authors of: at the same place in the author list, one author
  recorded twice (for); at different known places, two authors of one text
  (strongly against; a place unknown on either side says nothing);
* a text of the same title (and years at most one apart) on each side: one work
  recorded under both;
* publication years that follow on (one stops when the other starts).

Each line of evidence has a code, its params, its English words and its points;
a pair's score is the logistic of their sum. A pair is **clear** when a
conservative rule holds (:func:`is_clear`): the same ORCID or the same record
with names that agree; or the same full name and a text where both hold the same
place (one author recorded twice); never with two different ORCIDs, names that
do not agree or a text they wrote together. A name with an organisation and
co-authors in common is not enough on its own: two namesakes of one lab have
both. The clear pairs are what the automatic merge takes; the others wait for
a person.

Every row is weighed on its own; :func:`standing_pairs` then reads each pair as
the people that remain after the merges (a row merged into another is that
person), so a namesake of a merged row is still proposed with the person it is
merged into. Candidates come from blocks (the same words of a name, whatever their
order; a surname part and a first initial; an identifier), so a project of 10⁵
people never compares every pair. Every pair of the same full name is proposed,
however strong the evidence against; when more than :data:`MAX_NAMESAKES` people
bear a name, two of them are proposed only with evidence beyond their names, and
the name is reported.

:func:`link_groups` joins pairs into **groups** of people who may all be one
person, the most likely pairs first, never across a pair decided (two people,
later) nor two different ORCIDs: the review and the automatic merges work on them.
"""

from __future__ import annotations

import functools
import math
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from cartolex.project import Project
from cartolex.project.identity import merge_roots, merged_groups
from cartolex.project.tables import read_decision_csv, read_source_table

from .names import NameForm, name_agreement
from .names import name_form as _name_form

# The same names are compared many times over (a person meets every other of their block):
# their folded forms are computed once.
_CACHE = 1 << 18
name_form = functools.lru_cache(maxsize=_CACHE)(_name_form)


__all__ = [
    "MAX_AUTHORS",
    "MAX_BLOCK",
    "MAX_COMPARED_NAMESAKES",
    "MAX_GROUP",
    "MAX_NAMESAKES",
    "REVIEW_SCORE",
    "DuplicatePair",
    "PersonFacts",
    "choose_kept",
    "clear_groups",
    "duplicate_pairs",
    "is_clear",
    "link_groups",
    "person_facts",
    "review_groups",
    "standing_pairs",
]

#: A text with more authors in the project than this does not make co-authors.
MAX_AUTHORS = 25
#: A block of names larger than this is compared on exact names only.
MAX_BLOCK = 600
#: Two people of a name (or a block of names) more people than this bear are proposed only
#: with evidence beyond their names (an identifier, an organisation, co-authors, a text):
#: otherwise its pairs would drown the others. Such names are reported.
MAX_NAMESAKES = 50
#: A full name more people than this bear is not compared at all (a placeholder).
MAX_COMPARED_NAMESAKES = 1000
#: Pairs at least this likely join a group of the review; the others stay pairs of two.
#: (The same name and an organisation in common, two namesakes of one lab as often as
#: one person, stay below it.)
REVIEW_SCORE = 0.7
#: A group of the review holds at most this many people.
MAX_GROUP = 20
#: An identifier shared by more people than this is not evidence (a placeholder).
MAX_SHARED_ID = 20
#: The points a pair needs for a score of one half.
SCORE_MIDDLE = 2.0

#: Each line of evidence: its English words (with one, and with several) and its points.
EVIDENCE: dict[str, tuple[str, str, float]] = {
    "dup_same_orcid": ("the same ORCID ({orcid})", "", 4.0),
    "dup_same_record": ("the same {scheme} record ({value})", "", 3.0),
    "dup_other_orcid": ("two different ORCIDs ({a}, {b})", "", -6.0),
    "dup_same_name": ("the same name ({name})", "", 1.5),
    "dup_same_initials": ("the same surname and initials ({name})", "", 0.8),
    "dup_name_order": ("the same name, written another way ({a}, {b})", "", 1.3),
    "dup_other_given": ("the same surname, more given names on one side ({a}, {b})", "", 0.8),
    "dup_initial": ("the same surname, a first name as an initial ({a}, {b})", "", 0.6),
    "dup_surname_part": ("one surname is part of the other ({a}, {b})", "", 0.5),
    "dup_names_differ": ("names that do not agree ({a}, {b})", "", -1.5),
    "dup_shared_org": (
        "an organisation in common ({names})",
        "{n} organisations in common ({names})",
        0.9,
    ),
    "dup_coauthors": ("a co-author in common", "{n} co-authors in common", 0.8),
    "dup_same_place": (
        "a text where both hold the same place",
        "{n} texts where both hold the same place",
        2.5,
    ),
    "dup_together": ("a text they wrote together", "{n} texts they wrote together", -6.0),
    "dup_same_title": (
        "a text of the same title on each side",
        "{n} texts of the same title on each side",
        2.0,
    ),
    "dup_years_follow": ("their years follow on ({a}, then {b})", "", 0.5),
}
#: Points of the co-authors in common, at most.
MAX_COAUTHOR_POINTS = 2.0
#: Roles, the one the kept person of a merge should have first.
ROLE_RANK = ("mapped", "context", "projected", "undecided", "excluded", "")
IDENTITY_RANK = ("confirmed", "auto", "none", "pending", "")


def _line(code: str, **params: Any) -> dict[str, Any]:
    """One line of evidence: ``code``, ``params``, English ``text`` and ``points``."""
    one, several, points = EVIDENCE[code]
    text = several if several and int(params.get("n", 1)) != 1 else one
    if code == "dup_coauthors":
        points = min(MAX_COAUTHOR_POINTS, points * int(params.get("n", 1)))
    return {"code": code, "params": params, "text": text.format(**params), "points": points}


@dataclass
class PersonFacts:
    """What the duplicates' evidence reads of one person (and the rows merged into them)."""

    person_id: str
    last_name: str
    first_name: str
    #: Every name form: the row's, its aliases', the merged rows'.
    names: list[tuple[str, str]]
    orcids: set[str] = field(default_factory=set)
    #: Other identifiers, ``(scheme, value)``.
    ids: set[tuple[str, str]] = field(default_factory=set)
    orgs: set[str] = field(default_factory=set)
    role: str = ""
    identity: str = ""
    texts: int = 0
    first_year: int | None = None
    last_year: int | None = None


@dataclass
class DuplicatePair:
    """Two people who may be one, *a* < *b*, with the evidence weighed."""

    a: str
    b: str
    points: float
    score: float
    evidence: list[dict[str, Any]]
    clear: bool
    #: Two different ORCIDs: a merge needs someone to say they are one person anyway.
    conflict: bool = False
    #: The rows the evidence was read on, when a merge put one under another person.
    via: tuple[str, str] | None = None

    def as_dict(self) -> dict[str, Any]:
        out = {
            "a": self.a,
            "b": self.b,
            "points": round(self.points, 3),
            "score": round(self.score, 4),
            "evidence": self.evidence,
            "clear": self.clear,
            "conflict": self.conflict,
        }
        if self.via is not None:
            out["via"] = list(self.via)
        return out

    def codes(self) -> set[str]:
        return {e["code"] for e in self.evidence}


# ── what is known of each person ─────────────────────────────────────────────


def person_facts(
    project: Project,
    decisions: Mapping[str, Mapping[str, str]] | None = None,
    columns: Callable[[], Any] | None = None,
    org_roots: Mapping[str, str] | None = None,
) -> tuple[dict[str, PersonFacts], dict[str, np.ndarray], Any]:
    """Each person that stands on their own → their facts (with the rows merged into them);
    each one's texts (rows of the text columns); and the text columns. *org_roots* maps a
    merged organisation to the one that remains.

    *columns* gives the texts as columns (by default
    :func:`~cartolex.project.text_columns.read_text_columns`): anything with ``n``,
    ``year``, ``has_year``, ``author_text``, ``author_person``, ``person_ids`` and
    ``tid(row)``."""
    from cartolex.project.text_columns import read_text_columns

    layout = project.layout
    if decisions is None:
        decisions = {r["person_id"]: r for r in read_decision_csv(layout.people_csv, "people")}
    roots = merge_roots(decisions)
    groups = merged_groups(roots)
    path = layout.table("people")
    if not path.exists():
        return {}, {}, None
    table = read_source_table(
        path, "people", ["person_id", "last_name", "first_name", "orcid", "ids", "aliases"]
    ).to_pylist()
    facts: dict[str, PersonFacts] = {}
    rows_of: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in table:
        rows_of[roots.get(row["person_id"], row["person_id"])].append(row)
    for pid, rows in rows_of.items():
        if pid in roots:
            continue
        main = next((r for r in rows if r["person_id"] == pid), rows[0])
        f = PersonFacts(
            person_id=pid,
            last_name=main["last_name"] or "",
            first_name=main["first_name"] or "",
            names=[],
        )
        dec = decisions.get(pid) or {}
        f.role = dec.get("role") or ("mapped" if not decisions else "undecided")
        f.identity = dec.get("identity") or ""
        for r in rows:
            for form in [(r["last_name"] or "", r["first_name"] or "")] + [
                (a["last_name"] or "", a["first_name"] or "") for a in r["aliases"] or []
            ]:
                if (form[0].strip() or form[1].strip()) and form not in f.names:
                    f.names.append(form)
            if r["orcid"]:
                f.orcids.add(str(r["orcid"]))
            for scheme, values in dict(r["ids"] or []).items():
                f.ids.update((scheme, str(v)) for v in values or [] if v)
        for one in (pid, *groups.get(pid, ())):
            for record in ((decisions.get(one) or {}).get("records") or "").split(";"):
                scheme, sep, value = record.partition(":")
                if not sep or not value:
                    continue
                if scheme == "orcid":
                    f.orcids.add(value)
                else:
                    f.ids.add(("idhal" if scheme == "hal" else scheme, value))
        facts[pid] = f
    _organisations(project, facts, roots, org_roots or {})
    cols = columns() if columns is not None else read_text_columns(layout)
    texts = _texts(cols, facts, roots)
    for pid, rows in texts.items():
        f = facts[pid]
        f.texts = len(rows)
        dated = rows[cols.has_year[rows]]
        if len(dated):
            years = cols.year[dated]
            f.first_year, f.last_year = int(years.min()), int(years.max())
    return facts, texts, cols


def _organisations(
    project: Project,
    facts: dict[str, PersonFacts],
    roots: Mapping[str, str],
    org_roots: Mapping[str, str],
) -> None:
    path = project.layout.table("affiliations")
    if not path.exists():
        return
    aff = read_source_table(path, "affiliations", ["person_id", "org_id"])
    for pid, oid in zip(aff["person_id"].to_pylist(), aff["org_id"].to_pylist(), strict=True):
        f = facts.get(roots.get(pid, pid))
        if f is not None:
            f.orgs.add(org_roots.get(oid, oid))


def _texts(
    cols: Any, facts: Mapping[str, PersonFacts], roots: Mapping[str, str]
) -> dict[str, np.ndarray]:
    """Each person's texts (rows), with the rows merged into them; a text once."""
    if cols is None or not len(cols.author_text):
        return {}
    texts, who = np.asarray(cols.author_text), np.asarray(cols.author_person)
    order = np.argsort(who, kind="stable")
    bounds = np.searchsorted(who[order], np.arange(len(cols.person_ids) + 1))
    out: dict[str, list[np.ndarray]] = defaultdict(list)
    for code, pid in enumerate(cols.person_ids):
        if bounds[code + 1] == bounds[code]:
            continue
        root = roots.get(pid, pid)
        if root in facts:
            out[root].append(texts[order[bounds[code] : bounds[code + 1]]])
    return {pid: np.unique(np.concatenate(parts)) for pid, parts in out.items()}


# ── candidates ───────────────────────────────────────────────────────────────


def _candidates(
    facts: Mapping[str, PersonFacts], report: dict[str, Any] | None = None
) -> dict[tuple[str, str], int]:
    """The pairs worth weighing, each with how many people the smallest block that
    proposed it holds (0: an identifier in common): a shared identifier; the same words of
    a name, whatever their order (unless more than :data:`MAX_COMPARED_NAMESAKES` people
    bear it: a placeholder); or a block of names (a surname part and a first initial),
    compared on exact names only when it is too large. *report*'s ``common_names`` receives
    each name more than :data:`MAX_NAMESAKES` people bear (``name``, ``people``,
    ``compared``)."""
    pairs: dict[tuple[str, str], int] = {}

    def add(a: str, b: str, crowd: int) -> None:
        if a != b:
            key = (a, b) if a < b else (b, a)
            if crowd < pairs.get(key, crowd + 1):
                pairs[key] = crowd

    by_id: dict[tuple[str, str], list[str]] = defaultdict(list)
    for pid, f in facts.items():
        for orcid in f.orcids:
            by_id[("orcid", orcid)].append(pid)
        for key in f.ids:
            by_id[key].append(pid)
    for members in by_id.values():
        if 1 < len(members) <= MAX_SHARED_ID:
            for i, a in enumerate(members):
                for b in members[i + 1 :]:
                    add(a, b, 0)
    blocks: dict[tuple[str, str], set[str]] = defaultdict(set)
    exact: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for pid, f in facts.items():
        for last, first in f.names:
            parts, given, squashed = name_form(last, first)
            if not parts and not given:
                continue
            exact[tuple(sorted(parts + given))].add(pid)
            initial = given[0][0] if given else ""
            # the surname written in one word too (``O'Tavelin``, ``Otavelin``)
            for part in {*parts, squashed} - {""}:
                blocks[(part, initial)].add(pid)
                if not initial:
                    blocks[(part, "*")].add(pid)
    common = []
    for bag, members in exact.items():
        n = len(members)
        if n > MAX_NAMESAKES:
            common.append({"name": " ".join(bag), "people": n,
                           "compared": n <= MAX_COMPARED_NAMESAKES})  # fmt: skip
        if n < 2 or n > MAX_COMPARED_NAMESAKES:
            continue
        ordered = sorted(members)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1 :]:
                add(a, b, n)
    if report is not None:
        report["common_names"] = sorted(common, key=lambda c: (-c["people"], c["name"]))
    surnames: dict[str, set[str]] = defaultdict(set)
    for (part, _initial), members in blocks.items():
        surnames[part] |= members
    for (part, initial), members in blocks.items():
        if initial == "*":
            # without a first name: anyone of the surname, when the surname is rare enough
            n = len(surnames[part])
            if n <= MAX_BLOCK:
                for a in members:
                    for b in surnames[part]:
                        add(a, b, n)
            continue
        n = len(members)
        if n > MAX_BLOCK:
            continue
        ordered = sorted(members)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1 :]:
                add(a, b, n)
    return pairs


# ── evidence ─────────────────────────────────────────────────────────────────


#: How two names agree (:func:`~cartolex.collect.names.name_agreement`) → the rank of the
#: agreement (the closest first) and its evidence code.
_AGREEMENT = {
    "same": (0, "dup_same_name"),
    "initials": (1, "dup_same_initials"),
    "order": (2, "dup_name_order"),
    "given": (3, "dup_other_given"),
    "initial": (4, "dup_initial"),
    "part": (5, "dup_surname_part"),
}


def _agree(a: NameForm, b: NameForm) -> str | None:
    return name_agreement(a, b)


def _name_evidence(a: PersonFacts, b: PersonFacts) -> dict[str, Any] | None:
    """The best agreement between any name form of *a* and any of *b*."""
    best: tuple[int, dict[str, Any]] | None = None
    for la, fa in a.names:
        form_a = name_form(la, fa)
        for lb, fb in b.names:
            kind = _agree(form_a, name_form(lb, fb))
            if kind is None:
                continue
            rank, code = _AGREEMENT[kind]
            if best is not None and rank >= best[0]:
                continue
            shown_a = " ".join(x for x in (fa, la) if x)
            shown_b = " ".join(x for x in (fb, lb) if x)
            if code in ("dup_same_name", "dup_same_initials"):
                best = (rank, _line(code, name=shown_a))
            else:
                best = (rank, _line(code, a=shown_a, b=shown_b))
            if rank == 0:
                return best[1]
    return best[1] if best is not None else None


def _coauthors(
    cols: Any, texts: Mapping[str, np.ndarray], people: Iterable[str]
) -> dict[str, np.ndarray]:
    """Each of *people* → the codes of the people they wrote with (texts with at most
    :data:`MAX_AUTHORS` authors in the project)."""
    if cols is None or not len(cols.author_text):
        return {}
    n_texts = cols.n
    author_text, author_person = np.asarray(cols.author_text), np.asarray(cols.author_person)
    per_text = np.bincount(author_text, minlength=n_texts)
    small = per_text <= MAX_AUTHORS
    keep = small[author_text]
    at, who = author_text[keep], author_person[keep]
    order = np.argsort(at, kind="stable")
    at, who = at[order], who[order]
    bounds = np.searchsorted(at, np.arange(n_texts + 1))
    out: dict[str, np.ndarray] = {}
    for pid in people:
        rows = texts.get(pid)
        if rows is None or not len(rows):
            continue
        rows = rows[small[rows]]
        if not len(rows):
            continue
        found = np.concatenate([who[bounds[r] : bounds[r + 1]] for r in rows.tolist()])
        out[pid] = np.unique(found)
    return out


def _positions(
    project: Project, people: set[str], roots: Mapping[str, str]
) -> dict[tuple[str, str], int]:
    """(text id, person) → their place in the author list, for *people* (merged rows read as
    the person they are merged into); a place unknown (none, or 0) is left out."""
    import pyarrow as pa
    import pyarrow.compute as pc

    path = project.layout.table("authorships")
    if not people or not path.exists():
        return {}
    wanted = people | {pid for pid, root in roots.items() if root in people}
    table = read_source_table(path, "authorships", ["text_id", "person_id", "position"])
    table = table.filter(pc.is_in(table["person_id"], value_set=pa.array(sorted(wanted))))
    return {
        (tid, roots.get(pid, pid)): int(pos)
        for tid, pid, pos in zip(
            table["text_id"].to_pylist(),
            table["person_id"].to_pylist(),
            table["position"].to_pylist(),
            strict=True,
        )
        if pos
    }


#: The evidence codes of names that agree.
_NAMED = frozenset(code for _rank, code in _AGREEMENT.values())


def is_clear(
    pair: DuplicatePair, a: PersonFacts | None = None, b: PersonFacts | None = None
) -> bool:
    """The conservative rule of the automatic merge (see the module docstring)."""
    codes = pair.codes()
    if {"dup_other_orcid", "dup_together", "dup_names_differ"} & codes:
        return False
    named = bool(_NAMED & codes)
    if named and ({"dup_same_orcid", "dup_same_record"} & codes):
        return True
    # Without an identifier, a name with an organisation and co-authors in common is not
    # enough (two namesakes of one lab have both): one author recorded twice, at the same
    # place of a text, is.
    return "dup_same_name" in codes and "dup_same_place" in codes


def _weigh(
    a: PersonFacts,
    b: PersonFacts,
    *,
    named: dict[str, Any] | None = None,
    same_place: int,
    together: int,
    coauthors: int,
    coauthor_share: float = 0.0,
    same_titles: int = 0,
    org_names: Mapping[str, str],
) -> DuplicatePair | None:
    lines: list[dict[str, Any]] = []
    for orcid in sorted(a.orcids & b.orcids):
        lines.append(_line("dup_same_orcid", orcid=orcid))
    conflict = bool(a.orcids and b.orcids and not (a.orcids & b.orcids))
    if conflict:
        lines.append(_line("dup_other_orcid", a=sorted(a.orcids)[0], b=sorted(b.orcids)[0]))
    shared_ids = sorted(a.ids & b.ids)
    for scheme, value in shared_ids[:2]:
        lines.append(_line("dup_same_record", scheme=scheme, value=value))
    if named is None:
        named = _name_evidence(a, b)
    if named is not None:
        lines.append(named)
    elif a.orcids & b.orcids or shared_ids:
        lines.append(
            _line(
                "dup_names_differ",
                a=" ".join(x for x in (a.first_name, a.last_name) if x),
                b=" ".join(x for x in (b.first_name, b.last_name) if x),
            )
        )
    else:
        return None  # neither a name nor an identifier: not a candidate
    common = sorted(a.orgs & b.orgs)
    if common:
        shown = ", ".join(org_names.get(o, o) for o in common[:3])
        lines.append(_line("dup_shared_org", n=len(common), names=shown))
    if coauthors:
        lines.append(_line("dup_coauthors", n=coauthors, share=round(coauthor_share, 2)))
    if same_place:
        lines.append(_line("dup_same_place", n=same_place))
    if together:
        lines.append(_line("dup_together", n=together))
    if same_titles:
        lines.append(_line("dup_same_title", n=same_titles))
    if a.first_year and b.first_year and a.last_year and b.last_year:
        early, late = (a, b) if a.first_year <= b.first_year else (b, a)
        if early.last_year <= late.first_year <= early.last_year + 3 and early is not late:
            lines.append(
                _line(
                    "dup_years_follow",
                    a=f"{early.first_year}–{early.last_year}",
                    b=f"{late.first_year}–{late.last_year}",
                )
            )
    points = sum(line["points"] for line in lines)
    pair = DuplicatePair(
        a=a.person_id,
        b=b.person_id,
        points=points,
        score=_score(points),
        evidence=lines,
        clear=False,
        conflict=conflict,
    )
    pair.clear = is_clear(pair, a, b)
    return pair


def _score(points: float) -> float:
    return 1.0 / (1.0 + math.exp(-(points - SCORE_MIDDLE)))


def _same_titles(
    cols: Any,
    keys: tuple[np.ndarray, np.ndarray] | None,
    texts: Mapping[str, np.ndarray],
    pairs: Iterable[tuple[str, str]],
) -> dict[tuple[str, str], int]:
    """For each pair, how many titles one side has on a text and the other on another text
    (years at most one apart): one work recorded under both. Titles shorter than a copy's
    least length say nothing."""
    from cartolex.project.corpus import DUPLICATE_MIN_TITLE, DUPLICATE_YEAR_GAP

    if keys is None or cols is None:
        return {}
    hashes, lengths = keys
    if len(hashes) != cols.n:
        return {}
    long_enough = lengths >= DUPLICATE_MIN_TITLE

    def titled(pid: str) -> dict[int, list[tuple[int, int]]]:
        rows = texts.get(pid)
        out: dict[int, list[tuple[int, int]]] = defaultdict(list)
        if rows is None:
            return out
        for r in rows[long_enough[rows]].tolist():
            year = int(cols.year[r]) if cols.has_year[r] else -99
            out[int(hashes[r])].append((r, year))
        return out

    cache: dict[str, dict[int, list[tuple[int, int]]]] = {}
    rows_of: dict[str, frozenset[int]] = {}

    def text_sets(pid: str) -> frozenset[int]:
        if pid not in rows_of:
            rows = texts.get(pid)
            rows_of[pid] = frozenset(rows.tolist()) if rows is not None else frozenset()
        return rows_of[pid]

    found: dict[tuple[str, str], int] = {}
    for a, b in pairs:
        ta = cache.get(a)
        if ta is None:
            ta = cache[a] = titled(a)
        tb = cache.get(b)
        if tb is None:
            tb = cache[b] = titled(b)
        if not ta or not tb:
            continue
        rows_a, rows_b = text_sets(a), text_sets(b)
        n = 0
        for h in ta.keys() & tb.keys():
            # a text of one side the other is not on, and the other's likewise
            if any(
                r not in rows_b and s not in rows_a and abs(y - z) <= DUPLICATE_YEAR_GAP
                for r, y in ta[h]
                for s, z in tb[h]
            ):
                n += 1
        if n:
            found[(a, b)] = n
    return found


def _title_keys(project: Project, cols: Any) -> tuple[np.ndarray, np.ndarray] | None:
    """The texts' title hashes and lengths, in the rows of *cols*."""
    if cols is None:
        return None
    if hasattr(cols, "title_keys"):
        return cols.title_keys()
    from cartolex.project.corpus import title_keys

    path = project.layout.table("texts")
    if not path.exists():
        return None
    return title_keys(path.parent)


def duplicate_pairs(
    project: Project,
    *,
    decisions: Mapping[str, Mapping[str, str]] | None = None,
    columns: Callable[[], Any] | None = None,
    org_roots: Mapping[str, str] | None = None,
    org_names: Mapping[str, str] | None = None,
    report: dict[str, Any] | None = None,
) -> tuple[list[DuplicatePair], dict[str, PersonFacts]]:
    """Every pair of people who may be one person, the most likely first, with the facts
    of each person they name. Pairs already decided (``people_pairs.csv``) are not left
    out here: the caller filters them, so this can be computed once per version of the
    tables and the merges. *report*, when given, receives the names too common to be
    proposed on the name alone (``common_names``: each ``name``, its ``people`` and
    whether they were ``compared``)."""
    layout = project.layout
    if decisions is None:
        decisions = {r["person_id"]: r for r in read_decision_csv(layout.people_csv, "people")}
    roots = merge_roots(decisions)
    facts, texts, cols = person_facts(project, decisions, columns, org_roots)
    if not facts:
        if report is not None:
            report["common_names"] = []
        return [], {}
    # Only the pairs with a name or an identifier in common are weighed further: the others
    # of a block are dropped before their texts and co-authors are compared.
    crowd = _candidates(facts, report)
    named: dict[tuple[str, str], dict[str, Any] | None] = {}
    for a, b in crowd:
        line = _name_evidence(facts[a], facts[b])
        if line is not None or facts[a].orcids & facts[b].orcids or facts[a].ids & facts[b].ids:
            named[(a, b)] = line
    candidates = set(named)
    involved = {p for pair in candidates for p in pair}
    code_of = {pid: i for i, pid in enumerate(cols.person_ids)} if cols is not None else {}
    groups = merged_groups(roots)
    own = {
        pid: {code_of[x] for x in (pid, *groups.get(pid, ())) if x in code_of} for pid in involved
    }
    # Sets of a few hundred codes at most: Python's set operations beat numpy's per pair.
    coauthors = {
        pid: frozenset(found.tolist()) - own[pid]
        for pid, found in _coauthors(cols, texts, involved).items()
    }
    text_sets = {pid: frozenset(texts[pid].tolist()) for pid in involved if pid in texts}
    shared: dict[tuple[str, str], list[int]] = {}
    for a, b in candidates:
        ta, tb = text_sets.get(a), text_sets.get(b)
        if ta and tb:
            both = ta & tb
            if both:
                shared[(a, b)] = sorted(both)
    places = _positions(project, {p for pair in shared for p in pair}, roots)
    titles = _same_titles(cols, _title_keys(project, cols), texts, sorted(candidates))
    names = dict(org_names or {})
    if not names and layout.table("organisations").exists():
        orgs = read_source_table(layout.table("organisations"), "organisations",
                                 ["org_id", "name", "acronym"]).to_pylist()  # fmt: skip
        names = {o["org_id"]: o["acronym"] or o["name"] for o in orgs}
    out: list[DuplicatePair] = []
    for a, b in sorted(candidates):
        same = apart = 0
        for row in shared.get((a, b)) or ():
            tid = cols.tid(row)
            pa_, pb_ = places.get((tid, a)), places.get((tid, b))
            if pa_ is None or pb_ is None:
                continue  # a place unknown: one author twice, or two, nobody can tell
            if pa_ == pb_:
                same += 1
            else:
                apart += 1
        common, share = 0, 0.0
        ca, cb = coauthors.get(a), coauthors.get(b)
        if ca and cb:
            mine = own[a] | own[b]
            common = len((ca & cb) - mine)
            fewest = min(len(ca - mine), len(cb - mine))
            share = common / fewest if fewest else 0.0
        fa, fb = facts[a], facts[b]
        if crowd[(a, b)] > MAX_NAMESAKES and not (
            common or same or (a, b) in titles or fa.orgs & fb.orgs
            or fa.orcids & fb.orcids or fa.ids & fb.ids
        ):  # fmt: skip
            continue  # a name so common that it says nothing on its own
        pair = _weigh(
            fa,
            fb,
            named=named[(a, b)],
            same_place=same,
            together=apart,
            coauthors=common,
            coauthor_share=share,
            same_titles=titles.get((a, b), 0),
            org_names=names,
        )
        if pair is not None:
            out.append(pair)
    out.sort(key=lambda p: (-p.score, p.a, p.b))
    return out, facts


def standing_pairs(
    pairs: Iterable[DuplicatePair],
    facts: Mapping[str, PersonFacts],
    roots: Mapping[str, str],
) -> list[DuplicatePair]:
    """The pairs read as the people that remain after the merges *roots* (a merged row →
    the person it is merged into): a pair whose two rows are now one person is gone; a pair
    of a merged row is the pair of the person it is merged into (``via`` names the rows),
    the most likely of the pairs that land on the same two people; two people whose rows
    together carry different ORCIDs conflict. The most likely first."""
    from dataclasses import replace

    groups = merged_groups(roots)
    best: dict[tuple[str, str], DuplicatePair] = {}
    for p in pairs:
        ra, rb = roots.get(p.a, p.a), roots.get(p.b, p.b)
        if ra == rb:
            continue
        key = (ra, rb) if ra < rb else (rb, ra)
        held = best.get(key)
        direct = (p.a, p.b) == key
        if held is not None and (held.score, (held.a, held.b) == key) >= (p.score, direct):
            continue
        best[key] = p

    def orcids(pid: str) -> set[str]:
        out: set[str] = set()
        for x in (pid, *groups.get(pid, ())):
            f = facts.get(x)
            if f is not None:
                out |= f.orcids
        return out

    out: list[DuplicatePair] = []
    for (a, b), p in best.items():
        moved = (p.a, p.b) != (a, b)
        merged = bool(groups.get(a) or groups.get(b))
        if not moved and not merged:
            out.append(p)
            continue
        q = replace(p, a=a, b=b, via=(p.a, p.b) if moved else None, evidence=list(p.evidence))
        oa, ob = orcids(a), orcids(b)
        if oa and ob and not (oa & ob) and not p.conflict:
            q.evidence.append(_line("dup_other_orcid", a=sorted(oa)[0], b=sorted(ob)[0]))
            q.points = sum(line["points"] for line in q.evidence)
            q.score = _score(q.points)
            q.conflict, q.clear = True, False
        out.append(q)
    out.sort(key=lambda p: (-p.score, p.a, p.b))
    return out


def choose_kept(people: Iterable[PersonFacts]) -> str:
    """Which of several people that are one person remains: the first role (mapped, context,
    projected…), then the strongest identity, then the most texts, then the smallest id."""
    ranked = sorted(
        people,
        key=lambda f: (
            ROLE_RANK.index(f.role) if f.role in ROLE_RANK else len(ROLE_RANK),
            IDENTITY_RANK.index(f.identity) if f.identity in IDENTITY_RANK else len(IDENTITY_RANK),
            -f.texts,
            f.person_id,
        ),
    )
    return ranked[0].person_id


def link_groups(
    pairs: Iterable[DuplicatePair],
    facts: Mapping[str, PersonFacts],
    blocked: Collection[tuple[str, str]] = (),
    *,
    taken: Callable[[DuplicatePair], bool] | None = None,
    max_size: int = MAX_GROUP,
) -> dict[str, str]:
    """Groups of people who may all be one person: each pair *taken* (by default, those
    at least :data:`REVIEW_SCORE` likely, never two different ORCIDs), the most likely
    first, joins the groups of its two people, unless the group joined would hold a pair
    *blocked* (``(a, b)`` keys, the smaller id first: two people, later), two different
    ORCIDs or more than *max_size* people. Answers each person of a group of two or more
    → the group's smallest id."""
    if taken is None:

        def taken(p: DuplicatePair) -> bool:
            return p.score >= REVIEW_SCORE and not p.conflict

    apart: dict[str, set[str]] = defaultdict(set)
    for a, b in blocked:
        apart[a].add(b)
        apart[b].add(a)
    members: dict[str, list[str]] = {}
    orcids: dict[str, set[str]] = {}
    root_of: dict[str, str] = {}

    def root(x: str) -> str:
        if x not in root_of:
            root_of[x] = x
            members[x] = [x]
            f = facts.get(x)
            orcids[x] = set(f.orcids) if f is not None else set()
        return root_of[x]

    chosen = sorted((p for p in pairs if taken(p)), key=lambda p: (-p.score, p.a, p.b))
    for p in chosen:
        ra, rb = root(p.a), root(p.b)
        if ra == rb:
            continue
        small, large = (ra, rb) if len(members[ra]) <= len(members[rb]) else (rb, ra)
        if len(members[small]) + len(members[large]) > max_size:
            continue
        oa, ob = orcids[small], orcids[large]
        if oa and ob and not (oa & ob):
            continue
        inside = set(members[large])
        if any(apart.get(x, set()) & inside for x in members[small]):
            continue
        for x in members[small]:
            root_of[x] = large
        members[large].extend(members.pop(small))
        orcids[large] |= orcids.pop(small)
    out: dict[str, str] = {}
    for ids in members.values():
        if len(ids) > 1:
            first = min(ids)
            for x in ids:
                out[x] = first
    return out


def review_groups(
    pairs: Iterable[DuplicatePair],
    facts: Mapping[str, PersonFacts],
    distinct: Collection[tuple[str, str]] = (),
) -> list[tuple[list[str], list[DuplicatePair]]]:
    """The groups the review shows: the pairs not said to be *distinct* (``(a, b)`` keys),
    joined by :func:`link_groups` (the pairs at least :data:`REVIEW_SCORE` likely); every
    other pair, below it or that a group could not take, stays a group of its own two
    people. Each group: its members (sorted) and its pairs, the most likely first; the
    groups, the most likely pair first."""
    kept = [p for p in pairs if (p.a, p.b) not in distinct]
    group_of = link_groups(kept, facts, distinct)
    held: dict[str, list[DuplicatePair]] = defaultdict(list)
    alone: list[tuple[list[str], list[DuplicatePair]]] = []
    for p in kept:
        ga, gb = group_of.get(p.a), group_of.get(p.b)
        if ga is not None and ga == gb:
            held[ga].append(p)
        else:
            alone.append(([p.a, p.b], [p]))
    together = {g: sorted(x for x, y in group_of.items() if y == g) for g in held}
    out = [(together[g], sorted(ps, key=lambda p: (-p.score, p.a, p.b))) for g, ps in held.items()]
    out.extend(alone)
    out.sort(key=lambda g: (-g[1][0].score, g[0]))
    return out


def clear_groups(
    pairs: Iterable[DuplicatePair],
    facts: Mapping[str, PersonFacts],
    decided: Collection[tuple[str, str]] = (),
    now: Mapping[str, tuple[str, str]] | None = None,
    min_score: float | None = None,
) -> list[dict[str, Any]]:
    """The clear pairs not *decided* (``(a, b)`` keys, the smaller id first), joined into
    groups of people that are one person (:func:`link_groups`: never two people of a pair
    decided, two people said apart or left for later, joined through others, nor two
    different ORCIDs): each ``keep`` (:func:`choose_kept`, on the roles and identities of
    *now*: person id → ``(role, identity)``, else the facts'), the others to ``merge``,
    their ``names`` and the ``pairs`` it holds; sorted by the kept name.

    With *min_score*, the pairs taken are those whose score is at least *min_score* (clear
    or not), never a pair of two different ORCIDs."""
    from dataclasses import replace

    def taken(p: DuplicatePair) -> bool:
        if min_score is None:
            return p.clear and not p.conflict
        return p.score >= min_score and not p.conflict

    decided = set(decided)
    chosen = [p for p in pairs if taken(p) and (p.a, p.b) not in decided]
    group_of = link_groups(chosen, facts, decided, taken=taken, max_size=10**9)
    members: dict[str, list[str]] = defaultdict(list)
    held: dict[str, list[DuplicatePair]] = defaultdict(list)
    for pid, g in group_of.items():
        members[g].append(pid)
    for p in chosen:
        g = group_of.get(p.a)
        if g is not None and g == group_of.get(p.b):
            held[g].append(p)
    out = []
    for g, ids in members.items():
        group = []
        for pid in sorted(ids):
            f = facts[pid]
            if now is not None and pid in now:
                f = replace(f, role=now[pid][0], identity=now[pid][1])
            group.append(f)
        keep = choose_kept(group)
        out.append(
            {
                "keep": keep,
                "merge": [f.person_id for f in group if f.person_id != keep],
                "names": {
                    f.person_id: " ".join(x for x in (f.first_name, f.last_name) if x)
                    for f in group
                },
                "pairs": held[g],
            }
        )
    out.sort(key=lambda g: (g["names"][g["keep"]].casefold(), g["keep"]))
    return out
