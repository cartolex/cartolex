# SPDX-License-Identifier: MIT
"""The AI copilot kit: the half of cartolex a code-capable assistant runs on a bundle.

cartolex exports a *copilot bundle* (:mod:`cartolex.copilot.bundle`): one zip
with the task, a guide, the data it needs (anonymised: keywords and their
vectors, people as opaque numbers, never a text, a name or an identifier) and
this kit as a wheel. The assistant unpacks the kit with the bundle's
``setup/bootstrap.py`` (no network, no installation), opens the bundle::

    import sys; sys.path.insert(0, "setup/site")
    from cartolex.copilot import open_bundle
    session = open_bundle(".")
    print(session.summary())

works with cartolex's own grouping, layouts and measures, records each change
with its reason, and writes ``result/result.json``, which cartolex imports as
a proposal the curator reviews.

The kit imports only numpy, pandas, SciPy, scikit-learn and matplotlib
(and the cartolex engine modules built on them), the scientific libraries a
code sandbox usually has; UMAP only where it can be imported.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .bundle import FORMAT, RESULT_FORMAT, TASKS

__all__ = ["FORMAT", "RESULT_FORMAT", "TASKS", "open_bundle"]


def open_bundle(folder: Path | str = ".", *, truth: Any = None) -> Any:
    """The session of the bundle unpacked in *folder*: a
    :class:`~cartolex.copilot.themes.ThemesSession` or a
    :class:`~cartolex.copilot.triage.TriageSession`, by its task.

    *truth* (optional, a demo world's): keyword → theme for themes, the true
    keywords for triage; the measures then include the lab's scores.
    """
    from .bundle import read_manifest

    task = read_manifest(Path(folder))["task"]
    if task == "themes":
        from .themes import ThemesSession

        return ThemesSession(folder, truth=truth)
    from .triage import TriageSession

    return TriageSession(folder, truth=truth)
