# SPDX-License-Identifier: MIT
"""Stand-ins for the PDF reader, run inside the PDF worker process (named
``_pdf_fakes:<function>``): they are imported there, so a test cannot patch them in its own
process."""

from __future__ import annotations

import time
from pathlib import Path

_CALLS = {"n": 0}


def fails_first(path: Path) -> str:
    """The first file cannot be read (a font the reader does not know); the others are."""
    from cartolex.lexicon.pdf_text import extract_text

    _CALLS["n"] += 1
    if _CALLS["n"] == 1:
        raise RuntimeError("a font the extractor cannot read")
    return extract_text(path)


def hangs_on_slow(path: Path) -> str:
    """A file whose name says « slow » never ends; the others are read."""
    if "slow" in Path(path).name:
        time.sleep(3600)
    from cartolex.lexicon.pdf_text import extract_text

    return extract_text(path)
