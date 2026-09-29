# SPDX-License-Identifier: MIT
"""The rejection lists: candidates never worth judging again (``cartolex-rejects/1``).

Two lists feed the ``rejected`` band of the extraction:

- **cartolex's list**, shipped in ``cartolex/_data/rejects/<lang>.json``:
  ``{"format": "cartolex-rejects/1", "language": "en", "terms": [...]}``;
- **the machine's cache**, in the app's own folder on this computer
  (``<data dir>/rejects/<lang>.jsonl``): one JSON object per line, the first
  one ``{"format": "cartolex-rejects/1", "language": "en"}``, then one entry
  per term and project: ``term``, ``language``, ``route`` (the AI route that
  answered: ``ai-api``, ``ai-handoff``, ``ai-copilot``), ``date`` (UTC day)
  and ``project`` (a fingerprint of the project, never its name).

Only an AI's ``never`` answers (never informative in any field, and sure of
it: :mod:`cartolex.lexicon.categories`) enter the cache; a person's decision on
a term removes it. Terms are compared case-insensitively.

At a build, a project's **snapshot** (:func:`snapshot`) holds, per language,
each term the extraction sets in the ``rejected`` band and where it comes from:
``list`` (cartolex's list) or ``earlier`` (the cache, from another project).
:func:`export_list` makes a new shipped list from a machine's cache.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Collection, Iterable, Mapping
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any

__all__ = [
    "FORMAT",
    "ORIGINS",
    "ROUTES",
    "MachineRejects",
    "export_list",
    "looks_like_name",
    "read_snapshot",
    "shipped_terms",
    "snapshot",
]

FORMAT = "cartolex-rejects/1"
#: The AI routes whose answers may enter the cache.
ROUTES = ("ai-api", "ai-handoff", "ai-copilot")
#: Where a term of a snapshot comes from: cartolex's list, or the cache (an earlier project).
ORIGINS = ("list", "earlier")
_LANG = re.compile(r"^[a-z]{2}$")


def _key(term: str) -> str:
    return " ".join(str(term).split()).casefold()


def shipped_terms(lang: str) -> frozenset[str]:
    """The terms of cartolex's list for *lang* (lower case), empty when there is none."""
    if not _LANG.match(lang):
        return frozenset()
    try:
        text = (
            resources.files("cartolex._data")
            .joinpath("rejects", f"{lang}.json")
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError):
        return frozenset()
    doc = json.loads(text)
    if doc.get("format") != FORMAT or doc.get("language") != lang:
        raise ValueError(f"rejects/{lang}.json is not a {FORMAT} list of {lang!r}")
    return frozenset(_key(t) for t in doc.get("terms") or [] if str(t).strip())


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class MachineRejects:
    """The rejection cache of this computer, one JSON-lines file per language in *folder*."""

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)

    def path(self, lang: str) -> Path:
        if not _LANG.match(lang):
            raise ValueError(f"not a language code: {lang!r}")
        return self.folder / f"{lang}.jsonl"

    def languages(self) -> list[str]:
        """The languages the cache has a file for."""
        if not self.folder.is_dir():
            return []
        return sorted(p.stem for p in self.folder.glob("*.jsonl") if _LANG.match(p.stem))

    def entries(self, lang: str) -> list[dict[str, str]]:
        """Every entry of *lang* (a damaged line is skipped)."""
        path = self.path(lang)
        if not path.is_file():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and "format" not in row and str(row.get("term", "")).strip():
                out.append(
                    {
                        k: str(row.get(k) or "")
                        for k in ("term", "language", "route", "date", "project")
                    }
                )
        return out

    def _write(self, lang: str, rows: list[dict[str, str]]) -> None:
        path = self.path(lang)
        if not rows:
            path.unlink(missing_ok=True)
            return
        head = json.dumps({"format": FORMAT, "language": lang})
        body = [json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows]
        _atomic_write(path, "\n".join([head, *body]) + "\n")

    def terms(self, lang: str, *, besides: str | None = None) -> set[str]:
        """The terms of *lang* (lower case); with *besides*, only those another project gave."""
        return {
            _key(r["term"])
            for r in self.entries(lang)
            if besides is None or r["project"] != besides
        }

    def add(self, rows: Iterable[Mapping[str, str]], *, route: str, project: str) -> int:
        """Add ``never`` answers (``term``, ``language``) of one project; returns how many are new."""
        if route not in ROUTES:
            raise ValueError(f"route is one of {ROUTES}")
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        by_lang: dict[str, list[str]] = {}
        for r in rows:
            lang = str(r.get("language") or "")
            if _LANG.match(lang) and str(r.get("term") or "").strip():
                by_lang.setdefault(lang, []).append(" ".join(str(r["term"]).split()))
        added = 0
        for lang, terms in by_lang.items():
            current = self.entries(lang)
            seen = {(_key(r["term"]), r["project"]) for r in current}
            for term in terms:
                if (_key(term), project) in seen:
                    continue
                seen.add((_key(term), project))
                current.append(
                    {
                        "term": term,
                        "language": lang,
                        "route": route,
                        "date": day,
                        "project": project,
                    }
                )
                added += 1
            if added:
                self._write(lang, current)
        return added

    def remove(self, lang: str, terms: Collection[str]) -> int:
        """Remove *terms* of *lang* (every project's entry); returns how many entries went."""
        drop = {_key(t) for t in terms}
        if not drop or not _LANG.match(lang):
            return 0
        current = self.entries(lang)
        kept = [r for r in current if _key(r["term"]) not in drop]
        if len(kept) != len(current):
            self._write(lang, kept)
        return len(current) - len(kept)

    def clear(self, lang: str | None = None) -> int:
        """Empty the cache (of one language); returns how many terms went."""
        n = 0
        for code in [lang] if lang else self.languages():
            n += len(self.terms(code))
            self.path(code).unlink(missing_ok=True)
        return n

    def counts(self) -> dict[str, int]:
        """How many distinct terms each language holds."""
        return {lang: len(self.terms(lang)) for lang in self.languages()}


def snapshot(
    languages: Iterable[str],
    machine: MachineRejects | None,
    *,
    project: str,
    exempt: Mapping[str, Collection[str]] | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    """The terms a project's extraction rejects, per language, with their origin.

    cartolex's list first (``list``), then the cache's terms that another project
    gave (``earlier``: a project's own answers never reject its own candidates).
    *exempt* holds, per language (``""``: every language), the terms a person
    decided on in the project: they are never rejected. Switched off
    (*enabled* false), the snapshot is empty.
    """
    out: dict[str, dict[str, str]] = {}
    exempt = exempt or {}
    for lang in languages:
        if not enabled:
            out[lang] = {}
            continue
        spared = {_key(t) for t in exempt.get(lang, ())} | {_key(t) for t in exempt.get("", ())}
        found = dict.fromkeys(sorted(shipped_terms(lang) - spared), "list")
        if machine is not None:
            for term in sorted(machine.terms(lang, besides=project) - spared):
                found.setdefault(term, "earlier")
        out[lang] = dict(sorted(found.items()))
    return {"format": FORMAT, "languages": out}


def read_snapshot(path: Path | None) -> dict[str, dict[str, str]]:
    """A snapshot's terms per language (``{}`` when the file is absent)."""
    if path is None or not Path(path).is_file():
        return {}
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("format") != FORMAT:
        raise ValueError(f"{path} is not a {FORMAT} snapshot")
    return {
        str(lang): {str(t): str(o) for t, o in (terms or {}).items()}
        for lang, terms in (doc.get("languages") or {}).items()
    }


def looks_like_name(term: str) -> bool:
    """Whether *term* looks like a proper name: a capital letter in it, or a digit."""
    return any(c.isupper() or c.isdigit() for c in str(term))


def export_list(
    machine: MachineRejects,
    *,
    min_projects: int = 2,
    names: Collection[str] = (),
) -> dict[str, list[str]]:
    """A shipped list per language from the cache: the terms seen in *min_projects* projects
    or more, without those that look like a name (:func:`looks_like_name`) or contain a
    word of *names* (people's and organisations' names of the user's projects)."""
    words = {w for n in names for w in re.findall(r"\w{3,}", str(n).casefold())}
    out: dict[str, list[str]] = {}
    for lang in machine.languages():
        projects: dict[str, set[str]] = {}
        shown: dict[str, str] = {}
        for r in machine.entries(lang):
            key = _key(r["term"])
            projects.setdefault(key, set()).add(r["project"])
            shown.setdefault(key, " ".join(r["term"].split()))
        terms = [
            key
            for key, seen in projects.items()
            if len(seen) >= min_projects
            and not looks_like_name(shown[key])
            and not (set(re.findall(r"\w+", key)) & words)
        ]
        out[lang] = sorted(terms)
    return out
