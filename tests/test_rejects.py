# SPDX-License-Identifier: MIT
"""Keyword categories and the rejection lists: the codes, the cache, the band, the settings."""

from __future__ import annotations

import pandas as pd
import pytest
from _app_helpers import TOKEN, Client, etag

from cartolex.app import AppSettings, create_app
from cartolex.copilot.bundle import check_result
from cartolex.lexicon import rejects
from cartolex.lexicon.categories import category_of
from cartolex.lexicon.extract_raw import reject_band
from cartolex.lexicon.rejects import MachineRejects, export_list, snapshot
from cartolex.project import Project
from cartolex.project.handoff import BundleItem, parse_answer
from cartolex.project.tables import decision_csv_bytes, read_decision_csv


def test_codes_old_and_new_read_into_categories():
    items = [BundleItem(t, "en", "check", "", 3, 3, 0.5, [], []) for t in "abcdef"]
    # A version-3 answer (C, G, F, N only) and the new codes P, D and H.
    parsed = parse_answer("1 | C | a\n2 | G | b\n3 | N | c\n4 | P | d\n5 | D | e\n6 | H | f", items)
    got = [parsed.verdicts[i].category for i in range(6)]
    assert got == ["concept", "never", "here", "place", "field", "here"]
    assert category_of("F") == category_of("K") == "never" and category_of("?") == ""


def test_the_category_column_is_optional_in_keywords_csv(tmp_path):
    path = tmp_path / "keywords.csv"
    old = "term,language,decision,target,reason,source,decided_at\nwave,en,keep,,,person,t\n"
    path.write_text(old, encoding="utf-8")
    rows = read_decision_csv(path, "keywords")
    assert rows[0]["category"] == ""
    assert decision_csv_bytes("keywords", rows).decode() == old  # written only when filled
    rows[0]["category"] = "concept"
    path.write_bytes(decision_csv_bytes("keywords", rows))
    assert read_decision_csv(path, "keywords")[0]["category"] == "concept"
    rows[0]["category"] = "tasty"
    path.write_bytes(decision_csv_bytes("keywords", rows))
    with pytest.raises(ValueError, match="category"):
        read_decision_csv(path, "keywords")


def test_the_machine_cache_and_a_project_snapshot(tmp_path, monkeypatch):
    machine = MachineRejects(tmp_path / "rejects")
    rows = [{"term": "further work", "language": "en"}, {"term": "Results", "language": "en"}]
    assert machine.add(rows, route="ai-api", project="a") == 2
    assert machine.add(rows[:1], route="ai-handoff", project="a") == 0  # one entry per project
    machine.add([{"term": "further  WORK", "language": "en"}], route="ai-copilot", project="b")
    assert machine.terms("en") == {"further work", "results"}
    assert machine.terms("en", besides="b") == {"further work", "results"}
    assert machine.terms("en", besides="a") == {"further work"}

    monkeypatch.setattr(rejects, "shipped_terms", lambda lang: frozenset({"et al"}))
    snap = snapshot(["en"], machine, project="a", exempt={"": ["et al"]})
    assert snap["languages"]["en"] == {"further work": "earlier"}  # a's own answers, and a keep
    snap = snapshot(["en"], machine, project="c")
    assert snap["languages"]["en"] == {
        "et al": "list",
        "further work": "earlier",
        "results": "earlier",
    }
    assert snapshot(["en"], machine, project="c", enabled=False)["languages"]["en"] == {}

    # Seen in two projects, no capital letter, no name of the user's projects.
    assert export_list(machine, min_projects=2) == {"en": ["further work"]}
    assert export_list(machine, min_projects=1) == {"en": ["further work"]}  # "Results": a name?
    assert export_list(machine, min_projects=1, names=["Ada Work"]) == {"en": []}

    assert machine.remove("en", ["FURTHER WORK"]) == 2
    assert machine.terms("en") == {"results"} and machine.clear() == 1
    assert machine.counts() == {}


def test_the_rejected_band_matches_any_form():
    table = pd.DataFrame(
        {
            "term": ["further work", "tide gauge", "study"],
            "forms": ["further work|further works", "tide gauge", "studies|study"],
            "band": ["kept", "kept", "check"],
            "reason": ["multiword", "multiword", "single-word"],
        }
    )
    out = reject_band(table, {"further works": "earlier", "studies": "list"})
    assert list(out["band"]) == ["rejected", "kept", "rejected"]
    assert list(out["reason"]) == ["rejected-earlier", "multiword", "rejected-list"]


def test_a_copilot_result_carries_known_categories():
    head = {"format": "cartolex-copilot-result/1", "task": "triage", "bundle": "b"}
    decision = {"term": "x", "language": "en", "decision": "exclude", "reason": "r"}
    ok = {**head, "decisions": [{**decision, "category": "never"}]}
    assert check_result(ok, task="triage") == []
    bad = {**head, "decisions": [{**decision, "category": "tasty"}]}
    assert any("category" in p for p in check_result(bad, task="triage"))


@pytest.fixture()
def app_client(tmp_path):
    root = tmp_path / "p"
    Project.init(root, name="Small map", domain_title="Ocean physics").close()
    app = create_app(AppSettings(project=root, launch_token=TOKEN, data_dir=tmp_path / "data"))
    yield Client(app), MachineRejects(tmp_path / "data" / "rejects")
    app.state.cartolex.shutdown()


def test_a_person_s_decision_leaves_the_cache_and_the_settings_switch(app_client):
    client, machine = app_client
    machine.add(
        [{"term": "further work", "language": "en"}, {"term": "past decades", "language": "en"}],
        route="ai-api",
        project="other",
    )
    put_back = client.post(
        "/api/keywords/decisions",
        json={"decisions": [{"term": "further work", "language": "en", "decision": "keep"}]},
        headers={"If-Match": '"none"'},
    )
    assert put_back.status_code == 200 and machine.terms("en") == {"past decades"}
    wrong = client.post(
        "/api/keywords/decisions",
        json={
            "decisions": [{"term": "x", "language": "en", "decision": "keep", "category": "never"}]
        },
        headers={"If-Match": etag(put_back)},
    )
    assert wrong.status_code == 422
    assert wrong.json()["error"]["code"] == "keyword_category_mismatch"

    view = client.get("/api/settings/rejects")
    assert view.json()["enabled"] is True and view.json()["machine"]["total"] == 1
    off = client.put(
        "/api/settings/rejects", json={"enabled": False}, headers={"If-Match": etag(view)}
    )
    assert off.status_code == 200 and off.json()["enabled"] is False
    assert client.get("/api/settings/rejects").json()["enabled"] is False
    terms = client.get("/api/settings/rejects/terms").json()
    assert [t["term"] for t in terms["items"]] == ["past decades"]
    cleared = client.post("/api/settings/rejects/clear", json={})
    assert cleared.json()["removed"] == 1 and machine.counts() == {}
