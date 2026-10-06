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
  aside (a name form among the aliases counts), a first name given as an
  initial, one surname part of the other;
* an organisation both belonged to; co-authors in the project both wrote with;
* texts both are authors of: at the same place in the author list, one author
  recorded twice (for); at different places, two authors of one text
  (strongly against);
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

Rows merged into another person are not proposed: the person they are merged
into stands for them, with their names, identifiers, organisations and texts.
Candidates come from blocks (a surname part and a first initial; an
identifier), so a project of 10⁵ people never compares every pair.
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

from .names import compatible_first_names as _compatible
from .names import name_key as _name_key
from .names import surname_parts as _surname_parts
from .names import words as _words

# The same names are compared many times over (a person meets every other of their block):
# their folded forms are computed once.
_CACHE = 1 << 18
compatible_first_names = functools.lru_cache(maxsize=_CACHE)(_compatible)
name_key = functools.lru_cache(maxsize=_CACHE)(_name_key)


@functools.lru_cache(maxsize=_CACHE)
def surname_parts(last: str) -> tuple[str, ...]:
    return tuple(_surname_parts(last))


@functools.lru_cache(maxsize=_CACHE)
def words(text: str) -> tuple[str, ...]:
    return tuple(_words(text))

__all__ = [
    "MAX_AUTHORS",
    "MAX_BLOCK",
    "DuplicatePair",
    "PersonFacts",
    "choose_kept",
    "clear_groups",
    "duplicate_pairs",
    "is_clear",
    "person_facts",
]

#: A text with more authors in the project than this does not make co-authors.
MAX_AUTHORS = 25
#: A block of names larger than this is compared on exact names only.
MAX_BLOCK = 600
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "a": self.a,
            "b": self.b,
            "points": round(self.points, 3),
            "score": round(self.score, 4),
            "evidence": self.evidence,
            "clear": self.clear,
            "conflict": self.conflict,
        }

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
                if form[0] and form not in f.names:
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


def _first_initial(first: str) -> str:
    w = words(first)
    return w[0][0] if w else ""


def _candidates(facts: Mapping[str, PersonFacts]) -> set[tuple[str, str]]:
    """The pairs worth weighing: a shared identifier, or a block of names (a surname part and
    a first initial). A block too large is compared on exact names only."""
    pairs: set[tuple[str, str]] = set()

    def add(a: str, b: str) -> None:
        if a != b:
            pairs.add((a, b) if a < b else (b, a))

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
                    add(a, b)
    blocks: dict[tuple[str, str], set[str]] = defaultdict(set)
    exact: dict[str, set[str]] = defaultdict(set)
    for pid, f in facts.items():
        for last, first in f.names:
            exact[name_key(last, first)].add(pid)
            initial = _first_initial(first)
            for part in surname_parts(last):
                blocks[(part, initial)].add(pid)
                if not initial:
                    blocks[(part, "*")].add(pid)
    for members in exact.values():
        if 1 < len(members) <= MAX_SHARED_ID * 5:
            ordered = sorted(members)
            for i, a in enumerate(ordered):
                for b in ordered[i + 1 :]:
                    add(a, b)
    surnames: dict[str, set[str]] = defaultdict(set)
    for (part, _initial), members in blocks.items():
        surnames[part] |= members
    for (part, initial), members in blocks.items():
        if initial == "*":
            # without a first name: anyone of the surname, when the surname is rare enough
            if len(surnames[part]) <= MAX_BLOCK:
                for a in members:
                    for b in surnames[part]:
                        add(a, b)
            continue
        if len(members) > MAX_BLOCK:
            continue
        ordered = sorted(members)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1 :]:
                add(a, b)
    return pairs


# ── evidence ─────────────────────────────────────────────────────────────────


def _name_evidence(a: PersonFacts, b: PersonFacts) -> dict[str, Any] | None:
    """The best agreement between any name form of *a* and any of *b*."""
    best: tuple[int, dict[str, Any]] | None = None

    def offer(rank: int, line: dict[str, Any]) -> None:
        nonlocal best
        if best is None or rank < best[0]:
            best = (rank, line)

    for la, fa in a.names:
        for lb, fb in b.names:
            sa, sb = surname_parts(la), surname_parts(lb)
            if not sa or not sb:
                continue
            shown_a = " ".join(x for x in (fa, la) if x)
            shown_b = " ".join(x for x in (fb, lb) if x)
            if name_key(la, fa) == name_key(lb, fb):
                full = any(len(w) > 1 for w in words(fa))
                code = "dup_same_name" if full else "dup_same_initials"
                offer(0 if full else 1, _line(code, name=shown_a))
            elif sa == sb and compatible_first_names(fa, fb):
                offer(2, _line("dup_initial", a=shown_a, b=shown_b))
            elif (set(sa) < set(sb) or set(sb) < set(sa)) and compatible_first_names(fa, fb):
                offer(3, _line("dup_surname_part", a=shown_a, b=shown_b))
    if best is not None:
        return best[1]
    return None


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
    the person they are merged into)."""
    import pyarrow as pa
    import pyarrow.compute as pc

    path = project.layout.table("authorships")
    if not people or not path.exists():
        return {}
    wanted = people | {pid for pid, root in roots.items() if root in people}
    table = read_source_table(path, "authorships", ["text_id", "person_id", "position"])
    table = table.filter(pc.is_in(table["person_id"], value_set=pa.array(sorted(wanted))))
    return {
        (tid, roots.get(pid, pid)): int(pos or 0)
        for tid, pid, pos in zip(
            table["text_id"].to_pylist(),
            table["person_id"].to_pylist(),
            table["position"].to_pylist(),
            strict=True,
        )
    }


def is_clear(
    pair: DuplicatePair, a: PersonFacts | None = None, b: PersonFacts | None = None
) -> bool:
    """The conservative rule of the automatic merge (see the module docstring)."""
    codes = pair.codes()
    if {"dup_other_orcid", "dup_together", "dup_names_differ"} & codes:
        return False
    named = bool({"dup_same_name", "dup_same_initials", "dup_initial", "dup_surname_part"} & codes)
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
    shared_texts: int,
    same_place: int,
    coauthors: int,
    coauthor_share: float = 0.0,
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
    if shared_texts - same_place > 0:
        lines.append(_line("dup_together", n=shared_texts - same_place))
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
        score=1.0 / (1.0 + math.exp(-(points - SCORE_MIDDLE))),
        evidence=lines,
        clear=False,
        conflict=conflict,
    )
    pair.clear = is_clear(pair, a, b)
    return pair


def duplicate_pairs(
    project: Project,
    *,
    decisions: Mapping[str, Mapping[str, str]] | None = None,
    columns: Callable[[], Any] | None = None,
    org_roots: Mapping[str, str] | None = None,
    org_names: Mapping[str, str] | None = None,
) -> tuple[list[DuplicatePair], dict[str, PersonFacts]]:
    """Every pair of people who may be one person, the most likely first, with the facts
    of each person they name. Pairs already decided (``people_pairs.csv``) are not left
    out here: the caller filters them, so this can be computed once per version of the
    tables and the merges."""
    layout = project.layout
    if decisions is None:
        decisions = {r["person_id"]: r for r in read_decision_csv(layout.people_csv, "people")}
    roots = merge_roots(decisions)
    facts, texts, cols = person_facts(project, decisions, columns, org_roots)
    if not facts:
        return [], {}
    candidates = _candidates(facts)
    involved = {p for pair in candidates for p in pair}
    coauthors = _coauthors(cols, texts, involved)
    code_of = {pid: i for i, pid in enumerate(cols.person_ids)} if cols is not None else {}
    shared: dict[tuple[str, str], np.ndarray] = {}
    for a, b in candidates:
        ta, tb = texts.get(a), texts.get(b)
        if ta is not None and tb is not None and len(ta) and len(tb):
            both = np.intersect1d(ta, tb, assume_unique=True)
            if len(both):
                shared[(a, b)] = both
    places = _positions(project, {p for pair in shared for p in pair}, roots)
    names = dict(org_names or {})
    if not names and layout.table("organisations").exists():
        orgs = read_source_table(layout.table("organisations"), "organisations",
                                 ["org_id", "name", "acronym"]).to_pylist()  # fmt: skip
        names = {o["org_id"]: o["acronym"] or o["name"] for o in orgs}
    groups = merged_groups(roots)
    own = {
        pid: [code_of[x] for x in (pid, *groups.get(pid, ())) if x in code_of] for pid in involved
    }
    out: list[DuplicatePair] = []
    for a, b in sorted(candidates):
        both = shared.get((a, b))
        same = 0
        if both is not None:
            for row in both.tolist():
                tid = cols.tid(row)
                pa_, pb_ = places.get((tid, a)), places.get((tid, b))
                if pa_ is not None and pa_ == pb_:
                    same += 1
        common, share = 0, 0.0
        ca, cb = coauthors.get(a), coauthors.get(b)
        if ca is not None and cb is not None:
            common = len(np.setdiff1d(np.intersect1d(ca, cb), own[a] + own[b]))
            fewest = min(
                len(np.setdiff1d(ca, own[a] + own[b])), len(np.setdiff1d(cb, own[a] + own[b]))
            )
            share = common / fewest if fewest else 0.0
        pair = _weigh(
            facts[a],
            facts[b],
            shared_texts=0 if both is None else len(both),
            same_place=same,
            coauthors=common,
            coauthor_share=share,
            org_names=names,
        )
        if pair is not None:
            out.append(pair)
    out.sort(key=lambda p: (-p.score, p.a, p.b))
    return out, facts


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


def clear_groups(
    pairs: Iterable[DuplicatePair],
    facts: Mapping[str, PersonFacts],
    decided: Collection[tuple[str, str]] = (),
    now: Mapping[str, tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """The clear pairs not *decided* (``(a, b)`` keys, the smaller id first), joined into
    groups of people that are one person: each ``keep`` (:func:`choose_kept`, on the roles
    and identities of *now*: person id → ``(role, identity)``, else the facts'), the others
    to ``merge``, their ``names`` and the ``pairs`` it holds; sorted by the kept name. A
    group whose people carry two different ORCIDs is left out: a person decides."""
    from dataclasses import replace

    parent: dict[str, str] = {}

    def find(x: str) -> str:
        root = x
        while parent.get(root, root) != root:
            root = parent[root]
        while parent.get(x, x) != root:  # shorten the path walked
            parent[x], x = root, parent[x]
        return root

    chosen = [p for p in pairs if p.clear and (p.a, p.b) not in decided]
    for p in chosen:
        ra, rb = find(p.a), find(p.b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    members: dict[str, list[str]] = defaultdict(list)
    held: dict[str, list[DuplicatePair]] = defaultdict(list)
    for p in chosen:
        held[find(p.a)].append(p)
    for pid in {x for p in chosen for x in (p.a, p.b)}:
        members[find(pid)].append(pid)
    out = []
    for root, ids in members.items():
        group = []
        for pid in sorted(ids):
            f = facts[pid]
            if now is not None and pid in now:
                f = replace(f, role=now[pid][0], identity=now[pid][1])
            group.append(f)
        orcids = [f.orcids for f in group if f.orcids]
        if any(not (x & y) for i, x in enumerate(orcids) for y in orcids[i + 1 :]):
            continue
        keep = choose_kept(group)
        out.append(
            {
                "keep": keep,
                "merge": [f.person_id for f in group if f.person_id != keep],
                "names": {
                    f.person_id: " ".join(x for x in (f.first_name, f.last_name) if x)
                    for f in group
                },
                "pairs": held[root],
            }
        )
    out.sort(key=lambda g: (g["names"][g["keep"]].casefold(), g["keep"]))
    return out
