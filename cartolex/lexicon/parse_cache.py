# SPDX-License-Identifier: MIT
"""The parse cache: the noun-phrase analysis of each text, kept between runs.

Parsing is the slow part of the keyword extraction, and a corpus mostly grows
by adding documents, so each analysed text (a :class:`TextAnalysis`) is kept,
keyed by the sha256 of the text, the identity of the model that parsed it
(``name@version``) and :data:`~cartolex.lexicon.noun_phrases.PATTERN_VERSION`.
A later run parses only the texts it has not seen with the same model and
patterns.

Layout (the caller chooses *folder*)::

    <folder>/<model name>-<model version>/<pattern version>/part-<digest>.jsonl

Each part is JSON lines, UTF-8: a header line
``{"format": "cartolex-parse/1", "model": "<name@version>", "patterns": "<version>"}``
then one line per text, ``{"sha256": …, "runs": …, "lemmas": …}``. A part is
written once, to a temporary file renamed into place, and never modified; its
name is the digest of its content, so two runs never write the same file
differently. A part whose header does not match, or that cannot be read, is
skipped with a warning (its texts are parsed again). Another model version or
pattern version lives in another folder: it is never read, and the folder can
be deleted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Collection, Mapping
from pathlib import Path

from .noun_phrases import PATTERN_VERSION, TextAnalysis

__all__ = ["FORMAT", "PART_SIZE", "ParseCache", "text_key"]

logger = logging.getLogger(__name__)

#: Format tag of every part's header line.
FORMAT = "cartolex-parse/1"
#: Texts per part file.
PART_SIZE = 1000


def text_key(text: str) -> str:
    """The cache key of a text: the sha256 of its UTF-8 bytes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ParseCache:
    """The cached analyses of one model and one pattern version, under *folder*."""

    def __init__(self, folder: Path | str, model: str, *, patterns: str = PATTERN_VERSION) -> None:
        name, sep, version = model.partition("@")
        if not sep or not name or not version:
            raise ValueError(f"model identity must be 'name@version', not {model!r}")
        self.model = model
        self.patterns = patterns
        self.dir = Path(folder) / f"{name}-{version}" / patterns

    def _header(self) -> dict[str, str]:
        return {"format": FORMAT, "model": self.model, "patterns": self.patterns}

    def read(
        self, wanted: Collection[str] | None = None, *, share: dict | None = None
    ) -> dict[str, TextAnalysis]:
        """The cached analyses, by text key (only those in *wanted* when given).

        With *share*, each analysis is read in its shared form
        (:meth:`TextAnalysis.shared`), through that table.
        """
        out: dict[str, TextAnalysis] = {}
        if not self.dir.is_dir():
            return out
        want = set(wanted) if wanted is not None else None
        header = self._header()
        for part in sorted(self.dir.glob("part-*.jsonl")):
            try:
                with part.open(encoding="utf-8") as handle:
                    first = json.loads(handle.readline() or "null")
                    if first != header:
                        logger.warning("Parse cache: skipping %s (another format or model).", part)
                        continue
                    found: dict[str, TextAnalysis] = {}
                    for line in handle:
                        if not line.strip():
                            continue
                        entry = json.loads(line)
                        key = entry["sha256"]
                        if (want is None or key in want) and key not in out:
                            analysis = TextAnalysis.from_json(entry)
                            found[key] = analysis if share is None else analysis.shared(share)
            except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
                logger.warning("Parse cache: skipping unreadable %s (%s).", part, exc)
                continue
            for key, analysis in found.items():
                out.setdefault(key, analysis)
        return out

    def write(self, analyses: Mapping[str, TextAnalysis]) -> list[Path]:
        """Store *analyses* (by text key) in new parts; return the parts written."""
        if not analyses:
            return []
        self.dir.mkdir(parents=True, exist_ok=True)
        keys = sorted(analyses)
        header = json.dumps(self._header(), sort_keys=True, ensure_ascii=False)
        written = []
        for start in range(0, len(keys), PART_SIZE):
            lines = [header]
            for key in keys[start : start + PART_SIZE]:
                entry = {"sha256": key, **analyses[key].to_json()}
                lines.append(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
            data = ("\n".join(lines) + "\n").encode("utf-8")
            path = self.dir / f"part-{hashlib.sha256(data).hexdigest()[:24]}.jsonl"
            if not path.exists():
                fd, tmp = tempfile.mkstemp(prefix=".part-", suffix=".tmp", dir=self.dir)
                try:
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(data)
                    os.replace(tmp, path)
                except BaseException:
                    Path(tmp).unlink(missing_ok=True)
                    raise
            written.append(path)
        return written
