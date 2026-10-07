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
    # the people's details and the keywords' users in parts, loaded when they are shown
    for part in ("people", "keywords"):
        files = sorted((folder / "data" / part).glob("*.js"))
        assert files and all(f"data/{part}/{p.name}" in record["files"] for p in files)
    assert record["format"] == "cartolex-site/3"
    readme = (folder / "README.txt").read_text(encoding="utf-8")
    assert readme.startswith("UNZIP THE WHOLE FOLDER FIRST")
    page = (folder / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)", page) and "style=" not in page
    assert "cx-missing" in page  # shown until the scripts start
    core = (folder / "data" / "core.js").read_text(encoding="utf-8")
    assert '"names":false' in core and "person_id" not in core


def test_the_atlas_data_of_a_site(project):
    """What the site's data source answers from: the bundle as columns (each person's
    themes per level, their organisations), the keywords' users, the vectors."""
    from cartolex.site.data import KEYWORD_USERS, gather

    data = gather(project, names=False)
    people, nodes = data.core["people"], data.core["nodes"]
    assert len(people["shares"]) == len(people["id"]) and data.core["has"]["vectors"]
    top = [n["id"] for n in nodes if n["level"] == 1]
    first = people["shares"][0][0]
    assert first and nodes[first[0]]["id"] in top and 0 < first[1] <= 1000
    assert first[1::2] == sorted(first[1::2], reverse=True)
    assert any(people["orgs"]) and all(isinstance(o, int) for row in people["orgs"] for o in row)
    users = next(iter(data.keywords.values()))
    assert users[0] >= (len(users) - 1) // 2 and (len(users) - 1) // 2 <= KEYWORD_USERS
    assert all(0 <= i < len(people["id"]) for i in users[1::2])
    assert all(set(d) <= {"k"} for d in data.people.values())


def test_the_vectors_parts_keep_the_spaces_cosines(project):
    """The people's vectors (« Compare » and the « Distances » page): int8 rows in site
    order, in parts by the people's rule, whose cosines are the space's."""
    import base64

    import numpy as np

    from cartolex.app.space_index import space_run, space_view
    from cartolex.site.data import gather, project_context, vector_parts

    data = gather(project, names=True)
    assert data.core["measure"] == "space"
    v = data.vectors
    assert v.dtype == np.int8 and len(v) == len(data.core["people"]["id"])
    parts = vector_parts(v, 3)
    # site number s<k> is in part (k − 1) mod 3, row (k − 1) // 3
    k = 5
    row = np.frombuffer(base64.b64decode(parts[(k - 1) % 3]), np.int8).reshape(-1, v.shape[1])
    assert (row[(k - 1) // 3] == v[k - 1]).all()
    # the cosines are the space's, to the int8 rounding
    from cartolex.app.routes.atlas import build_bundle, lineage
    from cartolex.app.atlas_layers import map_extras

    ctx = project_context(project)
    bundle = build_bundle(ctx, lineage(ctx))
    view = space_view(ctx, space_run(ctx.layout), bundle, map_extras(ctx, bundle["people"]))
    by_name = {p["name"]: p["person_id"] for p in bundle["people"]}
    names = data.core["people"]["name"]
    z = np.asarray(view.space.vectors)
    a, b = view.row_of[by_name[names[0]]], view.row_of[by_name[names[1]]]
    unit = v[:2].astype(float) / np.linalg.norm(v[:2].astype(float), axis=1, keepdims=True)
    assert abs(float(unit[0] @ unit[1]) - float(z[a] @ z[b])) < 0.02


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
    expected = {}
    for code, pid in enumerate(graph.ids):
        nb, cnt = graph.links(code)
        for j, n in zip(nb.tolist(), cnt.tolist(), strict=True):
            a, b = names[pid], names[graph.ids[j]]
            if a in order and b in order:
                expected[(a, b)] = n
    assert got == expected and got
    assert named.counts["coauthor_links"] == len(got) // 2
    # organisations write together too, one level at a time
    orgs = named.links["orgs"]
    assert len(orgs["ptr"]) == len(named.core["orgs"]["id"]) + 1 and orgs["nbr"]
    levels = named.core["orgs"]["level"]
    assert all(
        levels[i] == levels[orgs["nbr"][k]]
        for i in range(len(levels))
        for k in range(orgs["ptr"][i], orgs["ptr"][i + 1])
    )
    # projected people the site does not name are left out of the links
    hidden = gather(project, names=False)
    assert len(hidden.links["people"]["ptr"]) == len(hidden.core["people"]["id"]) + 1


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


def test_the_plan_estimates_the_atlas_and_warns_when_it_is_large(project, monkeypatch):
    from cartolex.site import checks
    from cartolex.site.checks import plan

    small = plan(project, SiteOptions(names=False))
    weight = small["summary"]["site_bytes"]
    assert weight["atlas"] == weight["core"] + weight["links"] > 0 and weight["links"] > 0
    assert "site_large" not in {c["code"] for c in small["checks"]}
    monkeypatch.setattr(checks, "LARGE_ATLAS_BYTES", weight["atlas"] - 1)
    found = next(
        c for c in plan(project, SiteOptions(names=False))["checks"] if c["code"] == "site_large"
    )
    assert found["level"] == "warning" and found["params"]["size"] == weight["atlas"]
    assert found["params"]["total"] == weight["atlas"] + weight["parts"]


def test_the_home_page_carries_the_lexicons_word_cloud(project, monkeypatch):
    """The app's word cloud of the lexicon, drawn at the build for each look; none without
    a lexicon."""
    from cartolex.app import lexicon_view

    def built(record) -> tuple[Path, str]:
        folder = project.layout.outputs / "sites" / record["id"]
        return folder, (folder / "data" / "core.js").read_text(encoding="utf-8")

    record = build_site(project, SiteOptions(names=False))
    folder, core = built(record)
    light, dark = (
        (folder / "assets" / f"cloud-{look}.svg").read_text(encoding="utf-8")
        for look in ("light", "dark")
    )
    fills = [set(re.findall(r"fill:(#[0-9a-fA-F]{6})", svg)) for svg in (light, dark)]
    assert "<text" in light and "<title>" in light and fills[0] and fills[0] != fills[1]
    assert {"assets/cloud-light.svg", "assets/cloud-dark.svg"} <= set(record["files"])
    assert '"cloud":true' in core
    monkeypatch.setattr(lexicon_view, "word_cloud", lambda *args, **kwargs: None)
    folder, core = built(build_site(project, SiteOptions(names=False)))
    assert not list((folder / "assets").glob("cloud-*")) and '"cloud":false' in core


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
