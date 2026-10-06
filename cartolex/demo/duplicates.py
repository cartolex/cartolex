# SPDX-License-Identifier: MIT
"""Duplicate people in a demo project, with the truth: to try and measure the duplicates'
review and the automatic merge.

A demo world has no duplicate: every person is one row. :func:`add_duplicates`
changes a project written by :func:`cartolex.demo.project.write_project` the way
real lists and collections do, and returns the truth:

* **true duplicates**: a second row for some people, which takes over part of
  their texts (as a second record of the same author would), written in one of
  the ways a source writes it: the same name and ORCID (a list imported twice),
  the same name and OpenAlex record, the same name and nothing else, a first
  name as an initial, a double surname, or the same name recorded twice on a
  text (at the same place in the author list);
* **homonyms**: people renamed so that they share someone's name, in another
  group or, harder, in the same group, each keeping their own identifiers.

Everything is drawn from *seed*: the same project and seed give the same rows.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import pyarrow as pa

from cartolex.project import Project
from cartolex.project.tables import (
    SOURCE_SCHEMAS,
    decision_csv_bytes,
    read_decision_csv,
    read_source_table,
    source_key,
    write_source_table,
)

from .model import DemoWorld

__all__ = ["DUPLICATE_KINDS", "DuplicatesTruth", "add_duplicates"]

#: How a true duplicate is written, in turn.
DUPLICATE_KINDS = ("orcid", "openalex", "name", "initial", "double", "twice")


@dataclass
class DuplicatesTruth:
    """What :func:`add_duplicates` did: each pair, and whether it is one person."""

    #: (original, duplicate, kind) for each true duplicate.
    same: list[tuple[str, str, str]] = field(default_factory=list)
    #: (person, homonym, "other-group" | "same-group") for each pair of homonyms.
    homonyms: list[tuple[str, str, str]] = field(default_factory=list)

    def is_same(self, a: str, b: str) -> bool:
        """Whether two person ids are one person."""
        return frozenset((a, b)) in {frozenset(p[:2]) for p in self.same}


def _rows(project: Project, name: str) -> list[dict]:
    return read_source_table(project.layout.table(name), name).to_pylist()


def _write(project: Project, name: str, rows: list[dict]) -> None:
    schema = SOURCE_SCHEMAS[name]
    rows = sorted(rows, key=lambda r: source_key(name, r))
    table = pa.table({f.name: [r.get(f.name) for r in rows] for f in schema}, schema=schema)
    write_source_table(project.layout.table(name), name, table)


def add_duplicates(
    project: Project,
    world: DemoWorld,
    *,
    seed: int = 0,
    share: float = 0.15,
    homonyms: float = 0.05,
) -> DuplicatesTruth:
    """Give a *share* of the cohort a duplicate row and make a *homonyms* share of them
    namesakes of someone else (see the module docstring); return the truth. The new rows
    are ``mapped``, with the identity of what they carry."""
    rng = random.Random(f"duplicates:{world.size}:{world.seed}:{seed}")
    truth = DuplicatesTruth()
    people = {r["person_id"]: r for r in _rows(project, "people")}
    authorships = _rows(project, "authorships")
    affiliations = _rows(project, "affiliations")
    decisions = {r["person_id"]: r for r in read_decision_csv(project.layout.people_csv, "people")}
    texts_of: dict[str, list[dict]] = {}
    for a in authorships:
        texts_of.setdefault(a["person_id"], []).append(a)
    cohort = [p.person_id for p in world.cohort if len(texts_of.get(p.person_id, [])) >= 3]
    rng.shuffle(cohort)
    chosen = cohort[: max(1, round(len(cohort) * share))]
    group = {p.person_id: p.group for p in world.people}
    added_rows: list[dict] = []
    for n, pid in enumerate(chosen):
        kind = DUPLICATE_KINDS[n % len(DUPLICATE_KINDS)]
        src = people[pid]
        new = f"{pid}d"
        last, first = src["last_name"], src["first_name"]
        ids: list = []
        orcid = None
        if kind == "orcid":
            orcid = src["orcid"]
        elif kind == "openalex":
            ids = [(s, v) for s, v in src["ids"] or [] if s == "openalex"]
        elif kind == "initial":
            first = f"{first[0]}."
        elif kind == "double":
            last = f"{last}-{rng.choice(['Morlaix', 'Quesnel', 'Abreu', 'Lindqvist'])}"
        added_rows.append({**src, "person_id": new, "last_name": last, "first_name": first,
                           "orcid": orcid, "ids": ids, "aliases": []})  # fmt: skip
        mine = sorted(texts_of[pid], key=lambda a: a["text_id"])
        moved = rng.sample(mine[:-1], max(1, len(mine) // 3))  # any years: the last one stays
        for a in moved:
            a["person_id"] = new  # the second record holds these texts
        if kind == "twice":
            kept = mine[-1]
            authorships.append({**kept, "person_id": new})  # recorded twice, same place
        affiliations.append({"person_id": new, "org_id": group[pid], "source": "import"})
        decisions[new] = {"person_id": new, "role": "mapped", "set": "", "identity":
                          "confirmed" if orcid or ids else "pending", "decided_at":
                          "2026-01-01T00:00:00Z"}  # fmt: skip
        truth.same.append((pid, new, kind))
    others = [p.person_id for p in world.cohort if p.person_id not in chosen]
    rng.shuffle(others)
    count = max(1, round(len(world.cohort) * homonyms))
    for n, pid in enumerate(others[:count]):
        same_group = n % 2 == 1
        pool = [
            q for q in others[count:]
            if (group[q] == group[pid]) == same_group and q not in {h[1] for h in truth.homonyms}
        ]  # fmt: skip
        if not pool:
            continue
        namesake = rng.choice(pool)
        people[namesake] = {**people[namesake], "last_name": people[pid]["last_name"],
                            "first_name": people[pid]["first_name"]}  # fmt: skip
        truth.homonyms.append((pid, namesake, "same-group" if same_group else "other-group"))
    rows = sorted([*people.values(), *added_rows], key=lambda r: r["person_id"])
    _write(project, "people", rows)
    _write(project, "authorships", authorships)
    _write(project, "affiliations", affiliations)
    project.layout.people_csv.write_bytes(
        decision_csv_bytes("people", sorted(decisions.values(), key=lambda r: r["person_id"]))
    )
    return truth
