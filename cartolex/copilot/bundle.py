# SPDX-License-Identifier: MIT
"""The copilot bundle (``cartolex-copilot/1``) and its result (``cartolex-copilot-result/1``).

A bundle is one zip an assistant that can run code works from on its own. Its
top holds ``README_FIRST.md`` (to the assistant: the task, the rules, the two
checkpoints with the curator, the result, how to start), ``GUIDE.md`` (the
method), ``PRIVACY.md`` (what the bundle holds and never holds) and
``bundle.json`` (this manifest); ``setup/`` the stdlib-only ``bootstrap.py``
and the cartolex wheel it unpacks; ``data/`` what the task needs, anonymised;
``baseline/`` the current state's measures; ``result/`` where the result goes.

The manifest names the task (``themes`` or ``triage``), the bundle's id (the
result carries it back), the curator's language, the counts and the sha256 of
every file. The data are JSON and ``.npz`` arrays, never a pickle nor a
columnar file a sandbox may not read.

The result is one JSON document, ``result/result.json``:

- ``themes``: ``changes``, each ``{"kind", "ops", "reason"}`` whose ``ops`` are
  operations in the JSON form of ``POST /api/themes/ops``, applied in order;
  ``tree``, the tree they give; ``measures`` before and after;
- ``triage``: ``decisions``, each ``{"term", "language", "decision", "target",
  "code", "category", "confidence", "reason"}`` (``keep``, ``exclude``, or
  ``merge`` into ``target``; the category, :mod:`cartolex.lexicon.categories`,
  follows the code when absent; the confidence is ``sure`` or ``unsure``,
  ``unsure`` when absent); ``measures``;

and ``notes`` for the curator. :func:`check_result` lists what is wrong with a
result; the kit writes only results it passes, the application reads only those.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..lexicon.categories import CATEGORIES
from ..lexicon.categories import CODES as _CODES
from .ops import OP_KINDS

__all__ = [
    "CHANGE_KINDS",
    "CODES",
    "DECISIONS",
    "FORMAT",
    "MAX_CHANGES",
    "MAX_DECISIONS",
    "RESULT_FILE",
    "RESULT_FORMAT",
    "TASKS",
    "check_result",
    "file_hash",
    "read_manifest",
]

FORMAT = "cartolex-copilot/1"
RESULT_FORMAT = "cartolex-copilot-result/1"
#: Where the assistant writes the result, inside the bundle.
RESULT_FILE = "result/result.json"
TASKS = ("themes", "triage")
#: The kinds of change of a themes result (each carries one or more operations).
CHANGE_KINDS = (
    "rename",
    "move",
    "merge",
    "split",
    "create",
    "delete",
    "move_node",
    "set_aside",
    "put_back",
    "attribution",
    "restructure",
)
#: The decisions of a triage result.
DECISIONS = ("keep", "exclude", "merge")
#: How sure the assistant is of a decision (absent: ``unsure``).
CONFIDENCES = ("sure", "unsure")
#: The triage codes (those of the keyword handoff), and the categories they give.
CODES = {code: meaning for code, (_, meaning) in _CODES.items()}
MAX_CHANGES = 2_000
MAX_OPS = 5_000
MAX_DECISIONS = 50_000


def file_hash(data: bytes) -> str:
    """The sha256 of *data*, as the manifest writes it."""
    return hashlib.sha256(data).hexdigest()


def read_manifest(root: Path) -> dict[str, Any]:
    """The manifest of the bundle unpacked at *root* (refused when it is not one)."""
    path = Path(root) / "bundle.json"
    if not path.is_file():
        raise ValueError(f"{root} holds no bundle.json: unpack the bundle and open its folder")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("format") != FORMAT or doc.get("task") not in TASKS:
        raise ValueError(f"not a copilot bundle (format {doc.get('format')!r})")
    return doc


def _text(value: Any, limit: int) -> bool:
    return isinstance(value, str) and len(value) <= limit


def check_result(doc: Any, *, task: str | None = None) -> list[str]:
    """What is wrong with a result document (an empty list: nothing)."""
    if not isinstance(doc, Mapping):
        return ["the result is not a JSON object"]
    problems: list[str] = []
    if doc.get("format") != RESULT_FORMAT:
        problems.append(f"format is {doc.get('format')!r}, not {RESULT_FORMAT!r}")
    kind = doc.get("task")
    if kind not in TASKS:
        problems.append(f"task is {kind!r}, not one of {', '.join(TASKS)}")
    elif task is not None and kind != task:
        problems.append(f"this is a {kind} result, not a {task} one")
    if not _text(doc.get("bundle"), 64) or not doc.get("bundle"):
        problems.append("bundle (the id of the bundle it answers) is missing")
    if not _text(doc.get("notes", ""), 20_000):
        problems.append("notes is text of at most 20 000 characters")
    if kind == "themes":
        changes = doc.get("changes")
        if not isinstance(changes, list) or len(changes) > MAX_CHANGES:
            problems.append(f"changes is a list of at most {MAX_CHANGES}")
            return problems
        n_ops = 0
        for i, change in enumerate(changes, 1):
            if not isinstance(change, Mapping):
                problems.append(f"change {i} is not an object")
                continue
            if change.get("kind") not in CHANGE_KINDS:
                problems.append(f"change {i}: kind {change.get('kind')!r} is unknown")
            if not _text(change.get("reason"), 2_000) or not str(change.get("reason")).strip():
                problems.append(f"change {i}: every change gives its reason")
            ops = change.get("ops")
            if not isinstance(ops, list) or not ops:
                problems.append(f"change {i}: ops is a non-empty list")
                continue
            n_ops += len(ops)
            for op in ops:
                if not isinstance(op, Mapping) or op.get("op") not in OP_KINDS:
                    problems.append(f"change {i}: an operation is none of {', '.join(OP_KINDS)}")
                    break
        if n_ops > MAX_OPS:
            problems.append(f"the changes hold {n_ops} operations, more than {MAX_OPS}")
        if doc.get("tree") is not None and not isinstance(doc.get("tree"), Mapping):
            problems.append("tree is the tree the changes give, as a JSON object")
    elif kind == "triage":
        decisions = doc.get("decisions")
        if not isinstance(decisions, list) or len(decisions) > MAX_DECISIONS:
            problems.append(f"decisions is a list of at most {MAX_DECISIONS}")
            return problems
        for i, d in enumerate(decisions, 1):
            if not isinstance(d, Mapping):
                problems.append(f"decision {i} is not an object")
                continue
            if not _text(d.get("term"), 300) or not d.get("term"):
                problems.append(f"decision {i}: term is missing")
            if not _text(d.get("language", ""), 2):
                problems.append(f"decision {i}: language is a two-letter code")
            if d.get("decision") not in DECISIONS:
                problems.append(f"decision {i}: decision is one of {', '.join(DECISIONS)}")
            elif d.get("decision") == "merge" and not str(d.get("target") or "").strip():
                problems.append(f"decision {i}: a merge names its target")
            if d.get("code") not in (None, "", *CODES):
                problems.append(f"decision {i}: code is one of {', '.join(CODES)}")
            if d.get("category") not in (None, "", *CATEGORIES):
                problems.append(f"decision {i}: category is one of {', '.join(CATEGORIES)}")
            if d.get("confidence") not in (None, "", *CONFIDENCES):
                problems.append(f"decision {i}: confidence is one of {', '.join(CONFIDENCES)}")
            if not _text(d.get("reason", ""), 2_000):
                problems.append(f"decision {i}: reason is text")
            if len(problems) > 50:
                break
    return problems[:50]
