# SPDX-License-Identifier: MIT
"""The curator's notes for the AI copilot: ``decisions/curation-notes.md``.

A Markdown file the curator writes (in Settings › Project, or in either
copilot dialog): teams, keywords that belong together or apart, standing
context. Below its own heading it holds the **standing rules** the curator
agreed with a copilot, one per line with the task it is for::

    Two teams work on the same coast: keep their waves and tides together.

    ## Standing rules for the AI copilot

    - triage: research discourse is always excluded, sure
    - themes: never merge the two policy themes

Every copilot bundle carries the notes and its task's rules (context for the
assistant, never instructions); importing a result adds the rules it brought.
The file is a decision file: written through
:func:`cartolex.project.files.write_decision` (the version read, the history).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

__all__ = ["RULES_HEADING", "TASKS", "CurationNotes", "parse", "render"]

#: The heading the standing rules sit under.
RULES_HEADING = "## Standing rules for the AI copilot"
#: The tasks a rule is for.
TASKS = ("triage", "themes")
_RULE = re.compile(r"^\s*[-*]\s*(triage|themes)\s*:\s*(.+?)\s*$", re.IGNORECASE)
MAX_RULE = 300


@dataclass
class CurationNotes:
    """The notes (free Markdown) and the standing rules, ``(task, text)`` in order."""

    notes: str = ""
    rules: list[tuple[str, str]] = field(default_factory=list)

    def rules_for(self, task: str) -> list[str]:
        """The rules of one task, in order."""
        return [text for t, text in self.rules if t == task]

    def with_rules(self, task: str, texts: Iterable[str]) -> tuple[CurationNotes, int]:
        """These notes with the rules of *texts* added for *task* (the new ones only), and
        how many were new."""
        rules = list(self.rules)
        seen = set(rules)
        added = 0
        for text in texts:
            text = " ".join(str(text).split())[:MAX_RULE]
            if text and (task, text) not in seen:
                seen.add((task, text))
                rules.append((task, text))
                added += 1
        return CurationNotes(self.notes, rules), added


def parse(text: str) -> CurationNotes:
    """The notes and rules of the file's text (a rule line that is not ``- <task>: <rule>``
    stays in the notes, under the heading, so nothing the curator wrote is lost)."""
    lines = str(text or "").splitlines()
    try:
        at = next(i for i, line in enumerate(lines) if line.strip() == RULES_HEADING)
    except StopIteration:
        return CurationNotes("\n".join(lines).strip(), [])
    rules: list[tuple[str, str]] = []
    rest: list[str] = []
    for line in lines[at + 1 :]:
        m = _RULE.match(line)
        if m:
            rules.append((m.group(1).casefold(), " ".join(m.group(2).split())[:MAX_RULE]))
        elif line.strip():
            rest.append(line)
    notes = "\n".join(lines[:at]).strip()
    if rest:
        notes = (notes + "\n\n" + "\n".join(rest)).strip()
    return CurationNotes(notes, rules)


def render(doc: CurationNotes) -> str:
    """The file's text: the notes, then the rules under their heading."""
    parts = [doc.notes.strip()] if doc.notes.strip() else []
    if doc.rules:
        parts.append(RULES_HEADING + "\n\n" + "\n".join(f"- {t}: {text}" for t, text in doc.rules))
    return ("\n\n".join(parts) + "\n") if parts else ""
