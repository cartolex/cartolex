# SPDX-License-Identifier: MIT
"""From a project's sources to the engine's corpus contract.

The engine reads a corpus as one index CSV per slot (``last_name``,
``first_name``, ``unit``, ``txt_path``, ``doc_year``, ``doc_type``) and one text
file per document (see ``docs/dev/engine.md``). :func:`assemble_corpus` writes
that contract from a project's tables and decisions, following the reading
order of ``docs/format/sources.md``: people in ``person_id`` order, each
person's texts in slot order then ``position``, each text as its chosen parts.
It is the ``corpus.assemble`` stage's work, and the only place that knows both
sides.

A preprint whose published version is in the same tables (``version_of``)
is left out: only the published version is read.

**One text per work.** An index often holds one work several times: the
same article under two DOIs, an article and its preprint that nothing links,
twin chapters, the conference version of an article. The source tables keep
every record; the corpus reads one text per work. Two texts of a slot are one
work when their normalised titles are the same (at least
:data:`DUPLICATE_MIN_TITLE` characters), their years at most
:data:`DUPLICATE_YEAR_GAP` apart, and they share an author. The text read is
the version of record (:data:`VERSION_RANK`), else the one with the most
words in its parts, else the smallest id; its readers are the authors of
every copy. The copies left out are counted (:attr:`CorpusSummary.duplicate_texts`).

Who goes where comes from ``decisions/people.csv``: ``mapped`` people fill the
fit slots, each projected set's people fill ``overlays/<set>/``; when the file
does not exist, every person is mapped. ``context`` people are left out until
the engine weighs them.

A person's attributes (``columns`` in ``people.parquet``: the filter columns of
an imported list) follow the index's columns, one column each, so the map can
colour and filter people by them; an attribute named like a column of the
contract is written ``person_<name>``.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .layout import ProjectLayout
from .models import ProjectFile
from .tables import read_decision_csv

__all__ = [
    "INDEX_COLUMNS",
    "PEOPLE_COLUMNS",
    "DUPLICATE_MIN_TITLE",
    "DUPLICATE_YEAR_GAP",
    "VERSION_RANK",
    "CorpusSummary",
    "assemble_corpus",
    "attribute_column",
    "duplicate_groups",
    "normalised_title",
    "render_text",
    "version_rank",
]

#: The columns of an engine index, in order.
INDEX_COLUMNS = ("last_name", "first_name", "unit", "txt_path", "doc_year", "doc_type")
#: The columns of ``people.csv``: who each engine identity is (then their attributes).
PEOPLE_COLUMNS = ("person_id", "last_name", "first_name", "unit")
#: A target folder's pairs (one row per person and text) and texts (one row per text).
PAIRS_FILE = "pairs.parquet"
TEXTS_FILE = "texts.parquet"
#: The order parts are read in; ``full`` stands alone.
PART_ORDER = ("title", "abstract", "body")

#: A title shorter than this (normalised, in characters) never makes two texts one work.
DUPLICATE_MIN_TITLE = 25
#: Two texts of one work are at most this many years apart.
DUPLICATE_YEAR_GAP = 1
#: Which text of a work is read, best first: the version of record. Other types come
#: after these, in name order, and a preprint last.
VERSION_RANK = ("article", "review", "chapter", "communication", "proceedings")

_WORD = re.compile(r"[^\W_]+")


def normalised_title(title: str | None) -> str:
    """A title for comparing: lower case, no accents, words only, single spaces."""
    if not title:
        return ""
    plain = unicodedata.normalize("NFKD", title.lower())
    return " ".join(_WORD.findall("".join(c for c in plain if not unicodedata.combining(c))))


def version_rank(doc_type: str | None) -> tuple[int, str]:
    """Where a document type ranks as the version of record (smaller first)."""
    kind = doc_type or ""
    if kind in VERSION_RANK:
        return (VERSION_RANK.index(kind), "")
    return (len(VERSION_RANK) + (kind == "preprint"), kind)


def duplicate_groups(
    texts: Mapping[str, Mapping[str, object]],
    authors: Mapping[str, Iterable[str]],
    *,
    min_title: int = DUPLICATE_MIN_TITLE,
    year_gap: int = DUPLICATE_YEAR_GAP,
) -> list[list[str]]:
    """The texts that are one work (see the module docstring), in groups of two or more.

    *texts* maps a text id to its ``slot``, ``title`` and ``year``; *authors* a text
    id to its authors' ids. Each group is sorted by id, the groups by their first id.
    *min_title* and *year_gap* replace :data:`DUPLICATE_MIN_TITLE` and
    :data:`DUPLICATE_YEAR_GAP`.
    """
    by_title: dict[tuple[str, str], list[str]] = defaultdict(list)
    for tid in sorted(texts):
        row = texts[tid]
        title = normalised_title(row.get("title"))  # type: ignore[arg-type]
        if len(title) >= min_title and row.get("year") is not None:
            by_title[(str(row["slot"]), title)].append(tid)
    parent: dict[str, str] = {}

    def find(t: str) -> str:
        while parent.get(t, t) != t:
            t = parent[t]
        return t

    for tids in by_title.values():
        for i, a in enumerate(tids):
            for b in tids[i + 1 :]:
                gap = abs(int(texts[a]["year"]) - int(texts[b]["year"]))  # type: ignore[call-overload]
                if gap <= year_gap and set(authors.get(a, ())) & set(authors.get(b, ())):
                    ra, rb = find(a), find(b)
                    if ra != rb:
                        parent[max(ra, rb)] = min(ra, rb)
    groups: dict[str, list[str]] = defaultdict(list)
    for t in parent:
        groups[find(t)].append(t)
    for root in list(groups):
        groups[root].append(root)
    return sorted((sorted(set(g)) for g in groups.values()), key=lambda g: g[0])


#: Column names an attribute cannot take (the engine reads them as the document's).
RESERVED = frozenset({*INDEX_COLUMNS, "source", "person_id"})


def attribute_column(name: str) -> str:
    """The index column of a person attribute: its name, or ``person_<name>`` when the
    contract already uses that name."""
    return f"person_{name}" if name in RESERVED else name


@dataclass
class CorpusSummary:
    """What was written, per slot (``overlay:<set>`` for projected sets)."""

    slots: dict[str, dict[str, int]] = field(default_factory=dict)
    skipped_people: int = 0
    texts_without_parts: int = 0
    #: Texts of a document type their slot does not read (a dataset in a collection slot).
    texts_left_out_by_type: int = 0
    #: Extra copies of a work read once (see the module docstring).
    duplicate_texts: int = 0
    #: The characters of the texts written, per target folder.
    characters: dict[Path, int] = field(default_factory=dict)


def render_text(parts: Sequence[tuple[str, str, str]], *, chosen: Sequence[str]) -> str:
    """The text the engine reads for one document.

    *parts* are ``(part, language, content)``. A ``full`` part stands alone;
    otherwise the chosen parts are joined in title, abstract, body order, each in
    every language it has (languages sorted), separated by a blank line, with a
    final newline. Returns ``""`` when nothing is chosen.
    """
    full = sorted((lang, content) for part, lang, content in parts if part == "full")
    if full and "full" in chosen:
        return "\n\n".join(content.strip("\n") for _, content in full) + "\n"
    blocks: list[str] = []
    for kind in PART_ORDER:
        if kind not in chosen:
            continue
        blocks += [
            content.strip("\n")
            for _, content in sorted((lang, c) for p, lang, c in parts if p == kind)
        ]
    return "\n\n".join(blocks) + "\n" if blocks else ""


def _write(path: Path, data: bytes) -> None:
    """A plain write: the corpus goes into a stage's staging folder, flushed once when it is
    swapped into place, so a file is never forced to disk on its own."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _csv_bytes(rows: Iterable[Sequence[object]], columns: Sequence[str] = INDEX_COLUMNS) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def assemble_corpus(
    layout: ProjectLayout,
    config: ProjectFile,
    out_dir: Path,
    *,
    parts: Sequence[str] | Mapping[str, Sequence[str]] = ("title", "abstract"),
    provider_priority: Sequence[str] = (),
    doc_types: Sequence[str] | Mapping[str, Sequence[str] | None] | None = None,
    unit_level: str | None = None,
    duplicate_min_title: int = DUPLICATE_MIN_TITLE,
    duplicate_year_gap: int = DUPLICATE_YEAR_GAP,
) -> CorpusSummary:
    """Write the engine's corpus for *config*'s fit slots and projected sets into *out_dir*.

    A packed corpus in ``out_dir/<slot>/`` for each fit slot, in the project's slot
    order: ``pairs.parquet`` (one row per person and text), ``people.csv`` naming the
    ``person_id`` behind each engine identity (last name, first name, unit, then the
    person's attributes) and ``texts.parquet`` (each text once);
    ``out_dir/overlays/<set>/`` likewise for each projected set: its ``projected``
    people from the project's own tables, or every person of its own ``root``'s
    tables (``<root>/tables/``, laid out like ``sources/tables/``; a relative root is
    relative to the project). *parts* are the parts read of every text, or, by the
    kind of the text's slot (``collection``, ``folder``, ``corpus``), the parts read
    of that slot's texts (a slot the project does not declare, as in an overlay's own
    folder, reads as a collection). *provider_priority* picks one provider per (text,
    part, language), earlier first, unknown providers last in name order. *doc_types*
    are the document types read of every slot, or by the kind of the slot (``None``:
    every type); a slot's own ``doc_types`` in ``project.json`` replace them. A text of
    another type is left out, and counted. *unit_level* names the level whose
    organisation fills the ``unit`` column (default: the project's first level, else
    any affiliation). *duplicate_min_title* and *duplicate_year_gap* are how two texts
    are found to be one work (:func:`duplicate_groups`).

    The tables are read as columns (a text is a few numbers, its id a few bytes) and
    the texts written while their parts are read: memory does not hold the corpus.
    """
    same_work = {"min_title": duplicate_min_title, "year_gap": duplicate_year_gap}
    out_dir = Path(out_dir)
    kinds = {s.id: s.kind for s in config.slots}
    own_types = {s.id: set(s.doc_types) for s in config.slots if s.doc_types}
    slot_rank = {s.id: i for i, s in enumerate(config.slots)}
    fit_slots = [s.id for s in config.slots if s.fit]

    def types_of(slot: str) -> set[str] | None:
        if slot in own_types:
            return own_types[slot]
        if doc_types is None:
            return None
        if isinstance(doc_types, Mapping):
            chosen = doc_types.get(kinds.get(slot, "collection"))
            return set(chosen) if chosen is not None else None
        return set(doc_types)

    def parts_of(slot: str) -> Sequence[str]:
        if isinstance(parts, Mapping):
            return parts.get(kinds.get(slot, "collection"), ("title", "abstract"))
        return parts

    def prepared(tables: Path) -> _Loaded:
        src = _load(tables, config, unit_level)
        src.readable = src.mask_types(types_of)
        src.order = src.slot_order(slot_rank)
        src.moved = _one_text_per_work(src, tables, same_work)
        return src

    main = prepared(layout.tables)
    roles = _roles(layout, main.people)
    summary = CorpusSummary()

    # Who is read where: each target folder, its people and the slots they are read from.
    mapped = sorted(pid for pid, (role, _) in roles.items() if role == "mapped")
    plans: list[tuple[str, Path, list[str], set[str] | None, _Loaded]] = [
        (slot_id, out_dir / slot_id, mapped, {slot_id}, main) for slot_id in fit_slots
    ]
    for overlay in config.overlays:
        target = out_dir / "overlays" / overlay.id
        if overlay.root is None:
            members = sorted(
                pid for pid, (role, s) in roles.items() if role == "projected" and s == overlay.id
            )
            plans.append((f"overlay:{overlay.id}", target, members, None, main))
            continue
        root = Path(overlay.root)
        if not root.is_absolute():
            root = layout.root / root
        own = prepared(root / "tables")
        plans.append((f"overlay:{overlay.id}", target, sorted(own.people), None, own))

    # The texts each target reads; then written while their parts are read, a row group
    # at a time: the parts of the whole corpus are never held in memory together.
    by_source: dict[int, list[tuple[Path, np.ndarray]]] = defaultdict(list)
    sources: dict[int, _Loaded] = {}
    read_any: dict[int, np.ndarray] = {}
    for _, target, members, slots, src in plans:
        wanted = np.zeros(src.n, dtype=bool)
        for pid in members:
            mine = src.texts_of(pid, slots)
            wanted[mine[src.readable[mine]]] = True
        by_source[id(src)].append((target, wanted))
        sources[id(src)] = src
        read_any[id(src)] = read_any.get(id(src), np.zeros(src.n, dtype=bool)) | wanted
    summary.duplicate_texts = sum(
        int(read_any[key][list(src.moved.values())].sum()) if src.moved else 0
        for key, src in sources.items()
    )
    written: dict[Path, tuple[np.ndarray, np.ndarray]] = {}
    for key, targets in by_source.items():
        src = sources[key]
        bodies, rows, chars = _write_texts(src, targets, parts_of, provider_priority)
        for target, _ in targets:
            written[target] = (bodies, rows[target])
        summary.characters.update(chars)
    for name, target, members, slots, src in plans:
        bodies, rows = written[target]
        summary.slots[name] = _emit(target, members, slots, src, bodies, rows, summary)
    summary.skipped_people = sum(
        1 for role, _ in roles.values() if role not in ("mapped", "projected")
    )
    return summary


def _emit(
    target: Path,
    members: list[str],
    slots: set[str] | None,
    src: _Loaded,
    bodies: np.ndarray,
    rows: np.ndarray,
    summary: CorpusSummary,
) -> dict[str, int]:
    """A target's pairs and people (its texts are written): people in ``person_id`` order,
    each person's texts in slot order then ``position``."""
    people, units = src.people, src.units
    attributes = sorted({k for pid in members for k in people[pid]["columns"]})
    keys: list[tuple[str, ...]] = []
    identities: set[tuple[str, str, str]] = set()
    texts = np.zeros(src.n, dtype=bool)
    n_pairs = 0
    with _PairsWriter(target / PAIRS_FILE) as pairs:
        for pid in members:
            person = people[pid]
            n_before = n_pairs
            for t in src.texts_of(pid, slots).tolist():
                if not src.readable[t]:
                    summary.texts_left_out_by_type += 1
                    continue
                if not bodies[t]:
                    summary.texts_without_parts += 1
                    continue
                year = int(src.year[t]) if src.has_year[t] else None
                pairs.add(pid, int(rows[t]), year, src.types[src.doc_type[t]])
                texts[t] = True
                n_pairs += 1
            if n_pairs > n_before:
                unit = units.get(pid, "")
                first = person["first_name"] or ""
                keys.append(
                    (pid, person["last_name"], first, unit,
                     *(person["columns"].get(a, "") for a in attributes))
                )  # fmt: skip
                identities.add((person["last_name"], first, unit))
    columns = (*PEOPLE_COLUMNS, *(attribute_column(a) for a in attributes))
    _write(target / "people.csv", _csv_bytes(keys, columns))
    return {"rows": n_pairs, "texts": int(texts.sum()), "people": len(identities)}


@dataclass
class _Loaded:
    """What the adapter reads from one set of tables (the project's, or an overlay's own),
    as columns: one entry per text of the ``texts`` table, in its order (by ``text_id``)."""

    tables: Path
    keys: np.ndarray  # text ids, UTF-8 bytes, sorted
    slot: np.ndarray
    slots: list[str]
    position: np.ndarray
    year: np.ndarray
    has_year: np.ndarray
    doc_type: np.ndarray
    types: list[str]
    #: Texts read: not a preprint whose published version is in the tables, not a copy.
    alive: np.ndarray
    people: dict[str, dict]
    units: dict[str, str]
    #: Each person's texts (indices, alive only, in id order).
    person_texts: dict[str, np.ndarray]
    readable: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))
    order: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    moved: dict[int, int] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return len(self.keys)

    def tid(self, i: int) -> str:
        return bytes(self.keys[i]).decode("utf-8")

    def lookup(self, ids: Sequence[str]) -> np.ndarray:
        """The index of each text id (``-1``: not in the tables)."""
        wanted = np.array([i.encode("utf-8") for i in ids], dtype=self.keys.dtype)
        found = np.searchsorted(self.keys, wanted)
        found = np.minimum(found, max(self.n - 1, 0))
        hit = self.keys[found] == wanted if self.n else np.zeros(len(ids), dtype=bool)
        return np.where(hit, found, -1)

    def mask_types(self, types_of: Callable[[str], set[str] | None]) -> np.ndarray:
        """Each text: whether its slot reads its document type."""
        allowed = np.ones((len(self.slots), len(self.types)), dtype=bool)
        for s, slot in enumerate(self.slots):
            chosen = types_of(slot)
            if chosen is not None:
                allowed[s] = [t in chosen for t in self.types]
        return allowed[self.slot, self.doc_type] if self.n else np.zeros(0, dtype=bool)

    def slot_order(self, slot_rank: Mapping[str, int]) -> np.ndarray:
        """Each slot code's place in the reading order (the project's order, then by id)."""
        ranked = sorted(
            range(len(self.slots)),
            key=lambda s: (slot_rank.get(self.slots[s], 1 << 30), self.slots[s]),
        )
        order = np.empty(len(self.slots), dtype=np.int64)
        order[ranked] = np.arange(len(ranked))
        return order

    def texts_of(self, pid: str, slots: set[str] | None) -> np.ndarray:
        """A person's texts read from *slots* (every slot: ``None``), in slot order then
        ``position``."""
        mine = self.person_texts.get(pid)
        if mine is None or not len(mine):
            return np.zeros(0, dtype=np.int64)
        if slots is not None:
            codes = [i for i, s in enumerate(self.slots) if s in slots]
            mine = mine[np.isin(self.slot[mine], codes)]
        return mine[np.lexsort((self.position[mine], self.order[self.slot[mine]]))]


def _table(tables: Path, name: str) -> Path:
    return Path(tables) / f"{name}.parquet"


def _codes(column: Any) -> tuple[np.ndarray, list[str]]:
    import pyarrow.compute as pc

    encoded = pc.dictionary_encode(
        column.combine_chunks() if hasattr(column, "combine_chunks") else column
    )
    names = [str(v) if v is not None else "" for v in encoded.dictionary.to_pylist()]
    return np.asarray(
        encoded.indices.fill_null(0).to_numpy(zero_copy_only=False), dtype=np.int32
    ), names


def _load(tables: Path, config: ProjectFile, unit_level: str | None) -> _Loaded:
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    missing = [
        n
        for n in ("texts", "text_parts", "people", "authorships")
        if not _table(tables, n).exists()
    ]
    if missing:
        raise FileNotFoundError(f"{tables}: missing source table(s) {missing}")
    texts = pq.read_table(
        _table(tables, "texts"),
        columns=["text_id", "slot", "position", "year", "doc_type", "version_of"],
    )
    ids = texts["text_id"].to_pylist()
    width = max((len(i.encode("utf-8")) for i in ids), default=1)
    keys = np.array([i.encode("utf-8") for i in ids], dtype=f"S{max(width, 1)}")
    if len(keys) > 1 and not bool(np.all(keys[1:] > keys[:-1])):
        raise ValueError(f"{_table(tables, 'texts')}: rows are not sorted by text_id")
    del ids
    slot, slots = _codes(texts["slot"])
    doc_type, types = _codes(texts["doc_type"])
    year_col = texts["year"]
    has_year = np.asarray(pc.is_valid(year_col).to_numpy(zero_copy_only=False), dtype=bool)
    year = np.asarray(year_col.fill_null(0).to_numpy(zero_copy_only=False), dtype=np.int64)
    position = np.asarray(
        texts["position"].fill_null(0).to_numpy(zero_copy_only=False), dtype=np.int64
    )
    # A preprint whose published version is in the tables is not read: the published text
    # (the version of record, with its year and DOI) is, so a work counts once.
    superseded = pc.is_in(texts["version_of"], value_set=texts["text_id"].combine_chunks())
    alive = ~np.asarray(superseded.fill_null(False).to_numpy(zero_copy_only=False), dtype=bool)
    del texts
    people = {
        row["person_id"]: {**row, "columns": dict(row["columns"] or [])}
        for row in pq.read_table(
            _table(tables, "people"), columns=["person_id", "last_name", "first_name", "columns"]
        ).to_pylist()
    }
    src = _Loaded(
        tables=Path(tables),
        keys=keys,
        slot=slot,
        slots=slots,
        position=position,
        year=year,
        has_year=has_year,
        doc_type=doc_type,
        types=types,
        alive=alive,
        people=people,
        units=_units(tables, config, unit_level),
        person_texts={},
    )
    src.person_texts = _texts_by_person(src)
    return src


def _authorships(src: _Loaded) -> tuple[np.ndarray, list[str]]:
    """Every authorship of the tables: its text's index (``-1``: not a text of the tables)
    and its person's id."""
    import pyarrow.parquet as pq

    table = pq.read_table(_table(src.tables, "authorships"), columns=["text_id", "person_id"])
    texts = src.lookup(table["text_id"].to_pylist())
    return texts, table["person_id"].to_pylist()


def _texts_by_person(src: _Loaded) -> dict[str, np.ndarray]:
    texts, people = _authorships(src)
    keep = (texts >= 0) & src.alive[np.maximum(texts, 0)] if len(texts) else texts >= 0
    by_person: dict[str, list[int]] = defaultdict(list)
    for t, pid in zip(
        texts[keep].tolist(),
        (p for p, k in zip(people, keep.tolist(), strict=True) if k),
        strict=True,
    ):
        by_person[pid].append(t)
    return {pid: np.asarray(ts, dtype=np.int64) for pid, ts in by_person.items()}


def _one_text_per_work(src: _Loaded, tables: Path, same_work: Mapping[str, int]) -> dict[int, int]:
    """Read one text per work of *src* (see the module docstring), in place.

    The copies left out are no longer read; each of their authors reads the text kept
    instead. Returns each copy left out → the text kept (indices).
    """
    import hashlib

    import pyarrow.parquet as pq

    min_title = same_work.get("min_title", DUPLICATE_MIN_TITLE)
    year_gap = same_work.get("year_gap", DUPLICATE_YEAR_GAP)
    candidate = src.alive & src.readable & src.has_year
    # Texts that may share a title: a 64-bit hash of (slot, normalised title) each.
    hashes, owners = [], []
    pf = pq.ParquetFile(_table(tables, "texts"))
    start = 0
    for batch in pf.iter_batches(batch_size=65_536, columns=["title"]):
        for j, title in enumerate(batch.column(0).to_pylist()):
            i = start + j
            if not candidate[i]:
                continue
            norm = normalised_title(title)
            if len(norm) >= min_title:
                key = f"{src.slots[src.slot[i]]}\x00{norm}".encode()
                hashes.append(
                    int.from_bytes(hashlib.blake2b(key, digest_size=8).digest(), "little")
                )
                owners.append(i)
        start += batch.num_rows
    pf.close()
    if not hashes:
        return {}
    h = np.asarray(hashes, dtype=np.uint64)
    o = np.asarray(owners, dtype=np.int64)
    order = np.argsort(h, kind="stable")
    h, o = h[order], o[order]
    repeated = np.zeros(len(h), dtype=bool)
    same = h[1:] == h[:-1]
    repeated[1:] |= same
    repeated[:-1] |= same
    wanted = set(o[repeated].tolist())
    if not wanted:
        return {}
    # The candidates' titles and authors, to apply the rule itself.
    titles: dict[int, str] = {}
    start = 0
    pf = pq.ParquetFile(_table(tables, "texts"))
    for batch in pf.iter_batches(batch_size=65_536, columns=["title"]):
        for j, title in enumerate(batch.column(0).to_pylist()):
            if start + j in wanted:
                titles[start + j] = normalised_title(title)
        start += batch.num_rows
    pf.close()
    texts, people = _authorships(src)
    authors: dict[str, set[str]] = defaultdict(set)
    for t, pid in zip(texts.tolist(), people, strict=True):
        if t in wanted:
            authors[src.tid(t)].add(pid)
    meta = {
        src.tid(i): {"slot": src.slots[src.slot[i]], "title": None, "year": int(src.year[i])}
        for i in wanted
    }
    groups = _groups_of(meta, {src.tid(i): titles[i] for i in wanted}, authors, year_gap)
    if not groups:
        return {}

    def rank(tid: str) -> tuple[int, str]:
        i = int(src.lookup([tid])[0])
        return version_rank(src.types[src.doc_type[i]])

    tied = {t for g in groups for t in g if sum(1 for u in g if rank(u) == min(map(rank, g))) > 1}
    words = _part_words(tables, tied) if tied else {}
    moved: dict[int, int] = {}
    for group in groups:
        keep = min(group, key=lambda t: (rank(t), -words.get(t, 0), t))
        k = int(src.lookup([keep])[0])
        for t in group:
            if t != keep:
                moved[int(src.lookup([t])[0])] = k
    for copy in moved:
        src.alive[copy] = False
    for pid, mine in list(src.person_texts.items()):
        if any(t in moved for t in mine.tolist()):
            src.person_texts[pid] = np.asarray(
                list(dict.fromkeys(moved.get(t, t) for t in mine.tolist())), dtype=np.int64
            )
    return moved


def _groups_of(
    texts: Mapping[str, Mapping[str, Any]],
    titles: Mapping[str, str],
    authors: Mapping[str, Iterable[str]],
    year_gap: int,
) -> list[list[str]]:
    """:func:`duplicate_groups` over titles already normalised (and long enough)."""
    by_title: dict[tuple[str, str], list[str]] = defaultdict(list)
    for tid in sorted(texts):
        by_title[(str(texts[tid]["slot"]), titles[tid])].append(tid)
    parent: dict[str, str] = {}

    def find(t: str) -> str:
        while parent.get(t, t) != t:
            t = parent[t]
        return t

    for tids in by_title.values():
        for i, a in enumerate(tids):
            for b in tids[i + 1 :]:
                gap = abs(int(texts[a]["year"]) - int(texts[b]["year"]))
                if gap <= year_gap and set(authors.get(a, ())) & set(authors.get(b, ())):
                    ra, rb = find(a), find(b)
                    if ra != rb:
                        parent[max(ra, rb)] = min(ra, rb)
    groups: dict[str, list[str]] = defaultdict(list)
    for t in parent:
        groups[find(t)].append(t)
    for root in list(groups):
        groups[root].append(root)
    return sorted((sorted(set(g)) for g in groups.values()), key=lambda g: g[0])


def _part_words(tables: Path, tids: set[str]) -> dict[str, int]:
    """The words in all the parts of each text of *tids* (every provider, every language)."""
    import pyarrow.parquet as pq

    table = pq.read_table(
        _table(tables, "text_parts"),
        columns=["text_id", "content"],
        filters=[("text_id", "in", sorted(tids))],
    )
    out: dict[str, int] = defaultdict(int)
    for tid, content in zip(
        table["text_id"].to_pylist(), table["content"].to_pylist(), strict=True
    ):
        out[tid] += len((content or "").split())
    return dict(out)


def _roles(layout: ProjectLayout, people: dict[str, dict]) -> dict[str, tuple[str, str]]:
    """person_id → (role, set) from ``people.csv``; every person is mapped without it."""
    if not layout.people_csv.exists():
        return {pid: ("mapped", "") for pid in people}
    roles = {pid: ("undecided", "") for pid in people}
    for row in read_decision_csv(layout.people_csv, "people"):
        if row["person_id"] in roles and not row["merged_into"]:
            roles[row["person_id"]] = (row["role"] or "undecided", row["set"])
    return roles


def _units(tables: Path, config: ProjectFile, unit_level: str | None) -> dict[str, str]:
    """person_id → the acronym (else name) of their current organisation at *unit_level*:
    the affiliation that ends last (an open one last of all), the first in the table's
    order on a tie."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    orgs_path, aff_path = _table(tables, "organisations"), _table(tables, "affiliations")
    if not orgs_path.exists() or not aff_path.exists():
        return {}
    level = unit_level or (config.levels[0].id if config.levels else None)
    labels = {
        r["org_id"]: r["acronym"] or r["name"]
        for r in pq.read_table(
            orgs_path, columns=["org_id", "name", "acronym", "level"]
        ).to_pylist()
        if level is None or r["level"] == level
    }
    aff = pq.read_table(aff_path, columns=["person_id", "org_id", "end_year"])
    aff = aff.filter(pc.is_in(aff["org_id"], value_set=pa.array(sorted(labels), pa.string())))
    if aff.num_rows == 0:
        return {}
    rank = pc.fill_null(aff["end_year"].cast(pa.int64()), 1 << 30)
    aff = aff.append_column("rank", rank).append_column("row", pa.array(np.arange(aff.num_rows)))
    aff = aff.sort_by([("person_id", "ascending"), ("rank", "descending"), ("row", "ascending")])
    best: dict[str, str] = {}
    for pid, oid in zip(aff["person_id"].to_pylist(), aff["org_id"].to_pylist(), strict=True):
        if pid not in best:
            best[pid] = labels[oid]
    return best


def _choose(
    rows: list[tuple[str, str, str, str]], rank: dict[str, int]
) -> list[tuple[str, str, str]]:
    """One ``(part, language, content)`` per part and language of one text, by provider priority."""
    best: dict[tuple[str, str], tuple[tuple[int, str], str]] = {}
    for part, lang, provider, content in rows:
        key = (part, lang)
        order = (rank.get(provider, len(rank)), provider)
        if key not in best or order < best[key][0]:
            best[key] = (order, content)
    return [(part, lang, content) for (part, lang), (_, content) in sorted(best.items())]


#: Rows of ``text_parts`` read at a time.
PARTS_BATCH = 20_000


def _write_texts(
    src: _Loaded,
    targets: list[tuple[Path, np.ndarray]],
    parts_of: Callable[[str], Sequence[str]],
    provider_priority: Sequence[str],
) -> tuple[np.ndarray, dict[Path, np.ndarray], dict[Path, int]]:
    """Write each wanted text into the ``texts.parquet`` of the target folders that want it.

    ``text_parts`` is read a batch of rows at a time, in its order (by
    ``text_id``): a text's parts are chosen (one per part and language, by
    provider priority) and the text written once they are all read, so each
    target's texts are in ``text_id`` order. Each batch is checked like the
    whole table (columns, types, values, order, keys), and the order across
    batches too. Returns which texts have a body, each target's row of each text
    (``-1``: not written) and each target's characters.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    from .tables import TableError, _check

    path = _table(src.tables, "text_parts")
    rank = {p: i for i, p in enumerate(provider_priority)}
    wanted_by = [(target, wanted) for target, wanted in targets if wanted.any()]
    writers = {target: _TextsWriter(target / TEXTS_FILE) for target, _ in wanted_by}
    rows = {target: np.full(src.n, -1, dtype=np.int32) for target, _ in targets}
    bodies = np.zeros(src.n, dtype=bool)
    current: int | None = None
    pending: list[tuple[str, str, str, str]] = []
    last_key: tuple | None = None

    def finish(t: int | None) -> None:
        if t is None or t < 0 or not src.alive[t]:
            return
        body = render_text(_choose(pending, rank), chosen=parts_of(src.slots[src.slot[t]]))
        if not body:
            return
        tid = src.tid(t)
        for target, wanted in wanted_by:
            if wanted[t]:
                rows[target][t] = writers[target].add(tid, body)
        bodies[t] = True

    columns = ["text_id", "part", "language", "provider", "content"]
    previous: str | None = None
    pf = pq.ParquetFile(path)
    try:
        for batch in pf.iter_batches(batch_size=PARTS_BATCH):
            table = _check("text_parts", pa.Table.from_batches([batch]), str(path))
            if table.num_rows == 0:
                continue
            first = tuple(table.slice(0, 1).select(list(_KEY)).to_pylist()[0].values())
            if last_key is not None and _order(first) <= _order(last_key):
                raise TableError(f"{path}: rows are not sorted by {', '.join(_KEY)}")
            last_key = tuple(
                table.slice(table.num_rows - 1, 1).select(list(_KEY)).to_pylist()[0].values()
            )
            values = [table[c].to_pylist() for c in columns]
            index = src.lookup(values[0]).tolist()
            for t, (tid, part, lang, provider, content) in zip(
                index, zip(*values, strict=True), strict=True
            ):
                if tid != previous:
                    finish(current)
                    current, pending, previous = t, [], tid
                pending.append((part, lang, provider, content))
        finish(current)
    finally:
        pf.close()
        for writer in writers.values():
            writer.close()
    chars = {target: w.characters for target, w in writers.items()}
    return bodies, rows, chars


class _TextsWriter:
    """A target's ``texts.parquet``: ``text_id`` and ``text``, a row group at a time."""

    GROUP = 10_000

    def __init__(self, path: Path) -> None:
        import pyarrow as pa

        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.schema = pa.schema([("text_id", pa.string()), ("text", pa.large_string())])
        self.count = 0
        self.characters = 0
        self._ids: list[str] = []
        self._texts: list[str] = []
        self._writer = None

    def add(self, tid: str, text: str) -> int:
        """Append a text; returns its row."""
        row = self.count
        self.count += 1
        self.characters += len(text)
        self._ids.append(tid)
        self._texts.append(text)
        if len(self._ids) >= self.GROUP:
            self._flush()
        return row

    def _flush(self) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        if self._writer is None:
            self._writer = pq.ParquetWriter(self.path, self.schema, compression="zstd")
        table = pa.table([pa.array(self._ids), pa.array(self._texts, pa.large_string())],
                         schema=self.schema)  # fmt: skip
        self._writer.write_table(table)
        self._ids, self._texts = [], []

    def close(self) -> None:
        self._flush()
        if self._writer is not None:
            self._writer.close()


class _PairsWriter:
    """A target's ``pairs.parquet``: one row per (person, text), a row group at a time."""

    GROUP = 100_000

    def __init__(self, path: Path) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        path.parent.mkdir(parents=True, exist_ok=True)
        self.schema = pa.schema(
            [("person_id", pa.string()), ("text", pa.int32()), ("doc_year", pa.int32()),
             ("doc_type", pa.string())]
        )  # fmt: skip
        self._writer = pq.ParquetWriter(path, self.schema, compression="zstd")
        self._columns: tuple[list, list, list, list] = ([], [], [], [])

    def add(self, pid: str, row: int, year: int | None, doc_type: str) -> None:
        for column, value in zip(self._columns, (pid, row, year, doc_type), strict=True):
            column.append(value)
        if len(self._columns[0]) >= self.GROUP:
            self._flush()

    def _flush(self) -> None:
        import pyarrow as pa

        if self._columns[0]:
            self._writer.write_table(
                pa.table([pa.array(c, f.type) for c, f in zip(self._columns, self.schema,
                                                                strict=True)],
                         schema=self.schema)
            )  # fmt: skip
            self._columns = ([], [], [], [])

    def __enter__(self) -> _PairsWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self._flush()
        self._writer.close()


_KEY = ("text_id", "part", "language", "provider")


def _order(key: tuple) -> tuple:
    """A key of ``text_parts`` in the table's sort order (a missing value last)."""
    return tuple((v is None, v or "") for v in key)
