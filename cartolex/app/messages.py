# SPDX-License-Identifier: MIT
"""What the interface shows besides errors: empty results, skipped stages, failed attempts.

Like an error (:mod:`cartolex.app.errors`), each is ``{"code", "params",
"message"}``: the interface shows the text of the code from its catalogues,
filled with the params, and ``message`` is the English fallback. An empty
result also names its next action (V2-013). :data:`MESSAGES` declares every
code once; ``docs/dev/api.md`` lists them.

The build describes a skipped stage and a failed attempt in English
(``StageStatus.skip_reason``, the attempt's ``error``); :func:`skip_message`
and :func:`attempt_message` give each a code and its params.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "MESSAGES",
    "attempt_message",
    "empty",
    "job_error",
    "job_pause",
    "message",
    "reason_message",
    "skip_message",
    "traceback_text",
]


@dataclass(frozen=True)
class MessageKind:
    """One message code: its English template, and for an empty result its next action."""

    template: str
    next_label: str = ""
    next_action: str = ""


#: Every message code (empty results, skipped stages, failed attempts, reasons).
MESSAGES: dict[str, MessageKind] = {
    # empty results
    "empty_no_people": MessageKind(
        "no people yet: import a list of names", "Import people", "import-people"
    ),
    "empty_no_match": MessageKind("nothing matches these filters", "Clear the filters", "none"),
    "empty_no_keywords": MessageKind(
        "no keywords yet: build the keywords first", "Build the keywords", "build"
    ),
    "empty_no_themes": MessageKind(
        "no themes yet: build the themes to get a first draft", "Build the themes", "build"
    ),
    "empty_no_borderline": MessageKind(
        "no keyword sits near the border between two nodes", "Close", "none"
    ),
    "empty_no_texts": MessageKind(
        "the grouping did not read the texts: build the themes with the comb", "Close", "none"
    ),
    "empty_no_levels": MessageKind(
        "every keyword sits on the level its texts support, or you kept it there", "Close", "none"
    ),
    "empty_tree_never_saved": MessageKind("the tree was never saved", "Close", "none"),
    "empty_no_space": MessageKind(
        "the keywords are not placed in a space yet: build the themes", "Build the themes", "build"
    ),
    "empty_no_grouping": MessageKind(
        "the keywords are not grouped yet: build the themes", "Build the themes", "build"
    ),
    "empty_no_corpus": MessageKind(
        "the texts are not gathered yet: build the corpus", "Build the corpus", "build"
    ),
    "empty_no_map": MessageKind("no map yet: build the map", "Build the map", "build"),
    "empty_no_map_versions": MessageKind(
        "no map yet: the first build draws one and pins it", "Build the map", "build"
    ),
    "empty_up_to_date": MessageKind("everything is up to date", "Close", "none"),
    "empty_nothing_built": MessageKind("nothing was built yet", "Build", "build"),
    "empty_no_jobs": MessageKind("no job has run yet", "Build", "build"),
    "empty_no_recent": MessageKind("no project opened yet", "Create a project", "open-project"),
    "empty_file_never_written": MessageKind("{file} was never written", "Close", "none"),
    "empty_no_site": MessageKind("no site built yet", "Close", "none"),
    "empty_no_site_unavailable": MessageKind(
        "no site built yet; building a site comes in a later version", "Close", "none"
    ),
    "empty_no_collection": MessageKind("no collection has run", "Plan a collection", "collect"),
    "empty_no_identity_to_check": MessageKind("nobody waits for a check", "See everyone", "none"),
    "empty_no_identity_in_state": MessageKind("nobody is in this state", "See everyone", "none"),
    "empty_triage": MessageKind("no term to send in this band", "Close", "none"),
    "empty_no_proposals": MessageKind("no AI answers imported yet", "Export terms", "none"),
    "empty_no_rejects": MessageKind("no term rejected by an AI on this computer yet", "", "none"),
    "empty_no_decisions": MessageKind(
        "no decision yet: keep, exclude or merge keywords in the list", "Close", "none"
    ),
    # the state of collection
    "collection_unavailable": MessageKind(
        "collecting texts from bibliographic services is not available in this version; import "
        "texts into a folder or corpus slot instead"
    ),
    # skipped stages
    "stage_switched_off": MessageKind(
        "switched off (set {stage}.enabled in decisions/params.json to run it)"
    ),
    "stage_no_overlay": MessageKind("the project has no overlay"),
    "stage_not_applicable": MessageKind("{reason}"),
    # failed or cancelled attempts
    "stage_cancelled": MessageKind("the stage was cancelled; its previous results are kept"),
    "stage_refused": MessageKind("the stage could not run: {detail}"),
    "language_model_missing": MessageKind("a language model is missing: {detail}"),
    "stage_failed": MessageKind("the stage failed ({error_type}): {detail}"),
    # why a job failed (``error`` of a failed job, :func:`job_error`)
    "job_failed": MessageKind("the job failed ({error_type}): {detail}"),
    "collect_budget_spent": MessageKind(
        "{host} refused more requests (status {status}): its daily budget is spent; set a free "
        "API key, or wait for the next day's budget"
    ),
    "collect_service_unavailable": MessageKind(
        "{host} gave no usable answer after every attempt ({what}); try again later"
    ),
    "collect_incomplete": MessageKind("{host} cut a page of a list short; collect again"),
    "collect_malformed": MessageKind("{host} gave an answer that could not be read ({what})"),
    "collect_refused": MessageKind(
        "{host} refused a request (status {status}); copy a diagnostic and report it"
    ),
    "collect_cache_miss": MessageKind("an answer is not in the cache: collect without cache-only"),
    # why a job paused (``result.pause`` of a paused job; it can be resumed)
    "collect_size_confirm": MessageKind(
        "{total} works are signed there: reading them takes about {requests} requests and "
        "{seconds} s; confirm to go on, or narrow the years or the units, or read the OpenAlex "
        "snapshot instead"
    ),
    "collect_budget_paused": MessageKind(
        "the daily budget is spent; {works} of {total} works are kept: resume once it comes "
        "back ({resets_at})"
    ),
    "collect_stopped": MessageKind("stopped after {works} of {total} works; resume to go on"),
    "collect_paused": MessageKind(
        "a page still failed after its retries; {works} of {total} works are kept: resume to go on"
    ),
    "harvest_stopped": MessageKind(
        "the harvest was stopped; {n} of {total} people are kept: resume to go on"
    ),
    "harvest_paused": MessageKind(
        "the harvest stopped after failures in a row; {n} of {total} people are kept: resume to go on"
    ),
    # the overview's health panel (``GET /api/overview``)
    "health_map_stale": MessageKind(
        "the map was drawn from inputs that changed since; building the map restores it",
        "Build the map",
        "build",
    ),
    "health_model_missing": MessageKind(
        "the language model {model} for {language} is not installed; the keyword extraction "
        "needs it",
        "Open the settings",
        "settings",
    ),
    "health_too_large": MessageKind(
        "{stage} needs about {need_mb} MB of memory and this machine has about {budget_mb} MB",
        "Open the settings",
        "settings",
    ),
    "health_languages_split": MessageKind(
        "the texts are in {languages}: without the AI clean-up, keywords of each language may "
        "form themes of their own",
        "Open the settings",
        "settings",
    ),
    "health_space_languages": MessageKind(
        "{share}% of the keywords are not in {language}: in a space of texts, themes may split "
        "by language; the people's space may suit this corpus better",
        "Open the space settings",
        "open:/themes?tune=1",
    ),
    "health_snowball_cap": MessageKind(
        "the last proposal of collaborators in {slot} stopped at the cap of {cap} people",
        "Open the settings",
        "settings",
    ),
    # a preview that cannot show a value
    "preview_needs_extraction": MessageKind(
        "{param} at {value} reaches past the last build's {built}: the candidates outside its "
        "window were never kept, a new extraction shows them"
    ),
    # the overview's one next step
    "next_watch_build": MessageKind("a build is running", "Follow the build", "open:/build"),
    "next_copilot_waiting": MessageKind(
        "the build waits for your copilot ({step})", "Continue the build", "open:/build"
    ),
    "next_import_people": MessageKind(
        "start with the people whose texts make the map", "Add people", "open:/people"
    ),
    "next_install_model": MessageKind(
        "install the language model the keyword extraction needs", "Open the settings", "settings"
    ),
    "next_see_failure": MessageKind(
        "{stage} failed: see why and build again", "See what failed", "build"
    ),
    "next_first_build": MessageKind("build the keywords, the themes and the map", "Build", "build"),
    "next_restore_map": MessageKind(
        "the map is out of date: building it restores it", "Build the map", "build"
    ),
    "next_update": MessageKind("some results need an update", "Build", "build"),
    "next_curate_themes": MessageKind(
        "check the themes the grouping proposed and name them", "Open the themes", "open:/themes"
    ),
    "next_open_map": MessageKind("everything is up to date", "Open the map", "open:/map"),
    # why a stage needs an update (``Reason.kind``)
    "reason_code": MessageKind("{detail}"),
    "reason_input": MessageKind("{detail}"),
    "reason_parameter": MessageKind("{detail}"),
    "reason_project": MessageKind("{detail}"),
    "reason_upstream": MessageKind("{detail}"),
}


def message(code: str, **params: Any) -> dict[str, Any]:
    """``{"code", "params", "message"}`` of *code*."""
    text = MESSAGES[code].template.format(
        **{k: ", ".join(map(str, v)) if isinstance(v, list) else str(v) for k, v in params.items()}
    )
    return {"code": code, "params": params, "message": text}


def empty(code: str, **params: Any) -> dict[str, Any]:
    """An empty result: what it means and what to do next."""
    kind = MESSAGES[code]
    return {
        **message(code, **params),
        "next": {"label": kind.next_label, "action": kind.next_action},
    }


_SWITCHED_OFF = re.compile(r"^switched off \(set (\S+)\.enabled ")


def skip_message(text: str) -> dict[str, Any]:
    """The code of a stage's skip reason (the build's English words)."""
    m = _SWITCHED_OFF.match(text)
    if m:
        return message("stage_switched_off", stage=m.group(1))
    if text == "the project has no overlay":
        return message("stage_no_overlay")
    return message("stage_not_applicable", reason=text)


def attempt_message(outcome: str, error: str | None) -> dict[str, Any]:
    """The code of a failed or cancelled attempt (the build records ``Type: message``)."""
    text = (error or "").strip()
    if outcome == "cancelled":
        return message("stage_cancelled")
    kind, sep, detail = text.partition(": ")
    if not sep or not kind.isidentifier():
        kind, detail = "", text
    if kind == "StageRefused" or (not kind and detail):
        return message("stage_refused", detail=detail)
    if kind == "LanguageModelMissing":
        return message("language_model_missing", detail=detail)
    return message("stage_failed", error_type=kind or "Error", detail=detail)


def reason_message(kind: str, subject: str, detail: str) -> dict[str, Any]:
    """Why a stage needs an update: its kind as a code, the subject and the words."""
    out = message(f"reason_{kind}", detail=detail) if f"reason_{kind}" in MESSAGES else None
    out = out or message("reason_upstream", detail=detail)
    out["params"] = {"subject": subject, "detail": detail}
    return out


def with_message(entry: Mapping[str, Any], code: str, **params: Any) -> dict[str, Any]:
    """*entry* with a message's code, params and English text added."""
    return {**entry, **message(code, **params)}


def _cause_code(exc: BaseException) -> tuple[str, dict[str, Any]]:
    """The code and params of an exception that ended a job."""
    from cartolex.collect.http import (
        CacheMiss,
        IncompleteResults,
        MalformedResponse,
        RequestRefused,
        ServiceError,
        ServiceUnavailable,
    )

    if isinstance(exc, ServiceError):
        params = {"host": exc.host, "status": exc.status, "what": exc.what}
        if exc.retry_after is not None:
            params["wait_s"] = round(exc.retry_after)
        if isinstance(exc, ServiceUnavailable) and exc.budget_spent:
            return "collect_budget_spent", {**params, "keyed": bool(getattr(exc, "keyed", False))}
        if isinstance(exc, IncompleteResults):
            return "collect_incomplete", params
        if isinstance(exc, MalformedResponse):
            return "collect_malformed", params
        if isinstance(exc, RequestRefused):
            return "collect_refused", params
        return "collect_service_unavailable", params
    if isinstance(exc, CacheMiss):
        return "collect_cache_miss", {}
    return "job_failed", {"error_type": type(exc).__name__, "detail": str(exc).strip()[:300]}


#: What a failed job's record keeps of its last progress: where it was, never a name.
_STEP_KEYS = ("stage", "fraction", "stage_fraction", "phase", "phases", "code", "params")


#: The characters of a traceback a failed job keeps (its end: the innermost frames).
MAX_TRACEBACK = 8_000


def traceback_text(value: BaseException | str | None) -> str:
    """A traceback for a diagnostic: an exception's (or one already written, as a child
    process sends it), the home folder written ``~``, its last :data:`MAX_TRACEBACK`
    characters; empty without one."""
    import traceback
    from pathlib import Path

    if isinstance(value, BaseException):
        text = getattr(value, "child_traceback", None) or "".join(
            traceback.format_exception(type(value), value, value.__traceback__)
        )
    else:
        text = value or ""
    text = text.strip()
    if not text:
        return ""
    try:
        home = str(Path.home())
    except (KeyError, RuntimeError):  # pragma: no cover - no home folder
        home = ""
    if len(home) > 1:
        text = text.replace(home, "~")
    return text[-MAX_TRACEBACK:]


def job_error(exc: BaseException, progress: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Why a job failed, for its log, its card and the diagnostic: the code, params and words
    of the cause, the exception's class and a short message, the step the job was in and how
    far it got, and the traceback (:func:`traceback_text`). (Messages of cartolex name no
    person; the message is cut at 300 characters.)"""
    code, params = _cause_code(exc)
    out = message(code, **params)
    if code == "collect_budget_spent":  # without a key, a free one; with one, waiting
        out["next"] = (
            {"label": "Wait for the next day's budget", "action": "none"}
            if params.get("keyed")
            else {"label": "Set a free API key", "action": "open:/settings?section=sources"}
        )
    out["exception"] = type(exc).__name__
    out["detail"] = str(exc).strip()[:300]
    if exc.__traceback__ is not None or getattr(exc, "child_traceback", None):
        out["traceback"] = traceback_text(exc)
    if progress:
        step = {k: progress[k] for k in _STEP_KEYS if progress.get(k) is not None}
        out["step"] = step.get("stage")
        out["progress"] = step
    return out


def job_pause(paused: Any) -> dict[str, Any]:
    """Why a job paused (a :class:`~cartolex.project.checkpoints.JobPaused`): the code, params
    and words, the checkpoint to resume from, how far it got, and the cause, if any."""
    params = dict(paused.params)
    out = (
        message(paused.code, **params)
        if paused.code in MESSAGES
        else {"code": paused.code, "params": params, "message": paused.message}
    )
    out["checkpoint"] = paused.checkpoint
    out["progress"] = dict(paused.progress)
    out["cause"] = job_error(paused.cause) if paused.cause is not None else None
    return out
