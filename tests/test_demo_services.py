# SPDX-License-Identifier: MIT
"""The demo services: a deterministic bibliographic layer and the real APIs' shapes, offline."""

from __future__ import annotations

import csv
import unicodedata

import pytest
import requests

from cartolex.demo import generate
from cartolex.demo.cli import main as demo_main
from cartolex.demo.identifiers import DOI_RE, ORCID_RE
from cartolex.demo.services import DemoServices, Request, StubService, build_bibliography
from cartolex.demo.services.biblio import SPECIALS
from cartolex.demo.writers import world_files


@pytest.fixture(scope="module")
def services():
    with DemoServices(generate("XS", 0)) as svc:
        yield svc


@pytest.fixture()
def demo(services):
    services.faults.clear()
    services.requests.clear()
    return services


def _get(demo, service: str, path: str, **params) -> requests.Response:
    headers = {"Accept": "application/json"}
    return requests.get(f"{demo.url(service)}/{path}", params=params, headers=headers, timeout=5)


# ── the layer ──


@pytest.mark.parametrize(("size", "seed"), [("XS", 0), ("S", 0), ("S", 1)])
def test_every_special_case_exists_and_the_layer_is_deterministic(size, seed) -> None:
    world = generate(size, seed)
    a, b = build_bibliography(world), build_bibliography(generate(size, seed))
    assert set(a.specials) == set(SPECIALS)
    assert len(set(a.specials.values())) == len(SPECIALS)
    assert a.works == b.works and a.authors == b.authors and a.truth == b.truth
    assert a.registry == b.registry and a.specials == b.specials
    other = build_bibliography(world, seed=1)
    assert other.specials != a.specials or other.authors != a.authors


def test_the_world_does_not_move(tmp_path) -> None:
    before = world_files(generate("S", 0))
    world = generate("S", 0)
    with DemoServices(world):
        pass
    assert world_files(world) == before


def test_identifiers_stay_in_the_demo_blocks() -> None:
    bib = build_bibliography(generate("S", 0))
    for rid in bib.authors:
        assert rid.startswith("A999") and len(rid) == 11
    for wid, w in bib.works.items():
        assert wid.startswith("W999") and len(wid) == 11
        assert w.doi is None or w.doi.startswith("10.5555/")
    for iid in bib.institutions:
        assert iid.startswith("I999") and len(iid) == 11
    for orcid in bib.registry:
        assert ORCID_RE.match(orcid)
    world_dois = {w.doi for w in bib.world.works if w.doi}
    for rec in bib.registry.values():
        for w in rec.works:
            assert w.doi in world_dois and DOI_RE.match(w.doi)


def test_the_special_cases_are_what_they_say() -> None:
    bib = build_bibliography(generate("XS", 0))
    world = bib.world
    sp = bib.specials

    split = world.person(sp["split"])
    records = bib.record_ids(split.person_id)
    assert len(records) == 2
    copies = [ids for ids in bib.index_of.values() if len(ids) == 2]
    assert len(copies) == 1
    original, copy = (bib.works[i] for i in copies[0])
    assert original.doi and copy.doi is None and copy.title == original.title

    mixed = world.person(sp["mixed"])
    (record,) = (bib.authors[r] for r in bib.record_ids(mixed.person_id))
    assert record.orcid == mixed.orcid
    foreign = [w for w in record.works if bib.works[w].world_work is None]
    assert foreign, "the mixed record holds works of an outside person"
    assert bib.truth[mixed.person_id].records == (f"orcid:{mixed.orcid}",)

    for name in ("homonym", "trap"):
        person = world.person(sp[name])
        own = bib.authors[person.openalex_id]
        twins = [a for a in bib.authors.values() if a.display_name == own.display_name]
        assert len(twins) == 2
    trap = world.person(sp["trap"])
    lab = bib.lab_of[trap.group]
    twin = next(
        a
        for a in bib.authors.values()
        if a.display_name == bib.authors[trap.openalex_id].display_name and a.person_id is None
    )
    cited = {i for w in twin.works for a in bib.works[w].authorships for i in a.institutions}
    assert cited == {lab}
    own_cited = {
        i
        for w in bib.authors[trap.openalex_id].works
        for a in bib.works[w].authorships
        if a.person_id == trap.person_id
        for i in a.institutions
    }
    assert lab not in own_cited and bib.institutions[lab].parent in own_cited

    compound = bib.truth[sp["compound"]]
    assert "-" in compound.last_name
    shown = bib.authors[world.person(sp["compound"]).openalex_id].display_name
    assert compound.last_name.split("-")[0] in shown and compound.last_name not in shown

    diacritics = bib.truth[sp["diacritics"]]
    shown = bib.authors[world.person(sp["diacritics"]).openalex_id].display_name
    listed = f"{diacritics.first_name} {diacritics.last_name}"
    assert listed != shown
    fold = "".join(c for c in unicodedata.normalize("NFKD", listed) if not unicodedata.combining(c))
    assert fold == shown

    moved = world.person(sp["moved"])
    years = {
        (bib.works[w].year, a.institutions)
        for w in bib.authors[moved.openalex_id].works
        for a in bib.works[w].authorships
        if a.person_id == moved.person_id
    }
    assert {insts for y, insts in years if y < bib.moved_year} != {
        insts for y, insts in years if y >= bib.moved_year
    }

    nobody = sp["none"]
    assert bib.record_ids(nobody) == () and bib.truth[nobody].identity == "none"


def test_outside_co_authors_exist() -> None:
    bib = build_bibliography(generate("S", 0))
    outside = [
        a
        for w in bib.works.values()
        if w.world_work
        for a in w.authorships
        if a.person_id is None and a.author_id
    ]
    assert outside
    assert all(a.author_id in bib.authors for a in outside)


def test_people_rows_are_an_import_list() -> None:
    bib = build_bibliography(generate("XS", 0))
    rows = bib.people_rows()
    assert len(rows) == len(bib.world.people)
    assert set(rows[0]) == {
        "last_name",
        "first_name",
        "lab",
        "institution",
        "orcid",
        "career_stage",
        "role",
        "set",
    }
    assert any(r["orcid"] for r in rows) and any(not r["orcid"] for r in rows)
    assert {r["role"] for r in rows} == {"mapped", "projected"}


def test_the_command_writes_the_people_list(tmp_path, capsys) -> None:
    out = tmp_path / "people.csv"
    assert demo_main(["services", "--size", "XS", "--people-list", str(out), "--list-only"]) == 0
    with open(out, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == len(generate("XS", 0).people)


# ── the APIs ──


def test_openalex_lists_have_the_documented_shape(demo) -> None:
    body = _get(demo, "openalex", "authors", search="Aiko").json()
    assert set(body) == {"meta", "results", "group_by"}
    assert {"count", "per_page", "page"} <= set(body["meta"])
    author = body["results"][0]
    for key in (
        "id",
        "orcid",
        "display_name",
        "display_name_alternatives",
        "works_count",
        "affiliations",
        "last_known_institutions",
        "topics",
        "counts_by_year",
    ):
        assert key in author
    assert author["id"].startswith("https://openalex.org/A999")
    assert _get(demo, "openalex", "authors", per_page=101).status_code == 400
    assert _get(demo, "openalex", "authors", filter="nonsense:1").status_code == 400


def test_openalex_search_is_literal_about_accents(demo) -> None:
    bib = demo.bibliography
    truth = bib.truth[bib.specials["diacritics"]]
    listed = f"{truth.first_name} {truth.last_name}"
    assert _get(demo, "openalex", "authors", search=listed).json()["meta"]["count"] == 0
    folded = "".join(
        c for c in unicodedata.normalize("NFKD", listed) if not unicodedata.combining(c)
    )
    assert _get(demo, "openalex", "authors", search=folded).json()["meta"]["count"] >= 1


def test_openalex_cursor_paging_ends_with_a_null_cursor(demo) -> None:
    author = max(demo.bibliography.authors.values(), key=lambda a: len(a.works))
    seen, cursor = [], "*"
    while True:
        body = _get(
            demo, "openalex", "works", filter=f"author.id:{author.id}", per_page=2, cursor=cursor
        ).json()
        if not body["results"]:
            break
        seen += [w["id"] for w in body["results"]]
        cursor = body["meta"]["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == len(author.works)
    bad = _get(demo, "openalex", "works", filter="author.id:A1", cursor=body["meta"]["next_cursor"])
    assert bad.status_code in (200, 400)


def test_openalex_works_by_doi_in_one_request(demo) -> None:
    dois = [w.doi for w in demo.bibliography.works.values() if w.doi][:50]
    body = _get(demo, "openalex", "works", filter="doi:" + "|".join(dois), per_page=100).json()
    assert body["meta"]["count"] == len(dois)
    work = body["results"][0]
    assert work["doi"].startswith("https://doi.org/10.5555/")
    assert isinstance(work["abstract_inverted_index"], dict)
    assert {"author_position", "author", "institutions", "is_corresponding"} <= set(
        work["authorships"][0]
    )
    too_many = "|".join(f"10.5555/x.{i}" for i in range(101))
    assert _get(demo, "openalex", "works", filter=f"doi:{too_many}").status_code == 400


def test_openalex_single_entities_and_institution_search(demo) -> None:
    bib = demo.bibliography
    rid = next(iter(bib.authors))
    assert _get(demo, "openalex", f"authors/{rid}").json()["id"].endswith(rid)
    assert _get(demo, "openalex", "authors/A9990000000").status_code == 404
    group = bib.world.groups[0]
    found = _get(demo, "openalex", "institutions", search=group.name).json()["results"]
    assert found[0]["id"].endswith(bib.lab_of[group.group_id])
    assert found[0]["associated_institutions"][0]["relationship"] == "parent"


def test_orcid_answers_json_only_when_asked(demo) -> None:
    orcid = next(o for o, r in demo.bibliography.registry.items() if r.works)
    url = f"{demo.url('orcid')}/{orcid}/works"
    assert "xml" in requests.get(url, timeout=5).headers["Content-Type"]
    body = _get(demo, "orcid", f"{orcid}/works").json()
    group = body["group"][0]
    ext = group["external-ids"]["external-id"][0]
    assert ext["external-id-type"] == "doi" and ext["external-id-value"].startswith("10.5555/")
    record = _get(demo, "orcid", f"{orcid}/record").json()
    assert record["orcid-identifier"]["path"] == orcid
    assert record["activities-summary"]["employments"]["affiliation-group"]
    assert _get(demo, "orcid", "0000-0000-0000-0001/works").status_code == 404


def test_every_service_is_served_and_a_stub_answers_501(demo) -> None:
    for name in ("hal", "scielo", "arxiv", "biorxiv", "europepmc", "files"):
        assert _get(demo, name, "anything").status_code == 404
    assert requests.get(f"{demo.base_url}/nothing/here", timeout=5).status_code == 404
    reply = StubService("later").handle(Request("anything", {}, {}))
    assert reply.status == 501


@pytest.mark.parametrize("kind", ["status", "malformed", "drop", "cut_page", "early_end"])
def test_faults_are_injected_as_asked(demo, kind) -> None:
    demo.faults.add(kind, service="openalex", status=429, retry_after="1", times=1)
    params = {"filter": f"author.id:{next(iter(demo.bibliography.authors))}", "cursor": "*"}
    if kind == "drop":
        with pytest.raises(requests.exceptions.ConnectionError):
            _get(demo, "openalex", "works", **params)
    else:
        reply = _get(demo, "openalex", "works", per_page=1, **params)
        if kind == "status":
            assert reply.status_code == 429 and reply.headers["Retry-After"] == "1"
        elif kind == "malformed":
            with pytest.raises(ValueError):
                reply.json()
        elif kind == "cut_page":
            assert reply.json()["results"] == []
        else:
            assert reply.json()["meta"]["next_cursor"] is None
    assert _get(demo, "openalex", "works", per_page=1, **params).status_code == 200
    assert len(demo.requests) == 2


# ── units, several parents, outside works, the large collaboration, the snapshot ──


def test_a_joint_unit_has_two_parents_and_institutions_have_a_demo_ror() -> None:
    bib = build_bibliography(generate("XS", 0))
    joint = bib.institutions[bib.joint_lab]
    assert joint.parent and len(joint.parents) == 2 and joint.lineage == (joint.id, *joint.parents)
    tops = [i for i in bib.institutions.values() if i.parent is None]
    assert tops and all(i.ror and i.ror.startswith("0zz") and len(i.ror) == 9 for i in tops)
    assert all(i.ror is None for i in bib.institutions.values() if i.parent)


def test_outside_co_authors_write_on_other_themes_and_a_collaboration_has_thirty() -> None:
    bib = build_bibliography(generate("S", 0))
    big = bib.works[bib.consortium]
    assert len(big.authorships) == 30 and big.doi.startswith("10.5555/")
    inside = [a for a in big.authorships if a.person_id]
    assert len(inside) == 2 and all(a.author_id in bib.authors for a in big.authorships)
    own = [
        w
        for w in bib.works.values()
        if w.world_work is None and len(w.authorships) == 1 and w.authorships[0].person_id is None
    ]
    outside_ids = {a.author_id for w in own for a in w.authorships}
    joint = {
        a.author_id
        for w in bib.works.values()
        if w.world_work
        for a in w.authorships
        if a.person_id is None
    }
    assert joint and joint <= outside_ids  # every outside co-author also has works of their own


def test_institutions_by_ror_and_by_lineage(demo) -> None:
    bib = demo.bibliography
    joint = bib.institutions[bib.joint_lab]
    second = joint.parents[1]
    below = _get(demo, "openalex", "institutions", filter=f"lineage:{second}").json()
    ids = {r["id"].rsplit("/", 1)[-1] for r in below["results"]}
    assert {second, joint.id} <= ids
    one = _get(demo, "openalex", f"institutions/{joint.id}").json()
    parents = [a["id"].rsplit("/", 1)[-1] for a in one["associated_institutions"]
               if a["relationship"] == "parent"]  # fmt: skip
    assert parents == list(joint.parents)
    top = bib.institutions[joint.parent]
    by_ror = _get(demo, "openalex", f"institutions/ror:{top.ror}").json()
    assert by_ror["id"].endswith(top.id) and by_ror["ror"] == f"https://ror.org/{top.ror}"
    works = _get(
        demo, "openalex", "works", filter=f"authorships.institutions.lineage:{top.id}", per_page=100
    ).json()
    cited = {
        i["id"].rsplit("/", 1)[-1] for w in works["results"] for a in w["authorships"]
        for i in a["institutions"]
    }  # fmt: skip
    assert works["meta"]["count"] > 0 and cited & ({top.id} | {joint.id})


def test_the_mini_snapshot_holds_the_api_records(tmp_path) -> None:
    import gzip
    import json

    from cartolex.demo.services import write_snapshot
    from cartolex.demo.services.openalex import OpenAlexService

    bib = build_bibliography(generate("XS", 0))
    manifest = write_snapshot(bib, tmp_path / "snap", per_part=25)
    service = OpenAlexService(bib)
    seen: dict[str, dict] = {}
    for entity in ("works", "authors", "institutions"):
        folder = tmp_path / "snap" / "data" / "jsonl" / entity
        parts = sorted(folder.glob("updated_date=*/part_*.gz"))
        assert len({p.parent.name for p in parts}) == 2 and len(parts) >= 2
        for path in parts:
            for line in gzip.open(path, "rt", encoding="utf-8"):
                record = json.loads(line)
                seen[record["id"]] = record
        entry = json.loads((folder / "manifest.json").read_text())
        assert entry["entity"] == entity and entry["record_count"] == sum(
            f["meta"]["record_count"] for f in entry["files"]
        )
    for wid, work in service._works.items():
        got = dict(seen["https://openalex.org/" + wid])
        assert got.pop("is_xpac") is False and "has_content" in got
        got.pop("has_content")
        assert got == work
    deleted = gzip.open(tmp_path / "snap/data/jsonl/works/deleted_ids.csv.gz", "rt").read()
    assert deleted.splitlines()[0] == "work_id,deleted_date"
    gone = deleted.splitlines()[1].split(",")[0]
    assert gone in seen and gone.rsplit("/", 1)[-1] not in service._works
    assert manifest["meta"]["record_count"] == len(seen)
    again = tmp_path / "again"
    write_snapshot(bib, again, per_part=25)
    first = sorted((p.relative_to(tmp_path / "snap"), p.read_bytes()) for p in
                   (tmp_path / "snap").rglob("*") if p.is_file())  # fmt: skip
    second = sorted((p.relative_to(again), p.read_bytes()) for p in again.rglob("*") if p.is_file())
    assert first == second


def test_the_layer_strings_are_written_for_the_vocabulary_scan(tmp_path, capsys) -> None:
    out = tmp_path / "world"
    assert demo_main(["create", "--size", "XS", "--out", str(out), "--layer"]) == 0
    strings = (out / "layer" / "strings.txt").read_text(encoding="utf-8").splitlines()
    bib = build_bibliography(generate("XS", 0))
    big = bib.works[bib.consortium]
    assert {a.name for a in big.authorships} <= set(strings)
    assert bib.institutions[bib.joint_lab].name in strings
