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
    kids = [n["id"] for n in s.tree["nodes"] if n["parent"] == tops[1]]
    if len(kids) > 1:
        s.merge(kids[1], kids[0], "one theme")
    own = s.keywords(kids[0], own=True)
    s.split(kids[0], own[:1], "A part", "a distinct group")
    s.set_aside(own[-1:], "too generic")
    s.draw_map(); s.draw_treemap()
    s.write_result("notes", curator_agreed=True)
    print(json.dumps(s.timings))
"""

TRIAGE_SESSION = """
    import json, sys, subprocess
    boot = subprocess.run([sys.executable, "setup/bootstrap.py"], capture_output=True, text=True)
    assert boot.returncode == 0, boot.stdout + boot.stderr
    exec(next(l for l in boot.stdout.splitlines() if l.startswith("import sys")))
    from cartolex.copilot import open_bundle
    s = open_bundle(".")
    s.summary(); s.table("check"); s.pairs(min_people=1); s.neighbours(s.items[0]["term"], s.items[0]["lang"])
    en = next(i for i in s.items if i["lang"] == "en")
    fr = next(i for i in s.items if i["lang"] == "fr")
    s.keep(en["term"], "en", "a concept of the field")
    s.exclude(s.items[-1]["term"], s.items[-1]["lang"], "too generic")
    s.merge(fr["term"], "fr", en["term"], "its translation")
    s.write_result("notes", curator_agreed=True)
    print(json.dumps(s.timings))
"""


def test_a_whole_session_runs_offline_and_its_result_imports_as_a_proposal(client, tmp_path):
    python = _sandbox(tmp_path)
    folder = _unpack(client, THEMES, tmp_path / "themes")
    _run(python, folder, THEMES_SESSION)
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
    result = json.loads((folder / "result/result.json").read_text(encoding="utf-8"))
    r = client.post("/api/keywords/copilot/import", json={"result": result})
    assert r.status_code == 200, r.text
    proposal = r.json()
    assert [i["proposed"] for i in proposal["items"]] == ["keep", "exclude", "merge"]
    accepted = client.post(
        f"/api/handoff/proposals/{proposal['id']}/accept",
        json={"all": True},
        headers={"If-Match": f'"{proposal["keywords_version"]}"'},
    )
    assert accepted.status_code == 200 and accepted.json()["accepted"] == 3
    decided = client.get("/api/keywords?route=ai-copilot").json()
    assert decided["total"] == 3
    bad = client.post("/api/keywords/copilot/import", json={"result": {"format": "other"}})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_copilot_result"
