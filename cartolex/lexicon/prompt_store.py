# SPDX-License-Identifier: MIT
"""Prompt templates: loaded from an explicit prompt directory, checked when used.

The LLM prompt defaults are **not hard-coded**: they are plain-text files in
the package's ``cartolex/_data/prompts/`` directory — the editable surface of
the repository — read through :mod:`importlib.resources`, so a checkout, a
wheel or a zip all work. A run may point at another directory (the context's
``prompt_dir``). Templates are read and checked only when a stage uses them;
a missing or malformed template raises :class:`PromptTemplateError`, naming
the file and the placeholder. Where supported, a per-workspace override takes
precedence (see :func:`cartolex.lexicon.triage_typed.load_typed_template`).
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from importlib.abc import Traversable

    #: A folder of prompt templates: a directory on disk or packaged resources.
    PromptDir = Path | Traversable


class PromptTemplateError(ValueError):
    """A prompt template is missing or malformed; the message names the file and the placeholder."""


def packaged_prompt_dir() -> Traversable:
    """The prompt templates shipped in the package (``cartolex/_data/prompts``)."""
    return files("cartolex._data") / "prompts"


def _placeholders(text: str, source: str) -> set[str]:
    """The named placeholders of *text* (``{name}``), or an error naming *source*."""
    names: set[str] = set()
    try:
        for _literal, name, _spec, _conversion in string.Formatter().parse(text):
            if name is not None:
                names.add(name)
    except ValueError as exc:
        raise PromptTemplateError(f"Prompt template {source} is malformed: {exc}") from exc
    return names


@dataclass(frozen=True)
class PromptTemplate:
    """One prompt template and the file it was read from."""

    name: str
    source: str
    text: str

    def render(self, **values: str) -> str:
        """The template with its placeholders filled in.

        Raises :class:`PromptTemplateError` naming the file and the placeholder
        when the template uses a placeholder no value is given for, or when its
        braces are malformed.
        """
        unknown = sorted(n for n in _placeholders(self.text, self.source) if n not in values)
        if unknown:
            listed = ", ".join("{" + n + "}" for n in unknown)
            known = ", ".join("{" + k + "}" for k in sorted(values))
            raise PromptTemplateError(
                f"Prompt template {self.source} uses unknown placeholder(s) {listed} "
                f"(known: {known})"
            )
        try:
            return self.text.format(**values)
        except (IndexError, KeyError, ValueError) as exc:
            raise PromptTemplateError(f"Prompt template {self.source} is malformed: {exc}") from exc


def load_prompt(
    name: str,
    *,
    required_placeholders: tuple[str, ...] = (),
    prompt_dir: PromptDir | None = None,
) -> PromptTemplate:
    """Load and check the template ``<prompt_dir>/<name>.txt``.

    *prompt_dir* defaults to the packaged prompts. Raises
    :class:`PromptTemplateError` when the file is absent, when its braces are
    malformed, or when it lacks one of *required_placeholders* (written with
    their braces, e.g. ``"{domain_title}"``).
    """
    folder = prompt_dir if prompt_dir is not None else packaged_prompt_dir()
    resource = folder / f"{name}.txt"
    if not resource.is_file():
        raise PromptTemplateError(f"Prompt template not found: {resource}")
    text = resource.read_text(encoding="utf-8")
    source = str(resource)
    _placeholders(text, source)
    missing = [p for p in required_placeholders if p not in text]
    if missing:
        raise PromptTemplateError(
            f"Prompt template {source} lacks the placeholder(s) {', '.join(missing)}"
        )
    return PromptTemplate(name=name, source=source, text=text)
