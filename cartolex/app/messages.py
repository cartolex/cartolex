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

__all__ = ["MESSAGES", "attempt_message", "empty", "message", "reason_message", "skip_message"]


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
    "empty_tree_never_saved": MessageKind("the tree was never saved", "Close", "none"),
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
    "empty_handoff": MessageKind("no term to send in this band", "Close", "none"),
    "empty_no_proposals": MessageKind("no AI answers imported yet", "Export terms", "none"),
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
    "health_snowball_cap": MessageKind(
        "the last proposal of collaborators in {slot} stopped at the cap of {cap} people",
        "Open the settings",
        "settings",
    ),
    # the overview's one next step
    "next_watch_build": MessageKind("a build is running", "Follow the build", "open:/build"),
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
