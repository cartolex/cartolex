# SPDX-License-Identifier: MIT
"""Running a build for the app: in a process of its own when it can, else in a thread.

A build of a large project holds gigabytes while a stage runs. In a thread of the app,
that memory would stay with the app once the build ends, and a build the computer stops
for want of memory would stop the app with it. So the app runs a build in a child
process: the child opens the project under the app's lock (the lock's *holder*, see
:class:`~cartolex.project.lock.ProjectLock`), makes the same stages from what the app
sends it (the engine's options with this computer's budget, the extensions' stage
declarations and patches, the AI key), and sends its progress
back through a pipe; a cancel goes the other way, and the child stops when the app is
gone. Its memory goes back to the computer when it ends.

A registry the child cannot remake (one given in code, as tests do, or stage
declarations that cannot be sent to another process) runs the build in a thread of the
app, as before.
"""

from __future__ import annotations

import logging
import multiprocessing
import threading
import traceback
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .messages import attempt_message, message, run_notes

__all__ = [
    "FAILED_NEXT",
    "BuildChildFailed",
    "build_outcome",
    "child_recipe",
    "progress_json",
    "run_build",
    "someone_mapped",
]

logger = logging.getLogger(__name__)

#: What to do after a failed stage, by the code of its attempt.
FAILED_NEXT = {
    "language_model_missing": ("Open the settings", "settings"),
    "stage_refused": ("Open the settings", "settings"),
    "stage_failed": ("Copy a diagnostic", "report"),
    "stage_no_texts": ("Collect the texts", "open:/people?collect=harvest"),
    "stage_no_mapped": ("Open the people", "open:/people"),
    "stage_ai_not_set": ("Open the settings", "settings"),
    "stage_ai_no_key": ("Open the settings", "settings"),
    "stage_themes_rebase": ("Open the themes", "open:/themes"),
    "stage_no_pinned_map": ("Open the map's settings", "open:/map?tune=1"),
    "stage_layout_missing": ("Open the map's settings", "open:/map?tune=1"),
}
#: Seconds between two looks at the child (progress, cancel, its end).
POLL_S = 0.25


class BuildChildFailed(RuntimeError):
    """The build's process failed outside a stage; ``child_traceback`` is its traceback."""

    def __init__(self, text: str, child_traceback: str = "") -> None:
        super().__init__(text)
        self.child_traceback = child_traceback


def progress_json(event: Any) -> dict[str, Any]:
    """A build's progress event for the tracker, with an estimate of the time left."""
    eta = None
    if event.fraction >= 0.02 and event.elapsed_s > 0:
        eta = round(event.elapsed_s * (1 - event.fraction) / event.fraction, 1)
    return {
        "phase": event.phase,
        "phases": event.phases,
        "stage": event.stage,
        "name": event.name,
        "stage_fraction": event.stage_fraction,
        "fraction": event.fraction,
        "message": event.message,
        "elapsed_s": event.elapsed_s,
        "eta_s": eta,
        "heartbeat": event.heartbeat,
    }


def someone_mapped(project: Any) -> bool:
    """Whether the project maps anyone: a person whose role is ``mapped`` (without
    ``decisions/people.csv``, everyone in the people's table is)."""
    from cartolex.project.tables import read_decision_csv

    layout = project.layout
    if not layout.people_csv.exists():
        return layout.table("people").exists()
    try:
        return any(
            r.get("role") == "mapped" for r in read_decision_csv(layout.people_csv, "people")
        )
    except (OSError, ValueError):
        return True


def build_outcome(
    project: Any,
    targets: list[str] | None,
    *,
    registry: Any,
    consent: Sequence[str],
    progress: Callable[[dict[str, Any]], None],
    cancel: threading.Event,
    **options: Any,
) -> dict[str, Any]:
    """Build *targets* and say how it went, as the build job reports it (plain values)."""
    from cartolex.build import build

    accepted = set(consent)
    result = build(
        project,
        targets,
        registry=registry,
        consent=lambda request: request.stage in accepted,
        progress=lambda event: progress(progress_json(event)),
        cancel=cancel,
        **options,
    )
    out: dict[str, Any] = {
        "outcome": result.outcome,
        "summary": result.summary(),
        "ran": list(result.ran_ids),
        "refused": dict(result.refused),
        "not_run": list(result.not_run),
        "changed": result.changed,
        # what the stages that ran say beside their results (fewer theme levels, …)
        "notes": [n for r in result.ran for n in run_notes(r.stage, r.measures.counts)],
    }
    # The tokens the AI provider reported for the stages that ran (none from the cache).
    spent = [r.measures.counts for r in result.ran if "tokens_in" in r.measures.counts]
    if spent:
        out["ai_usage"] = {
            "tokens_in": sum(c["tokens_in"] for c in spent),
            "tokens_out": sum(c.get("tokens_out", 0) for c in spent),
        }
    if result.failed:
        said = attempt_message("failed", result.failed[1])
        if said["code"] == "stage_no_texts" and not someone_mapped(project):
            said = message("stage_no_mapped")
        label, action = FAILED_NEXT.get(said["code"], FAILED_NEXT["stage_failed"])
        out["failed"] = {
            "stage": result.failed[0],
            "error": result.failed[1],
            **said,
            "next": {"label": label, "action": action},
        }
        out["error"] = f"{result.failed[0]}: {result.failed[1]}"
        if result.failed_traceback:
            out["traceback"] = result.failed_traceback  # the job's error keeps it
    return out


# ── the child process ────────────────────────────────────────────────────────


def child_recipe(runtime: Any) -> dict[str, Any] | None:
    """What a child process needs to make the app's stages, or ``None`` when it cannot
    (a registry or an AI access given in code). Stage declarations that cannot be sent to
    another process are found when the child starts: the build then runs here."""
    from cartolex.build.engine import EngineOptions
    from cartolex.lexicon.providers import PROVIDERS

    settings = runtime.settings
    if settings.registry is not None or settings.ai_access is not None:
        return None  # made in code: the child could not make them again
    if not settings.build_in_child:
        return None
    # Of each extension, what makes the stages: its declarations and its patches.
    extensions = [
        (ext.id, tuple(ext.stages), dict(ext.stage_patches))
        for ext in runtime.extensions.extensions
    ]
    recipe = {
        "options": EngineOptions(
            prompt_dir=runtime.extensions.prompt_dir,
            stopword_overlay=runtime.extensions.stopword_overlay or None,
            rejects_folder=runtime.rejects_folder,
            budget=runtime.budget.budget(),
        ),
        "extensions": extensions,
        # each provider's key, as the app would give it (environment, else saved here)
        "ai_keys": {p: getattr(runtime.ai_access(p), "api_key", None) for p in PROVIDERS},
    }
    return recipe


def _registry(recipe: Mapping[str, Any]) -> Any:
    from types import SimpleNamespace

    from cartolex.build.engine import AIAccess, engine_registry

    from .extensions import patched_registry

    keys = recipe["ai_keys"]

    def ai_access(provider: str) -> AIAccess | None:
        key = keys.get(provider)
        return AIAccess(api_key=key) if key else None

    base = engine_registry(ai_access, recipe["options"])
    extensions = [
        SimpleNamespace(id=ext_id, stages=stages, stage_patches=patches)
        for ext_id, stages, patches in recipe["extensions"]
    ]
    return patched_registry(base, extensions)  # type: ignore[arg-type]


def _child(conn: Any, root: str, holder: dict[str, Any], recipe: dict, kwargs: dict) -> None:
    """In the child: build, sending the progress and the outcome to the app."""
    from cartolex.project import Project
    from cartolex.project.lock import LockInfo

    cancel = threading.Event()
    sending = threading.Lock()

    def send(message: tuple) -> None:
        with sending:
            conn.send(message)

    def listen() -> None:  # a cancel, or the app gone (the pipe closed): stop
        try:
            while conn.recv() != "cancel":
                pass
        except (EOFError, OSError):
            pass
        cancel.set()

    threading.Thread(target=listen, daemon=True).start()
    try:
        registry = _registry(recipe)
        with Project.open(Path(root), write=True, holder=LockInfo(**holder)) as project:
            out = build_outcome(
                project,
                registry=registry,
                cancel=cancel,
                progress=lambda data: send(("progress", data)),
                **kwargs,
            )
        send(("done", out))
    except BaseException as exc:  # noqa: BLE001 - said to the app, which fails the job
        send(("error", f"{type(exc).__name__}: {exc}", traceback.format_exc()))


def run_build(
    project: Any,
    recipe: dict[str, Any] | None,
    control: Any,
    *,
    registry: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build in a child process made from *recipe* (see the module docstring), else in
    this thread with *registry*; the progress goes to *control*, its cancel to the build.
    *kwargs* are :func:`build_outcome`'s (targets, consent and the build's options)."""
    if recipe is None or project.lock_info is None:
        return build_outcome(
            project, registry=registry, cancel=control.cancel, progress=control.progress, **kwargs
        )
    from dataclasses import asdict

    context = multiprocessing.get_context("spawn")
    here, there = context.Pipe()
    child = context.Process(
        target=_child,
        args=(there, str(project.layout.root), asdict(project.lock_info), recipe, kwargs),
        name=f"cartolex-build-{control.job_id}",
        daemon=False,  # its stages start worker processes; it stops when the pipe closes
    )
    try:
        child.start()
    except Exception as exc:  # what it needs cannot be sent (a stage made in code): here
        logger.warning("The build runs in the app's process: %s", exc)
        there.close()
        here.close()
        return build_outcome(
            project, registry=registry, cancel=control.cancel, progress=control.progress, **kwargs
        )
    there.close()
    asked_to_stop = False
    try:
        while True:
            if control.cancel.is_set() and not asked_to_stop:
                asked_to_stop = True
                here.send("cancel")
            if here.poll(POLL_S):
                try:
                    message = here.recv()
                except EOFError:
                    break
                if message[0] == "progress":
                    control.progress(message[1])
                elif message[0] == "done":
                    return message[1]
                else:
                    raise BuildChildFailed(message[1], message[2] if len(message) > 2 else "")
            elif not child.is_alive():
                break
    finally:
        here.close()
        child.join(timeout=30)
        if child.is_alive():
            child.kill()
            child.join()
    code = child.exitcode
    if code is not None and code < 0:
        raise RuntimeError(
            f"the build's process was stopped by the system (signal {-code}), "
            "most often for want of memory"
        )
    raise RuntimeError(f"the build's process ended without an outcome (exit code {code})")
