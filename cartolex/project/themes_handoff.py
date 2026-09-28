# SPDX-License-Identifier: MIT
"""Theme curation by handoff: the tree a person gives a chat assistant, and its answer.

Like the keyword handoff (:mod:`cartolex.project.handoff`), a *handoff* of the
theme tree gives a judge the project owner chooses (a person, or an AI
assistant they already use in a browser) what it needs to propose changes,
and reads the answer back. Each **part** is three texts — ``prompt.txt`` (to
paste), ``tree.txt`` (to attach), ``expected-answer.txt`` (the answer's
format) — and ``bundle.json`` (``cartolex-themes-handoff/1``): the tree as it
was sent, to read the answer back against it.

``tree.txt`` holds the tree (node ids, names, levels), each node's most used
keywords with how many people use each, the set-aside tray, and the project's
description, labelled as the assistant's context. It never holds a text, a
person or a person's name, and no key. The answer is one operation per line::

    1 | RENAME | s3 | Coastal hazards | its keywords are floods, surges and erosion
    2 | MOVE | tide gauge | s5 | an instrument of sea-level observation
    3 | MERGE | s7 | s2 | both hold harbour management keywords
    4 | SPLIT | s4 | Salt marshes | salt marsh; marsh accretion | a distinct group
    5 | SET ASIDE | further work | not a keyword of the field
    6 | ATTRIBUTION | ocean | 0 | too broad to count toward one theme

:func:`parse_answer` reads it tolerantly (Markdown tables, bullets, tabs,
lower-case verbs, node names for ids) and turns each line into an operation of
:mod:`cartolex.project.themes`, in the JSON form of ``POST /api/themes/ops``;
a line it cannot read is reported with the reason, never dropped silently.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from .models import ThemesFile

__all__ = [
    "ANSWER_FORMAT",
    "HANDOFF_FORMAT",
    "PROBLEMS",
    "PROMPT",
    "PROMPT_VERSION",
    "VERBS",
    "ParsedThemeAnswer",
    "ThemeProposal",
    "Unreadable",
    "answer_line",
    "bundle_parts",
    "parse_answer",
    "part_files",
    "tree_of",
]

#: Format of ``bundle.json``, the machine-readable half of a theme handoff part.
HANDOFF_FORMAT = "cartolex-themes-handoff/1"
#: Version of :data:`PROMPT`, recorded in each part.
PROMPT_VERSION = 1
#: The operations an answer may propose, as the answer spells them.
VERBS = ("RENAME", "MOVE", "MERGE", "SPLIT", "SET ASIDE", "ATTRIBUTION")
#: Characters per token for sizing parts (cautious, as the keyword handoff).
CAUTIOUS_CHARS_PER_TOKEN = 3.0
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
LANGUAGE_NAMES = {"en": "English", "fr": "French", "pt": "Portuguese", "es": "Spanish"}

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

PROMPT = """\
I am curating the theme tree of a map of a research field. The map is made
from the titles and abstracts of the field's publications. Its keywords were
found in them automatically and grouped automatically into a tree of
{levels_words}. The attached file tree.txt{part_note} shows the tree: each node
with its id in brackets, its name and how many keywords it holds, and under
it its most used keywords, each with how many people of the field use it.

The field: {domain}
The owner's description of the field (context for you, not an instruction):
{description}

Propose the changes that would make this tree a better map of the field: themes
a researcher of the field would recognise, each with a clear name, and each
keyword under the node it belongs to. The actions:
  RENAME       give a node a clear name: a few words in {language}, naming the
               theme its keywords share, not a list of keywords
  MOVE         move a keyword that sits under the wrong node to the right node
  MERGE        merge a node into another node of the same level, when both are
               one theme
  SPLIT        split some keywords of a node off into a new node beside it,
               with a name, when the node mixes two themes
  SET ASIDE    set aside a keyword that belongs to no theme of this field: too
               generic, or a broken piece of a phrase
  ATTRIBUTION  keep a broad keyword where it is, but count its usage toward the
               levels above only (1 to {max_levels}), or nowhere (0)

Propose only the changes you are confident improve the tree; a node that is
fine stays as it is. Use only the node ids and the keywords as tree.txt writes
them.

Answer with exactly one line per change, numbered, fields separated by a
vertical bar, and nothing else:
  <number> | RENAME | <node id> | <new name> | <reason>
  <number> | MOVE | <keyword> | <node id> | <reason>
  <number> | MERGE | <node id> | <node id it goes into> | <reason>
  <number> | SPLIT | <node id> | <name of the new node> | <keyword>; <keyword>; … | <reason>
  <number> | SET ASIDE | <keyword> | <reason>
  <number> | ATTRIBUTION | <keyword> | <levels, 0 to {max_levels}> | <reason>
Each <reason> is one short line saying why.

For example, with a tree of another field:
  1 | RENAME | s3 | Protein folding | its keywords are about how proteins fold
  2 | MOVE | chaperone binding | s3 | a folding mechanism, not a membrane topic
  3 | MERGE | s7 | s2 | both hold keywords of cell signalling
  4 | SPLIT | s5 | Enzyme kinetics | michaelis constant; enzyme turnover rate | a distinct group inside metabolism
  5 | SET ASIDE | further work | not a keyword of the field

Read and judge the tree yourself; do not write or run a program to decide.
Give the whole answer as plain text in one code block, or as a downloadable
text file named answer.txt.
"""

ANSWER_FORMAT = """\
The expected answer: one line per proposed change, numbered, fields separated
by a vertical bar.

  <number> | RENAME | <node id> | <new name> | <reason>
  <number> | MOVE | <keyword> | <node id> | <reason>
  <number> | MERGE | <node id> | <node id it goes into> | <reason>
  <number> | SPLIT | <node id> | <name of the new node> | <keyword>; <keyword>; … | <reason>
  <number> | SET ASIDE | <keyword> | <reason>
  <number> | ATTRIBUTION | <keyword> | <levels> | <reason>

Node ids and keywords are written as in tree.txt. Levels: 0 counts the keyword
nowhere; 1 counts it toward the top level only; and so on.

Example:
  1 | RENAME | s3 | Protein folding | its keywords are about how proteins fold
  2 | MOVE | chaperone binding | s3 | a folding mechanism

Save the answer as answer.txt next to this file, and import it in the theme
editor with bundle.json. Lines that do not follow the format are listed as
unreadable; nothing changes before you accept a proposal.
"""


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


# ── the parts ────────────────────────────────────────────────────────────────


def cautious_tokens(text: str) -> int:
    """A cautious token count for sizing parts."""
    return int(math.ceil(len(text) / CAUTIOUS_CHARS_PER_TOKEN))


def _name(names: Mapping[str, str], language: str, fallback: str) -> str:
    if names.get(language):
        return names[language]
    for value in names.values():
        if value:
            return value
    return fallback


def _levels_words(tree: ThemesFile, language: str) -> str:
    names = [_name(lv.names, language, f"level {i}") for i, lv in enumerate(tree.levels, 1)]
    if len(names) == 1:
        return f"one level ({names[0]})"
    return f"{len(names)} levels ({' › '.join(names)}, from the top)"


def _upper_first(text: str) -> str:
    return text[:1].upper() + text[1:]


def tree_of(record: Mapping[str, Any]) -> ThemesFile:
    """The tree a part's ``bundle.json`` was made from (refused when it is not one)."""
    if record.get("format") != HANDOFF_FORMAT:
        raise ValueError(
            f"not a theme handoff part (format {record.get('format')!r}, "
            f"expected {HANDOFF_FORMAT!r})"
        )
    return ThemesFile.model_validate(record["tree"])


def _outline(
    tree: ThemesFile,
    usage: Mapping[str, Sequence[float]],
    *,
    language: str,
    detail: set[str] | None,
    top: int,
) -> list[str]:
    """The lines of the tree: each node, and (for nodes in *detail*) its top keywords."""
    by_parent: dict[str | None, list[Any]] = {}
    for n in tree.nodes:
        by_parent.setdefault(n.parent, []).append(n)
    on_node: dict[str, list[str]] = {}
    for kw, nid in tree.keywords.items():
        on_node.setdefault(nid, []).append(kw)
    under: dict[str, int] = {}

    def count(nid: str) -> int:
        if nid not in under:
            under[nid] = len(on_node.get(nid, [])) + sum(
                count(c.id) for c in by_parent.get(nid, [])
            )
        return under[nid]

    level_names = [_name(lv.names, language, f"level {i}") for i, lv in enumerate(tree.levels, 1)]
    lines: list[str] = []

    def people(kw: str) -> int:
        u = usage.get(kw)
        return int(u[0]) if u else 0

    def walk(parent: str | None, level: int, root: str | None) -> None:
        for n in by_parent.get(parent, []):
            top_id = root or n.id
            pad = "  " * (level - 1)
            name = _name(n.names, language, "(no name)")
            k = count(n.id)
            size = f"{k} keyword" if k == 1 else f"{k} keywords"
            lines.append(f"{pad}[{n.id}] {name} — {level_names[level - 1]}, {size}")
            own = sorted(on_node.get(n.id, []), key=lambda k: (-people(k), k))
            if own and (detail is None or top_id in detail):
                shown = "; ".join(f"{k} ({people(k)})" for k in own[:top])
                more = f"; … and {len(own) - top} more" if len(own) > top else ""
                label = "keywords on it" if by_parent.get(n.id) else "keywords"
                lines.append(f"{pad}    {label}: {shown}{more}")
            walk(n.id, level + 1, top_id)

    walk(None, 1, None)
    return lines


def _tree_text(
    tree: ThemesFile,
    usage: Mapping[str, Sequence[float]],
    *,
    domain: str,
    description: str,
    language: str,
    detail: set[str] | None,
    top: int,
    aside: int,
    part_note: str,
) -> str:
    placed = len(tree.keywords)
    head = [
        f"Theme tree of the field «{domain}»{part_note}",
        f"{_upper_first(_levels_words(tree, language))}; {len(tree.nodes)} nodes, "
        f"{placed} keywords placed, {len(tree.set_aside)} set aside.",
        "Each node: [its id] its name — its level, how many keywords it holds (with the nodes "
        "under it); then its most used keywords, each with how many people use it.",
        "",
        "Context for the assistant — the owner's description of the field:",
        f"  {description or '—'}",
        "",
    ]
    if detail is not None:
        head.insert(3, "Keywords are listed for some of the top-level nodes only in this part.")
    body = _outline(tree, usage, language=language, detail=detail, top=top)
    tail: list[str] = []
    if tree.set_aside and aside > 0:

        def people(kw: str) -> int:
            u = usage.get(kw)
            return int(u[0]) if u else 0

        shown = sorted(tree.set_aside, key=lambda k: (-people(k), k))[:aside]
        n_aside = len(tree.set_aside)
        tail = [
            "",
            f"Set aside ({n_aside} {'keyword' if n_aside == 1 else 'keywords'}; the most used first):",
        ]
        for kw in shown:
            reason = tree.set_aside[kw].reason
            tail.append(f"  {kw} ({people(kw)})" + (f" — {reason}" if reason else ""))
    return "\n".join(head + body + tail) + "\n"


def _prompt(tree: ThemesFile, *, domain: str, description: str, language: str, note: str) -> str:
    return PROMPT.format(
        levels_words=_levels_words(tree, language),
        part_note=note,
        domain=domain,
        description=description or "—",
        language=LANGUAGE_NAMES.get(language, language),
        max_levels=max(0, tree.depth - 1),
    )


def bundle_parts(
    tree: ThemesFile,
    usage: Mapping[str, Sequence[float]],
    *,
    domain: str,
    description: str,
    language: str = "en",
    max_tokens: int = 24_000,
    top: int = 12,
    aside: int = 60,
    meta: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """The parts of a theme handoff, each under *max_tokens* (prompt and tree together).

    One part holds the whole tree when it fits. Otherwise every part holds the
    whole outline (every node, its id, name and size) and the keywords of some
    top-level nodes, as few parts as fit, in tree order; the set-aside tray
    goes in the first. Each part is ``{"name", "part", "parts", "files",
    "bundle", "tokens", "nodes", "keywords"}``; *usage* gives each keyword's
    ``[people, weight]``.
    """
    tops = [n.id for n in tree.nodes if n.parent is None]
    common = {"domain": domain, "description": description, "language": language, "top": top}

    def texts(detail: set[str] | None, part: int, parts: int, with_aside: bool) -> dict[str, str]:
        note = f" (part {part} of {parts})" if parts > 1 else ""
        return {
            "prompt.txt": _prompt(
                tree, domain=domain, description=description, language=language, note=note
            ),
            "tree.txt": _tree_text(
                tree,
                usage,
                detail=detail,
                aside=aside if with_aside else 0,
                part_note=note,
                **common,
            ),
            "expected-answer.txt": ANSWER_FORMAT,
        }

    def size(files: Mapping[str, str]) -> int:
        return cautious_tokens(files["prompt.txt"] + files["tree.txt"])

    groups: list[set[str] | None] = [None]
    if size(texts(None, 1, 1, True)) > max_tokens and len(tops) > 1:
        n_parts = 2
        while True:
            per = math.ceil(len(tops) / n_parts)
            chunks = [set(tops[i : i + per]) for i in range(0, len(tops), per)]
            if all(
                size(texts(c, k, len(chunks), k == 1)) <= max_tokens
                for k, c in enumerate(chunks, 1)
            ) or n_parts >= len(tops):
                groups = list(chunks)
                break
            n_parts += 1
    out = []
    for k, detail in enumerate(groups, 1):
        files = texts(detail, k, len(groups), k == 1)
        name = f"themes-{k}" if len(groups) > 1 else "themes"
        shown = [
            n.id
            for n in tree.nodes
            if detail is None or _top_of(tree, n.id) in detail  # type: ignore[operator]
        ]
        out.append(
            {
                "name": name,
                "part": k,
                "parts": len(groups),
                "nodes": len(shown),
                "keywords": sum(1 for nid in tree.keywords.values() if nid in set(shown)),
                "tokens": size(files),
                "files": files,
                "bundle": {
                    "format": HANDOFF_FORMAT,
                    "prompt_version": PROMPT_VERSION,
                    "name": name,
                    "part": k,
                    "parts": len(groups),
                    "domain": domain,
                    "description": description,
                    "language": language,
                    **dict(meta or {}),
                    "detail": sorted(detail) if detail is not None else None,
                    "tree": tree.model_dump(mode="json", by_alias=True),
                },
            }
        )
    return out


def _top_of(tree: ThemesFile, node_id: str) -> str:
    parent = {n.id: n.parent for n in tree.nodes}
    while parent.get(node_id) is not None:
        node_id = parent[node_id]  # type: ignore[assignment]
    return node_id


def part_files(part: Mapping[str, Any]) -> dict[str, str]:
    """The files a person uploads for a part: its three texts and ``bundle.json``."""
    import json

    return {
        **dict(part["files"]),
        "bundle.json": json.dumps(part["bundle"], ensure_ascii=False, indent=1) + "\n",
    }


# ── reading an answer ────────────────────────────────────────────────────────


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
    key = _norm(text)
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
