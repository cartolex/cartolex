# SPDX-License-Identifier: MIT
"""The AI copilot: what a bundle never holds, and a whole session offline.

- **Nothing personal.** No bundle holds a person's name, an identifier, an
  organisation or a sentence of a text; with usage lines (asked for), the
  excerpts have every name masked.
- **Offline, with the preinstalled libraries only.** A fresh virtual
  environment sees the scientific libraries a code sandbox has (numpy, pandas,
  SciPy, scikit-learn, matplotlib) and nothing else of cartolex's dependencies
  (every other import is refused, and so is any connection); in it, the
  bundle's ``bootstrap.py`` unpacks the kit, a short session runs, and its result
  imports back as a proposal: for themes, operations the project's own
  functions apply to the same tree the kit computed; for triage, decisions
  accepted into ``keywords.csv``.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sysconfig
import textwrap
import venv
import zipfile
from pathlib import Path

import pytest
from _app_helpers import TOKEN, Client

from cartolex.app import AppSettings, create_app
from cartolex.cli import main as cli
from cartolex.demo import generate
from cartolex.demo.project import write_project

pytestmark = pytest.mark.models("en", "fr")

#: cartolex's dependencies a code sandbox does not have: the session must not need them.
BLOCKED = (
    "adjustText",
    "fastapi",
    "httpx",
    "langdetect",
    "llvmlite",
    "mistralai",
    "multipart",
    "numba",
    "openTSNE",
    "pdfminer",
    "pyarrow",
    "pydantic",
    "pydantic_core",
    "pynndescent",
    "pypdf",
    "python_multipart",
    "requests",
    "spacy",
    "starlette",
    "thinc",
    "umap",
    "uvicorn",
)


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    """The XS demo world, built up to its two-level theme proposal."""
    root = tmp_path_factory.mktemp("copilot") / "project"
    write_project(generate("XS", 0), root).close()
    assert cli(["params", str(root), "--set", "pinned_year=2026"]) == 0
    assert cli(["params", str(root), "--set", "themes.group.level_sizes=[4,12]"]) == 0
    assert cli(["build", str(root), "--only", "themes.group"]) == 0
    return root


@pytest.fixture(scope="module")
def client(project, tmp_path_factory) -> Client:
    app = create_app(
        AppSettings(
            project=project,
            launch_token=TOKEN,
            data_dir=tmp_path_factory.mktemp("copilot-app"),
            build_budget_mb=1e9,
            build_year=2026,
        )
    )
    return Client(app)


#: The themes bundle of the tree being edited (here: none sent, so the saved one or the proposal).
THEMES = ("/api/themes/copilot/export", {"language": "fr"})


def _bundle(client: Client, url: str | tuple[str, dict]) -> dict[str, bytes]:
    r = client.post(url[0], json=url[1]) if isinstance(url, tuple) else client.get(url)
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def _texts(files: dict[str, bytes]) -> str:
    """Every readable member (the arrays hold numbers only; the wheel holds the code)."""
    return "\n".join(
        data.decode("utf-8") for name, data in files.items() if not name.endswith((".npz", ".whl"))
    ).casefold()


def test_no_bundle_holds_a_name_an_identifier_an_organisation_or_a_text(client):
    world = generate("XS", 0)
    themes = _texts(_bundle(client, THEMES))
    triage = _texts(_bundle(client, "/api/keywords/copilot/export?scope=both"))
    audit = _texts(_bundle(client, "/api/keywords/copilot/export?scope=both&usage_lines=true"))
    assert "[name]" in audit or "…" in audit  # the excerpts are there, masked
    for text in (themes, triage, audit):
        for p in world.people:
            for value in (
                p.last_name,
                f"{p.first_name} {p.last_name}",
                p.person_id,
                p.orcid,
                p.openalex_id,
                p.idhal,
            ):
                if value:
                    assert not re.search(
                        r"(?<!\w)" + re.escape(value.casefold()) + r"(?!\w)", text
                    ), value
        for g in world.groups:
            for value in (g.acronym, g.name, g.institution):
                assert value.casefold() not in text, value
        for w in world.works:
            assert not w.doi or w.doi.casefold() not in text
    for text in (themes, triage):
        for w in world.works:
            sentences = [
                s for s in re.split(r"(?<=[.!?])\s+", f"{w.title}. {w.abstract}") if len(s) > 60
            ]
            for s in sentences:
                assert s.casefold() not in text, s


def _sandbox(tmp: Path) -> Path:
    """A fresh virtual environment that sees only the preinstalled libraries, offline."""
    env_dir = tmp / "sandbox"
    venv.create(env_dir, with_pip=False, symlinks=os.name != "nt")
    python = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = Path(
        subprocess.run(
            [str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    (site / "_offline.py").write_text(
        textwrap.dedent(
            f"""
            import socket, sys
            BLOCKED = {BLOCKED!r}
            class Blocker:
                def find_spec(self, name, path=None, target=None):
                    if name.split(".")[0] in BLOCKED:
                        raise ModuleNotFoundError(f"No module named {{name!r}}", name=name)
                    return None
            sys.meta_path.insert(0, Blocker())
            def refuse(*args, **kwargs):
                raise OSError("no network in the sandbox")
            socket.socket.connect = refuse
            socket.create_connection = refuse
            """
        ),
        encoding="utf-8",
    )
    # The libraries of this interpreter (a sandbox has them preinstalled), never cartolex.
    libs = {sysconfig.get_paths()["purelib"], sysconfig.get_paths()["platlib"]}
    (site / "_preinstalled.pth").write_text(
        "\n".join(sorted(libs)) + "\nimport _offline\n", encoding="utf-8"
    )
    return python


def _run(python: Path, folder: Path, code: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["MPLCONFIGDIR"] = str(folder / ".mpl")
    out = subprocess.run(
        [str(python), "-c", textwrap.dedent(code)],
        cwd=folder,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    return out.stdout


def _unpack(client: Client, url: str | tuple[str, dict], folder: Path) -> Path:
    for name, data in _bundle(client, url).items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
    return folder


BOOT = """
    import json, sys, subprocess
    boot = subprocess.run([sys.executable, "setup/bootstrap.py"], capture_output=True, text=True)
    assert boot.returncode == 0, boot.stdout + boot.stderr
    exec(next(l for l in boot.stdout.splitlines() if l.startswith("import sys")))
    from cartolex.copilot import open_bundle
"""


THEMES_SESSION = """
    import json, sys
    for name in ("pydantic", "spacy", "umap"):
        try:
            __import__(name)
        except ImportError:
            continue
        raise SystemExit(f"{name} should not be importable before the bootstrap")
    import subprocess
    boot = subprocess.run([sys.executable, "setup/bootstrap.py"], capture_output=True, text=True)
    assert boot.returncode == 0, boot.stdout + boot.stderr
    line = next(l for l in boot.stdout.splitlines() if l.startswith("import sys"))
    exec(line)
    import cartolex
    from cartolex.copilot import open_bundle
    from cartolex.copilot.session import CheckpointNeeded
    assert "site" in cartolex.__file__ and "setup" in cartolex.__file__, cartolex.__file__
    s = open_bundle(".")
    s.summary(); s.outline(); s.measure(); s.borderline(n=5)
    other = s.regroup([3, 8])
    s.compare(s.tree, other)
    s.stability([3, 8], draws=1)
    try:
        s.adopt(other, "clearer themes")
    except CheckpointNeeded:
        pass
    else:
        raise SystemExit("a restructuring needs the curator's agreement")
    s.adopt(other, "clearer themes", curator_agreed=True)
    tops = [n["id"] for n in s.tree["nodes"] if n["parent"] is None]
    s.rename(tops[0], "Renamed theme", "its keywords share this")
    print(json.dumps(s.timings))
"""

#: The next step, in a fresh process: the session as the last one left it.
THEMES_NEXT_STEP = (
    BOOT
    + """
    from cartolex.copilot import load
    s = load(".")
    assert [c["kind"] for c in s.changes] == ["restructure", "rename"], s.changes
    s.outline(); s.levels(); s.suggest(list(s.tree["keywords"])[:1])
    tops = [n["id"] for n in s.tree["nodes"] if n["parent"] is None]
    kids = [n["id"] for n in s.tree["nodes"] if n["parent"] == tops[1]]
    if len(kids) > 1:
        s.merge(kids[1], kids[0], "one theme")
    own = s.keywords(kids[0], own=True)
    s.split(kids[0], own[:1], "A part", "a distinct group")
    s.set_aside(own[-1:], "too generic")
    s.draw_map(); s.draw_treemap()
    s.write_result("notes", curator_agreed=True)
"""
)


#: The first conversation: groups read and decided in lines, a rule, then a partial result.
TRIAGE_SESSION = (
    BOOT
    + """
    s = open_bundle(".")
    s.summary(); s.budget(); s.table("check"); s.pairs()
    batch = s.next_batch(2)
    g1, g2 = [l.split(" ")[0] for l in batch.splitlines() if l.startswith("g")]
    unseen = next(g.id for g in s.groups if g.id not in (g1, g2))
    out = s.apply(f"{g1} C ; kept\\n{g1}.1 G!\\n{unseen} G\\nnonsense")
    assert "not shown yet" in out and "not understood" in out, out
    assert s.next_batch(1).split(" ")[0] not in (g1, g2)  # a group is shown once
    cov = s.coverage()["all"]
    assert cov["decided_by_group"] == len(s.group(g1).members) - 1 and cov["decided_by_term"] == 1
    s.add_rule("research discourse: always excluded, sure")
    s.write_result("first half", partial=True)
"""
)

#: The next conversation takes the work up from result/ and hands back.
TRIAGE_RESUMED = (
    BOOT
    + """
    s = open_bundle(".")
    print(s.resume())
    assert s.rules and len(s.decisions) >= 2
    en = next(i for i in s.items if i["lang"] == "en" and (i["term"], "en") not in s.decisions)
    fr = next(i for i in s.items if i["lang"] == "fr")
    s.merge(fr["term"], "fr", en["term"], "its translation")
    s.write_result("notes", curator_agreed=True, read_lightly="the kept band")
"""
)


def test_a_whole_session_runs_offline_and_its_result_imports_as_a_proposal(client, tmp_path):
    python = _sandbox(tmp_path)
    folder = _unpack(client, THEMES, tmp_path / "themes")
    _run(python, folder, THEMES_SESSION)
    _run(python, folder, THEMES_NEXT_STEP)
    result = json.loads((folder / "result/result.json").read_text(encoding="utf-8"))
    assert (
        result["format"] == "cartolex-copilot-result/1"
        and result["changes"][0]["kind"] == "restructure"
    )
    r = client.post("/api/themes/copilot/import", json={"result": result})
    assert r.status_code == 200, r.text
    proposal = r.json()
    # The project's own operations give the tree the kit computed.
    assert proposal["matches"] is True
    assert proposal["applicable"] == len(proposal["items"]) == len(result["changes"])
    ops = [op for item in proposal["items"] for op in item["ops"]]
    tree = client.get("/api/themes").json()["tree"]
    applied = client.post("/api/themes/ops", json={"tree": tree, "ops": ops})
    assert applied.status_code == 200, applied.text

    folder = _unpack(client, "/api/keywords/copilot/export?scope=both", tmp_path / "triage")
    _run(python, folder, TRIAGE_SESSION)
    partial = json.loads((folder / "result/result.json").read_text(encoding="utf-8"))
    assert partial["partial"] is True and partial["rules"]
    assert (folder / "result/decisions.jsonl").is_file()
    _run(python, folder, TRIAGE_RESUMED)
    result = json.loads((folder / "result/result.json").read_text(encoding="utf-8"))
    assert result["coverage"]["all"]["decided_unread"] == 1  # the merge: never shown
    # The two results merge (the later wins), with the kit's counts and caveats.
    r = client.post("/api/keywords/copilot/import", json={"results": [result, partial]})
    assert r.status_code == 200, r.text
    proposal = r.json()
    assert proposal["merged"] == 2 and proposal["caveats"]["read_lightly"] == "the kept band"
    assert {i["proposed"] for i in proposal["items"]} == {"keep", "exclude", "merge"}
    assert any(i["by"] == "group" for i in proposal["items"])
    accepted = client.post(
        f"/api/ai/proposals/{proposal['id']}/accept",
        json={"all": True},
        headers={"If-Match": f'"{proposal["keywords_version"]}"'},
    )
    assert accepted.status_code == 200 and accepted.json()["accepted"] == len(proposal["items"])
    decided = client.get("/api/keywords?route=ai-copilot").json()
    assert decided["total"] == len(proposal["items"])
    # The curator's standing rule is kept, and the next bundle carries it.
    # The curator's notes: written in the project beside the rules, carried by every bundle.
    got = client.get("/api/settings/curation")
    assert got.json()["rules"][0]["task"] == "triage"
    put = client.put(
        "/api/settings/curation",
        json={"notes": "Keep the two teams' wave terms together."},
        headers={"If-Match": got.headers["ETag"]},
    )
    assert put.status_code == 200 and put.json()["rules"] == got.json()["rules"]
    again = _bundle(client, "/api/keywords/copilot/export?scope=check")
    readme = again["README_FIRST.md"].decode()
    assert "research discourse: always excluded" in readme
    assert "Keep the two teams' wave terms together." in readme
    assert "Keep the two teams" in _bundle(client, THEMES)["GUIDE.md"].decode()
    bad = client.post("/api/keywords/copilot/import", json={"result": {"format": "other"}})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_copilot_result"


def test_twins_are_found_by_their_words_not_by_shared_users(client, tmp_path):
    """A term and its translation pair up; terms that merely share their few users do not
    (co-usage alone scored nearly every rare pair 1.0)."""
    import numpy as np
    from scipy import sparse

    from cartolex.copilot.sorting import twin_pairs
    from cartolex.copilot.triage import TriageSession
    from cartolex.demo.vocabulary import THEMES

    folder = _unpack(client, "/api/keywords/copilot/export?scope=all", tmp_path / "pairs")
    s = TriageSession(folder)
    found = s.pairs(n=300, detail=True)
    assert len(found) >= 5 and all(p["score"] >= 0.75 for p in found)
    truth = {t.fr.casefold(): t.en.casefold() for th in THEMES for t in th.terms}
    for p in found:
        if p["a"].casefold() in truth:
            assert truth[p["a"].casefold()] == p["b"].casefold(), p
    fr = [i for i, it in enumerate(s.items) if it["lang"] == "fr"]
    en = [i for i, it in enumerate(s.items) if it["lang"] == "en"]
    same_users = int(((s._V[fr] @ s._V[en].T).toarray() >= 0.99).sum())
    assert same_users > 3 * len(found)  # identical users are common; they make no pair
    # Four candidates: two twins used by different people, two strangers used by the same one.
    items = [
        {"term": "diversité des cryptophytes", "lang": "fr", "people": 2},
        {"term": "cryptophyte diversity", "lang": "en", "people": 2},
        {"term": "gestion des ports", "lang": "fr", "people": 2},
        {"term": "reef fish mortality", "lang": "en", "people": 2},
    ]
    V = sparse.csr_matrix(np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [0, 0, 1]], dtype=float))
    pairs = twin_pairs(items, V, "en")
    assert [(p["a"], p["b"]) for p in pairs] == [
        ("diversité des cryptophytes", "cryptophyte diversity")
    ]


def test_the_pre_sort_flags_patterns_keeps_formulas_whole_and_groups_families():
    from cartolex.copilot.sorting import flag, head_word, is_formula

    assert flag("further work", "en") == ("pattern", "discourse")
    assert flag("rôle de l'étude", "fr") == ("pattern", "discourse")
    assert flag("evolution of the", "en") == ("pattern", "edge")
    assert flag("table 2", "en") == ("pattern", "number")
    assert flag("coastal sediment budget", "en", people=1, texts=1) == ("specific", "one text")
    assert flag("sea level rise", "en", people=9, texts=12) == ("", "")
    assert is_formula("CO2") and is_formula("CO") and not is_formula("Alexandrium")
    assert head_word("binding assays", "en") == head_word("cell viability assay", "en")
    assert head_word("érosion des plages", "fr") == head_word("érosion côtière", "fr")


def test_a_restructuring_of_more_operations_than_a_request_takes_comes_as_its_tree(client):
    """A restructuring's operations can outnumber what ``POST /api/themes/ops`` takes (500):
    the proposal carries the tree it gives, which the editor puts in place as one step."""
    tree = client.get("/api/themes").json()["tree"]
    top = next(n["id"] for n in tree["nodes"] if n["parent"] is None)
    keywords = sorted(tree["keywords"])
    ops = [{"op": "create_node", "parent": None, "names": {"en": "All of it"}, "node_id": "ai1"}]
    ops += [{"op": "move_keywords", "keywords": [k], "node_id": "ai1"} for k in keywords]
    while len(ops) <= 520:  # harmless steps, as a large restructuring has many
        ops += [{"op": "set_attribution", "keywords": [k], "levels": None} for k in keywords]
    result = {
        "format": "cartolex-copilot-result/1",
        "task": "themes",
        "bundle": "b1",
        "changes": [
            {"kind": "restructure", "ops": ops, "reason": "one theme"},
            {"kind": "rename", "ops": [{"op": "rename_node", "node_id": top,
                                         "names": {"en": "Emptied"}}], "reason": "r"},
        ],
    }  # fmt: skip
    r = client.post("/api/themes/copilot/import", json={"result": result})
    assert r.status_code == 200, r.text
    first, second = r.json()["items"]
    assert not first["refused"] and set(first["tree"]["keywords"].values()) == {"ai1"}
    assert "tree" not in second
    # The rest applies on top of that tree, within one request.
    applied = client.post("/api/themes/ops", json={"tree": first["tree"], "ops": second["ops"]})
    assert applied.status_code == 200, applied.text


def test_the_grouping_settings_travel_into_the_bundle_and_the_kit_combs_with_them(tmp_path):
    """A pinned θ of the project's grouping is in the bundle's context, and the session's
    comb on the current tree reads it (not the kit's default calibration)."""
    from cartolex.copilot import open_bundle
    from cartolex.lexicon.theme_comb import CombOptions, tree_levels

    root = tmp_path / "project"
    write_project(generate("XS", 0), root).close()
    for setting in ("pinned_year=2026", "themes.group.level_sizes=[4,12]"):
        assert cli(["params", str(root), "--set", setting]) == 0
    assert cli(["params", str(root), "--set", "themes.group.comb_theta=0.15"]) == 0
    assert cli(["build", str(root), "--only", "themes.group"]) == 0
    app = create_app(
        AppSettings(
            project=root,
            launch_token=TOKEN,
            data_dir=tmp_path / "app",
            build_budget_mb=1e9,
            build_year=2026,
        )
    )
    folder = _unpack(Client(app), THEMES, tmp_path / "themes")
    app.state.cartolex.shutdown()
    context = json.loads((folder / "data/context.json").read_text(encoding="utf-8"))
    assert context["grouping"]["comb_theta"] == 0.15
    assert context["grouping"]["own_name_floor"] == 0.5  # the others at their defaults
    session = open_bundle(folder)
    assert session.comb.theta == 0.15 and session.D is not None
    theta, found = tree_levels(session.tree, session.terms, session.D, options=session.comb)
    assert theta == 0.15
    assert (
        session.levels(detail=True)
        == [
            {"keyword": x.keyword, "node": x.node, "to": x.to, "share": x.share, "texts": x.texts}
            for x in found
        ][:200]
    )
    calibrated, _ = tree_levels(session.tree, session.terms, session.D, options=CombOptions())
    assert calibrated != 0.15  # the default would have calibrated another θ


def test_a_regrouping_at_another_depth_is_adopted_and_saved_at_that_depth(client, tmp_path):
    """Two levels regrouped into three: adopted as one restructuring, imported in the app as
    one tree step, saved at depth 3; a list change skips what it cannot apply to."""
    from _app_helpers import etag

    from cartolex.copilot import open_bundle

    session = open_bundle(_unpack(client, THEMES, tmp_path / "themes"))
    assert session.tree["depth"] == 2
    other = session.regroup([3, 6, 12])
    assert other["depth"] == 3 and len(other["levels"]) == 3
    assert "levels" in session.compare(session.tree, other)
    session.adopt(other, "three levels read better", curator_agreed=True)
    assert session.tree["depth"] == 3 and len(session.changes) == 1
    assert session.level_sizes() == [3, 6, 12]
    placed = sorted(session.tree["keywords"])
    session.set_aside(placed[:1], "too generic")
    skipped = session.attribution([placed[0], placed[1]], 0, "broad")
    assert list(skipped) == [placed[0]] and "set aside" in skipped[placed[0]]
    assert session.tree["attribution"] == {placed[1]: 0}
    result = json.loads(session.write_result("notes", curator_agreed=True).read_text("utf-8"))
    themes = client.get("/api/themes")
    r = client.post("/api/themes/copilot/import", json={"result": result})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert not any(i["refused"] for i in items) and items[0]["tree"]["depth"] == 3
    tree = items[0]["tree"]
    for item in items[1:]:
        tree = client.post("/api/themes/ops", json={"tree": tree, "ops": item["ops"]}).json()[
            "tree"
        ]
    assert tree["keywords"] == session.tree["keywords"]
    assert tree["levels"] == session.tree["levels"]
    saved = client.put(
        "/api/themes",
        json={"tree": tree, "action": "AI answer"},
        headers={"If-Match": etag(themes)},
    )
    assert saved.status_code == 200, saved.text
    assert client.get("/api/themes").json()["tree"]["depth"] == 3


def test_the_views_show_per_node_stability_coherence_and_the_people_behind_a_nearness(
    client, tmp_path
):
    """Each node's stability and coherence in the detailed outline, the people who use both a
    keyword and a suggested node, and many renames in one change, a bad one skipped."""
    from cartolex.copilot import open_bundle

    session = open_bundle(_unpack(client, THEMES, tmp_path / "themes"))
    assert session.context["space_unit"] == "text"  # refitted on the texts, as the project
    out = session.stability(draws=1, detail=True)
    nodes = out["nodes"]
    assert len(nodes) == len(session.tree["nodes"])
    assert [x["jaccard_mean"] for x in nodes] == sorted(x["jaccard_mean"] for x in nodes)
    assert all(0 <= x["jaccard_lowest"] <= x["jaccard_mean"] <= 1 for x in nodes)
    text = session.outline(detail=True)
    assert "coherence" in text and "stability" in text
    keyword = next(iter(session.tree["keywords"]))
    place = session.suggest(keyword)[keyword][0]
    assert place["shared_people"] == session.shared_people(
        keyword, session.keywords(place["node"], own=True)
    )
    assert "other_people" in session.borderline(n=1)[0]
    tops = [n["id"] for n in session.tree["nodes"] if n["parent"] is None]
    skipped = session.rename_many({tops[0]: "First", "nope": "Nothing", tops[1]: "Second"}, "r")
    assert list(skipped) == ["nope"] and len(session.changes) == 1
    assert (session.name(tops[0]), session.name(tops[1])) == ("First", "Second")
