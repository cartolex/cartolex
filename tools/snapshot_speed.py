# SPDX-License-Identifier: MIT
"""Measure how fast cartolex streams an OpenAlex snapshot, on a synthetic one.

Usage::

    python tools/snapshot_speed.py --works 200000 [--out DIR] [--keep]

Writes, in a temporary folder (or *DIR*), a snapshot in the real layout whose
works are copies of the demo services' index works under new ids (the shape
and size of real records), then times three passes of
:class:`cartolex.collect.snapshot.Snapshot`: the works of 100 author records
(a harvest's pass; again with four worker processes), the works of 5,000
DOIs, and the works signed at one institution and its units. Prints, for each, the lines read and parsed, the
seconds, and the compressed and plain megabytes read per second. Run it
through ``~/cartolex-work/heavy.sh`` (it writes a few hundred MB).
"""

from __future__ import annotations

import argparse
import copy
import gzip
import json
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cartolex.collect.snapshot import Snapshot  # noqa: E402
from cartolex.demo import generate  # noqa: E402
from cartolex.demo.services import build_bibliography  # noqa: E402
from cartolex.demo.services.openalex import ROOT as OA  # noqa: E402
from cartolex.demo.services.openalex import OpenAlexService  # noqa: E402

PER_PART = 20_000


def write_sample(root: Path, works: int, seed: int = 0) -> dict:
    """A synthetic snapshot of *works* works; returns what the passes ask for."""
    rng = random.Random(seed)
    service = OpenAlexService(build_bibliography(generate("L", 0)))
    templates = list(service._works.values())
    institutions = list(service._institutions.values())
    base = root / "data" / "jsonl"
    authors = [f"A97{i:08d}" for i in range(max(1000, works // 4))]
    plain = 0
    folder = base / "works" / "updated_date=2026-01-14"
    folder.mkdir(parents=True)
    dois = []
    for part, start in enumerate(range(0, works, PER_PART)):
        lines = []
        for n in range(start, min(works, start + PER_PART)):
            w = copy.deepcopy(rng.choice(templates))
            wid, doi = f"W97{n:08d}", f"10.5555/speed.{n}"
            w.update(id=OA + wid, doi=f"https://doi.org/{doi}", is_xpac=False)
            w["ids"] = {"openalex": OA + wid, "doi": w["doi"]}
            for a in w["authorships"]:
                a["author"]["id"] = OA + rng.choice(authors)
            lines.append(json.dumps(w, separators=(",", ":")))
            if n % (works // 5000 or 1) == 0:
                dois.append(doi)
        text = "\n".join(lines) + "\n"
        plain += len(text.encode("utf-8"))
        (folder / f"part_{part:04d}.gz").write_bytes(gzip.compress(text.encode("utf-8"), 6))
    inst_folder = base / "institutions" / "updated_date=2026-01-14"
    inst_folder.mkdir(parents=True)
    (inst_folder / "part_0000.gz").write_bytes(
        gzip.compress("".join(json.dumps(i) + "\n" for i in institutions).encode("utf-8"))
    )
    top = next(i for i in institutions if not i["associated_institutions"] or all(
        a["relationship"] != "parent" for a in i["associated_institutions"]))  # fmt: skip
    return {
        "plain_bytes": plain,
        "authors": rng.sample(authors, 100),
        "dois": dois[:5000],
        "institution": top["id"].rsplit("/", 1)[-1],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--works", type=int, default=200_000)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)
    base = args.out or Path(tempfile.mkdtemp(prefix="cartolex-snapshot-"))
    try:
        t0 = time.perf_counter()
        asked = write_sample(base, args.works)
        snap = Snapshot(base)
        size = snap.size(["works"])
        print(f"sample: {args.works} works, {size / 1e6:.0f} MB compressed, "
              f"{asked['plain_bytes'] / 1e6:.0f} MB plain ({time.perf_counter() - t0:.0f} s)")  # fmt: skip
        results = {}
        for label, query, jobs in (
            ("100 author records", {"author_ids": asked["authors"]}, 1),
            ("100 author records, 4 processes", {"author_ids": asked["authors"]}, 4),
            ("5,000 DOIs", {"dois": asked["dois"]}, 1),
            ("one institution and its units", {"lineage": [asked["institution"]]}, 1),
        ):
            snap = Snapshot(base, jobs=jobs)
            t = time.perf_counter()
            found = snap.works(**query)
            seconds = time.perf_counter() - t
            r = snap.report
            results[label] = {
                "found": len(found),
                "lines": r.lines,
                "parsed": r.parsed,
                "seconds": round(seconds, 2),
                "lines_per_second": round(r.lines / seconds),
                "compressed_mb_per_second": round(size / 1e6 / seconds, 1),
                "plain_mb_per_second": round(asked["plain_bytes"] / 1e6 / seconds, 1),
            }
            print(f"{label}: {json.dumps(results[label])}")
        return 0
    finally:
        if not args.keep and args.out is None:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
