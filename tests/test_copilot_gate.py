# SPDX-License-Identifier: MIT
"""The copilot's acceptance gate.

Once a copilot's triage is accepted for the current extraction, only the
keywords someone accepted enter the vocabulary; the candidates nobody judged are
counted, can be sent to the AI alone, or kept anyway.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from _app_helpers import TOKEN, Client

from cartolex.app import AppSettings, create_app
from cartolex.cli import main as cli
from cartolex.demo import generate
from cartolex.demo.project import write_project

pytestmark = pytest.mark.models("en", "fr")


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    """The XS demo world, built up to its vocabulary."""
    root = tmp_path_factory.mktemp("gate") / "project"
    write_project(generate("XS", 0), root).close()
    assert cli(["params", str(root), "--set", "pinned_year=2026"]) == 0
    assert cli(["build", str(root), "--only", "keywords.build"]) == 0
    return root


@pytest.fixture(scope="module")
def client(project, tmp_path_factory) -> Client:
    app = create_app(
        AppSettings(
            project=project,
            launch_token=TOKEN,
            data_dir=tmp_path_factory.mktemp("gate-app"),
            build_budget_mb=1e9,
            build_year=2026,
        )
    )
    return Client(app)


def _result(bundle: str, decisions: list[dict]) -> dict:
    return {
        "format": "cartolex-copilot-result/1",
        "task": "triage",
        "bundle": bundle,
        "made_at": "2030-01-01T00:00:00Z",
        "decisions": decisions,
    }


def test_an_accepted_copilot_triage_lets_in_only_the_accepted_keywords(client, project):
    listed = client.get("/api/keywords", params={"band": "kept", "limit": 3}).json()
    assert listed["gate"] == {"mode": "bands", "unjudged": 0}
    kept = listed["items"][:2]
    decisions = [{"term": k["term"], "language": k["language"], "decision": "keep"} for k in kept]
    proposal = client.post(
        "/api/keywords/copilot/import", json={"result": _result("b1", decisions)}
    ).json()
    accepted = client.post(
        f"/api/ai/proposals/{proposal['id']}/accept",
        json={"all": True},
        headers={"If-Match": f'"{proposal["keywords_version"]}"'},
    )
    assert accepted.status_code == 200, accepted.text
    after = client.get("/api/keywords", params={"unjudged": True, "limit": 500}).json()
    gate = after["gate"]
    assert gate["mode"] == "copilot" and gate["unjudged"] == after["total"] > 0
    assert not {(k["term"], k["language"]) for k in kept} & {
        (i["term"], i["language"]) for i in after["items"]
    }
    # only those two reach the vocabulary
    started = client.post("/api/build", json={"scope": ["keywords.build"], "dry_run": False})
    assert client.wait_job(started.json()["job"]["id"], 300)["state"] == "succeeded"
    with open(project / "derived/keywords.build/keywords_global_refined.csv") as fh:
        vocabulary = {r["term"].lower() for r in csv.DictReader(fh)}
    assert vocabulary == {k["term"].lower() for k in kept}
    # the bundle of the unjudged ones alone, then « keep them anyway »
    summary = client.get("/api/keywords/copilot/summary", params={"scope": "unjudged"}).json()
    assert summary["counts"]["terms"] == gate["unjudged"]
    kept_all = client.post(
        "/api/keywords/decisions/where",
        json={"where": {"unjudged": True}, "decision": "keep"},
        headers={"If-Match": f'"{after["version"]}"'},
    )
    assert kept_all.json()["decided"] == gate["unjudged"]
    assert client.get("/api/keywords").json()["gate"]["unjudged"] == 0
