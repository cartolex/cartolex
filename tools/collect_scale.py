# SPDX-License-Identifier: MIT
"""Measure rebuilding the source tables of a large synthetic collection.

Usage::

    python tools/collect_scale.py --people 10000 [--works 12] [--out DIR] [--keep]

Writes, in a temporary folder (or *DIR*), a project whose raw runs are what
collecting *people* people would have left: an imported list, then harvests of
1,000 people each, every person with *works* works in the index's shape (texts
and abstracts taken from the demo services' index, some works signed by two
people of the project). Then it times:

1. a first rebuild of the tables (no index yet: every run is read);
2. a rebuild with nothing new (the index says nothing changed);
3. a rebuild after a new harvest of ten people (only what changed is read);
4. the same tables rebuilt from scratch, which must give the same bytes.

Run it through ``~/cartolex-work/heavy.sh`` from 10⁴ people up (it takes a few GB).
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cartolex.collect.tables import IdRegistry, RawWriter, iso, rebuild_sources  # noqa: E402
from cartolex.demo import generate  # noqa: E402
from cartolex.demo import names as nm  # noqa: E402
from cartolex.demo.services import build_bibliography  # noqa: E402
from cartolex.demo.services.openalex import ROOT as OA  # noqa: E402
from cartolex.demo.services.openalex import OpenAlexService  # noqa: E402
from cartolex.project import Project  # noqa: E402
from cartolex.project.layout import SOURCE_TABLES  # noqa: E402
from cartolex.project.models import Slot  # noqa: E402

SLOT = "collected"
BATCH = 1000


def _templates() -> list[dict]:
    service = OpenAlexService(build_bibliography(generate("L", 0)))
    return [w for w in service._works.values() if w["abstract_inverted_index"]]


def write_collection(root: Path, people: int, works: int, seed: int = 0) -> Project:
    """A project holding the raw runs of *people* people with *works* works each."""
    rng = random.Random(seed)
    project = Project.init(root, name="Scale", domain_title="Invented field",
                           slots=(Slot(id=SLOT, kind="collection"),))  # fmt: skip
    at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    names = []
    with RawWriter(project.layout, SLOT, "people", {"source": "synthetic", "rows_read": people},
                   now=at) as out:  # fmt: skip
        for i in range(people):
            last, first = nm.invented_surname(rng), rng.choice(nm.FIRST_NAMES)
            names.append((last, first))
            out.add({"row": i + 2, "key": f"import:scale{i:07d}", "last_name": last,
                     "first_name": first, "orcid": None, "ids": {}, "columns": {}, "orgs": [],
                     "role": "mapped", "set": "", "retrieved_at": iso(at)})  # fmt: skip
    rebuild_sources(project.layout, project.config)
    registry = IdRegistry(project.layout, [SLOT])
    pids = [registry.lookup("people", [f"import:scale{i:07d}"], SLOT) for i in range(people)]
    templates = _templates()
    authors = [f"A98{i:08d}" for i in range(people)]
    # Each person's works; a third of the works are signed by a second person of the project.
    own: list[list[dict]] = [[] for _ in range(people)]
    n = 0
    for i in range(people):
        for _ in range(works):
            n += 1
            work = copy.deepcopy(rng.choice(templates))
            wid = f"W98{n:08d}"
            year = rng.randint(2012, 2026)
            work.update(id=OA + wid, doi=f"https://doi.org/10.5555/scale.{n}",
                        publication_year=year, publication_date=f"{year}-06-01")  # fmt: skip
            work["ids"] = {"openalex": OA + wid}
            signers = [i]
            if rng.random() < 0.33:
                signers.append(rng.randrange(people))
            auths = []
            for k in signers:
                a = copy.deepcopy(work["authorships"][0])
                a["author"] = {"id": OA + authors[k], "display_name": f"{names[k][1]} {names[k][0]}",
                               "orcid": None}  # fmt: skip
                auths.append(a)
            work["authorships"] = auths + work["authorships"][1:2]
            for k in set(signers):
                own[k].append(work)
    for start in range(0, people, BATCH):
        write_harvest(project, range(start, min(people, start + BATCH)), pids, authors, names, own)
    return project


def write_harvest(project, indices, pids, authors, names, own) -> None:
    at = datetime.now(timezone.utc)
    header = {"years": None, "people": {}, "source": "api"}
    with RawWriter(project.layout, SLOT, "openalex", header) as out:
        for i in indices:
            pid = pids[i]
            out.header["people"][pid] = {"records": [f"openalex:{authors[i]}"],
                                         "names": [list(names[i])], "orcid": None,
                                         "orcids": [], "dois": []}  # fmt: skip
            out.add({"type": "author", "person_id": pid, "retrieved_at": iso(at),
                     "record": {"id": OA + authors[i], "display_name": " ".join(names[i]),
                                "works_count": len(own[i]), "affiliations": []}})  # fmt: skip
            for work in own[i]:
                out.add({"type": "work", "via": "openalex", "person_id": pid,
                         "retrieved_at": iso(at), "record": work})  # fmt: skip


def _bytes(project) -> dict[str, bytes]:
    return {n: project.layout.table(n).read_bytes() for n in SOURCE_TABLES}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--people", type=int, default=10_000)
    parser.add_argument("--works", type=int, default=12)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--keep", action="store_true", help="keep the project afterwards")
    args = parser.parse_args(argv)
    base = args.out or Path(tempfile.mkdtemp(prefix="cartolex-scale-"))
    try:
        t0 = time.perf_counter()
        project = write_collection(base / "project", args.people, args.works)
        raw = sum(p.stat().st_size for p in (base / "project" / "sources").rglob("*.jsonl"))
        print(f"collection written: {args.people} people, {raw / 1e6:.0f} MB of raw runs "
              f"({time.perf_counter() - t0:.0f} s)")  # fmt: skip
        timings = {}
        for label in ("first rebuild", "nothing new"):
            t = time.perf_counter()
            report = rebuild_sources(project.layout, project.config)
            timings[label] = time.perf_counter() - t
            print(f"{label}: {timings[label]:.1f} s, {report.rows}")
        # A new harvest of ten people.
        registry = IdRegistry(project.layout, [SLOT])
        pids = [registry.lookup("people", [f"import:scale{i:07d}"], SLOT) for i in range(10)]
        from cartolex.project.tables import read_source_table

        rows = read_source_table(project.layout.table("people"), "people").to_pylist()[:10]
        names = [(r["last_name"], r["first_name"]) for r in rows]
        authors = [f"A98{i:08d}" for i in range(10)]
        write_harvest(project, range(10), pids, authors, names, [[] for _ in range(10)])
        t = time.perf_counter()
        report = rebuild_sources(project.layout, project.config)
        timings["after a small harvest"] = time.perf_counter() - t
        print(f"after a small harvest: {timings['after a small harvest']:.1f} s, {report.rows}")
        incremental = _bytes(project)
        t = time.perf_counter()
        rebuild_sources(project.layout, project.config, incremental=False)
        timings["from scratch"] = time.perf_counter() - t
        same = _bytes(project) == incremental
        print(f"from scratch: {timings['from scratch']:.1f} s; same bytes: {same}")
        print(json.dumps({k: round(v, 2) for k, v in timings.items()}))
        return 0 if same else 1
    finally:
        if not args.keep and args.out is None:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
