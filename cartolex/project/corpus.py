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

Who goes where comes from ``decisions/people.csv``: ``mapped`` people fill the
fit slots, each projected set's people fill ``overlays/<set>/``; when the file
does not exist, every person is mapped. ``context`` people are left out until
the engine weighs them.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .files import atomic_write_bytes
from .layout import ProjectLayout
from .models import ProjectFile
from .tables import read_decision_csv, read_source_table

__all__ = ["INDEX_COLUMNS", "CorpusSummary", "assemble_corpus", "render_text"]

#: The columns of an engine index, in order.
INDEX_COLUMNS = ("last_name", "first_name", "unit", "txt_path", "doc_year", "doc_type")
#: The order parts are read in; ``full`` stands alone.
PART_ORDER = ("title", "abstract", "body")


@dataclass
class CorpusSummary:
    """What was written, per slot (``overlay:<set>`` for projected sets)."""

    slots: dict[str, dict[str, int]] = field(default_factory=dict)
    skipped_people: int = 0
    texts_without_parts: int = 0


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


def _csv_bytes(rows: Iterable[Sequence[object]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(INDEX_COLUMNS)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def assemble_corpus(
    layout: ProjectLayout,
    config: ProjectFile,
    out_dir: Path,
    *,
    parts: Sequence[str] = ("title", "abstract"),
    provider_priority: Sequence[str] = (),
    unit_level: str | None = None,
) -> CorpusSummary:
    """Write the engine's corpus for *config*'s fit slots and projected sets into *out_dir*.

    ``out_dir/<slot>/index.csv`` and ``out_dir/<slot>/texts/<text_id>.txt`` for each
    fit slot, in the project's slot order; ``out_dir/overlays/<set>/`` likewise
    for each projected set: its ``projected`` people from the project's own
    tables, or every person of its own ``root``'s tables (``<root>/tables/``,
    laid out like ``sources/tables/``; a relative root is relative to the
    project).
    *provider_priority* picks one provider per (text, part, language), earlier
    first, unknown providers last in name order. *unit_level* names the level
    whose organisation fills the ``unit`` column (default: the project's first
    level, else any affiliation).
    """
    out_dir = Path(out_dir)
    main = _load(layout.tables, config, unit_level, provider_priority)
    roles = _roles(layout, main.people)
    summary = CorpusSummary()
    slot_rank = {s.id: i for i, s in enumerate(config.slots)}
    fit_slots = [s.id for s in config.slots if s.fit]
    written: dict[Path, set[str]] = defaultdict(set)

    def emit(
        target: Path, members: list[str], slots: set[str] | None, src: _Loaded
    ) -> dict[str, int]:
        rows = []
        people, units, by_person = src.people, src.units, src.by_person
        text_meta, chosen_parts = src.text_meta, src.chosen_parts
        for pid in members:
            person = people[pid]
            texts_of = [
                t for t in by_person.get(pid, ()) if slots is None or text_meta[t]["slot"] in slots
            ]
            texts_of.sort(
                key=lambda t: (
                    slot_rank.get(text_meta[t]["slot"], 1 << 30),
                    text_meta[t]["slot"],
                    text_meta[t]["position"],
                )
            )
            for tid in texts_of:
                body = render_text(chosen_parts.get(tid, ()), chosen=parts)
                if not body:
                    summary.texts_without_parts += 1
                    continue
                rel = f"texts/{tid}.txt"
                if tid not in written[target]:
                    atomic_write_bytes(target / rel, body.encode("utf-8"))
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
                    )
                )
        atomic_write_bytes(target / "index.csv", _csv_bytes(rows))
        return {
            "rows": len(rows),
            "texts": len(written[target]),
            "people": len({(r[0], r[1], r[2]) for r in rows}),
        }

    mapped = sorted(pid for pid, (role, _) in roles.items() if role == "mapped")
    for slot_id in fit_slots:
        summary.slots[slot_id] = emit(out_dir / slot_id, mapped, {slot_id}, main)
    for overlay in config.overlays:
        target = out_dir / "overlays" / overlay.id
        if overlay.root is None:
            members = sorted(
                pid for pid, (role, s) in roles.items() if role == "projected" and s == overlay.id
            )
            summary.slots[f"overlay:{overlay.id}"] = emit(target, members, None, main)
            continue
        root = Path(overlay.root)
        if not root.is_absolute():
            root = layout.root / root
        own = _load(root / "tables", config, unit_level, provider_priority)
        summary.slots[f"overlay:{overlay.id}"] = emit(target, sorted(own.people), None, own)
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
    chosen_parts: dict[str, list[tuple[str, str, str]]]


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
    text_meta = {
        row["text_id"]: row
        for row in texts.select(["text_id", "slot", "position", "year", "doc_type"]).to_pylist()
    }
    people = {
        row["person_id"]: row
        for row in read_source_table(_table(tables, "people"), "people")
        .select(["person_id", "last_name", "first_name"])
        .to_pylist()
    }
    return _Loaded(
        text_meta=text_meta,
        people=people,
        units=_units(tables, config, unit_level),
        by_person=_texts_by_person(tables, text_meta),
        chosen_parts=_chosen_parts(tables, set(text_meta), provider_priority),
    )


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


def _chosen_parts(
    tables: Path, text_ids: set[str], provider_priority: Sequence[str]
) -> dict[str, list[tuple[str, str, str]]]:
    """text_id → one (part, language, content) per part and language, by provider priority."""
    table = read_source_table(_table(tables, "text_parts"), "text_parts").select(
        ["text_id", "part", "language", "provider", "content"]
    )
    rank = {p: i for i, p in enumerate(provider_priority)}
    best: dict[tuple[str, str, str], tuple[tuple[int, str], str]] = {}
    for tid, part, lang, provider, content in zip(
        *(table[c].to_pylist() for c in ("text_id", "part", "language", "provider", "content")),
        strict=True,
    ):
        if tid not in text_ids:
            continue
        key = (tid, part, lang)
        order = (rank.get(provider, len(rank)), provider)
        if key not in best or order < best[key][0]:
            best[key] = (order, content)
    chosen: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for (tid, part, lang), (_, content) in sorted(best.items()):
        chosen[tid].append((part, lang, content))
    return chosen
