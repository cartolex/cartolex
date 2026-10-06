# SPDX-License-Identifier: MIT
"""The offline site: what it carries (never a name when pseudonymised, never a private part),
builds that never overwrite each other and go stale, and the share routes."""

from __future__ import annotations

import io
import json
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from _app_helpers import TOKEN, Client

from cartolex.app import AppSettings, create_app
from cartolex.project.project import Project
from cartolex.project.tables import PRIVATE_PARTS, read_source_table
from cartolex.site.builder import (
    APP_TOKENS,
    ASSETS,
    CATALOGUES,
    SiteOptions,
    build_site,
    list_builds,
)

pytestmark = pytest.mark.models("en", "fr")

SITE = Path(__file__).resolve().parents[1] / "cartolex" / "site"


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    """The XS demo world, written as a project and built once for the module."""
    from cartolex.cli import main as cli
    from cartolex.demo import generate
    from cartolex.demo.project import write_project

    root = tmp_path_factory.mktemp("site") / "xs"
    write_project(generate("XS", 0), root).close()
    assert cli(["params", str(root), "--set", "pinned_year=2026"]) == 0
    assert cli(["build", str(root)]) == 0
    return root


@pytest.fixture()
def project(built, tmp_path):
    root = shutil.copytree(built, tmp_path / "p", ignore=shutil.ignore_patterns(".lock"))
    p = Project.open(root)
    yield p
    p.close()


def _site_text(folder: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in folder.rglob("*") if p.is_file())


def _people_names(project: Project) -> list[str]:
    table = read_source_table(project.layout.table("people"), "people")
    return sorted({n for n in table.column("last_name").to_pylist() if n and len(n) > 3})


def test_a_pseudonymous_site_carries_no_name_and_no_text(project):
    record = build_site(project, SiteOptions(names=False))
    folder = project.layout.outputs / "sites" / record["id"]
    text = _site_text(folder)
    names = _people_names(project)
    assert names and not [n for n in names if n in text]
    assert not (folder / "data" / "texts").exists() and record["counts"]["texts"] == 0
    # the people's details in parts, loaded with the person
    people = sorted((folder / "data" / "people").glob("*.js"))
    assert people and record["format"] == "cartolex-site/2"
    assert all(f"data/people/{p.name}" in record["files"] for p in people)
    readme = (folder / "README.txt").read_text(encoding="utf-8")
    assert readme.startswith("UNZIP THE WHOLE FOLDER FIRST")
    page = (folder / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)", page) and "style=" not in page
    assert "cx-missing" in page  # shown until the scripts start
    core = (folder / "data" / "core.js").read_text(encoding="utf-8")
    assert '"names":false' in core and "person_id" not in core


def test_the_links_are_the_coauthors_over_the_sites_own_indexes(project):
    from cartolex.app.coauthors import person_graph
    from cartolex.site.data import gather

    named = gather(project, names=True, names_projected=True)
    people, projected = named.core["people"], named.core["projected"]
    order = [*people["name"], *projected["name"]]
    links = named.links["people"]
    assert len(links["ptr"]) == len(order) + 1 and len(links["outside"]) == len(order)
    got = {
        (order[i], order[links["nbr"][k]]): links["cnt"][k]
        for i in range(len(order))
        for k in range(links["ptr"][i], links["ptr"][i + 1])
    }
    # the same pairs as the app's graph, between the people the site carries
    graph = person_graph(project)
    names = {
        r["person_id"]: f"{r['first_name']} {r['last_name']}".strip()
        for r in read_source_table(project.layout.table("people"), "people").to_pylist()
    }
    carried = set(order)
    expected = {}
    for code, pid in enumerate(graph.ids):
        nb, cnt = graph.links(code)
        for j, n in zip(nb.tolist(), cnt.tolist(), strict=True):
            a, b = names[pid], names[graph.ids[j]]
            if a in carried and b in carried:
                expected[(a, b)] = n
    assert got == expected and got
    # projected people the site does not name are left out of the links
    hidden = gather(project, names=False)
    assert len(hidden.links["people"]["ptr"]) == len(hidden.core["people"]["id"]) + 1
    assert "orgs" in named.links and named.counts["coauthor_links"] == len(got) // 2
    # each page reads its own partners from its details: flat pairs (index, works)
    first = named.details["people"][people["id"][0]]["co"]
    lo, hi = links["ptr"][0], links["ptr"][1]
    pairs = zip(links["nbr"][lo:hi], links["cnt"][lo:hi], strict=True)
    assert first == [v for pair in pairs for v in pair]


def test_titles_and_abstracts_never_carry_a_private_part(project):
    parts = read_source_table(project.layout.table("text_parts"), "text_parts").to_pylist()
    private = [p["content"][:60] for p in parts if p["part"] in PRIVATE_PARTS and p["content"]]
    record = build_site(project, SiteOptions(names=True, texts="abstracts"))
    folder = project.layout.outputs / "sites" / record["id"]
    text = _site_text(folder)
    assert record["counts"]["texts"] > 0 and list((folder / "data" / "texts").glob("*.js"))
    assert not [c for c in private if c in text]
    assert any(n in text for n in _people_names(project))  # names were asked for
    script = (folder / "data" / "core.js").read_text(encoding="utf-8")
    core = json.loads(script.split('["core"] = ', 1)[1].rstrip().rstrip(";"))
    projected = core["projected"]
    assert projected["id"] and projected["name"] == [None] * len(
        projected["id"]
    )  # still pseudonyms


def test_projected_names_need_their_own_choice(project):
    from cartolex.site.checks import plan

    codes = {c["code"] for c in plan(project, SiteOptions(names=False))["checks"]}
    assert "projected_names_shown" not in codes
    shown = plan(project, SiteOptions(names=False, names_projected=True))
    assert shown["summary"]["projected"] > 0 and shown["ready"]
    assert "projected_names_shown" in {c["code"] for c in shown["checks"]}


def test_the_plan_says_what_the_texts_add_and_warns_when_it_is_large(project, monkeypatch):
    from cartolex.site import checks
    from cartolex.site.checks import plan

    small = plan(project, SiteOptions(names=False, texts="abstracts"))
    sizes = small["summary"]["text_bytes"]
    assert 0 < sizes["titles"] < sizes["abstracts"]
    assert not {"abstracts_large", "titles_large"} & {c["code"] for c in small["checks"]}
    monkeypatch.setattr(checks, "LARGE_TEXTS_BYTES", sizes["titles"] - 1)
    large = plan(project, SiteOptions(names=False, texts="abstracts"))
    found = next(c for c in large["checks"] if c["code"] == "abstracts_large")
    assert found["level"] == "warning" and found["fix"]["field"] == "texts"
    assert found["params"] == {"size": sizes["abstracts"], "titles": sizes["titles"]}
    titles = plan(project, SiteOptions(names=False, texts="titles"))
    assert "titles_large" in {c["code"] for c in titles["checks"]}
    # the titles as the build writes them: what the plan counted
    record = build_site(project, SiteOptions(names=False, texts="titles"))
    written = sum(n for name, n in record["files"].items() if name.startswith("data/texts/"))
    assert abs(written - sizes["titles"]) < 0.2 * sizes["titles"]


def test_builds_are_never_overwritten_and_go_stale(project):
    at = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
    first = build_site(project, SiteOptions(names=False), now=at)["id"]
    second = build_site(project, SiteOptions(names=False), now=at)["id"]
    assert second == f"{first}-2"
    builds = list_builds(project)
    assert [b["id"] for b in builds] == [second, first]
    assert [b["latest"] for b in builds] == [True, False]
    assert not any(b["stale"] for b in builds)
    params = project.layout.params_json
    doc = json.loads(params.read_text(encoding="utf-8"))
    doc["seed"] = 7
    params.write_text(json.dumps(doc), encoding="utf-8")
    assert all(b["stale"] for b in list_builds(project))


def test_the_share_routes(built, tmp_path):
    root = shutil.copytree(built, tmp_path / "p", ignore=shutil.ignore_patterns(".lock"))
    app = create_app(AppSettings(project=root, launch_token=TOKEN, data_dir=tmp_path / "data",
                                 build_year=2026))  # fmt: skip
    try:
        client = Client(app)
        plan = client.get("/api/share/plan").json()
        assert not plan["ready"] and plan["summary"]["people"] > 0
        assert "names_unanswered" in {c["code"] for c in plan["checks"]}
        refused = client.post("/api/share/builds", json={})
        assert refused.status_code == 422 and refused.json()["error"]["code"] == "names_question"
        started = client.post("/api/share/builds", json={"names": "pseudonyms", "language": "fr"})
        assert started.status_code == 202
        assert client.wait_job(started.json()["job"]["id"])["state"] == "succeeded"
        share = client.get("/api/share").json()
        (item,) = share["items"]
        assert item["latest"] and not item["stale"] and item["names"] is False
        page = client.get(f"/api/share/builds/{item['id']}/site/index.html")
        assert page.status_code == 200 and 'lang="fr"' in page.text
        assert client.get(f"/api/share/builds/{item['id']}/site/../project.json").status_code == 404
        zipped = zipfile.ZipFile(
            io.BytesIO(client.get(f"/api/share/builds/{item['id']}/zip").content)
        )
        assert zipped.namelist()[0].endswith("/README.txt")
        state = {a["id"]: a["state"] for a in client.get("/api/project/state").json()["areas"]}
        assert state["share"] == "up_to_date"
        png = client.get("/api/share/figures/map", params={"width": 400, "height": 300})
        assert png.content.startswith(b"\x89PNG")
        svg = client.get("/api/share/figures/map", params={"format": "svg", "theme": "dark"})
        assert b"<svg" in svg.content
        csv_text = client.get("/api/share/tables/themes.csv").text
        assert csv_text.startswith("id,level,parent,name_")
        job = client.post("/api/share/exports", json={"kind": "project"}).json()["job"]
        assert client.wait_job(job["id"])["state"] == "succeeded"
        (export,) = client.get("/api/share").json()["exports"]
        names = zipfile.ZipFile(
            io.BytesIO(client.get(f"/api/share/exports/{export['name']}").content)
        ).namelist()
        assert "p/project.json" in names and not [
            n for n in names if n.startswith(("p/cache/", "p/outputs/exports/"))
        ]
    finally:
        app.state.cartolex.shutdown()


def test_the_site_keeps_the_apps_tokens_and_complete_catalogues():
    assert (ASSETS / "tokens.css").read_text(encoding="utf-8") == APP_TOKENS.read_text(
        encoding="utf-8"
    ), "cartolex/site/assets/tokens.css drifted from the app's: copy it again"
    catalogues = {
        code: json.loads((CATALOGUES / f"{code}.json").read_text(encoding="utf-8"))
        for code in ("en", "fr", "pt-BR")
    }
    keys = set(catalogues["en"])
    assert all(set(c) == keys for c in catalogues.values())
    source = "\n".join(p.read_text(encoding="utf-8") for p in ASSETS.glob("*.js"))
    used = set(re.findall(r"\bt\('([\w.-]+)'", source))
    used |= {f"{k}.other" for k in re.findall(r"\btn\('([\w.-]+)'", source)}
    assert used and used <= keys, sorted(used - keys)
