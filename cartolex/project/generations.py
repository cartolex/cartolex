# SPDX-License-Identifier: MIT
"""Generations of a stage's results: swapping a staging folder into place, and its journal.

A stage writes into ``derived/.staging/<stage>.<run id>/``. When it succeeds,
:func:`swap_in` makes that folder the stage's results in a few renames inside
``derived/``, after recording the swap in ``derived/.journal.json``:

1. the journal names the stage, the new run and the run it replaces;
2. the new ``run.json`` is written into the staging folder, then the files that
   only a running stage uses (:data:`STAGING_MARKER`, :data:`CHUNKS`) are removed;
3. ``derived/.previous/<stage>/`` is moved aside, into the staging area;
4. ``derived/<stage>/`` becomes ``derived/.previous/<stage>/``;
5. the staging folder becomes ``derived/<stage>/``;
6. the stage's last failed attempt, now superseded, is forgotten;
7. the journal is removed, then the generation moved aside.

A process killed anywhere in between leaves the journal behind.
:func:`recover`, which a writer calls when it opens the project, reads it: when
the new generation is complete (a ``run.json`` naming the journal's run), the
swap is completed; otherwise it is undone and the previous results are put
back. Either way every stage folder holds one whole generation.

Nothing here reads a file date: the generations are told apart by the run id
recorded in their ``run.json``.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .files import _fsync_dir, atomic_write_bytes, json_bytes
from .layout import ProjectLayout

__all__ = [
    "CHUNKS",
    "JOURNAL_FORMAT",
    "STAGING_MARKER",
    "clear_staging_files",
    "generation_run_id",
    "recover",
    "remove_tree",
    "swap_in",
]

#: The format id of ``derived/.journal.json``.
JOURNAL_FORMAT = "cartolex-journal/1"
#: The file a running stage keeps in its staging folder: who runs it, and for which inputs.
STAGING_MARKER = ".attempt.json"
#: The folder of a stage's chunk checkpoints, inside its staging folder.
CHUNKS = ".chunks"
#: Suffix of a replaced generation moved aside during a swap (inside ``derived/.staging/``).
DISCARD_SUFFIX = ".discard"

Probe = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def generation_run_id(folder: Path) -> str | None:
    """The run id recorded in a generation's ``run.json``, or ``None`` when there is none."""
    try:
        raw = json.loads((Path(folder) / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    run_id = raw.get("run_id") if isinstance(raw, dict) else None
    return run_id if isinstance(run_id, str) else None


def remove_tree(path: Path) -> None:
    """Remove a folder and everything in it; nothing happens when it does not exist."""
    path = Path(path)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def clear_staging_files(folder: Path) -> None:
    """Remove what only a running stage uses (its marker and its chunk checkpoints)."""
    remove_tree(Path(folder) / CHUNKS)
    with contextlib.suppress(FileNotFoundError):
        (Path(folder) / STAGING_MARKER).unlink()


def _rename(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)
    _fsync_dir(dst.parent)
    if src.parent != dst.parent:
        _fsync_dir(src.parent)


def _discard(layout: ProjectLayout, stage_id: str, run_id: str) -> Path:
    return layout.staging(stage_id, run_id).with_name(
        layout.staging(stage_id, run_id).name + DISCARD_SUFFIX
    )


def _read_journal(layout: ProjectLayout) -> dict[str, Any] | None:
    try:
        raw = json.loads(layout.journal.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ValueError(f"{layout.journal}: unreadable swap journal ({exc})") from exc
    if not isinstance(raw, dict) or raw.get("format") != JOURNAL_FORMAT:
        raise ValueError(f"{layout.journal}: not a {JOURNAL_FORMAT} file")
    return raw


def swap_in(
    layout: ProjectLayout,
    stage_id: str,
    run_id: str,
    record: bytes,
    *,
    probe: Probe | None = None,
) -> None:
    """Make the staging folder of *run_id* the results of *stage_id*.

    *record* is the new ``run.json``. The replaced generation is kept in
    ``derived/.previous/<stage>/``. *probe*, for tests, is called with the name
    of each step once it is done.
    """
    probe = probe or _noop
    if layout.journal.exists():
        recover(layout)
    staging = layout.staging(stage_id, run_id)
    if not staging.is_dir():
        raise FileNotFoundError(f"no staging folder for {stage_id} run {run_id}: {staging}")
    current = layout.stage(stage_id)
    discard = _discard(layout, stage_id, run_id)
    remove_tree(discard)
    journal = {
        "format": JOURNAL_FORMAT,
        "stage": stage_id,
        "run_id": run_id,
        "replaces": generation_run_id(current),
    }
    atomic_write_bytes(layout.journal, json_bytes(journal))
    probe("swap:journal")
    atomic_write_bytes(staging / "run.json", record)
    probe("swap:recorded")
    _complete(layout, journal, probe)


def _complete(layout: ProjectLayout, journal: dict[str, Any], probe: Probe) -> None:
    """Carry a journaled swap through to the end; each step checks what is already done."""
    stage_id, run_id = journal["stage"], journal["run_id"]
    staging = layout.staging(stage_id, run_id)
    current = layout.stage(stage_id)
    previous = layout.previous(stage_id)
    discard = _discard(layout, stage_id, run_id)
    if generation_run_id(current) != run_id:
        clear_staging_files(staging)
        probe("swap:cleaned")
        if current.exists():
            # Steps 3 and 4: the older previous generation goes aside, the current one
            # becomes the previous one.
            if previous.exists():
                remove_tree(discard)  # when both exist, the one aside is the older
                _rename(previous, discard)
            probe("swap:aside")
            _rename(current, previous)
            probe("swap:previous")
        elif journal.get("replaces") is None and previous.exists() and not discard.exists():
            # Nothing was current: an older previous generation stays no longer.
            _rename(previous, discard)
            probe("swap:aside")
        _rename(staging, current)
        probe("swap:placed")
    else:
        clear_staging_files(current)
    with contextlib.suppress(FileNotFoundError):
        layout.attempt(stage_id).unlink()
    probe("swap:attempt-cleared")
    layout.journal.unlink()
    _fsync_dir(layout.journal.parent)
    probe("swap:journal-removed")
    remove_tree(discard)
    probe("swap:done")


def _undo(layout: ProjectLayout, journal: dict[str, Any]) -> None:
    """Put the replaced generation back; the unfinished staging folder is left as it is."""
    stage_id, run_id = journal["stage"], journal["run_id"]
    current = layout.stage(stage_id)
    previous = layout.previous(stage_id)
    discard = _discard(layout, stage_id, run_id)
    replaces = journal.get("replaces")
    if not current.exists() and replaces and generation_run_id(previous) == replaces:
        _rename(previous, current)
    if discard.exists() and not previous.exists():
        _rename(discard, previous)
    layout.journal.unlink()
    _fsync_dir(layout.journal.parent)
    remove_tree(discard)


def recover(layout: ProjectLayout) -> list[str]:
    """Complete or undo a swap a killed process left half done; tidy the staging area.

    Called by a writer when it opens a project. Returns what was done, in words
    (empty when there was nothing to do). Staging folders that a killed run can
    resume from are kept.
    """
    done: list[str] = []
    journal = _read_journal(layout)
    if journal is not None:
        stage_id, run_id = journal["stage"], journal["run_id"]
        new_ready = run_id in (
            generation_run_id(layout.stage(stage_id)),
            generation_run_id(layout.staging(stage_id, run_id)),
        )
        if new_ready:
            _complete(layout, journal, _noop)
            done.append(f"completed the interrupted swap of {stage_id} (run {run_id})")
        else:
            _undo(layout, journal)
            done.append(f"undid the interrupted swap of {stage_id} (run {run_id})")
    root = layout.staging_root
    if root.is_dir():
        for entry in sorted(root.iterdir()):
            if entry.name.endswith(DISCARD_SUFFIX):
                remove_tree(entry)
                done.append(f"removed a replaced generation left aside ({entry.name})")
            elif entry.is_dir() and not (entry / STAGING_MARKER).exists():
                remove_tree(entry)
                done.append(f"removed an unfinished staging folder ({entry.name})")
    return done
