# SPDX-License-Identifier: MIT
"""The Lexicon tab: what the last vocabulary build made, its CSV and its word cloud."""

from __future__ import annotations

import pytest
from _app_helpers import TOKEN, Client

from cartolex.app import AppSettings, create_app
from cartolex.cli import main as cli
from cartolex.demo import generate
from cartolex.demo.project import write_project

pytestmark = pytest.mark.models("en", "fr")


@pytest.fixture(scope="module")
def client(tmp_path_factory) -> Client:
    root = tmp_path_factory.mktemp("lexicon") / "project"
    write_project(generate("XS", 0), root).close()
    assert cli(["params", str(root), "--set", "pinned_year=2026"]) == 0
    assert cli(["build", str(root), "--only", "themes.apply"]) == 0
    app = create_app(
        AppSettings(project=root, launch_token=TOKEN, data_dir=tmp_path_factory.mktemp("app"))
    )
    return Client(app)


def test_the_lexicon_lists_every_keyword_of_the_space_with_its_candidates(client):
    first = client.get("/api/keywords/lexicon", params={"limit": 500}).json()
    assert first["total"] == first["keywords"] > 0
    row = first["items"][0]
    assert row["rank"] == 1 and row["people"] > 0 and row["candidates"]
    assert set(row["terms"]) == set(first["languages"])
    # every keyword of the space, and only those
    space = client.get("/api/themes/usage").json()
    terms = space["terms"]
    assert {i["concept"].lower() for i in first["items"]} <= {t.lower() for t in terms}
    by_people = client.get("/api/keywords/lexicon", params={"sort": "-people", "limit": 3})
    people = [i["people"] for i in by_people.json()["items"]]
    assert people == sorted(people, reverse=True)
    csv = client.get("/api/keywords/lexicon/export").text.splitlines()
    assert csv[0].startswith("rank,term_") and len(csv) == first["total"] + 1


def test_the_word_cloud_is_an_svg_with_its_font_and_the_page_colours(client):
    light = client.get("/api/keywords/lexicon/cloud", params={"theme": "light"})
    dark = client.get("/api/keywords/lexicon/cloud", params={"theme": "dark"})
    assert light.headers["content-type"].startswith("image/svg+xml")
    svg = light.text
    assert svg.startswith("<svg") and "viewBox" in svg and "<text" in svg
    assert "@font-face" in svg and "<title>" in svg
    assert light.text != dark.text  # the dark page's hues


def test_the_word_cloud_takes_the_colour_scheme_and_the_overview_shows_it(client):
    hues = ["0a0b0c", "1a1b1c", "2a2b2c", "3a3b3c", "4a4b4c", "5a5b5c"] * 2
    cloud = client.get("/api/keywords/lexicon/cloud", params={"hues": ",".join(hues)})
    assert cloud.status_code == 200
    used = {h for h in hues if f"#{h}" in cloud.text.lower()}
    assert used and "#c25d58" not in cloud.text.lower()  # the scheme's, not the tokens'
    bad = client.get("/api/keywords/lexicon/cloud", params={"hues": "red,blue"})
    assert bad.status_code == 422
    # the overview names the build its cloud is drawn from, once there is one
    lexicon = client.get("/api/overview").json()["lexicon"]
    assert lexicon["run"] == client.get("/api/keywords/lexicon").json()["run"]
