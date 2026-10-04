# SPDX-License-Identifier: MIT
"""From one demo institution to a built map through the command line, and the other new verbs:
collaborators, the snapshot, coverage and a slot's window, each with --dry-run."""

from __future__ import annotations

import json

import pytest

from cartolex.cli import main
from cartolex.demo import generate
from cartolex.demo.cli import main as demo_main
from cartolex.demo.services import build_bibliography
from cartolex.project import Project
from cartolex.project.tables import read_decision_csv, read_source_table

DEMO = ["--services", "demo", "--world", "S:0"]


def _biggest_institution():
    bib = build_bibliography(generate("S", 0))
    counts = {
        iid: sum(1 for w in bib.works.values() for a in w.authorships
                 if any(iid in bib.institutions[i].lineage for i in a.institutions))
        for iid in bib.institution_of.values()
    }  # fmt: skip
    return bib, bib.institutions[max(counts, key=lambda i: (counts[i], i))]


@pytest.fixture(scope="module")
def from_institution(tmp_path_factory):
    folder = tmp_path_factory.mktemp("cli") / "p"
    assert (
        main(["init", str(folder), "--name", "Demo", "--field", "Coasts", "--languages", "en,fr"])
        == 0
    )
    return folder


@pytest.mark.models("en", "fr")
def test_from_one_institution_to_a_built_map(from_institution, capsys) -> None:
    folder = from_institution
    bib, inst = _biggest_institution()
    # Find it by name: candidates to choose from, nothing taken.
    word = inst.name.split()[-1]
    assert main(["collect", "institutions", str(folder), "--search", word, *DEMO]) == 0
    out = capsys.readouterr().out
    assert inst.id in out and "--institution" in out
    # What would leave the computer, before anything does.
    assert main(["collect", "institutions", str(folder), "--institution", inst.id, *DEMO,
                 "--dry-run"]) == 0  # fmt: skip
    out = capsys.readouterr().out
    assert "institution identifiers" in out and "never leaves" in out
    assert not list((folder / "sources").rglob("*.jsonl*"))
    # The proposal, by its ROR id, then everyone proposed taken.
    assert main(["collect", "institutions", str(folder), "--institution",
                 f"https://ror.org/{inst.ror}", "--years", "2012-", *DEMO]) == 0  # fmt: skip
    out = capsys.readouterr().out
    assert "author(s) with 2 work(s) or more" in out and "levels:" in out
    assert main(["collect", "institutions", str(folder), "--take", "all"]) == 0
    out = capsys.readouterr().out
    taken = int(out.split(" person(s) taken")[0].split()[-1])
    assert taken >= 5
    people = read_decision_csv(folder / "decisions" / "people.csv", "people")
    assert len(people) == taken and {p["identity"] for p in people} == {"confirmed"}
    project = Project.open(folder)
    assert [lv.id for lv in project.config.levels] == ["unit", "institution"]
    # Their works, then the coverage, then the map.
    assert main(["collect", "harvest", str(folder), *DEMO]) == 0
    assert "harvested" in capsys.readouterr().out
    assert main(["collect", "coverage", str(folder)]) == 0
    out = capsys.readouterr().out
    assert f"{taken} people (0 excluded)" in out and "good" in out
    assert main(["build", str(folder), "--yes"]) == 0
    capsys.readouterr()
    runs = sorted(p.parent.name for p in (folder / "derived").rglob("run.json"))
    assert "map.layout" in runs
    orgs = read_source_table(folder / "sources" / "tables" / "organisations.parquet",
                             "organisations").to_pylist()  # fmt: skip
    assert {o["level"] for o in orgs} >= {"unit", "institution"}


def test_collaborators_from_the_command_line(tmp_path, capsys) -> None:
    folder, people = tmp_path / "p", tmp_path / "people.csv"
    demo_main(["services", "--size", "XS", "--people-list", str(people), "--list-only"])
    lines = people.read_text(encoding="utf-8").splitlines()
    people.write_text("\n".join(lines[:5]) + "\n", encoding="utf-8")  # four people only
    main(["init", str(folder), "--name", "Demo", "--field", "Coasts"])
    main(["collect", "people", str(folder), str(people)])
    xs = ["--services", "demo", "--world", "XS:0"]
    main(["collect", "resolve", str(folder), *xs, "--auto"])
    capsys.readouterr()
    assert main(["collect", "collaborators", str(folder), *xs, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "author identifiers" in out and "What never leaves" in out
    assert main(["collect", "collaborators", str(folder), *xs]) == 0
    out = capsys.readouterr().out
    assert "round 1:" in out and "decide with --decide" in out
    rows = read_decision_csv(folder / "decisions" / "snowball.csv", "snowball")
    assert rows and {r["decision"] for r in rows} == {"context"}
    first = rows[0]["person_id"]
    assert main(["collect", "collaborators", str(folder), "--decide", f"{first}=no"]) == 0
    people_rows = {
        r["person_id"]: r for r in read_decision_csv(folder / "decisions" / "people.csv", "people")
    }
    assert people_rows[first]["role"] == "excluded"
    assert main(["collect", "collaborators", str(folder), "--decide", f"{first}=perhaps"]) == 1


def test_the_snapshot_coverage_and_a_window(tmp_path, capsys) -> None:
    folder, people, snap = tmp_path / "p", tmp_path / "people.csv", tmp_path / "snapshot"
    demo_main(["services", "--size", "XS", "--people-list", str(people), "--list-only"])
    assert demo_main(["snapshot", "--size", "XS", "--out", str(snap)]) == 0
    main(["init", str(folder), "--name", "Demo", "--field", "Coasts"])
    main(["collect", "people", str(folder), str(people)])
    xs = ["--services", "demo", "--world", "XS:0"]
    main(["collect", "resolve", str(folder), *xs, "--auto"])
    assert main(["collect", "window", str(folder), "2015-"]) == 0
    assert "2015–…" in capsys.readouterr().out
    project = Project.open(folder)
    assert project.config.slots[0].years.as_tuple() == (2015, None)
    assert main(["collect", "snapshot", str(folder), str(snap), *xs, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "api.openalex.org" not in out and "snapshot" in out
    spill = tmp_path / "fast-disk"  # what the passes find waits there, not in the project
    assert main(["collect", "snapshot", str(folder), str(snap), *xs, "--spill", str(spill)]) == 0
    out = capsys.readouterr().out
    assert "harvested" in out and "sent to openalex" not in out
    assert spill.is_dir() and not (folder / "cache" / "snapshot").exists()
    texts = read_source_table(folder / "sources" / "tables" / "texts.parquet", "texts").to_pylist()
    assert texts and min(t["year"] for t in texts) >= 2015
    assert main(["collect", "coverage", str(folder), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert sum(report["states"].values()) == report["people"]
    pid = report["persons"][0]["person_id"]
    assert main(["collect", "coverage", str(folder), "--person", pid]) == 0
    out = capsys.readouterr().out
    assert pid in out and "actions:" in out
    assert main(["collect", "coverage", str(folder), "--retry", *xs, "--dry-run"]) == 0
    assert "nothing to retry" in capsys.readouterr().out
    assert main(["collect", "coverage", str(folder), "--exclude", pid]) == 0
    assert main(["collect", "window", str(folder), "none"]) == 0
    assert Project.open(folder).config.slots[0].years is None


def test_a_stop_signal_stops_a_collection_cleanly(tmp_path, monkeypatch, capsys) -> None:
    import os
    import signal

    from cartolex import cli_collect

    folder, people = tmp_path / "p", tmp_path / "people.csv"
    demo_main(["services", "--size", "XS", "--people-list", str(people), "--list-only"])
    main(["init", str(folder), "--name", "Demo", "--field", "Coasts"])
    main(["collect", "people", str(folder), str(people)])
    capsys.readouterr()

    def runner(args, project, client):
        os.kill(os.getpid(), signal.SIGTERM)  # a service manager stopping the job
        client.check_cancel()  # the next request: the job stops there, cleanly
        raise AssertionError("not stopped")

    monkeypatch.setitem(cli_collect.RUNNERS, "harvest", runner)
    xs = ["--services", "demo", "--world", "XS:0"]
    before = signal.getsignal(signal.SIGTERM)
    assert main(["collect", "harvest", str(folder), *xs]) == 130
    assert "stopping after the current request" in capsys.readouterr().out
    assert signal.getsignal(signal.SIGTERM) == before  # the handler is put back
