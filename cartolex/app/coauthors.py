# SPDX-License-Identifier: MIT
"""Who writes with whom: the co-authors of each person, and the organisations each
organisation writes with, from the authorships of the tables.

Two people are **co-authors** when they signed the same work; their weight is the number
of works they signed together. Everyone in the project counts (the people on the map, and
those collected but not mapped: context, projected, collaborators); an author who is not in
the project is only counted (``outside``: the authors of a person's works who are not in
the project, a person counted once per work). A work counts once:

- the copies of a work (a preprint and its published version, the same title in years
  apart, as ``corpus.assemble`` finds them) are one work, under the text it reads;
- a person merged into another counts as that person;
- a work with more than ``max_authors`` authors in all (``collect.snowball.max_authors``
  of ``params.json``, 25 by default, the rule the collaborators' proposals follow) adds no
  pair and is not counted for anyone: a large collaboration says little about who works
  with whom. Each person's ``large`` counts their works left out so.

Two organisations of one level write together when a work has an author currently in each
(directly or through an organisation below it); the same work counts once per pair, the
same person may count for both.

The graph is sparse: per person, their co-authors and the works together, the strongest
first (CSR arrays). It is computed once per version of the tables, of the merges and of
those parameters and kept in the project's cache beside the texts' view
(``cache/views/texts-<version>/coauthors-<key>/``, ``coorgs-<key>/`` per level), which the
app maps into memory. Nothing holds a people × people matrix.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

__all__ = [
    "FORMAT",
    "Graph",
    "circle",
    "org_graph",
    "person_graph",
]

#: The layout of the kept graph; a new value makes a new copy.
FORMAT = "cartolex-coauthors/1"
#: The graphs of one view kept (the latest ones; merges and parameters change them).
KEEP = 3
#: The pairs made at a time (a block of works with as many authors each).
BLOCK_PAIRS = 4_000_000
#: The most first-circle links read to find the second circle (the strongest first).
MAX_SCAN = 20_000_000
_ARRAYS = ("ptr", "nbr", "cnt", "texts", "outside", "large")


@dataclass
class Graph:
    """Co-authorship between entities (people, or organisations of one level), by code:
    each one's partners (``nbr``, in ``ptr`` ranges) with the works together (``cnt``),
    the strongest first; per entity the works counted (``texts``), the authors of those
    works outside the project (``outside``) and the works left out as too large
    (``large``)."""

    ids: list[str]
    arrays: dict[str, np.ndarray]
    max_authors: int
    _index: dict[str, int] | None = field(default=None, repr=False)

    def code(self, id_: str) -> int | None:
        if self._index is None:
            self._index = {k: i for i, k in enumerate(self.ids)}
        return self._index.get(id_)

    def links(self, code: int) -> tuple[np.ndarray, np.ndarray]:
        """The partners of *code* and the works together, the strongest first."""
        ptr = self.arrays["ptr"]
        lo, hi = int(ptr[code]), int(ptr[code + 1])
        return np.asarray(self.arrays["nbr"][lo:hi]), np.asarray(self.arrays["cnt"][lo:hi])

    def stat(self, name: str, code: int) -> int:
        return int(self.arrays[name][code])

    @property
    def pairs(self) -> int:
        """How many pairs of partners (each counted once)."""
        return len(self.arrays["nbr"]) // 2


# ── building ──────────────────────────────────────────────────────────────────


def build_arrays(
    text: np.ndarray,
    ent: np.ndarray,
    n: int,
    total: np.ndarray,
    inside: np.ndarray,
    max_authors: int,
) -> dict[str, np.ndarray]:
    """The graph of the pairs (*text*, *ent*), distinct and sorted by text then entity, of
    *n* entities: *total* is each text's number of authors in all, *inside* in the project.
    A text whose *total* passes *max_authors* adds no pair (see the module docstring)."""
    text = np.asarray(text, dtype=np.int64)
    ent = np.asarray(ent, dtype=np.int64)
    big = total[text] > max_authors
    small = ~big
    out: dict[str, np.ndarray] = {
        "texts": np.bincount(ent[small], minlength=n).astype(np.int32),
        "large": np.bincount(ent[big], minlength=n).astype(np.int32),
        "outside": np.bincount(
            ent[small], weights=np.maximum(total - inside, 0)[text[small]], minlength=n
        ).astype(np.int64),
    }
    t, e = text[small], ent[small]
    del text, ent, big, small
    keys: list[np.ndarray] = []
    if len(t):
        starts = np.flatnonzero(np.r_[True, t[1:] != t[:-1]])
        k = np.diff(np.r_[starts, len(t)])
        for kk in np.unique(k[k >= 2]).tolist():
            first = starts[k == kk]
            i, j = np.triu_indices(kk, 1)
            step = max(1, BLOCK_PAIRS // len(i))
            for lo in range(0, len(first), step):
                block = e[first[lo : lo + step, None] + np.arange(kk)]
                keys.append((block[:, i] * n + block[:, j]).ravel())
    if keys:
        every = np.concatenate(keys)
        del keys
        every.sort()
        edge = np.flatnonzero(np.r_[True, every[1:] != every[:-1]])
        uniq = every[edge]
        cnt = np.diff(np.r_[edge, len(every)]).astype(np.int32)
        del every
        a, b = uniq // n, uniq % n
        del uniq
        src = np.concatenate([a, b])
        dst = np.concatenate([b, a])
        w = np.concatenate([cnt, cnt])
        del a, b, cnt
        order = np.lexsort((dst, -w, src))
        out["nbr"] = dst[order].astype(np.int32)
        out["cnt"] = w[order].astype(np.int32)
        out["ptr"] = np.r_[0, np.cumsum(np.bincount(src, minlength=n))].astype(np.int64)
    else:
        out["nbr"] = np.zeros(0, np.int32)
        out["cnt"] = np.zeros(0, np.int32)
        out["ptr"] = np.zeros(n + 1, np.int64)
    return out


def _distinct(text: np.ndarray, ent: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """The distinct pairs (*text*, *ent*), sorted by text then entity."""
    from cartolex.scale import sorted_unique

    key = sorted_unique(np.asarray(text, np.int64) * max(n, 1) + np.asarray(ent, np.int64))
    return key // max(n, 1), key % max(n, 1)


def _kept(folder: Path | None, max_authors: int, make: Any) -> Graph:
    """The graph kept in *folder*, else made (``make() → (ids, arrays)``) and kept there."""
    if folder is not None:
        with contextlib.suppress(OSError, ValueError, KeyError):
            meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
            if meta.get("format") == FORMAT:
                arrays = {
                    k: np.load(folder / f"{k}.npy", mmap_mode="r", allow_pickle=False)
                    for k in _ARRAYS
                }
                return Graph(ids=list(meta["ids"]), arrays=arrays, max_authors=max_authors)
    ids, arrays = make()
    graph = Graph(ids=ids, arrays=arrays, max_authors=max_authors)
    if folder is None:
        return graph
    with contextlib.suppress(OSError):
        folder.parent.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=f".{folder.name}.", dir=folder.parent))
        try:
            for k in _ARRAYS:
                np.save(work / f"{k}.npy", arrays[k], allow_pickle=False)
            meta = {"format": FORMAT, "ids": ids, "max_authors": max_authors}
            (work / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            try:
                os.rename(work, folder)
            except OSError:  # kept meanwhile by another app on this project: the same
                pass
        finally:
            shutil.rmtree(work, ignore_errors=True)
        prefix = folder.name.rsplit("-", 1)[0]
        older = sorted(
            (p for p in folder.parent.glob(f"{prefix}-*") if p != folder and p.is_dir()),
            key=lambda p: p.stat().st_mtime_ns,
        )
        for stale in older[: max(0, len(older) - (KEEP - 1))]:
            shutil.rmtree(stale, ignore_errors=True)
    return graph


def _digest(*parts: Any) -> str:
    return hashlib.blake2b(repr(parts).encode("utf-8"), digest_size=8).hexdigest()


def _max_authors(project: Any) -> int:
    from cartolex.collect.decisions import collect_params

    try:
        return int(collect_params(project, "snowball")["max_authors"])
    except Exception:  # an unreadable params.json: the default the collection uses
        from cartolex.collect.decisions import COLLECT_DEFAULTS

        return int(COLLECT_DEFAULTS["snowball"]["max_authors"])


@dataclass
class _Works:
    """The authorships of the tables at the level of works: each authorship's work (a row
    of the texts' view: the text the corpus reads) and person (a code in ``ids``, merged
    people folded into the person they are merged into), distinct; each work's authors in
    all (``total``) and in the project (``inside``)."""

    text: np.ndarray
    person: np.ndarray
    ids: list[str]
    total: np.ndarray
    inside: np.ndarray
    #: The texts' view, each text's work (``canon``), and each authorship of the view as
    #: its text's row (``raw``) and its person's code (``who``), in the view's order.
    view: Any = None
    fold: np.ndarray | None = None
    canon: np.ndarray | None = None
    raw: np.ndarray | None = None
    who: np.ndarray | None = None


def _works(project: Any, cache: Any, roots: dict[str, str]) -> _Works:
    import pyarrow.compute as pc

    from .corpus_view import work_copies
    from .texts_view import texts_view

    view = texts_view(project, cache)
    n = view.n
    # Each text → the text the corpus reads instead (a preprint's published version, the
    # copy of a work it keeps), else itself.
    canon = np.arange(n, dtype=np.int64)
    if n:
        ids = view.table["text_id"]
        version = view.table["version_of"]
        at = pc.index_in(version, value_set=ids.combine_chunks())
        at = np.asarray(at.fill_null(-1).to_numpy(zero_copy_only=False), dtype=np.int64)
        canon = np.where(at >= 0, at, canon)
        copies = work_copies(project, cache)
        if copies:
            keys = list(copies)
            src = pc.index_in(ids, value_set=_strings(keys))
            src = np.asarray(src.fill_null(-1).to_numpy(zero_copy_only=False), dtype=np.int64)
            to = pc.index_in(_strings([copies[k] for k in keys]), value_set=ids.combine_chunks())
            to = np.asarray(to.fill_null(-1).to_numpy(zero_copy_only=False), dtype=np.int64)
            hit = np.flatnonzero(src >= 0)
            target = to[src[hit]]
            ok = target >= 0
            canon[hit[ok]] = target[ok]
        for _ in range(3):  # a chain (a preprint of a copy): followed a few steps
            canon = canon[canon]
    # People: merged rows read as the person they are merged into.
    person_ids = list(view.person_ids)
    index = {p: i for i, p in enumerate(person_ids)}
    fold = np.arange(len(person_ids), dtype=np.int64)
    for pid, root in roots.items():
        i = index.get(pid)
        if i is None:
            continue
        if root not in index:
            index[root] = len(person_ids)
            person_ids.append(root)
        fold[i] = index[root]
    fold = np.r_[fold, np.arange(len(fold), len(person_ids), dtype=np.int64)]
    authors = view.authors
    raw = np.asarray(authors["text"], dtype=np.int64) if len(authors) else np.zeros(0, np.int64)
    who = fold[np.asarray(authors["person"], dtype=np.int64)] if len(authors) else raw
    text, person = _distinct(canon[raw], who, len(person_ids))
    inside = np.bincount(text, minlength=n).astype(np.int64)
    given = (
        np.asarray(view.table["n_authors"].fill_null(0).to_numpy(), dtype=np.int64)
        if n
        else np.zeros(0, np.int64)
    )
    # A copy's authors count for the work: the most any copy gives.
    total = given.copy()
    if n:
        np.maximum.at(total, canon, given)
    total = np.maximum(total, inside)
    return _Works(text=text, person=person, ids=person_ids, total=total, inside=inside,
                  view=view, fold=fold, canon=canon, raw=raw, who=who)  # fmt: skip


def _strings(values: list[str]) -> Any:
    import pyarrow as pa

    return pa.array(values, pa.string())


def _codes_of(values: Any, value_set: Any) -> np.ndarray:
    """Where each of the Arrow strings *values* is in *value_set* (``-1``: not there)."""
    import pyarrow.compute as pc

    found = pc.index_in(values, value_set=value_set)
    return np.asarray(found.fill_null(-1).to_numpy(zero_copy_only=False), dtype=np.int64)


def _roots(project: Any) -> dict[str, str]:
    """Each merged person → the person they are merged into (``people.csv``)."""
    from cartolex.collect.decisions import read_people
    from cartolex.project.identity import merge_roots

    try:
        return merge_roots(read_people(project.layout))
    except (OSError, ValueError):  # no decisions to read: nobody merged
        return {}


def _stat(path: Path) -> tuple[Any, ...]:
    try:
        st = Path(path).stat()
        return (st.st_size, st.st_mtime_ns)
    except FileNotFoundError:
        return (None,)


def _key(project: Any) -> tuple[Any, ...]:
    """What the graph of people depends on, cheaply: the tables (the texts' view), the
    decisions on people and the parameters (their files' versions)."""
    from .texts_view import view_stamp

    layout = project.layout
    return (FORMAT, view_stamp(project), _stat(layout.people_csv), _stat(layout.params_json))


def _folder(view: Any, name: str, *parts: Any) -> Path | None:
    """Where a graph is kept beside the texts' view: named by what it is made from."""
    return view.folder / f"{name}-{_digest(*parts)}" if view.folder is not None else None


def person_graph(project: Any, cache: Any = None) -> Graph:
    """The co-authors of every person of the project (see the module docstring), kept in
    the project's cache once per version of the tables, of the merges and of the
    parameters; kept in the app's *cache* too."""
    from .corpus_view import _same_work
    from .texts_view import texts_view

    key = _key(project)

    def compute() -> Graph:
        max_authors = _max_authors(project)
        roots = _roots(project)
        view = texts_view(project, cache)
        folder = _folder(view, "coauthors", key[1], max_authors, sorted(roots.items()),
                         sorted(_same_work(project).items()))  # fmt: skip

        def make() -> tuple[list[str], dict[str, np.ndarray]]:
            works = _works(project, cache, roots)
            arrays = build_arrays(works.text, works.person, len(works.ids), works.total,
                                  works.inside, max_authors)  # fmt: skip
            return works.ids, arrays

        return _kept(folder, max_authors, make)

    return cache.get(("coauthors", key), compute) if cache is not None else compute()


def org_graph(project: Any, level: str, cache: Any = None) -> Graph:
    """The organisations of *level* that write together: a work counts for an organisation
    when one of its authors is currently in it (directly or through an organisation below
    it), once per pair of organisations; kept like :func:`person_graph`."""
    from .corpus_view import _same_work, org_stamp
    from .texts_view import texts_view

    key = (*_key(project), org_stamp(project), level)

    def compute() -> Graph:
        max_authors = _max_authors(project)
        roots = _roots(project)
        view = texts_view(project, cache)
        folder = _folder(view, f"coorgs-{_digest(level)}", key[1], max_authors,
                         sorted(roots.items()), sorted(_same_work(project).items()),
                         org_stamp(project), level)  # fmt: skip

        def make() -> tuple[list[str], dict[str, np.ndarray]]:
            works = _works(project, cache, roots)
            org_ids, text, org = _org_pairs(project, cache, level, works, roots)
            arrays = build_arrays(text, org, len(org_ids), works.total, works.inside,
                                  max_authors)  # fmt: skip
            return org_ids, arrays

        return _kept(folder, max_authors, make)

    return cache.get(("coorgs", key), compute) if cache is not None else compute()


#: The authorships whose organisations are found at a time.
ORG_CHUNK = 1_000_000


def _csr(keys: np.ndarray, values: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """*values* grouped by *keys* (codes below *n*): ``ptr`` and the values in key order."""
    order = np.argsort(keys, kind="stable")
    return np.r_[0, np.cumsum(np.bincount(keys, minlength=n))].astype(np.int64), values[order]


def _expand(rows: np.ndarray, ptr: np.ndarray, flat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each of *rows* (codes into ``ptr``) repeated once per value it has in *flat*: the
    positions in *rows* and the values."""
    deg = ptr[rows + 1] - ptr[rows]
    at = np.repeat(np.arange(len(rows), dtype=np.int64), deg)
    first = np.repeat(ptr[rows], deg)
    offset = np.arange(len(at), dtype=np.int64) - np.repeat(np.cumsum(deg) - deg, deg)
    return at, flat[first + offset]


def _org_pairs(
    project: Any, cache: Any, level: str, works: _Works, roots: dict[str, str]
) -> tuple[list[str], np.ndarray, np.ndarray]:
    """The organisations of *level* (their ids), and the distinct pairs (work, organisation)
    of the works and the organisations they count for. For each author of a text, the
    organisations are (the rule of the tables, see the format's documentation): those
    stated on the text for that author; else the author's affiliations whose years contain
    the text's year; else their current affiliations; each lifted to *level* through the
    organisations' parents (the first of these that reaches *level*). Organisations merged
    into another count as that one; a merged person's affiliations as their person's."""
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    from cartolex.project.organisations import org_decisions, org_roots
    from cartolex.project.tables import find_ids, id_keys
    from cartolex.scale import sorted_unique

    from .corpus_view import effective_affiliation_table, organisations

    layout = project.layout
    orgs = organisations(project, cache) if layout.table("organisations").exists() else []
    at_level = sorted(o["org_id"] for o in orgs if (o["level"] or "") == level)
    none = (at_level, np.zeros(0, np.int64), np.zeros(0, np.int64))
    if not at_level or not len(works.who):
        return none
    # Every organisation → the organisations of *level* at or above it (CSR by its code).
    known = {o["org_id"]: i for i, o in enumerate(orgs)}
    merged = org_roots(org_decisions(layout))
    parents = {o["org_id"]: list(o["parents"]) for o in orgs}
    level_code = {o: i for i, o in enumerate(at_level)}
    up_rows: list[int] = []
    up_vals: list[int] = []
    for oid, i in known.items():
        seen: set[str] = set()
        todo = [oid]
        while todo:
            o = todo.pop()
            if o in seen:
                continue
            seen.add(o)
            todo.extend(parents.get(o, []))
        for o in seen:
            if o in level_code:
                up_rows.append(i)
                up_vals.append(level_code[o])
    up_ptr, up = _csr(np.asarray(up_rows, np.int64), np.asarray(up_vals, np.int64), len(orgs))

    known_ids = _strings(list(known))
    merged_from = _strings(list(merged))
    merged_to = np.asarray([known.get(r, -1) for r in merged.values()], dtype=np.int64)

    def org_code(ids: Any) -> np.ndarray:
        """The codes of the organisation ids of the Arrow array *ids* (``-1``: unknown)."""
        code = _codes_of(ids, known_ids)
        lost = np.flatnonzero(code < 0)
        if len(lost) and len(merged_to):
            via = _codes_of(ids.take(lost), merged_from)
            code[lost] = np.where(via >= 0, merged_to[np.maximum(via, 0)], -1)
        return code

    view = works.view
    n_auth = len(works.who)
    # 1. Stated on the text: the authorships table's ``orgs``, row for row with the view's.
    s_row: list[np.ndarray] = []
    s_org: list[np.ndarray] = []
    path = layout.table("authorships")
    if path.exists() and view.n:
        keys = id_keys(view.table["text_id"])
        names = pq.ParquetFile(path).schema_arrow.names
        start = 0
        aligned = "orgs" in names
        if aligned:
            for batch in pq.ParquetFile(path).iter_batches(
                batch_size=262_144, columns=["text_id", "orgs"]
            ):
                at = find_ids(keys, batch.column(0))
                ok = np.flatnonzero(at >= 0)
                end = start + len(ok)
                if end > n_auth or not np.array_equal(at[ok], works.raw[start:end]):
                    aligned = False  # the view was made from another version: none stated
                    break
                lists = batch.column(1).take(ok)
                lengths = np.asarray(
                    pc.list_value_length(lists).fill_null(0).to_numpy(zero_copy_only=False),
                    dtype=np.int64,
                )
                if lengths.sum():
                    flat = pc.list_flatten(lists)
                    s_row.append(np.repeat(np.arange(start, end, dtype=np.int64), lengths))
                    s_org.append(org_code(flat))
                start = end
        if not aligned:
            s_row, s_org = [], []
    row = np.concatenate(s_row) if s_row else np.zeros(0, np.int64)
    org = np.concatenate(s_org) if s_org else np.zeros(0, np.int64)
    ok = org >= 0
    row, org = row[ok], org[ok]
    at, lifted = _expand(org, up_ptr, up)
    found_rows = [row[at]]
    found_orgs = [lifted]
    done = np.zeros(n_auth, dtype=bool)
    done[row[at]] = True
    # 2. and 3. The affiliations, dated, then current, of the authorships still without one.
    if layout.table("affiliations").exists():
        table = effective_affiliation_table(project, ["start_year", "end_year"])
        a_person = _codes_of(table["person_id"], _strings(works.ids))
        a_person = np.where(a_person >= 0, works.fold[np.maximum(a_person, 0)], -1)
        a_org = org_code(table["org_id"].combine_chunks())
        a_start = np.asarray(table["start_year"].fill_null(-(1 << 30)).to_numpy(), np.int64)
        a_end = np.asarray(table["end_year"].fill_null(1 << 30).to_numpy(), np.int64)
        a_open = np.asarray(pc.is_null(table["end_year"]).to_numpy(zero_copy_only=False), bool)
        keep = (a_person >= 0) & (a_org >= 0)
        aff = np.flatnonzero(keep)
        aff_ptr, aff_sorted = _csr(a_person[aff], aff, len(works.ids))
        year = np.where(view.has_year, np.asarray(view.year, np.int64), -1)
        span = int(year.max()) + 2 if len(year) else 1
        for dated in (True, False):
            # What an author's organisations depend on: the person and the text's year
            # (the person alone for the current ones), found once per distinct key.
            todo = np.flatnonzero(~done)
            who = works.who[todo]
            key = who * span + (year[works.raw[todo]] + 1 if dated else 0)
            keys = sorted_unique(key)
            k_who, k_year = keys // span, keys % span - 1
            k_rows: list[np.ndarray] = []
            k_orgs: list[np.ndarray] = []
            for lo in range(0, len(keys), ORG_CHUNK):
                part = np.arange(lo, min(lo + ORG_CHUNK, len(keys)), dtype=np.int64)
                pos, aff_at = _expand(k_who[part], aff_ptr, aff_sorted)
                if dated:
                    y = k_year[part[pos]]
                    fit = (y >= 0) & (a_start[aff_at] <= y) & (y <= a_end[aff_at])
                else:
                    fit = a_open[aff_at]
                pos, aff_at = pos[fit], aff_at[fit]
                at, lifted = _expand(a_org[aff_at], up_ptr, up)
                k_rows.append(part[pos[at]])
                k_orgs.append(lifted)
            k_row = np.concatenate(k_rows) if k_rows else np.zeros(0, np.int64)
            k_org = np.concatenate(k_orgs) if k_orgs else np.zeros(0, np.int64)
            k_row, k_org = _distinct(k_row, k_org, len(at_level))
            k_ptr, k_flat = _csr(k_row, k_org, len(keys))
            pos, lifted = _expand(np.searchsorted(keys, key), k_ptr, k_flat)
            hit = todo[pos]
            found_rows.append(hit)
            found_orgs.append(lifted)
            done[hit] = True
    rows = np.concatenate(found_rows)
    text, org = _distinct(works.canon[works.raw[rows]], np.concatenate(found_orgs), len(at_level))
    return at_level, text, org


# ── reading ───────────────────────────────────────────────────────────────────


@dataclass
class Circle:
    """The partners of one entity: the first circle (``first``: codes, ``texts``) and, when
    asked, the second (``second``: codes, ``paths``, ``weight``; :meth:`via` the first-circle
    partners each is reached through; ``edges``: links from the first circle to the second,
    as ``(from, to, works)``)."""

    first: np.ndarray
    texts: np.ndarray
    second: np.ndarray | None = None
    paths: np.ndarray | None = None
    weight: np.ndarray | None = None
    edges: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None
    partial: bool = False
    _from: np.ndarray | None = None
    _start: np.ndarray | None = None

    def via(self, rank: int, most: int = 3) -> list[int]:
        """The first-circle partners the *rank*-th of the second circle is reached
        through, the strongest link first (at most *most*)."""
        if self._from is None or self._start is None:
            return []
        lo = int(self._start[rank])
        return self._from[lo : lo + min(most, int(self.paths[rank]))].tolist()


def circle(graph: Graph, code: int, second: bool = False, max_scan: int = MAX_SCAN) -> Circle:
    """The first circle of *code*, and the second when asked: the partners of its partners
    that are neither it nor a partner, ranked by how many partners lead to them
    (``paths``), then by the works along those links; at most *max_scan* links are read
    (the strongest partners first; ``partial`` says when some were left)."""
    first, texts = graph.links(code)
    out = Circle(first=first, texts=texts)
    if not second:
        return out
    empty = np.zeros(0, np.int64)
    ptr = graph.arrays["ptr"]
    sizes = (np.asarray(ptr[first + 1]) - np.asarray(ptr[first])).astype(np.int64)
    room = np.cumsum(sizes) <= max_scan
    out.partial = not bool(room.all())
    parts_to: list[np.ndarray] = []
    parts_from: list[np.ndarray] = []
    parts_w: list[np.ndarray] = []
    for q in first[room].tolist():
        nb, w = graph.links(q)
        parts_to.append(nb)
        parts_from.append(np.full(len(nb), q, dtype=np.int64))
        parts_w.append(w)
    if not parts_to:
        out.second, out.paths, out.weight = empty, empty, empty
        out.edges = (empty, empty, empty)
        return out
    to = np.concatenate(parts_to).astype(np.int64)
    frm = np.concatenate(parts_from)
    w = np.concatenate(parts_w).astype(np.int64)
    known = np.zeros(len(graph.ids), dtype=bool)
    known[first] = True
    known[code] = True
    keep = ~known[to]
    to, frm, w = to[keep], frm[keep], w[keep]
    out.edges = (frm, to, w)
    if not len(to):
        out.second, out.paths, out.weight = empty, empty, empty
        return out
    order = np.lexsort((-w, to))
    to_s, frm_s, w_s = to[order], frm[order], w[order]
    start = np.flatnonzero(np.r_[True, to_s[1:] != to_s[:-1]])
    paths = np.diff(np.r_[start, len(to_s)])
    weight = np.add.reduceat(w_s, start)
    who = to_s[start]
    rank = np.lexsort((who, -weight, -paths))
    out.second, out.paths, out.weight = who[rank], paths[rank], weight[rank]
    out._from, out._start = frm_s, start[rank]
    return out


# ── answers ───────────────────────────────────────────────────────────────────

#: The most links an answer gives to draw (the strongest first).
MAX_LINES = 2_000


def answer(
    graph: Graph,
    id_: str,
    describe: Any,
    drawn: Any,
    *,
    second: bool = False,
    offset: int = 0,
    limit: int = 50,
    offset2: int = 0,
    limit2: int = 50,
) -> dict[str, Any]:
    """The partners of *id_* as the API gives them (see ``GET /api/atlas/coauthors``):
    *describe(ids)* → for each id ``{id, name, …, place}``, *drawn(ids)* → whether each
    has a place on the map (the links to draw are between those)."""
    code = graph.code(id_)
    out: dict[str, Any] = {"id": id_, "circle": 2 if second else 1,
                           "max_authors": graph.max_authors}  # fmt: skip
    nobody: dict[str, Any] = {"count": 0, "items": [], "lines": []}
    if code is None:  # in the project, but no work counted: nobody
        out.update(texts=0, large=0, outside=0, placed=0, offset=offset, limit=limit, **nobody)
        if second:
            out["second"] = {**nobody, "offset": offset2, "limit": limit2, "partial": False}
        return out
    found = circle(graph, code, second)
    ids = graph.ids
    first = [ids[c] for c in found.first.tolist()]
    texts = found.texts.tolist()
    on = np.asarray(drawn(first), dtype=bool) if first else np.zeros(0, bool)
    page = slice(offset, offset + limit)
    out.update(
        texts=graph.stat("texts", code),
        large=graph.stat("large", code),
        outside=graph.stat("outside", code),
        count=len(first),
        placed=int(on.sum()),
        offset=offset,
        limit=limit,
        items=[{**e, "texts": n} for e, n in zip(describe(first[page]), texts[page], strict=True)],
        lines=[[first[k], texts[k]] for k in np.flatnonzero(on)[:MAX_LINES].tolist()],
    )
    if second:
        assert found.second is not None and found.paths is not None
        assert found.weight is not None and found.edges is not None
        rows = list(range(offset2, min(offset2 + limit2, len(found.second))))
        shown = describe([ids[int(found.second[r])] for r in rows])
        # The links from the first circle to the second, between partners on the map.
        frm, to, w = found.edges
        lines: list[list[Any]] = []
        if len(frm):
            codes = np.unique(np.r_[frm, to])
            flag = np.zeros(len(ids), dtype=bool)
            flag[codes] = np.asarray(drawn([ids[c] for c in codes.tolist()]), dtype=bool)
            keep = flag[frm] & flag[to]
            order = np.argsort(-w[keep], kind="stable")[:MAX_LINES]
            lines = [
                [ids[f], ids[t], n]
                for f, t, n in zip(frm[keep][order].tolist(), to[keep][order].tolist(),
                                   w[keep][order].tolist(), strict=True)
            ]  # fmt: skip
        out["second"] = {
            "count": len(found.second),
            "offset": offset2,
            "limit": limit2,
            "items": [
                {
                    **e,
                    "paths": int(found.paths[r]),
                    "texts": int(found.weight[r]),
                    "via": [ids[c] for c in found.via(r)],
                }
                for e, r in zip(shown, rows, strict=True)
            ],  # fmt: skip
            "lines": lines,
            "partial": found.partial,
        }
    return out
