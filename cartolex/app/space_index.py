# SPDX-License-Identifier: MIT
"""The people's places in the space of the themes: distances, and who uses a keyword.

The space stage (``themes.space``) keeps each person's vector (``Z_ind`` of
``models/embeddings.json``) and their use of each keyword (``X_tf``, else ``X``, of
``models/lexical_data.json``). They are read once per run of that stage and copied into the
project's cache (``cache/atlas/space-<key>/``), which the app then reads memory-mapped:

- ``vectors.npy``: each person's vector as float32, of length one, so a cosine is a dot
  product;
- ``row_*.npy`` and ``col_*.npy``: each person's keyword use as **shares** (the share of
  their use each keyword holds), by person (CSR) and by keyword (CSC).

An organisation's vector is the mean of its current members' (made of length one again), its
keyword use the mean of theirs; a projected person's vector is the ``z`` stored with their
place. Nothing here holds a people × people matrix: the nearest are found one row at a
time, and the exports (:mod:`cartolex.app.distance_exports`) write theirs by blocks of rows.
Only the people on the map are candidates.
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import json
import os
import shutil
import threading
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

__all__ = [
    "SpaceView",
    "compare",
    "keyword_users",
    "members_of",
    "nearest",
    "space_run",
    "space_view",
]

#: The layout of the cached copy; a new value makes a new copy.
VERSION = 1
#: Copies of the space kept in the cache (the latest ones).
KEEP = 2
#: The most people a keyword's answer places on the map (bundle indexes).
MAX_AT = 50_000
#: The shared keywords and texts a comparison lists.
SHARED_KEYWORDS = 15
SHARED_TEXTS = 20
DIGITS = 4
_ARRAYS = ("vectors", "row_ptr", "row_cols", "row_share", "col_ptr", "col_rows", "col_share")
_lock = threading.Lock()


def space_run(layout: Any) -> str | None:
    """The run id of the space stage's current results, or ``None``."""
    from cartolex.build.records import read_record

    record = read_record(layout, "themes.space")
    return record.run_id if record else None


def _folder(layout: Any, run: str) -> Path:
    name = hashlib.blake2b(f"{VERSION}:{run}".encode(), digest_size=8).hexdigest()
    return layout.cache / "atlas" / f"space-{name}"


def _prepare(layout: Any, run: str) -> Path:
    """The cached copy of the space of *run*, written first when it is not there."""
    folder = _folder(layout, run)
    if (folder / "meta.json").is_file():
        return folder
    with _lock:
        if (folder / "meta.json").is_file():
            return folder
        from scipy import sparse

        from cartolex.atlas.model_files import load_embeddings, load_lexical_data

        models = layout.stage("themes.space") / "models"
        data = load_lexical_data(models / "lexical_data.json")
        emb = load_embeddings(models / "embeddings.json")
        z = np.asarray(emb.Z_ind, dtype=np.float32)
        norms = np.linalg.norm(z, axis=1, keepdims=True)
        z /= np.where(norms > 0, norms, 1.0)
        use = data.X_tf if data.X_tf is not None else data.X
        use = sparse.csr_matrix(use, dtype=np.float32)
        use.data[use.data < 0] = 0
        use.eliminate_zeros()
        totals = np.asarray(use.sum(axis=1), dtype=np.float64).ravel()
        inv = np.divide(1.0, totals, out=np.zeros_like(totals), where=totals > 0)
        shares = sparse.csr_matrix(sparse.diags(inv.astype(np.float32)) @ use, dtype=np.float32)
        shares.sort_indices()
        by_term = shares.tocsc()
        by_term.sort_indices()
        arrays = {
            "vectors": z,
            "row_ptr": shares.indptr.astype(np.int64),
            "row_cols": shares.indices.astype(np.int32),
            "row_share": shares.data.astype(np.float32),
            "col_ptr": by_term.indptr.astype(np.int64),
            "col_rows": by_term.indices.astype(np.int32),
            "col_share": by_term.data.astype(np.float32),
        }
        part = folder.with_name(f".{folder.name}.{os.getpid()}.{threading.get_ident()}")
        shutil.rmtree(part, ignore_errors=True)
        part.mkdir(parents=True)
        for name, array in arrays.items():
            np.save(part / f"{name}.npy", array)
        meta = {"version": VERSION, "run": run, "rids": list(data.individuals),
                "terms": list(data.terms)}  # fmt: skip
        (part / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        try:
            os.replace(part, folder)
        except OSError:  # another process wrote it meanwhile
            shutil.rmtree(part, ignore_errors=True)
        with contextlib.suppress(OSError):
            older = sorted(
                (p for p in folder.parent.glob("space-*") if p != folder),
                key=lambda p: p.stat().st_mtime_ns,
            )
            for stale in older[: max(0, len(older) - (KEEP - 1))]:
                shutil.rmtree(stale, ignore_errors=True)
    return folder


@dataclass
class Space:
    """The cached copy of one run of the space, memory-mapped."""

    run: str
    rids: list[str]
    terms: list[str]
    arrays: dict[str, np.ndarray]
    column: dict[str, int]

    @property
    def vectors(self) -> np.ndarray:
        return self.arrays["vectors"]

    def use_of(self, row: int) -> tuple[np.ndarray, np.ndarray]:
        """The keyword columns and shares of a person's row."""
        a = self.arrays
        lo, hi = int(a["row_ptr"][row]), int(a["row_ptr"][row + 1])
        return np.asarray(a["row_cols"][lo:hi]), np.asarray(a["row_share"][lo:hi])

    def users_of(self, col: int) -> tuple[np.ndarray, np.ndarray]:
        """The rows of the people who use the keyword of *col*, and its share of their use."""
        a = self.arrays
        lo, hi = int(a["col_ptr"][col]), int(a["col_ptr"][col + 1])
        return np.asarray(a["col_rows"][lo:hi]), np.asarray(a["col_share"][lo:hi])


def _load(layout: Any, run: str) -> Space:
    folder = _prepare(layout, run)
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    arrays = {name: np.load(folder / f"{name}.npy", mmap_mode="r") for name in _ARRAYS}
    column = {t.casefold(): j for j, t in enumerate(meta["terms"])}
    aliases = layout.stage("keywords.build") / "models" / "term_aliases.csv"
    if aliases.is_file():
        with open(aliases, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                j = column.get((r.get("canonical") or "").casefold())
                if j is not None:
                    column.setdefault((r.get("alias") or "").casefold(), j)
    return Space(run=run, rids=list(meta["rids"]), terms=list(meta["terms"]), arrays=arrays,
                 column=column)  # fmt: skip


def members_of(extras: dict[str, Any] | None) -> dict[str, list[str]]:
    """Each organisation's current members on the map (directly or through an organisation
    below it), from the atlas page's extras (``people`` → ``orgs``, ``organisations`` →
    ``parents``)."""
    members: dict[str, list[str]] = defaultdict(list)
    orgs = (extras or {}).get("organisations") or []
    parents = {o["id"]: o.get("parents") or [] for o in orgs}
    for pid, info in ((extras or {}).get("people") or {}).items():
        todo = list(info.get("orgs") or [])
        seen: set[str] = set()
        while todo:
            o = todo.pop()
            if o in seen:
                continue
            seen.add(o)
            members[o].append(pid)
            todo.extend(parents.get(o, []))
    return dict(members)


@dataclass
class SpaceView:
    """The space with the map's people: each row's person id, name and place in the bundle,
    and the organisations' members. Made once per lineage and tables (see
    :func:`space_view`)."""

    space: Space
    person: list[str]
    name: list[str]
    at: np.ndarray
    row_of: dict[str, int]
    orgs: dict[str, dict[str, Any]]
    members: dict[str, list[str]]
    shares: dict[str, dict[str, float]]
    #: The organisations' levels, smallest first.
    levels: list[str] = field(default_factory=list)
    _org_vectors: dict[str, tuple[list[str], np.ndarray]] = field(default_factory=dict)

    @property
    def mapped(self) -> np.ndarray:
        """Whether each row is a person on the map (a candidate)."""
        return self.at >= 0

    def member_rows(self, org: str) -> np.ndarray:
        rows = [self.row_of[p] for p in self.members.get(org, ()) if p in self.row_of]
        return np.asarray(sorted(rows), dtype=np.int64)

    def org_vector(self, org: str) -> np.ndarray | None:
        rows = self.member_rows(org)
        if not len(rows):
            return None
        return _unit(np.asarray(self.space.vectors[rows], dtype=np.float32).mean(axis=0))

    def level_of(self, level: str | None) -> str:
        """*level* when the organisations have it, else the smallest level."""
        known = self.levels or sorted({o.get("level") or "" for o in self.orgs.values()})
        return level if level in known else (known[0] if known else "")

    def org_vectors(self, level: str) -> tuple[list[str], np.ndarray]:
        """The organisations of *level* that have members on the map, and their vectors."""
        if level not in self._org_vectors:
            ids: list[str] = []
            out: list[np.ndarray] = []
            for oid, o in self.orgs.items():
                if o.get("level") != level:
                    continue
                v = self.org_vector(oid)
                if v is not None:
                    ids.append(oid)
                    out.append(v)
            d = self.space.vectors.shape[1]
            self._org_vectors[level] = (
                ids,
                np.vstack(out) if out else np.zeros((0, d), np.float32),
            )
        return self._org_vectors[level]


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return (v / n).astype(np.float32) if n > 0 else v.astype(np.float32)


def space_view(
    ctx: Any, run: str, bundle: dict[str, Any], extras: dict[str, Any] | None
) -> SpaceView:
    """The space of *run* with the map's people (the bundle's, in the order of the map's
    ``umap_individuals.csv``) and the organisations of the atlas page's *extras*."""
    space = _load(ctx.layout, run)
    order: dict[str, int] = {}
    path = ctx.layout.stage("map.layout") / "umap_individuals.csv"
    if path.is_file():
        with open(path, encoding="utf-8", newline="") as fh:
            for k, r in enumerate(csv.DictReader(fh)):
                order.setdefault(r.get("id", ""), k)
    people = bundle.get("people") or []
    n = len(space.rids)
    at = np.full(n, -1, dtype=np.int64)
    person = [""] * n
    name = [""] * n
    row_of: dict[str, int] = {}
    shares: dict[str, dict[str, float]] = {}
    for row, rid in enumerate(space.rids):
        k = order.get(rid)
        if k is None or k >= len(people):
            continue
        p = people[k]
        if not p.get("person_id"):
            continue
        person[row] = p["person_id"]
        name[row] = p.get("name") or ""
        row_of[p["person_id"]] = row
        shares[p["person_id"]] = dict((p.get("shares") or [{}])[0] or {})
        if p.get("x") is not None:
            at[row] = k
    orgs = {o["id"]: o for o in (extras or {}).get("organisations") or []}
    levels = [lv["id"] for lv in (extras or {}).get("organisation_levels") or []]
    return SpaceView(space=space, person=person, name=name, at=at, row_of=row_of, orgs=orgs,
                     members=members_of(extras), shares=shares, levels=levels)  # fmt: skip


# ── the nearest ───────────────────────────────────────────────────────────────


def _top(sims: np.ndarray, k: int) -> np.ndarray:
    """The indexes of the *k* largest finite values of *sims*, the largest first."""
    ok = np.isfinite(sims)
    k = min(k, int(ok.sum()))
    if k <= 0:
        return np.zeros(0, dtype=np.int64)
    best = np.argpartition(-sims, k - 1)[:k]
    return best[np.argsort(-sims[best], kind="stable")]


def query_vector(view: SpaceView, ctx: Any, kind: str, id_: str) -> np.ndarray | None:
    """The vector of a person, an organisation or a projected person (``None``: none)."""
    if kind == "person":
        row = view.row_of.get(id_)
        return None if row is None else np.asarray(view.space.vectors[row], dtype=np.float32)
    if kind == "organisation":
        return view.org_vector(id_)
    if kind == "projected":
        for overlay in ctx.project.config.overlays:
            path = ctx.layout.stage("overlays.position") / overlay.id / "positions.json"
            if not path.is_file():
                continue
            doc = json.loads(path.read_text(encoding="utf-8"))
            for item in doc.get("items") or []:
                if item.get("person_id") == id_ and item.get("z"):
                    z = np.asarray(item["z"], dtype=np.float32)
                    if z.shape[0] == view.space.vectors.shape[1]:
                        return _unit(z)
        return None
    return None


def nearest(view: SpaceView, ctx: Any, kind: str, id_: str, k: int) -> list[dict[str, Any]] | None:
    """The *k* nearest of *id_* by cosine similarity in the space: people for a person or a
    projected person, organisations of the same level for an organisation (``None``: *id_*
    has no place in the space). Each ``{id, name, similarity}``."""
    q = query_vector(view, ctx, kind, id_)
    if q is None:
        return None
    if kind == "organisation":
        level = (view.orgs.get(id_) or {}).get("level") or ""
        ids, vectors = view.org_vectors(level)
        sims = vectors @ q if len(ids) else np.zeros(0, np.float32)
        sims = sims.astype(np.float64)
        if id_ in ids:
            sims[ids.index(id_)] = -np.inf
        return [
            {
                "id": ids[j],
                "name": view.orgs[ids[j]].get("name") or ids[j],
                "similarity": round(float(sims[j]), DIGITS),
            }  # fmt: skip
            for j in _top(sims, k)
        ]
    sims = np.asarray(view.space.vectors @ q, dtype=np.float64)
    sims[~view.mapped] = -np.inf
    if kind == "person":
        sims[view.row_of[id_]] = -np.inf
    return [
        {"id": view.person[j], "name": view.name[j], "similarity": round(float(sims[j]), DIGITS)}
        for j in _top(sims, k)
    ]


# ── who uses a keyword ───────────────────────────────────────────────────────


def keyword_users(view: SpaceView, term: str, limit: int) -> dict[str, Any] | None:
    """The people who use *term* (or a form merged into it), ranked by the share of their
    keyword use it holds: ``count``, the first *limit* (``items``: ``id``, ``name``,
    ``share``) and ``at`` (their places in the bundle's people, the map's ones, at most
    ``MAX_AT``). ``None`` when the space does not know the term."""
    col = view.space.column.get(term.strip().casefold())
    if col is None:
        return None
    rows, share = view.space.users_of(col)
    known = np.fromiter((bool(view.person[r]) for r in rows), dtype=bool, count=len(rows))
    rows, share = rows[known], share[known]
    order = np.lexsort((rows, -share))
    rows, share = rows[order], share[order]
    at = view.at[rows]
    at = at[at >= 0]
    return {
        "term": view.space.terms[col],
        "count": int(len(rows)),
        "items": [
            {"id": view.person[r], "name": view.name[r], "share": round(float(s), DIGITS)}
            for r, s in zip(rows[:limit].tolist(), share[:limit].tolist(), strict=True)
        ],
        "at": at[:MAX_AT].tolist(),
        "at_capped": bool(len(at) > MAX_AT),
    }


# ── comparing two ─────────────────────────────────────────────────────────────


def _use(view: SpaceView, kind: str, id_: str) -> dict[int, float]:
    """The keyword use of a person, or the mean of an organisation's members'."""
    rows = [view.row_of[id_]] if kind == "person" and id_ in view.row_of else []
    if kind == "organisation":
        rows = view.member_rows(id_).tolist()
    total: dict[int, float] = defaultdict(float)
    for r in rows:
        cols, shares = view.space.use_of(r)
        for c, s in zip(cols.tolist(), shares.tolist(), strict=True):
            total[c] += s
    return {c: s / len(rows) for c, s in total.items()} if rows else {}


def _themes(view: SpaceView, kind: str, id_: str) -> dict[str, float]:
    """The top-level theme shares of a person, or the mean of an organisation's members'."""
    if kind == "person":
        return dict(view.shares.get(id_) or {})
    people = [p for p in view.members.get(id_, ()) if p in view.shares]
    out: dict[str, float] = defaultdict(float)
    for p in people:
        for node, s in view.shares[p].items():
            out[node] += s / len(people)
    return dict(out)


def _people(view: SpaceView, kind: str, id_: str) -> set[str]:
    return {id_} if kind == "person" else set(view.members.get(id_, ()))


def _shared_texts(ctx: Any, a: set[str], b: set[str]) -> tuple[int, list[str]]:
    """How many texts have an author among *a* and one among *b* (the same person counts
    for both), and the first ids."""
    import pyarrow as pa
    import pyarrow.compute as pc

    from .atlas_layers import _batches

    layout = ctx.layout
    if not layout.table("authorships").exists() or not a or not b:
        return 0, []
    wanted = pa.array(sorted(a | b), pa.string())
    of_a: set[str] = set()
    of_b: set[str] = set()
    for batch in _batches(layout.table("authorships"), "authorships", ["text_id", "person_id"]):
        keep = pc.is_in(batch.column(1), value_set=wanted)
        if not pc.any(keep).as_py():
            continue
        rows = batch.filter(keep)
        for tid, pid in zip(rows.column(0).to_pylist(), rows.column(1).to_pylist(), strict=True):
            if pid in a:
                of_a.add(tid)
            if pid in b:
                of_b.add(tid)
    both = sorted(of_a & of_b)
    return len(both), both[:SHARED_TEXTS]


def _cosine(a: dict[Any, float], b: dict[Any, float]) -> float:
    dot = sum(v * b[k] for k, v in a.items() if k in b)
    na = float(np.sqrt(sum(v * v for v in a.values())))
    nb = float(np.sqrt(sum(v * v for v in b.values())))
    return dot / (na * nb) if na > 0 and nb > 0 else 0.0


def compare(view: SpaceView, ctx: Any, a: tuple[str, str], b: tuple[str, str]) -> dict[str, Any]:
    """Two people or organisations side by side: ``space`` (the cosine of their vectors),
    ``keywords`` (``cosine`` and ``jaccard`` of their keyword use, the ``shared`` keywords
    that weigh most for both, ``a`` and ``b`` counts), ``themes`` (``overlap``: Σ min of their
    top-level shares, and the ``shared`` themes) and ``texts`` (``shared``: the texts with an
    author on each side, and the first ``items``)."""
    va = query_vector(view, ctx, *a)
    vb = query_vector(view, ctx, *b)
    space = round(float(va @ vb), DIGITS) if va is not None and vb is not None else None
    ua, ub = _use(view, *a), _use(view, *b)
    common = set(ua) & set(ub)
    union = set(ua) | set(ub)
    shared = sorted(common, key=lambda c: (-min(ua[c], ub[c]), view.space.terms[c]))
    ta, tb = _themes(view, *a), _themes(view, *b)
    nodes = sorted(set(ta) & set(tb), key=lambda n: -min(ta[n], tb[n]))
    n_texts, texts = _shared_texts(ctx, _people(view, *a), _people(view, *b))
    return {
        "space": space,
        "keywords": {
            "cosine": round(_cosine(ua, ub), DIGITS),
            "jaccard": round(len(common) / len(union), DIGITS) if union else 0.0,
            "a": len(ua),
            "b": len(ub),
            "common": len(common),
            "shared": [
                {"term": view.space.terms[c], "a": round(ua[c], DIGITS), "b": round(ub[c], DIGITS)}
                for c in shared[:SHARED_KEYWORDS]
            ],
        },
        "themes": {
            "overlap": round(sum(min(ta[n], tb[n]) for n in nodes), DIGITS),
            "shared": [
                {"node": n, "a": round(ta[n], DIGITS), "b": round(tb[n], DIGITS)} for n in nodes
            ],
        },
        "texts": {"shared": n_texts, "items": texts},
    }


def rows_of(view: SpaceView, ids: Iterable[str] | None) -> np.ndarray:
    """The rows of the people on the map, every one or those of *ids*, in row order."""
    if ids is None:
        return np.flatnonzero(view.mapped)
    rows = {view.row_of[i] for i in ids if i in view.row_of}
    return np.asarray(sorted(r for r in rows if view.at[r] >= 0), dtype=np.int64)
