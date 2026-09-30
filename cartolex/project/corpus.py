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

from .layout import ProjectLayout
from .models import ProjectFile
from .tables import read_decision_csv, read_source_table

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
#: The columns of ``people.csv`` beside each index: who each engine identity is.
PEOPLE_COLUMNS = ("person_id", "last_name", "first_name", "unit")
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
    texts: Mapping[str, Mapping[str, object]], authors: Mapping[str, Iterable[str]]
) -> list[list[str]]:
    """The texts that are one work (see the module docstring), in groups of two or more.

    *texts* maps a text id to its ``slot``, ``title`` and ``year``; *authors* a text
    id to its authors' ids. Each group is sorted by id, the groups by their first id.
    """
    by_title: dict[tuple[str, str], list[str]] = defaultdict(list)
    for tid in sorted(texts):
        row = texts[tid]
        title = normalised_title(row.get("title"))  # type: ignore[arg-type]
        if len(title) >= DUPLICATE_MIN_TITLE and row.get("year") is not None:
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
                if gap <= DUPLICATE_YEAR_GAP and set(authors.get(a, ())) & set(authors.get(b, ())):
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
) -> CorpusSummary:
    """Write the engine's corpus for *config*'s fit slots and projected sets into *out_dir*.

    ``out_dir/<slot>/index.csv`` and ``out_dir/<slot>/texts/<text_id>.txt`` for each
    fit slot, in the project's slot order, with ``out_dir/<slot>/people.csv``
    naming the ``person_id`` behind each engine identity (last name, first
    name, unit); ``out_dir/overlays/<set>/`` likewise for each projected set:
    its ``projected`` people from the project's own tables, or every person of
    its own ``root``'s tables (``<root>/tables/``, laid out like
    ``sources/tables/``; a relative root is relative to the project).
    *parts* are the parts read of every text, or, by the kind of the text's slot
    (``collection``, ``folder``, ``corpus``), the parts read of that slot's texts
    (a slot the project does not declare, as in an overlay's own folder, reads as
    a collection). *provider_priority* picks one provider per (text, part,
    language), earlier first, unknown providers last in name order. *doc_types*
    are the document types read of every slot, or by the kind of the slot
    (``None``: every type); a slot's own ``doc_types`` in ``project.json``
    replace them. A text of another type is left out, and counted. *unit_level* names the level
    whose organisation fills the ``unit`` column (default: the project's first
    level, else any affiliation).
    """
    out_dir = Path(out_dir)
    main = _load(layout.tables, config, unit_level, provider_priority)
    copies: dict[Path, dict[str, str]] = {}
    roles = _roles(layout, main.people)
    summary = CorpusSummary()
    slot_rank = {s.id: i for i, s in enumerate(config.slots)}
    fit_slots = [s.id for s in config.slots if s.fit]
    written: dict[Path, set[str]] = defaultdict(set)
    kinds = {s.id: s.kind for s in config.slots}
    own_types = {s.id: set(s.doc_types) for s in config.slots if s.doc_types}

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

    def readable(tid: str, src: _Loaded) -> bool:
        allowed = types_of(src.text_meta[tid]["slot"])
        return allowed is None or src.text_meta[tid]["doc_type"] in allowed

    copies[layout.tables] = _one_text_per_work(main, layout.tables, readable)

    def texts_of(pid: str, slots: set[str] | None, src: _Loaded) -> list[str]:
        text_meta = src.text_meta
        found = [
            t for t in src.by_person.get(pid, ()) if slots is None or text_meta[t]["slot"] in slots
        ]
        found.sort(
            key=lambda t: (
                slot_rank.get(text_meta[t]["slot"], 1 << 30),
                text_meta[t]["slot"],
                text_meta[t]["position"],
            )
        )
        return found

    def emit(
        target: Path, members: list[str], slots: set[str] | None, src: _Loaded, bodies: set[str]
    ) -> dict[str, int]:
        rows = []
        keys: list[tuple[str, str, str, str]] = []
        people, units, text_meta = src.people, src.units, src.text_meta
        attributes = sorted({k for pid in members for k in people[pid]["columns"]})
        for pid in members:
            person = people[pid]
            n_before = len(rows)
            for tid in texts_of(pid, slots, src):
                if not readable(tid, src):
                    summary.texts_left_out_by_type += 1
                    continue
                if tid not in bodies:
                    summary.texts_without_parts += 1
                    continue
                rel = f"texts/{tid}.txt"
                written[target].add(tid)
                meta = text_meta[tid]
                rows.append(
                    (
                        person["last_name"],
                        person["first_name"] or "",
                        units.get(pid, ""),
                        rel,
                        "" if meta["year"] is None else meta["year"],
                        meta["doc_type"],
                        *(person["columns"].get(a, "") for a in attributes),
                    )
                )
            if len(rows) > n_before:
                keys.append(
                    (pid, person["last_name"], person["first_name"] or "", units.get(pid, ""))
                )
        columns = (*INDEX_COLUMNS, *(attribute_column(a) for a in attributes))
        _write(target / "index.csv", _csv_bytes(rows, columns))
        _write(target / "people.csv", _csv_bytes(keys, PEOPLE_COLUMNS))
        return {
            "rows": len(rows),
            "texts": len(written[target]),
            "people": len({(r[0], r[1], r[2]) for r in rows}),
        }

    # Who is read where: each target folder, its people and the slots they are read from.
    mapped = sorted(pid for pid, (role, _) in roles.items() if role == "mapped")
    plans: list[tuple[str, Path, list[str], set[str] | None, _Loaded, Path]] = [
        (slot_id, out_dir / slot_id, mapped, {slot_id}, main, layout.tables)
        for slot_id in fit_slots
    ]
    for overlay in config.overlays:
        target = out_dir / "overlays" / overlay.id
        if overlay.root is None:
            members = sorted(
                pid for pid, (role, s) in roles.items() if role == "projected" and s == overlay.id
            )
            plans.append((f"overlay:{overlay.id}", target, members, None, main, layout.tables))
            continue
        root = Path(overlay.root)
        if not root.is_absolute():
            root = layout.root / root
        own = _load(root / "tables", config, unit_level, provider_priority)
        copies[root / "tables"] = _one_text_per_work(own, root / "tables", readable)
        plans.append(
            (f"overlay:{overlay.id}", target, sorted(own.people), None, own, root / "tables")
        )

    # The texts are written while their parts are read, a row group at a time: the
    # parts of the whole corpus are never held in memory together.
    by_tables: dict[Path, list[tuple[Path, set[str]]]] = defaultdict(list)
    for _, target, members, slots, src, tables in plans:
        wanted = {t for pid in members for t in texts_of(pid, slots, src) if readable(t, src)}
        by_tables[tables].append((target, wanted))
    read = {t for targets in by_tables.values() for _, wanted in targets for t in wanted}
    summary.duplicate_texts = sum(
        1 for moved in copies.values() for kept in moved.values() if kept in read
    )
    bodies: dict[Path, set[str]] = {}
    for tables, targets in by_tables.items():
        src = main if tables == layout.tables else next(p[4] for p in plans if p[5] == tables)
        meta = src.text_meta
        bodies[tables] = _write_texts(
            tables, meta, targets, lambda tid, m=meta: parts_of(m[tid]["slot"]), provider_priority
        )
    for name, target, members, slots, src, tables in plans:
        summary.slots[name] = emit(target, members, slots, src, bodies[tables])
    summary.skipped_people = sum(
        1 for role, _ in roles.values() if role not in ("mapped", "projected")
    )
    return summary


@dataclass
class _Loaded:
    """What the adapter reads from one set of tables (the project's, or an overlay's own)."""

    text_meta: dict[str, dict]
    people: dict[str, dict]
    units: dict[str, str]
    by_person: dict[str, list[str]]


def _table(tables: Path, name: str) -> Path:
    return Path(tables) / f"{name}.parquet"


def _load(
    tables: Path, config: ProjectFile, unit_level: str | None, provider_priority: Sequence[str]
) -> _Loaded:
    missing = [
        n
        for n in ("texts", "text_parts", "people", "authorships")
        if not _table(tables, n).exists()
    ]
    if missing:
        raise FileNotFoundError(f"{tables}: missing source table(s) {missing}")
    texts = read_source_table(_table(tables, "texts"), "texts")
    rows = texts.select(["text_id", "slot", "position", "year", "doc_type", "version_of", "title"])
    text_meta = {row["text_id"]: row for row in rows.to_pylist()}
    # A preprint whose published version is in the tables is not read: the published text
    # (the version of record, with its year and DOI) is, so a work counts once.
    for tid in [t for t, row in text_meta.items() if row["version_of"] in text_meta]:
        del text_meta[tid]
    people = {
        row["person_id"]: {**row, "columns": dict(row["columns"] or [])}
        for row in read_source_table(_table(tables, "people"), "people")
        .select(["person_id", "last_name", "first_name", "columns"])
        .to_pylist()
    }
    return _Loaded(
        text_meta=text_meta,
        people=people,
        units=_units(tables, config, unit_level),
        by_person=_texts_by_person(tables, text_meta),
    )


def _one_text_per_work(
    src: _Loaded, tables: Path, readable: Callable[[str, _Loaded], bool]
) -> dict[str, str]:
    """Read one text per work of *src* (see the module docstring), in place.

    The copies left out leave ``text_meta``; each of their authors reads the text
    kept instead. Returns each copy left out → the text kept.
    """
    meta = src.text_meta
    authors: dict[str, set[str]] = defaultdict(set)
    for pid, tids in src.by_person.items():
        for tid in tids:
            authors[tid].add(pid)
    groups = duplicate_groups({t: m for t, m in meta.items() if readable(t, src)}, authors)
    if not groups:
        return {}

    def rank(tid: str) -> tuple[int, str]:
        return version_rank(meta[tid]["doc_type"])

    tied = {t for g in groups for t in g if sum(1 for u in g if rank(u) == min(map(rank, g))) > 1}
    words = _part_words(tables, tied) if tied else {}
    moved: dict[str, str] = {}
    for group in groups:
        keep = min(group, key=lambda t: (rank(t), -words.get(t, 0), t))
        moved.update({t: keep for t in group if t != keep})
    for pid, tids in src.by_person.items():
        src.by_person[pid] = list(dict.fromkeys(moved.get(t, t) for t in tids))
    for tid in moved:
        del meta[tid]
    return moved


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
    """person_id → the acronym (else name) of their current organisation at *unit_level*."""
    orgs_path, aff_path = _table(tables, "organisations"), _table(tables, "affiliations")
    if not orgs_path.exists() or not aff_path.exists():
        return {}
    level = unit_level or (config.levels[0].id if config.levels else None)
    orgs = {
        r["org_id"]: r
        for r in read_source_table(orgs_path, "organisations")
        .select(["org_id", "name", "acronym", "level"])
        .to_pylist()
    }
    best: dict[str, tuple[int, str]] = {}
    for aff in read_source_table(aff_path, "affiliations").to_pylist():
        org = orgs.get(aff["org_id"])
        if org is None or (level is not None and org["level"] != level):
            continue
        rank = aff["end_year"] if aff["end_year"] is not None else 1 << 30
        label = org["acronym"] or org["name"]
        if aff["person_id"] not in best or rank > best[aff["person_id"]][0]:
            best[aff["person_id"]] = (rank, label)
    return {pid: label for pid, (_, label) in best.items()}


def _texts_by_person(tables: Path, text_meta: dict[str, dict]) -> dict[str, list[str]]:
    table = read_source_table(_table(tables, "authorships"), "authorships").select(
        ["text_id", "person_id"]
    )
    by_person: dict[str, list[str]] = defaultdict(list)
    for tid, pid in zip(table["text_id"].to_pylist(), table["person_id"].to_pylist(), strict=True):
        if tid in text_meta:
            by_person[pid].append(tid)
    return by_person


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
    tables: Path,
    text_meta: dict[str, dict],
    targets: list[tuple[Path, set[str]]],
    parts: Sequence[str] | Callable[[str], Sequence[str]],
    provider_priority: Sequence[str],
) -> set[str]:
    """Write each wanted text into the target folders that want it; return the texts with a body.

    ``text_parts`` is read a batch of rows at a time, in its order (by
    ``text_id``): a text's parts are chosen (one per part and language, by
    provider priority) and its file written once they are all read. Each batch
    is checked like the whole table (columns, types, values, order, keys),
    and the order across batches too.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    from .tables import TableError, _check

    path = _table(tables, "text_parts")
    rank = {p: i for i, p in enumerate(provider_priority)}
    wanted_by = [(target, wanted) for target, wanted in targets if wanted]
    bodies: set[str] = set()
    current: str | None = None
    pending: list[tuple[str, str, str, str]] = []
    last_key: tuple | None = None

    def finish(tid: str | None) -> None:
        if tid is None or tid not in text_meta:
            return
        chosen = parts(tid) if callable(parts) else parts
        body = render_text(_choose(pending, rank), chosen=chosen)
        if not body:
            return
        data = None
        for target, wanted in wanted_by:
            if tid in wanted:
                data = data if data is not None else body.encode("utf-8")
                _write(target / f"texts/{tid}.txt", data)
        bodies.add(tid)

    columns = ["text_id", "part", "language", "provider", "content"]
    for batch in pq.ParquetFile(path).iter_batches(batch_size=PARTS_BATCH):
        table = _check("text_parts", pa.Table.from_batches([batch]), str(path))
        if table.num_rows == 0:
            continue
        first = tuple(table.slice(0, 1).select(list(_KEY)).to_pylist()[0].values())
        if last_key is not None and _order(first) <= _order(last_key):
            raise TableError(f"{path}: rows are not sorted by {', '.join(_KEY)}")
        last_key = tuple(
            table.slice(table.num_rows - 1, 1).select(list(_KEY)).to_pylist()[0].values()
        )
        for tid, part, lang, provider, content in zip(
            *(table[c].to_pylist() for c in columns), strict=True
        ):
            if tid != current:
                finish(current)
                current, pending = tid, []
            pending.append((part, lang, provider, content))
    finish(current)
    return bodies


_KEY = ("text_id", "part", "language", "provider")


def _order(key: tuple) -> tuple:
    """A key of ``text_parts`` in the table's sort order (a missing value last)."""
    return tuple((v is None, v or "") for v in key)
