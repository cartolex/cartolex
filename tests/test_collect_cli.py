# SPDX-License-Identifier: MIT
"""``cartolex collect`` offline against the demo services, and the « what leaves » summary."""

from __future__ import annotations

import json

from _collect_world import demo_project

from cartolex.cli import main
from cartolex.collect.privacy import plan_collection
from cartolex.collect.services import CollectSettings
from cartolex.demo import generate
from cartolex.demo.cli import main as demo_main
from cartolex.demo.services import build_bibliography
from cartolex.project import Project
from cartolex.project.tables import read_source_table


def test_the_whole_flow_from_the_command_line(tmp_path, capsys) -> None:
    folder, people = tmp_path / "p", tmp_path / "people.csv"
    assert demo_main(["services", "--size", "XS", "--people-list", str(people), "--list-only"]) == 0
    assert (
        main(["init", str(folder), "--name", "Demo", "--field", "Coasts", "--languages", "en,fr"])
        == 0
    )
    assert main(["collect", "people", str(folder), str(people), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "last_name: last name" in out and "identifier: orcid" in out
    assert not (folder / "sources" / "tables" / "people.parquet").exists()
    assert main(["collect", "people", str(folder), str(people)]) == 0
    assert "14 person(s) created" in capsys.readouterr().out

    demo = ["--services", "demo", "--world", "XS:0"]
    assert main(["collect", "resolve", str(folder), *demo, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "What leaves this computer" in out and "names" in out and "never leaves" in out
    assert not list((folder / "cache" / "http").rglob("*.gz"))

    assert main(["collect", "resolve", str(folder), *demo, "--auto"]) == 0
    out = capsys.readouterr().out
    assert " auto " in out and " pending " in out and "score:" in out
    bib = build_bibliography(generate("XS", 0))
    pending = [line.split()[0] for line in out.splitlines() if line.split()[1:2] == ["pending"]]
    rows = {p["person_id"]: p for p in _people(folder)}
    by_name = {(t.last_name, t.first_name): t for t in bib.truth.values()}
    for pid in pending:
        truth = by_name[(rows[pid]["last_name"], rows[pid]["first_name"])]
        args = list(truth.records) if truth.records else ["--none"]
        assert main(["collect", "confirm", str(folder), pid, *args]) == 0
    assert main(["collect", "harvest", str(folder), *demo]) == 0
    out = capsys.readouterr().out
    assert "harvested" in out and "tables:" in out
    texts = read_source_table(folder / "sources" / "tables" / "texts.parquet", "texts")
    assert texts.num_rows > 40
    before = (folder / "sources" / "tables" / "texts.parquet").read_bytes()
    # Offline, from the cache only (the demo services now listen on another port).
    assert main(["collect", "harvest", str(folder), *demo, "--cache-only"]) == 0
    assert (folder / "sources" / "tables" / "texts.parquet").read_bytes() == before
    # Job records hold hosts, kinds of data and counts, never a name.
    names = [r["last_name"] for r in _people(folder)]
    for log in (folder / "logs" / "jobs").glob("collect-*.jsonl"):
        text = log.read_text(encoding="utf-8")
        assert not any(n in text for n in names)
        events = [json.loads(line) for line in text.splitlines()]
        assert events[0]["event"] == "start" and events[-1]["event"] == "end"


def test_the_key_saved_in_the_app_serves_the_command_line(tmp_path, capsys, monkeypatch) -> None:
    from cartolex.app.machine import MachineKeys
    from cartolex.app.server import default_data_dir

    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    folder, people = tmp_path / "p", tmp_path / "people.csv"
    assert demo_main(["services", "--size", "XS", "--people-list", str(people), "--list-only"]) == 0
    main(["init", str(folder), "--name", "Demo", "--field", "Coasts"])
    assert main(["collect", "people", str(folder), str(people)]) == 0
    resolve = ["collect", "resolve", str(folder), "--services", "demo", "--dry-run"]
    capsys.readouterr()
    assert main(resolve) == 0
    assert "your API key" not in capsys.readouterr().out
    MachineKeys(default_data_dir()).save("openalex", "oa-test-12345678")
    assert main(resolve) == 0
    assert "your API key" in capsys.readouterr().out
    # Another folder of the app: its saved key, not the default folder's.
    assert main([*resolve, "--data-dir", str(tmp_path / "elsewhere")]) == 0
    assert "your API key" not in capsys.readouterr().out


def test_usage_errors_exit_with_one(tmp_path, capsys) -> None:
    folder = tmp_path / "p"
    main(["init", str(folder), "--name", "Demo", "--field", "Coasts"])
    assert main(["collect", "confirm", str(folder), "p000001"]) == 1
    assert main(["collect", "resolve", str(folder), "--services", "demo", "--world", "XS:0"]) == 1
    assert "import a list first" in capsys.readouterr().err


def _people(folder):
    return read_source_table(folder / "sources" / "tables" / "people.parquet", "people").to_pylist()


def test_the_summary_before_a_collection(tmp_path) -> None:
    bib = build_bibliography(generate("XS", 0))
    project = demo_project(tmp_path / "p", bib)
    plan = plan_collection(project, "resolve", CollectSettings(contact="me@x.test"))
    assert plan.people == len(bib.world.people)
    hosts = {h.service: h for h in plan.hosts}
    assert hosts["openalex"].host == "api.openalex.org"
    assert hosts["orcid"].host == "pub.orcid.org"
    assert "names" in hosts["openalex"].sends and "your contact address" in hosts["openalex"].sends
    assert hosts["openalex"].requests > plan.people and hosts["openalex"].cost_usd > 0
    text = "\n".join(plan.lines())
    assert "never leaves" in text and "cache/http/" in text
    assert not any(t.last_name in text for t in bib.truth.values())
    keyed = plan_collection(project, "resolve", CollectSettings(api_keys={"openalex": "k"})).hosts[
        0
    ]
    assert "your API key" in keyed.sends
    harvest_plan = plan_collection(project, "harvest", CollectSettings())
    assert harvest_plan.people == 0 and harvest_plan.hosts == []
    project.close()
    Project.open(tmp_path / "p")


def test_the_summary_of_institutions_collaborators_a_snapshot_and_a_retry(tmp_path) -> None:
    bib = build_bibliography(generate("XS", 0))
    project = demo_project(tmp_path / "p", bib)
    settings = CollectSettings(contact="me@x.test")
    search = plan_collection(project, "institutions", settings, search="Marine")
    (host,) = search.hosts
    assert host.sends[0] == "institution names" and host.requests == 1
    named = plan_collection(project, "institutions", settings, institutions=["I9990000001"])
    assert named.hosts[0].sends[0] == "institution identifiers"
    assert "one request per 100 works" in "\n".join(named.lines())
    from cartolex.collect.resolve import confirm

    for pid in ("p000001", "p000002"):
        confirm(project, pid, ["openalex:A9990000001"])
    rounds = plan_collection(project, "collaborators", settings, rounds=2)
    assert rounds.people == 2 and rounds.hosts[0].sends[0] == "author identifiers"
    from_snapshot = plan_collection(project, "harvest", settings, snapshot="openalex-snapshot")
    assert {h.service for h in from_snapshot.hosts} <= {"orcid"}
    assert any("snapshot" in n for n in from_snapshot.notes)
    retry = plan_collection(project, "coverage", settings)
    assert retry.hosts == [] and "nothing to retry" in "\n".join(retry.lines())
    text = "\n".join(named.lines() + rounds.lines())
    assert not any(t.last_name in text for t in bib.truth.values())
    project.close()
