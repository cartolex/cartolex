# SPDX-License-Identifier: MIT
"""People import: mappings, pasted lists, duplicates, folders of documents, an existing corpus."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from cartolex.collect.decisions import read_people
from cartolex.collect.names import name_similarity, split_full_name, variants
from cartolex.collect.people_import import (
    ImportMapping,
    confirm_merge,
    find_duplicates,
    import_corpus,
    import_folder,
    import_people,
    normalise_openalex_author,
    normalise_orcid,
    propose_mapping,
    read_list,
)
from cartolex.collect.tables import open_run
from cartolex.demo import generate
from cartolex.demo.services import build_bibliography
from cartolex.project import Project
from cartolex.project.corpus import assemble_corpus
from cartolex.project.models import Level
from cartolex.project.tables import read_source_table

LEVELS = (
    Level(id="lab", names={"en": "Lab", "fr": "Laboratoire"}),
    Level(id="institution", names={"en": "Institution", "fr": "Établissement"}),
)


def _project(root: Path, levels=LEVELS) -> Project:
    project = Project.init(root, name="Import test", domain_title="Invented field")
    if levels:
        project.save_config(
            project.config.model_copy(update={"levels": list(levels)}), action="levels"
        )
    return project


def _people(project: Project) -> list[dict]:
    return read_source_table(project.layout.table("people"), "people").to_pylist()


def _csv(path: Path, rows: list[dict], delimiter: str = ",") -> Path:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]), delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)
    return path


# ── names ──


def test_full_names_split_and_name_variants() -> None:
    assert split_full_name("Ada de Tavelin") == ("de Tavelin", "Ada")
    assert split_full_name("TAVELIN Ada") == ("TAVELIN", "Ada")
    assert split_full_name("Tavelin, Ada") == ("Tavelin", "Ada")
    assert split_full_name("Eun-ji Tavrelin") == ("Tavrelin", "Eun-ji")
    assert variants("Tavelin-Kousard", "Ada") == [
        "Ada Tavelin-Kousard",
        "Ada Tavelin",
        "Ada Kousard",
        "A Tavelin-Kousard",
    ]
    assert variants("Pruzort", "Chloé")[:2] == ["Chloé Pruzort", "Chloe Pruzort"]
    assert "Jean Fontaine" in variants("de la Fontaine", "Jean")
    assert name_similarity([("Tavelin", "Ada")], "A. Tavelin")[0] == 0.75
    assert name_similarity([("Pruzort", "Chloé")], "Chloe Pruzort") == (1.0, "same name")
    assert name_similarity([("Tavelin-Kousard", "Ada")], "Ada Tavelin")[0] == 0.85
    assert name_similarity([("Tavelin", "Ada")], "Bo Quell")[0] == 0.0


def test_identifiers_are_read_from_urls_and_checked() -> None:
    assert normalise_orcid("https://orcid.org/0000-0000-0000-0002") is None  # bad check digit
    good = "0000-0002-1825-0097"
    assert normalise_orcid(f"https://orcid.org/{good}") == good
    assert normalise_orcid("000000021825009x".upper()) is None
    assert normalise_openalex_author("https://openalex.org/A9991234567") == "A9991234567"
    assert normalise_openalex_author("no id") is None


# ── mappings ──


def test_a_mapping_is_proposed_from_an_english_header() -> None:
    rows, delim = read_list(
        "Last name,First name,ORCID,E-mail,Lab,Institution,Career stage\n"
        "Tavelin,Ada,0000-0002-1825-0097,ada@x.test,Tide Lab,Institute X,senior\n"
    )
    assert delim == ","
    mapping = propose_mapping(rows, levels=LEVELS)
    assert mapping.has_header
    assert (mapping.last_name, mapping.first_name, mapping.full_name) == (
        "Last name",
        "First name",
        None,
    )
    assert mapping.ids == {"orcid": "ORCID"}
    assert list(mapping.refused) == ["E-mail"]
    assert mapping.organisations == {"Lab": "lab", "Institution": "institution"}
    assert mapping.filters == ["Career stage"]
    assert any("refused" in line for line in mapping.describe())


def test_a_french_header_with_semicolons() -> None:
    rows, delim = read_list("Nom;Prénom;Laboratoire;Courriel\nTavelin;Ada;Tide Lab;a@x.test\n")
    assert delim == ";"
    mapping = propose_mapping(rows, levels=LEVELS)
    assert (mapping.last_name, mapping.first_name) == ("Nom", "Prénom")
    assert mapping.organisations == {"Laboratoire": "lab"}
    assert "Courriel" in mapping.refused


def test_pasted_lists_without_a_header() -> None:
    rows, _ = read_list("Ada Tavelin\nBo Quell\n")
    mapping = propose_mapping(rows)
    assert not mapping.has_header and mapping.full_name == "column 1"
    rows, delim = read_list("Tavelin\tAda\t0000-0002-1825-0097\nQuell\tBo\t\n")
    assert delim == "\t"
    mapping = propose_mapping(rows)
    assert (mapping.last_name, mapping.first_name) == ("column 1", "column 2")
    assert mapping.ids == {"orcid": "column 3"}


def test_a_mapping_is_checked() -> None:
    with pytest.raises(ValueError, match="no name column"):
        ImportMapping(columns=["a"]).check()
    with pytest.raises(ValueError, match="does not have"):
        ImportMapping(columns=["a"], last_name="b").check()
    with pytest.raises(ValueError, match="never an identifier"):
        ImportMapping(columns=["a", "m"], last_name="a", ids={"email": "m"}).check()
    with pytest.raises(ValueError, match="unknown mapping key"):
        ImportMapping.from_json({"columns": ["a"], "nonsense": 1})


# ── importing a list ──


def test_a_list_from_the_demo_world(tmp_path) -> None:
    world = generate("XS", 0)
    bib = build_bibliography(world)
    project = _project(tmp_path / "p")
    rows = bib.people_rows()
    rows[0]["email"] = "someone@example.test"
    for r in rows[1:]:
        r["email"] = ""
    path = _csv(tmp_path / "people.csv", rows)
    report = import_people(project, path)
    assert report.rows_read == len(rows) and report.people_created == len(rows)
    assert report.slot == "collected"
    people = _people(project)
    assert len(people) == len(rows)
    assert {p["columns"][0][0] for p in people if p["columns"]} == {"career_stage"}
    decisions = read_people(project.layout)
    assert {d["identity"] for d in decisions.values()} == {"pending"}
    projected = [d for d in decisions.values() if d["role"] == "projected"]
    assert len(projected) == len(world.overlay_sets["applicants"])
    assert {d["set"] for d in projected} == {"applicants"}
    assert [o.id for o in project.config.overlays] == ["applicants"]
    orgs = read_source_table(project.layout.table("organisations"), "organisations").to_pylist()
    labs = [o for o in orgs if o["level"] == "lab"]
    insts = {o["org_id"]: o for o in orgs if o["level"] == "institution"}
    assert labs and all(len(o["parents"]) == 1 and o["parents"][0] in insts for o in labs)
    # E-mail addresses are never stored.
    stored = b"".join(p.read_bytes() for p in (tmp_path / "p").rglob("*") if p.is_file())
    assert b"someone@example.test" not in stored and b"example.test" not in stored
    assert any("email" in note for note in report.notes)
    # A second import of the same list creates nobody and renumbers nothing.
    before = [p["person_id"] for p in _people(project)]
    again = import_people(project, path)
    assert again.people_created == 0 and again.people_known == len(rows)
    assert [p["person_id"] for p in _people(project)] == before
    project.close()


def test_rows_that_cannot_be_read_are_reported(tmp_path) -> None:
    project = _project(tmp_path / "p")
    text = (
        "last_name,first_name,orcid,role\n"
        "Tavelin,Ada,0000-0000-0000-0002,mapped\n"
        ",Bo,,\n"
        "Quell,Ivo,,somewhere\n"
        "Varno,Zora,,context\n"
    )
    report = import_people(project, text)
    assert report.rows_read == 4 and report.people_created == 3
    assert report.refused == [("row 3", "no name")]
    assert any("not a valid ORCID" in n for n in report.notes)
    assert any("'somewhere' is not one of" in n for n in report.notes)
    roles = sorted(d["role"] for d in read_people(project.layout).values())
    assert roles == ["context", "mapped", "mapped"]
    project.close()


def test_a_mapping_can_be_given_and_sets_a_projected_set(tmp_path) -> None:
    project = _project(tmp_path / "p")
    text = "Full name\tTeam\tGrade\nAda Tavelin\tTide Lab\tA\nIvo Quell\tSand Lab\tB\n"
    rows, _ = read_list(text)
    mapping = propose_mapping(rows, levels=LEVELS)
    mapping.projected_set = "visitors"
    mapping.organisations = {"Team": "team"}
    report = import_people(project, text, mapping=mapping)
    assert report.people_created == 2
    assert "levels added to the project: team" in report.notes
    assert [lv.id for lv in project.config.levels] == ["lab", "institution", "team"]
    assert {d["set"] for d in read_people(project.layout).values()} == {"visitors"}
    assert {p["last_name"] for p in _people(project)} == {"Tavelin", "Quell"}
    project.close()


def test_duplicates_are_proposed_never_merged(tmp_path) -> None:
    project = _project(tmp_path / "p")
    text = (
        "name,orcid\n"
        "Ada Tavelin,0000-0002-1825-0097\n"
        '"TAVELIN, Ada",\n'
        "A. Tavelin,\n"
        "Ada Tavelin-Kousard,\n"
        "Zora Varno,0000-0002-1825-0097\n"
        "Bo Quell,\n"
    )
    report = import_people(project, text)
    assert report.people_created == 6
    people = {p["person_id"]: f"{p['first_name']} {p['last_name']}" for p in _people(project)}
    pairs = {(people[d.person_id], people[d.other_id], d.reason) for d in report.duplicates}
    names = {frozenset(p[:2]) for p in pairs}
    assert frozenset({"Ada Tavelin", "Ada TAVELIN"}) in names
    assert frozenset({"Ada Tavelin", "A. Tavelin"}) in names
    assert frozenset({"Ada Tavelin", "Ada Tavelin-Kousard"}) in names
    assert frozenset({"Ada Tavelin", "Zora Varno"}) in names  # the same ORCID
    assert all("Bo Quell" not in (a, b) for a, b, _ in pairs)
    assert not any(d["merged_into"] for d in read_people(project.layout).values())
    keep = next(pid for pid, n in people.items() if n == "Ada Tavelin")
    merged = next(pid for pid, n in people.items() if n == "Ada TAVELIN")
    confirm_merge(project, keep, merged)
    assert read_people(project.layout)[merged]["merged_into"] == keep
    row = next(p for p in _people(project) if p["person_id"] == keep)
    assert {"last_name": "TAVELIN", "first_name": "Ada", "source": "import"} in row["aliases"]
    assert all(merged not in (d.person_id, d.other_id) for d in find_duplicates(project))
    project.close()


# ── folders ──


def test_a_folder_of_documents_one_file_at_a_time(tmp_path) -> None:
    project = _project(tmp_path / "p")
    import_people(project, "last_name,first_name\nTavelin,Ada\nQuell,Ivo\nVarno,Zora\n")
    docs = tmp_path / "docs"
    (docs / "Ada Tavelin").mkdir(parents=True)
    (docs / "Ada Tavelin" / "report_2021.txt").write_text(
        "Sediment transport on tidal flats was measured over two winters.", encoding="utf-8"
    )
    (docs / "Ada Tavelin" / "broken.pdf").write_bytes(b"%PDF-1.4 this is not a real document")
    (docs / "quell-notes-2019.txt").write_text(
        "Wave climate and storm surge along a sandy coast.", encoding="utf-8"
    )
    (docs / "nobody.txt").write_text("A text nobody wrote.", encoding="utf-8")
    (docs / "empty.md").write_text("   ", encoding="utf-8")
    (docs / "New Person").mkdir()
    (docs / "New Person" / "memo.txt").write_text(
        "Estuary salinity survey notes.", encoding="utf-8"
    )
    report = import_folder(project, docs, create_people=True)
    assert report.rows_read == 6 and report.texts == 3
    refused = dict(report.refused)
    assert set(refused) == {"Ada Tavelin/broken.pdf", "nobody.txt", "empty.md"}
    assert refused["Ada Tavelin/broken.pdf"].startswith("could not be read")
    assert refused["nobody.txt"] == "no person's name found in it"
    assert report.people_created == 1
    texts = read_source_table(project.layout.table("texts"), "texts").to_pylist()
    assert {t["title"]: t["year"] for t in texts} == {
        "report 2021": 2021,
        "quell-notes-2019": 2019,
        "memo": None,
    }
    parts = read_source_table(project.layout.table("text_parts"), "text_parts").to_pylist()
    assert {(p["part"], p["provider"]) for p in parts} == {("full", "folder")}
    # The build reads a folder's documents whole by default: params.json is left alone.
    params, _ = project.read_params()
    assert "corpus.assemble" not in params.stages
    assert project.config.slots[-1].kind == "folder"
    project.close()


# ── an existing corpus ──


def test_an_existing_corpus_comes_in_whole(tmp_path) -> None:
    world = generate("XS", 0)
    world.write_corpus(tmp_path / "ws")
    project = _project(tmp_path / "p", levels=(Level(id="unit", names={"en": "Unit"}),))
    report = import_corpus(project, tmp_path / "ws" / "manual_index.csv")
    with open(tmp_path / "ws" / "manual_index.csv", encoding="utf-8") as fh:
        original = list(csv.DictReader(fh))
    assert report.rows_read == len(original) and not report.refused
    assert report.texts == len({r["txt_path"] for r in original})
    out = tmp_path / "out"
    assemble_corpus(project.layout, project.config, out, parts=("title", "abstract", "full"))
    from cartolex.lexicon.corpus_store import index_rows

    def rows(index):
        return sorted(
            (r["last_name"], r["first_name"], r["unit"], r["doc_year"], r["doc_type"], r["text"])
            for r in index_rows(index)
        )

    rebuilt = rows(out / report.slot / "index.csv")
    assert rebuilt == rows(tmp_path / "ws" / "manual_index.csv")
    project.close()


def test_raw_records_hold_no_absolute_path(tmp_path) -> None:
    project = _project(tmp_path / "p")
    import_people(project, "last_name,first_name\nTavelin,Ada\n")
    docs = tmp_path / "docs"
    (docs / "Ada Tavelin").mkdir(parents=True)
    (docs / "Ada Tavelin" / "notes.txt").write_text("Tidal flats and salt marshes.", "utf-8")
    import_folder(project, docs)
    for path in (tmp_path / "p" / "sources").rglob("*.jsonl*"):
        with open_run(path) as fh:
            assert str(tmp_path) not in fh.read()
    project.close()
