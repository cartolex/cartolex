# SPDX-License-Identifier: MIT
"""The answers to a theme handoff, as earlier versions imported them: read them back.

A *handoff* of the theme tree gave a chat assistant the tree as text and
``bundle.json`` (``cartolex-themes-handoff/1``), the tree as it was sent; the
answer came back one operation per line::

    1 | RENAME | s3 | Coastal hazards | its keywords are floods, surges and erosion
    2 | MOVE | tide gauge | s5 | an instrument of sea-level observation
    3 | MERGE | s7 | s2 | both hold harbour management keywords
    4 | SPLIT | s4 | Salt marshes | salt marsh; marsh accretion | a distinct group
    5 | SET ASIDE | further work | not a keyword of the field
    6 | ATTRIBUTION | ocean | 0 | too broad to count toward one theme

The AI copilot (:mod:`cartolex.project.copilot`) has replaced it; the answers
already imported stay readable. :func:`parse_answer` reads one tolerantly
(Markdown tables, bullets, tabs, lower-case verbs, node names for ids) and
turns each line into an operation of :mod:`cartolex.project.themes`, in the
JSON form of ``POST /api/themes/ops``; a line it cannot read is reported with
the reason, never dropped silently.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from .models import ThemesFile

__all__ = [
    "HANDOFF_FORMAT",
    "PROBLEMS",
    "VERBS",
    "ParsedThemeAnswer",
    "ThemeProposal",
    "Unreadable",
    "parse_answer",
    "tree_of",
]

#: Format of ``bundle.json``, the machine-readable half of a theme handoff part.
HANDOFF_FORMAT = "cartolex-themes-handoff/1"
#: The operations an answer may propose, as the answer spells them.
VERBS = ("RENAME", "MOVE", "MERGE", "SPLIT", "SET ASIDE", "ATTRIBUTION")
#: Why a line of an answer could not be read.
PROBLEMS = {
    "unknown_action": "the action is none of " + ", ".join(VERBS),
    "missing_fields": "a field is missing",
    "unknown_node": "no node of the tree has this id or name",
    "unknown_keyword": "the tree holds no such keyword",
    "bad_levels": "the levels are a number from 0 to the depth minus one, or « none »",
    "empty_name": "the new name is empty",
    "same_node": "a node cannot be merged into itself",
}

_SYNONYMS = {
    "RENAME": "RENAME",
    "NAME": "RENAME",
    "RELABEL": "RENAME",
    "MOVE": "MOVE",
    "RELOCATE": "MOVE",
    "MERGE": "MERGE",
    "MERGE INTO": "MERGE",
    "COMBINE": "MERGE",
    "SPLIT": "SPLIT",
    "SET ASIDE": "SET ASIDE",
    "SETASIDE": "SET ASIDE",
    "ASIDE": "SET ASIDE",
    "DISCARD": "SET ASIDE",
    "EXCLUDE": "SET ASIDE",
    "REMOVE": "SET ASIDE",
    "ATTRIBUTION": "ATTRIBUTION",
    "ATTRIBUTE": "ATTRIBUTION",
    "COUNT": "ATTRIBUTION",
}


@dataclass
class ThemeProposal:
    """One proposed operation: the line's number, its action, the operation, the reason."""

    number: int
    verb: str
    op: dict[str, Any]
    reason: str
    text: str
    line: int
    refused: str = ""  # why the operation cannot be applied to the tree that was sent


@dataclass
class Unreadable:
    """A line of an answer that could not be read, and why (a key of :data:`PROBLEMS`)."""

    line: int
    text: str
    problem: str
    detail: str = ""


@dataclass
class ParsedThemeAnswer:
    """An answer read back: the proposed operations, in order, and what could not be read."""

    items: list[ThemeProposal] = field(default_factory=list)
    unreadable: list[Unreadable] = field(default_factory=list)
    lines: int = 0  # lines that name an action
    ignored: int = 0  # other non-empty lines (sentences, headers, fences)

    def as_dict(self) -> dict[str, Any]:
        return {
            "items": [asdict(i) for i in self.items],
            "unreadable": [asdict(u) for u in self.unreadable],
            "lines": self.lines,
            "ignored": self.ignored,
        }


def tree_of(record: Mapping[str, Any]) -> ThemesFile:
    """The tree a part's ``bundle.json`` was made from (refused when it is not one)."""
    if record.get("format") != HANDOFF_FORMAT:
        raise ValueError(
            f"not a theme handoff part (format {record.get('format')!r}, "
            f"expected {HANDOFF_FORMAT!r})"
        )
    return ThemesFile.model_validate(record["tree"])


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.strip().strip("\"'«»`*“”").casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[\s’']+", " ", text).strip()


_BULLET = re.compile(r"^\s*(?:[-*•+]\s+|>\s*)")
_NUMBER = re.compile(r"^\s*(\d+)\s*[.)]?\s*$")
_LEAD_NUMBER = re.compile(r"^\s*(\d+)\s*[.):]\s+(.*)$")
_REASON_LEAD = re.compile(r"^(?:reason|why|because)\s*[:=-]\s*", re.IGNORECASE)


def _cells(raw: str) -> tuple[int | None, list[str]] | None:
    """The number and the cells of a line that names an action; ``None`` for any other line."""
    line = _BULLET.sub("", raw.strip().strip("`")).strip()
    if not line:
        return None
    if "|" in line or "\t" in line:
        cells = [c.strip().strip("*").strip() for c in re.split(r"\||\t", line.strip().strip("|"))]
        while cells and cells[-1] == "":
            cells.pop()
    else:
        m = _LEAD_NUMBER.match(line)
        rest = m.group(2) if m else line
        # « 3. RENAME s3 → name » and similar loose forms are not read: the format has bars.
        verb = _verb(rest.split(" ")[0]) or _verb(" ".join(rest.split(" ")[:2]))
        if verb is None:
            return None
        return (int(m.group(1)) if m else None), [rest]
    if not cells or not any(cells):
        return None
    number = None
    m = _NUMBER.match(cells[0])
    if m:
        number = int(m.group(1))
        cells = cells[1:]
    elif _LEAD_NUMBER.match(cells[0]):
        lead = _LEAD_NUMBER.match(cells[0])
        assert lead is not None
        number, cells = int(lead.group(1)), [lead.group(2), *cells[1:]]
    return number, cells


def _verb(text: str) -> str | None:
    key = re.sub(r"[\s_-]+", " ", text.strip().strip("*").upper()).strip()
    return _SYNONYMS.get(key)


class _Tree:
    """Lookups on the tree an answer refers to."""

    def __init__(self, tree: ThemesFile, language: str) -> None:
        self.tree = tree
        self.ids = {n.id for n in tree.nodes}
        self.id_of: dict[str, str] = {}
        by_name: dict[str, list[str]] = {}
        for n in tree.nodes:
            self.id_of[_norm(n.id)] = n.id
            for name in n.names.values():
                by_name.setdefault(_norm(name), []).append(n.id)
        self.by_name = {k: v[0] for k, v in by_name.items() if len(set(v)) == 1}
        self.keyword_of: dict[str, str] = {}
        for kw in [*tree.keywords, *tree.set_aside]:
            self.keyword_of.setdefault(_norm(kw), kw)

    def node(self, text: str) -> str | None:
        raw = text.strip()
        m = re.search(r"\[([A-Za-z0-9_-]+)\]", raw)
        if m and m.group(1) in self.ids:
            return m.group(1)
        raw = re.sub(r"^(?:node|theme|topic)\s+", "", raw, flags=re.IGNORECASE)
        key = _norm(raw)
        return self.id_of.get(key) or self.by_name.get(key)

    def keyword(self, text: str) -> str | None:
        raw = re.sub(r"\s*\(\d+\)\s*$", "", text.strip())  # « tide gauge (9) » as listed
        return self.keyword_of.get(_norm(raw))


def _levels(text: str, depth: int) -> tuple[bool, int | None]:
    key = re.sub(r"^levels?\s*[:=]?\s*", "", _norm(text))
    if key in ("none", "nowhere", "0", "zero"):
        return True, 0
    if key in ("default", "all", "node", "its node", "every level"):
        return True, None
    m = re.fullmatch(r"(?:levels?\s*)?(?:1\s*(?:-|to|…|\.\.)\s*)?(\d)", key)
    if m and 0 <= int(m.group(1)) < depth:
        return True, int(m.group(1))
    return False, None


def parse_answer(text: str, tree: ThemesFile, *, language: str = "en") -> ParsedThemeAnswer:
    """Read an answer to a theme handoff part into proposed operations on *tree*.

    Lines are ``<number> | <ACTION> | <fields…> | <reason>`` (a Markdown table
    row, tab-separated cells, a bullet or a code fence around them also work;
    the action is any case). Node ids may be written ``s3``, ``[s3]`` or as the
    node's name when no other node has it; keywords as ``tree.txt`` writes them,
    whatever their case and accents. A line that names an action but cannot be
    read is listed in ``unreadable`` with its problem (:data:`PROBLEMS`); other
    lines are ignored. Each operation is also tried alone on *tree*: one that
    would be refused keeps the reason in ``refused``.
    """
    parsed = ParsedThemeAnswer()
    look = _Tree(tree, language)
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("```"):
            continue
        cells = _cells(raw)
        if cells is None:
            parsed.ignored += 1
            continue
        number, fields = cells
        verb = _verb(fields[0]) if fields else None
        if verb is None and len(fields) > 1:
            verb = _verb(" ".join(fields[:2])) if " " not in fields[0] else None
        shown = stripped[:300]
        if verb is None:
            if number is not None:  # a numbered line is meant as a change
                parsed.lines += 1
                parsed.unreadable.append(
                    Unreadable(lineno, shown, "unknown_action", fields[0][:60])
                )
            else:
                parsed.ignored += 1
            continue
        parsed.lines += 1
        args = fields[1:]
        if len(args) == 0 and len(fields) == 1:
            parsed.unreadable.append(Unreadable(lineno, shown, "missing_fields"))
            continue
        result = _read(verb, args, look, tree, language)
        if isinstance(result, Unreadable):
            result.line, result.text = lineno, shown
            parsed.unreadable.append(result)
            continue
        op, reason = result
        item = ThemeProposal(
            number=number if number is not None else len(parsed.items) + 1,
            verb=verb,
            op=op,
            reason=reason,
            text=shown,
            line=lineno,
        )
        item.refused = _refusal(tree, op)
        parsed.items.append(item)
    return parsed


def _read(
    verb: str, args: list[str], look: _Tree, tree: ThemesFile, language: str
) -> tuple[dict[str, Any], str] | Unreadable:
    """The operation and the reason of one line's fields, or why they cannot be read."""

    def need(n: int) -> Unreadable | None:
        return Unreadable(0, "", "missing_fields") if len(args) < n else None

    def reason_from(i: int) -> str:
        return _REASON_LEAD.sub("", " | ".join(args[i:]).strip())[:300]

    def node(text: str) -> str | Unreadable:
        found = look.node(text)
        return found if found else Unreadable(0, "", "unknown_node", text[:80])

    def keyword(text: str) -> str | Unreadable:
        found = look.keyword(text)
        return found if found else Unreadable(0, "", "unknown_keyword", text[:120])

    if verb == "RENAME":
        if (missing := need(2)) is not None:
            return missing
        nid = node(args[0])
        if isinstance(nid, Unreadable):
            return nid
        name = args[1].strip().strip("\"'«»“”").strip()
        if not name:
            return Unreadable(0, "", "empty_name")
        return {"op": "rename_node", "node_id": nid, "names": {language: name}}, reason_from(2)
    if verb == "MOVE":
        if (missing := need(2)) is not None:
            return missing
        kw = keyword(args[0])
        if isinstance(kw, Unreadable):
            return kw
        nid = node(args[1])
        if isinstance(nid, Unreadable):
            return nid
        kind = "put_back" if kw in tree.set_aside else "move_keywords"
        return {"op": kind, "keywords": [kw], "node_id": nid}, reason_from(2)
    if verb == "MERGE":
        if (missing := need(2)) is not None:
            return missing
        source = node(args[0])
        if isinstance(source, Unreadable):
            return source
        target = node(re.sub(r"^into\s+", "", args[1].strip(), flags=re.IGNORECASE))
        if isinstance(target, Unreadable):
            return target
        if source == target:
            return Unreadable(0, "", "same_node", source)
        return {"op": "merge_nodes", "source": source, "target": target}, reason_from(2)
    if verb == "SPLIT":
        if (missing := need(3)) is not None:
            return missing
        nid = node(args[0])
        if isinstance(nid, Unreadable):
            return nid
        name = args[1].strip().strip("\"'«»“”").strip()
        if not name:
            return Unreadable(0, "", "empty_name")
        members: list[str] = []
        for piece in re.split(r"[;,]", args[2]):
            if not piece.strip():
                continue
            kid = look.node(piece) if re.search(r"\[[A-Za-z0-9_-]+\]", piece) else None
            if kid is not None:
                members.append(kid)
                continue
            kw = keyword(piece)
            if isinstance(kw, Unreadable):
                return kw
            members.append(kw)
        if not members:
            return Unreadable(0, "", "missing_fields")
        part = {"members": list(dict.fromkeys(members)), "names": {language: name}}
        return {"op": "split_node", "node_id": nid, "parts": [part]}, reason_from(3)
    if verb == "SET ASIDE":
        if (missing := need(1)) is not None:
            return missing
        kw = keyword(args[0])
        if isinstance(kw, Unreadable):
            return kw
        reason = reason_from(1)
        return {"op": "set_aside", "keywords": [kw], "reason": f"AI: {reason}"[:500]}, reason
    # ATTRIBUTION
    if (missing := need(2)) is not None:
        return missing
    kw = keyword(args[0])
    if isinstance(kw, Unreadable):
        return kw
    ok, levels = _levels(args[1], tree.depth)
    if not ok:
        return Unreadable(0, "", "bad_levels", args[1][:40])
    return {"op": "set_attribution", "keywords": [kw], "levels": levels}, reason_from(2)


def _refusal(tree: ThemesFile, op: Mapping[str, Any]) -> str:
    """Why *op* alone would be refused on *tree* ('' when it applies)."""
    from . import themes as t

    try:
        kind = op["op"]
        if kind == "rename_node":
            t.rename_node(tree, op["node_id"], op["names"])
        elif kind == "move_keywords":
            t.move_keywords(tree, op["keywords"], op["node_id"])
        elif kind == "put_back":
            t.put_back(tree, op["keywords"], op["node_id"])
        elif kind == "merge_nodes":
            t.merge_nodes(tree, op["source"], op["target"])
        elif kind == "split_node":
            t.split_node(tree, op["node_id"], [(p["members"], p["names"]) for p in op["parts"]])
        elif kind == "set_aside":
            t.set_aside(tree, op["keywords"], op["reason"])
        elif kind == "set_attribution":
            t.set_attribution(tree, op["keywords"], op["levels"])
    except t.ThemeEditError as exc:
        return str(exc)
    return ""


def answer_line(number: int, verb: str, *fields: str) -> str:
    """One line of an answer in the expected format."""
    return " | ".join([str(number), verb, *fields])
