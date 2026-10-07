# SPDX-License-Identifier: MIT
"""Every message the interface shows has a stable code and params (the interface translates)."""

from __future__ import annotations

import ast
import re
import string
from pathlib import Path

import pytest

from cartolex.app.errors import ERRORS, NEXT_ACTIONS, ApiError
from cartolex.app.messages import MESSAGES, attempt_message, reason_message, skip_message

APP = Path(__file__).resolve().parents[1] / "cartolex" / "app"
DOCS = Path(__file__).resolve().parents[1] / "docs" / "dev" / "api.md"


def _fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def _calls():
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                yield path, node


def _name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Attribute):
        base = f.value.id if isinstance(f.value, ast.Name) else ""
        return f"{base}.{f.attr}"
    return f.id if isinstance(f, ast.Name) else ""


def test_errors_are_built_from_the_catalogue_with_their_params():
    seen = set()
    for path, node in _calls():
        name = _name(node)
        where = f"{path.name}:{node.lineno}"
        if name == "ApiError" and path.name != "errors.py":
            raise AssertionError(f"{where}: build errors with ApiError.of(code, …)")
        if name in ("ApiError.of", "body_of", "empty", "message", "MappingError"):
            first = node.args[0] if node.args else None
            if not isinstance(first, ast.Constant):
                continue  # a code chosen at run time (the catalogues are checked below)
            code = first.value
            table = ERRORS if name in ("ApiError.of", "body_of", "MappingError") else MESSAGES
            assert code in table, f"{where}: unknown code {code!r}"
            seen.add(code)
            if any(k.arg is None for k in node.keywords):
                continue  # **params
            given = {k.arg for k in node.keywords} - {"headers", "extra"}
            wanted = _fields(table[code].template)
            assert wanted <= given, f"{where}: {code} needs {sorted(wanted)}, got {sorted(given)}"
    unused = sorted(
        set(ERRORS)
        - seen
        - {
            "stale",
            "locked_here",
            "lock_lost",
            "locked",
            "not_a_project",
            "unsupported_format",
            "identity_frozen",
            "invalid_parameters",
            "theme_refused",
            "stages_running",
            "invalid_file",
            "invalid",
            "no_route",
            "method_not_allowed",
            "http_error",
            "internal",
            "busy",
        }
    )
    assert unused == [], f"codes never raised: {unused}"


def test_every_code_has_a_valid_next_action_and_is_documented():
    docs = DOCS.read_text(encoding="utf-8")
    for code, kind in ERRORS.items():
        assert re.fullmatch(r"[a-z][a-z0-9_]*", code), code
        assert kind.next_action in NEXT_ACTIONS, code
        assert f"`{code}`" in docs, f"{code} is not listed in docs/dev/api.md"
    for code in MESSAGES:
        assert f"`{code}`" in docs, f"{code} is not listed in docs/dev/api.md"


def test_an_error_carries_its_code_params_and_english_text():
    err = ApiError.of("unknown_people", ids=["p1", "p2"])
    body = err.body()["error"]
    assert body["code"] == "unknown_people" and body["params"] == {"ids": ["p1", "p2"]}
    assert body["message"] == "unknown person id(s): p1, p2" and err.status == 404
    assert body["next"] == {"label": "Reload", "action": "reload"}


@pytest.mark.parametrize(
    ("text", "code", "params"),
    [
        (
            "switched off (set keywords.triage.enabled in decisions/params.json to run it)",
            "stage_switched_off",
            {"stage": "keywords.triage"},
        ),
        ("the project has no overlay", "stage_no_overlay", {}),
        ("the host's own reason", "stage_not_applicable", {"reason": "the host's own reason"}),
    ],
)
def test_skip_reasons_have_codes(text, code, params):
    m = skip_message(text)
    assert (m["code"], m["params"]) == (code, params)
    # the words name no file and no setting's key
    assert "params.json" not in m["message"] and ".enabled" not in m["message"]


def test_attempts_have_codes():
    assert (
        attempt_message("cancelled", "keywords.extract was cancelled")["code"] == "stage_cancelled"
    )
    refused = attempt_message("failed", "keywords.extract: min_people is 9, but only 4 people")
    assert refused["code"] == "stage_refused" and refused["params"]["detail"].startswith("keywords")
    assert attempt_message("failed", "StageRefused: no pinned map version")["code"] == (
        "stage_no_pinned_map"
    )
    no_texts = attempt_message("failed", "StageRefused: no mapped person has a text yet: …")
    assert no_texts["code"] == "stage_no_texts" and "csv" not in no_texts["message"]
    missing = attempt_message("failed", "LanguageModelMissing: install fr_core_news_md")
    assert missing["code"] == "language_model_missing"
    broke = attempt_message("failed", "RuntimeError: extract broke on purpose")
    assert broke["code"] == "stage_failed"
    assert broke["params"] == {"error_type": "RuntimeError", "detail": "extract broke on purpose"}
    reason = reason_message("parameter", "min_people", "parameter min_people: 3 → 2")
    assert reason["code"] == "reason_parameter" and reason["params"]["subject"] == "min_people"
