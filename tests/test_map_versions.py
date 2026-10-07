# SPDX-License-Identifier: MIT
"""Several built map versions, flat or in space.

- ``maps.json``: a version is flat (2) or in space (3, the UMAP method only); versions
  marked ``built`` are drawn with the pinned one;
- placing on a map in space gives three coordinates;
- a build of the map draws the pinned version where it always was (its files unchanged)
  and each other built version in ``versions/<id>/``, with the time windows and the
  projected people placed on it;
- the atlas serves a version (``version=``), with ``z`` on a map in space;
- a site carries several layouts, aligned to its core's rows.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from _app_helpers import TOKEN, Client, etag
from pydantic import ValidationError

from cartolex.atlas.placement import MapAnchors, place
from cartolex.project.maps import add_version, built_versions, pin, set_built, try_another
from cartolex.project.models import MapLayout, MapsFile

# ── maps.json ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("method", ["tsne", "tree"])
def test_only_umap_draws_a_map_in_space(method):
    assert MapLayout(method="umap", dimensions=3).dimensions == 3
    with pytest.raises(ValidationError):
        MapLayout(method=method, dimensions=3)
    with pytest.raises(ValidationError):  # a UMAP recipe that draws flat maps
        MapLayout(method="umap", params={"layout": "tsne_anchored"}, dimensions=3)


def test_built_versions_are_the_pinned_first_then_those_marked():
    maps, v1 = add_version(MapsFile(), seed=0)
    maps, v2 = try_another(maps, seed=1, dimensions=3, built=True)
    maps, v3 = try_another(maps, seed=2)
    assert [v.layout.dimensions for v in maps.versions] == [2, 3, 2]
    assert [v.id for v in built_versions(maps)] == [v1, v2]
    maps = set_built(maps, v3, True)
    assert [v.id for v in built_versions(maps)] == [v1, v2, v3]
    maps = pin(set_built(maps, v2, False), v3)
    assert [v.id for v in built_versions(maps)] == [v3]  # the pinned one is always built
    # another method keeps the pinned version's dimensions only when it can draw them
    maps = pin(maps, v2)
    assert try_another(maps, seed=3)[0].versions[-1].layout.dimensions == 3
    assert try_another(maps, seed=3, method="tsne")[0].versions[-1].layout.dimensions == 2


# ── placing in space ─────────────────────────────────────────────────────────


def test_points_are_placed_in_as_many_dimensions_as_the_map():
    rng = np.random.default_rng(0)
    vectors = rng.normal(size=(40, 6))
    xyz = rng.normal(size=(40, 3))
    placed = place(vectors[:5] + 0.01, vectors, xyz).xy
    assert placed.shape == (5, 3)
    flat = place(vectors[:5] + 0.01, vectors, xyz[:, :2]).xy
    assert np.array_equal(flat, place(vectors[:5] + 0.01, vectors, xyz[:, :2].copy()).xy)
    assert MapAnchors(vectors, xyz).place(np.zeros((0, 6))).shape == (0, 3)
    assert MapAnchors(vectors, xyz[:, :2]).place(np.zeros((0, 6))).shape == (0, 2)


# ── a project with a map in space beside the pinned flat one ─────────────────

#: The files of the pinned version, which another built version must leave as they were.
PINNED = {
    "map.layout": (
        "umap_individuals.csv",
        "umap_terms.csv",
        "umap_labs.csv",
        "umap_diagnostics.json",
        "themes_applied.json",
        "models/embeddings.npz",
    ),
    "map.trajectories": ("umap_trajectories.csv", "trajectory_windows.json"),
    "overlays.position": ("applicants/positions.json",),
}


def _pinned_files(root: Path) -> dict[str, bytes]:
    derived = root / "derived"
    return {
        f"{s}/{n}": (derived / s / n).read_bytes() for s, names in PINNED.items() for n in names
    }


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> tuple[Path, dict[str, bytes]]:
    """The XS world built with its pinned flat map, then with a map in space built too."""
    from cartolex.cli import main as cli
    from cartolex.demo import generate
    from cartolex.demo.project import write_project

    root = tmp_path_factory.mktemp("versions") / "xs"
    write_project(generate("XS", 0), root).close()
    assert cli(["params", str(root), "--set", "pinned_year=2026"]) == 0
    assert cli(["build", str(root)]) == 0
    before = _pinned_files(root)
    assert cli(["versions", str(root), "--try-another", "--seed", "5", "--method", "tsne",
                "--dimensions", "3"]) == 2  # fmt: skip
    assert cli(["versions", str(root), "--try-another", "--seed", "5", "--dimensions", "3",
                "--built"]) == 0  # fmt: skip
    assert cli(["build", str(root)]) == 0
    return root, before


#: The tests that build the XS world (its language models).
models = pytest.mark.models("en", "fr")


@pytest.fixture()
def client(built, tmp_path):
    from cartolex.app import AppSettings, create_app

    root = shutil.copytree(built[0], tmp_path / "p", ignore=shutil.ignore_patterns(".lock"))
    app = create_app(AppSettings(project=root, launch_token=TOKEN, data_dir=tmp_path / "app",
                                 build_budget_mb=1e9, build_year=2026))  # fmt: skip
    yield Client(app)
    app.state.cartolex.shutdown()


@models
def test_a_map_in_space_is_built_beside_the_pinned_map_which_does_not_move(built):
    root, before = built
    assert _pinned_files(root) == before
    derived = root / "derived"
    record = json.loads((derived / "map.layout" / "run.json").read_text())
    measured = record["measures"]["versions"]
    assert [(v["id"], v["dimensions"]) for v in measured] == [("v1", 2), ("v2", 3)]
    assert all(0 < v["trustworthiness"] <= 1 and v["seconds"] >= 0 for v in measured)
    people = (derived / "map.layout" / "versions" / "v2" / "umap_individuals.csv").read_text()
    assert people.splitlines()[0].endswith("umap_x,umap_y,umap_z")
    applied = json.loads((derived / "map.layout/versions/v2/themes_applied.json").read_text())
    assert all("z" in n for n in applied["nodes"] if n.get("x") is not None)
    points = (derived / "map.trajectories/versions/v2/umap_trajectories.csv").read_text()
    assert points.splitlines()[0] == "researcher_id,bin_start,bin_end,n_docs,umap_x,umap_y,umap_z"
    windows = json.loads(
        (derived / "map.trajectories/versions/v2/trajectory_windows.json").read_text()
    )
    pinned = json.loads((derived / "map.trajectories/trajectory_windows.json").read_text())
    assert windows.keys() == pinned.keys()
    assert all({"key", "x", "y", "z"} == set(w) for ws in windows.values() for w in ws)
    projected = json.loads((derived / "overlays.position/versions/v2/applicants/positions.json")
                           .read_text())["items"]  # fmt: skip
    assert projected and all({"person_id", "x", "y", "z"} == set(p) for p in projected)


@models
def test_the_atlas_serves_each_built_version(client):
    flat = client.get("/api/atlas").json()
    assert (flat["map_version"], flat["pinned_version"], flat["dimensions"]) == ("v1", "v1", 2)
    assert [(v["id"], v["dimensions"], v["pinned"]) for v in flat["versions"]] == [
        ("v1", 2, True), ("v2", 3, False)]  # fmt: skip
    assert "z" not in flat["people"][0] and "zmin" not in flat["bounds"]
    space = client.get("/api/atlas?version=v2")
    body = space.json()
    assert (body["map_version"], body["dimensions"]) == ("v2", 3)
    assert {"zmin", "zmax"} <= set(body["bounds"])
    for layer in ("people", "keywords", "units", "overlays", "organisations"):
        placed = [i for i in body[layer] if i["x"] is not None]
        assert placed and all(i["z"] is not None for i in placed), layer
    assert [k["term"] for k in body["keywords"]] == [k["term"] for k in flat["keywords"]]
    assert [p["person_id"] for p in body["people"]] == [p["person_id"] for p in flat["people"]]
    assert space.headers["ETag"] != client.get("/api/atlas").headers["ETag"]
    texts = client.get("/api/atlas/texts?version=v2").json()
    assert len(texts["z"]) == len(texts["x"]) > 0
    windows = client.get("/api/atlas/windows?version=v2").json()
    assert len(windows["z"]) == len(windows["x"]) > 0
    assert "z" not in client.get("/api/atlas/windows").json()
    missing = client.get("/api/atlas?version=v9")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "map_version_not_built"
    based = client.get("/api/atlas?version=v2&base=elsewhere")
    assert based.status_code == 409 and based.json()["error"]["code"] == "base_needs_2d"


@models
def test_map_versions_are_marked_built_and_3d_is_refused_to_flat_methods(client):
    versions = client.get("/api/map/versions")
    assert versions.json()["dimensions"] == {"umap": [2, 3], "tsne": [2], "tree": [2]}
    refused = client.post("/api/map/versions", json={"action": "try", "method": "tree",
                          "dimensions": 3}, headers={"If-Match": etag(versions)})  # fmt: skip
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "layout_dimensions_unsupported"
    unbuilt = client.post("/api/map/versions", json={"action": "build", "version": "v2",
                          "built": False}, headers={"If-Match": etag(versions)})  # fmt: skip
    assert {v["id"]: v["built"] for v in unbuilt.json()["versions"]}["v2"] is False
    # each version says whether the last build drew it (still v2's until the next build),
    # and how many of each person's nearest stay nearest on it
    listed = {v["id"]: v for v in unbuilt.json()["versions"]}
    assert listed["v2"]["ready"] and listed["v2"]["measure"]["dimensions"] == 3
    assert all(0 < listed[v]["measure"]["trustworthiness"] <= 1 for v in ("v1", "v2"))


@models
def test_a_site_carries_several_layouts_aligned_to_its_core(built, tmp_path):
    from cartolex.project.project import Project
    from cartolex.site.builder import SiteOptions, build_site

    root = shutil.copytree(built[0], tmp_path / "p", ignore=shutil.ignore_patterns(".lock"))
    project = Project.open(root, write=True)
    try:
        named = {"names": "names", "names_projected": "names"}  # rows in a fixed order
        both = build_site(project, SiteOptions.of(named))
        space = build_site(project, SiteOptions.of({**named, "versions": ["v2"]}))
    finally:
        project.close()
    sites = root / "outputs" / "sites"

    def read(build: dict, name: str) -> dict:
        text = (sites / build["id"] / "data" / name).read_text(encoding="utf-8")
        return json.loads(text.split("] = ", 1)[1].rstrip().rstrip(";"))

    assert both["versions"] == ["v1", "v2"] and space["versions"] == ["v2"]
    assert "data/layout-v2.js" in both["files"] and "data/layout-v1.js" not in both["files"]
    core, layout, alone = read(both, "core.js"), read(both, "layout-v2.js"), read(space, "core.js")
    assert core["dimensions"] == 2 and alone["dimensions"] == layout["dimensions"] == 3
    assert [v["id"] for v in core["versions"]] == ["v1", "v2"]
    for part in ("people", "keywords", "orgs", "projected"):
        assert core[part][next(iter(core[part]))] == alone[part][next(iter(alone[part]))], part
        for axis in ("x", "y", "z"):  # the same rows (names: a fixed order) on either core
            assert layout[part][axis] == alone[part][axis], (part, axis)
    assert layout["nodes"]["z"] == [n["z"] for n in alone["nodes"]]
    assert layout["bounds"] == alone["bounds"]


@models
def test_a_project_whose_pinned_map_is_in_space_is_refused_as_a_base(built, client, tmp_path):
    from cartolex.cli import main as cli

    other = shutil.copytree(built[0], tmp_path / "other", ignore=shutil.ignore_patterns(".lock"))
    assert cli(["versions", str(other), "--pin", "v2"]) == 0
    assert cli(["build", str(other)]) == 0
    bases = client.get("/api/map/bases")
    refused = client.post(
        "/api/map/bases", json={"folder": str(other)}, headers={"If-Match": etag(bases)}
    )
    assert refused.status_code == 409
    error = refused.json()["error"]
    assert error["code"] == "base_needs_2d_source" and error["params"]["version"] == "v2"
    assert not (Path(client.app.state.cartolex.settings.project) / "sources" / "bases").exists()


# ── an unchanged version is kept, not drawn again ────────────────────────────

PLACED = ("map.layout", "map.trajectories", "overlays.position")


def _placed_files(root: Path) -> dict[str, bytes]:
    derived = root / "derived"
    return {
        p.relative_to(derived).as_posix(): p.read_bytes()
        for s in PLACED
        for p in (derived / s).rglob("*")
        if p.is_file() and p.name != "run.json"
    }


def _reused(root: Path, stage: str) -> dict[str, bool]:
    record = json.loads((root / "derived" / stage / "run.json").read_text())
    return {v["id"]: v["reused"] for v in record["measures"]["versions"]}


@models
def test_an_unchanged_version_is_kept_and_kept_files_are_the_computed_ones(built, tmp_path):
    from cartolex.cli import main as cli

    root, _ = built
    # the second build of the fixture kept the pinned map and drew the new one
    assert _reused(root, "map.layout") == {"v1": True, "v2": False}
    assert _reused(root, "map.trajectories") == {"v1": False, "v2": False}
    copy = shutil.copytree(root, tmp_path / "p", ignore=shutil.ignore_patterns(".lock"))
    kept = _placed_files(copy)

    # a version added but not built, a note: nothing is drawn again, every stage kept whole
    assert cli(["versions", str(copy), "--try-another", "--seed", "9"]) == 0
    assert cli(["build", str(copy)]) == 0
    for stage in PLACED:
        assert _reused(copy, stage) == {"v1": True, "v2": True}, stage
    assert _placed_files(copy) == kept

    # without the keys of the last generation, every version is computed: the same bytes
    for stage in PLACED:
        record = copy / "derived" / stage / "run.json"
        doc = json.loads(record.read_text())
        for v in doc["measures"]["versions"]:
            v.pop("key")
        record.write_text(json.dumps(doc))
    assert cli(["build", str(copy), "--force", "map.layout"]) == 0
    assert _reused(copy, "map.layout") == {"v1": False, "v2": False}
    assert _placed_files(copy) == kept

    # a parameter the layout reads: drawn again
    assert cli(["params", str(copy), "--set", "map.layout.neighbours=6"]) == 0
    assert cli(["build", str(copy)]) == 0
    assert _reused(copy, "map.layout") == {"v1": False, "v2": False}
