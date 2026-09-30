# SPDX-License-Identifier: MIT
"""Merging works found by several finders: the rules, refusals, versions, and two properties."""

from __future__ import annotations

import json
import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pytest

from cartolex.collect import rebuild_sources
from cartolex.collect.merge import WorkRecord, merge_works
from cartolex.collect.tables import RawWriter, parse_time
from cartolex.project import Project
from cartolex.project.corpus import assemble_corpus
from cartolex.project.models import Slot
from cartolex.project.tables import PRIVATE_PARTS, read_source_table, shareable_parts

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
TITLE = "Sediment transport on tidal flats of a sheltered bay"


def rec(tid: str, source: str, **kw) -> WorkRecord:
    kw.setdefault("title", TITLE)
    kw.setdefault("doc_type", "article")
    kw.setdefault("year", 2020)
    kw.setdefault("people", frozenset({"p1"}))
    return WorkRecord(text_id=tid, slot="s", source=source, retrieved_at=T0, **kw)


# ── the rules ────────────────────────────────────────────────────────────────


def test_the_same_doi_makes_one_text_filled_field_by_field() -> None:
    result = merge_works(
        [
            rec("t2", "orcid", doi="10.5555/A", year=2021, n_authors=3),
            rec("t1", "openalex", doi="https://doi.org/10.5555/a", date="2020-03-01", n_authors=4),
            rec("t3", "hal", doi=None, title="Autre titre", ids={"hal": "hal-09900001"}),
        ]
    )
    assert result.merged_into == {"t1": "t1", "t2": "t1", "t3": "t3"}
    text = result.texts["t1"]
    assert text["doi"] == "10.5555/a" and text["year"] == 2020 and text["n_authors"] == 4
    assert text["date"] == "2020-03-01" and text["source"] == "openalex"
    assert result.provenance["t1"]["year"] == "openalex"
    assert [(m.kept, m.merged, m.rule) for m in result.merges] == [("t1", ("t2",), "doi")]
    # Nothing is overwritten silently: the other year and author count are logged.
    assert {(c.field, c.other, c.other_from) for c in result.conflicts} == {
        ("year", 2021, "orcid"),
        ("n_authors", 3, "orcid"),
    }


def test_a_hal_deposit_joins_by_its_doi_link_and_a_shared_id_joins_too() -> None:
    result = merge_works(
        [
            rec("t1", "openalex", doi="10.5555/a"),
            rec("t2", "hal", doi="10.5555/a", ids={"hal": "hal-09900001"}),
            rec("t3", "hal", title="Other", ids={"hal": "hal-09900002", "arxiv": "9901.00001"}),
            rec(
                "t4",
                "openalex",
                title="Other words",
                ids={"arxiv": "9901.00001"},
                doc_type="preprint",
            ),
        ]
    )
    rules = {(m.kept, m.merged): m.rule for m in result.merges}
    assert rules == {("t1", ("t2",)): "hal_doi", ("t3", ("t4",)): "link:arxiv"}
    assert result.texts["t1"]["ids"] == {"hal": "hal-09900001"}


def test_title_and_year_merge_per_person_only() -> None:
    result = merge_works(
        [
            rec("t1", "hal", year=2020, ids={"hal": "hal-09900001"}),
            rec("t2", "scielo", year=2021, doi="10.5555/b", title=TITLE.upper() + "!"),
            rec("t3", "openalex", year=2023, doi=None),  # two years apart
            rec("t4", "openalex", year=2020, people=frozenset({"p9"})),  # someone else
            rec("t5", "hal", year=2020, title="Short title", people=frozenset({"p2"})),
            rec("t6", "orcid", year=2020, title="Short title", people=frozenset({"p2"})),
        ]
    )
    assert result.merged_into["t2"] == "t1" and result.texts["t1"]["doi"] == "10.5555/b"
    assert [m.rule for m in result.merges] == ["title_year"]
    assert {result.merged_into[t] for t in ("t3", "t4", "t5", "t6")} == {"t3", "t4", "t5", "t6"}


def test_two_people_sharing_a_work_give_one_text() -> None:
    result = merge_works(
        [
            rec("t1", "hal", doi="10.5555/c", people=frozenset({"p1"})),
            rec("t2", "hal", doi="10.5555/c", people=frozenset({"p2"})),
        ]
    )
    assert result.merged_into == {"t1": "t1", "t2": "t1"}


def test_a_false_match_is_refused() -> None:
    result = merge_works(
        [
            rec("t1", "openalex", doi="10.5555/a"),
            rec("t2", "hal", doi="10.5555/b"),  # same title and person, another DOI
            rec("t3", "hal", doc_type="thesis", year=2021),  # a thesis and an article
        ]
    )
    assert set(result.merged_into.values()) == {"t1", "t2", "t3"}
    reasons = {r.reason.split(" ")[0] for r in result.refused}
    assert reasons == {"different", "types"}


def test_a_preprint_and_its_published_version_stay_two_linked_texts() -> None:
    result = merge_works(
        [
            rec("t1", "openalex", doi="10.5555/a", year=2021),
            rec("t2", "hal", doc_type="preprint", year=2020, ids={"arxiv": "9901.00001"}),
            rec(
                "t3", "hal", doc_type="preprint", year=2019, title="An older draft with other words"
            ),
        ],
        stated_versions=[("t3", "https://doi.org/10.5555/A")],
    )
    assert set(result.merged_into.values()) == {"t1", "t2", "t3"}
    assert result.texts["t2"]["version_of"] == "t1"
    assert result.texts["t3"]["version_of"] == "t1"
    assert {(v.preprint, v.rule) for v in result.versions} == {
        ("t2", "title_year"),
        ("t3", "stated"),
    }
    assert result.texts["t1"]["version_of"] is None


def test_a_preprint_meeting_two_published_texts_links_to_the_version_of_record() -> None:
    """An article and its conference version: the preprint names the article. Two texts of
    the same rank leave it unlinked."""
    result = merge_works(
        [
            rec("t1", "openalex", doi="10.5555/a", year=2021),
            rec("t2", "openalex", doi="10.5555/b", year=2021, doc_type="communication"),
            rec("t3", "hal", doc_type="preprint", year=2020),
        ]
    )
    assert result.texts["t3"]["version_of"] == "t1"
    tied = merge_works(
        [
            rec("t1", "openalex", doi="10.5555/a", year=2021),
            rec("t2", "openalex", doi="10.5555/b", year=2021),
            rec("t3", "hal", doc_type="preprint", year=2020),
        ]
    )
    assert tied.texts["t3"]["version_of"] is None
    assert any("several published" in r.reason for r in tied.refused)


# ── properties ───────────────────────────────────────────────────────────────

_WORDS = "tidal flat dune marsh current sediment estuary wave shelf plankton coral reef".split()


def _random_records(rng: random.Random) -> tuple[list[WorkRecord], list[tuple[str, str]]]:
    records, stated = [], []
    n = 0
    for w in range(rng.randint(3, 12)):
        title = " ".join(rng.sample(_WORDS, rng.choice((2, 4, 5))))
        year = rng.randint(2015, 2022)
        doi = f"10.5555/w{w}" if rng.random() < 0.7 else None
        people = frozenset(rng.sample(["p1", "p2", "p3"], rng.randint(1, 2)))
        doc_type = rng.choice(["article", "article", "communication", "preprint", "thesis"])
        for finder in rng.sample(["openalex", "hal", "scielo", "orcid"], rng.randint(1, 3)):
            n += 1
            tid = f"t{n:06d}" if rng.random() < 0.85 or n == 1 else f"t{n - 1:06d}"
            ids = {}
            if finder == "hal" and rng.random() < 0.8:
                ids["hal"] = f"hal-0990{w:04d}"
            if rng.random() < 0.2:
                ids["arxiv"] = f"9901.{w:05d}"
            own_doi = doi if rng.random() < 0.7 else None
            if rng.random() < 0.08:  # a record that carries a wrong DOI
                own_doi = f"10.5555/x{rng.randint(0, 3)}"
            records.append(
                WorkRecord(
                    text_id=tid,
                    slot=rng.choice(["s", "s", "u"]),
                    source=finder,
                    title=title if rng.random() < 0.9 else title.upper(),
                    doc_type=doc_type,
                    retrieved_at=T0 + timedelta(days=rng.randint(0, 3)),
                    year=year + rng.choice((0, 0, 1, -1)),
                    doi=own_doi,
                    ids=ids,
                    n_authors=rng.randint(1, 5),
                    people=people,
                )
            )
            if doc_type == "preprint" and rng.random() < 0.3:
                stated.append((tid, f"10.5555/w{rng.randint(0, w)}"))
    return records, stated


def _same(a, b) -> None:
    assert a.texts == b.texts
    assert a.merged_into == b.merged_into
    assert a.versions == b.versions
    assert a.merges == b.merges and a.refused == b.refused and a.conflicts == b.conflicts


@pytest.mark.parametrize("seed", range(60))
def test_merging_does_not_depend_on_the_order_of_records(seed) -> None:
    rng = random.Random(seed)
    records, stated = _random_records(rng)
    first = merge_works(records, stated_versions=stated)
    for _ in range(3):
        shuffled = records[:]
        rng.shuffle(shuffled)
        again = list(stated)
        rng.shuffle(again)
        _same(first, merge_works(shuffled, stated_versions=again))


@pytest.mark.parametrize("seed", range(60))
def test_merging_a_merged_result_changes_nothing(seed) -> None:
    records, stated = _random_records(random.Random(1000 + seed))
    first = merge_works(records, stated_versions=stated)
    relabelled = [replace(r, text_id=first.merged_into[r.text_id]) for r in records]
    second = merge_works(relabelled, stated_versions=[(first.merged_into[t], d) for t, d in stated])
    assert second.texts == first.texts
    assert second.versions == first.versions
    assert all(k == v for k, v in second.merged_into.items())
    assert set(second.merged_into) == set(first.texts)


# ── on the source tables ─────────────────────────────────────────────────────


def _reader(runs, builder) -> None:
    for run in runs:
        for r in run.records():
            at = parse_time(r["at"])
            pid = builder.person(
                slot=run.slot, keys=[f"row:{r['person']}"], last_name=r["person"],
                first_name=None, source="import", retrieved_at=at,
            )  # fmt: skip
            tid = builder.text(
                slot=run.slot, keys=r["keys"], title=r["title"], doc_type=r["type"],
                source=r["finder"], retrieved_at=at, year=r["year"], doi=r.get("doi"),
                ids=r.get("ids", {}), n_authors=2,
            )  # fmt: skip
            builder.part(tid, part="title", language="en", provider=r["finder"],
                         content=r["title"], retrieved_at=at)  # fmt: skip
            if r.get("abstract"):
                builder.part(tid, part="abstract", language="en", provider=r["finder"],
                             content=r["abstract"], retrieved_at=at)  # fmt: skip
            if r.get("body"):
                builder.part(tid, part="body", language="en", provider=r["finder"],
                             content=r["body"], retrieved_at=at)  # fmt: skip
            builder.authorship(tid, pid, position=r["rank"])


RECORDS = [
    {"finder": "openalex", "keys": ["openalex:W1"], "doi": "10.5555/a", "title": TITLE,
     "type": "article", "year": 2021, "person": "Varno", "rank": 1},
    {"finder": "hal", "keys": ["hal:hal-1"], "doi": "10.5555/a", "title": TITLE, "type": "article",
     "year": 2021, "person": "Quell", "rank": 2, "abstract": "An abstract of the deposit.",
     "ids": {"hal": "hal-1"}},
    {"finder": "hal", "keys": ["hal:hal-2"], "title": TITLE, "type": "preprint", "year": 2020,
     "person": "Varno", "rank": 1, "abstract": "The preprint's abstract.", "body": "A body."},
    {"finder": "hal", "keys": ["hal:hal-3"], "title": "Wave climate of an open coast in winter",
     "type": "preprint", "year": 2019, "person": "Varno", "rank": 1,
     "abstract": "Only a preprint."},
    {"finder": "openalex", "keys": ["openalex:W4"], "doi": "10.5555/d",
     "title": "Wave climate of an open coast in winter", "type": "article", "year": 2020,
     "person": "Varno", "rank": 1},
]  # fmt: skip


def _project(tmp_path) -> Project:
    project = Project.init(
        tmp_path / "p", name="Merge test", domain_title="Invented field",
        slots=(Slot(id="collected", kind="collection", fit=True),),
    )  # fmt: skip
    with RawWriter(project.layout, "collected", "notes", {"source": "test"}) as w:
        for r in RECORDS:
            w.add({"at": T0.isoformat(), **r})
    return project


def test_rebuilding_merges_moves_rows_and_logs_every_merge(tmp_path) -> None:
    project = _project(tmp_path)
    report = rebuild_sources(project.layout, project.config, readers={"notes": _reader})
    assert report.merges["merged by hal_doi"] == 1 and report.merges["version links"] == 2
    layout = project.layout
    texts = {t["text_id"]: t for t in read_source_table(layout.table("texts"), "texts").to_pylist()}
    assert len(texts) == 4
    published = next(t for t in texts.values() if t["doi"] == "10.5555/a")
    assert dict(published["ids"]) == {"hal": "hal-1"}
    preprint = next(t for t in texts.values() if t["doc_type"] == "preprint" and t["year"] == 2020)
    assert preprint["version_of"] == published["text_id"]
    authors = read_source_table(layout.table("authorships"), "authorships").to_pylist()
    assert {
        (a["text_id"], a["position"]) for a in authors if a["text_id"] == published["text_id"]
    } == {
        (published["text_id"], 1),
        (published["text_id"], 2),
    }
    log = json.loads((layout.sources / "merges.json").read_text())
    assert log["format"] == "cartolex-merges/1"
    assert {m["rule"] for m in log["merges"]} == {"hal_doi"}
    assert {v["rule"] for v in log["versions"]} == {"title_year"}
    # The published text without an abstract reads its preprint's (the second pair).
    other = next(t for t in texts.values() if t["doi"] == "10.5555/d")
    assert log["fills"] == [
        {"from": next(t for t in texts if texts[t]["version_of"] == other["text_id"]),
         "part": "abstract/en/hal", "text_id": other["text_id"]}
    ]  # fmt: skip
    first = {n: layout.table(n).read_bytes() for n in ("texts", "text_parts", "authorships")}
    rebuild_sources(project.layout, project.config, readers={"notes": _reader})
    assert first == {n: layout.table(n).read_bytes() for n in first}
    assert log == json.loads((layout.sources / "merges.json").read_text())


def test_the_build_reads_only_the_published_version(tmp_path) -> None:
    project = _project(tmp_path)
    rebuild_sources(project.layout, project.config, readers={"notes": _reader})
    out = tmp_path / "corpus"
    assemble_corpus(project.layout, project.config, out, parts=["title", "abstract"])
    index = (out / "collected" / "index.csv").read_text().splitlines()
    texts = read_source_table(project.layout.table("texts"), "texts").to_pylist()
    preprints = {t["text_id"] for t in texts if t["version_of"]}
    read = {line.split(",")[3].split("/")[1].removesuffix(".txt") for line in index[1:]}
    assert preprints and not read & preprints
    assert len(read) == len(texts) - len(preprints)
    body = (out / "collected" / "texts" / f"{next(iter(read - preprints))}.txt").read_text()
    assert body.endswith("\n")


def test_full_texts_are_private_parts() -> None:
    assert PRIVATE_PARTS == {"body", "full"}
    table = pa.table(
        {
            "text_id": ["t1", "t1", "t1", "t1"],
            "part": ["abstract", "body", "full", "title"],
            "content": ["a", "b", "c", "d"],
        }
    )
    assert shareable_parts(table)["part"].to_pylist() == ["abstract", "title"]
    assert shareable_parts(table.slice(0, 0)).num_rows == 0
