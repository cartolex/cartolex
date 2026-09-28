# SPDX-License-Identifier: MIT
"""A mini OpenAlex snapshot of a demo world, laid out like the real one.

The real snapshot (see ``docs/collection.md``) is a folder per entity under
``data/jsonl/``, each holding ``updated_date=YYYY-MM-DD/part_NNNN.gz`` files of
gzip-compressed JSON lines (one entity per line, in the API's shape), a
``manifest.json`` per entity and one for the whole format, and, for works, a
``deleted_ids.csv.gz`` log (``work_id,deleted_date``). A record appears only in
the partition of the date it last changed.

:func:`write_snapshot` writes the same layout for the works, authors and
institutions the demo OpenAlex serves, record for record the same JSON as its
API answers, plus what the snapshot adds or leaves out: ``is_xpac`` and
``has_content`` on works. Records are spread over two update dates and several
parts; one extra work, a withdrawn copy of an indexed work, sits in the older
partition and is listed in the deletion log, so a reader that forgets the log
gets a work the API does not have.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
from pathlib import Path
from typing import Any

from .biblio import Bibliography
from .openalex import ROOT, OpenAlexService

__all__ = ["SNAPSHOT_DATES", "write_snapshot"]

#: The two update dates records are spread over, oldest first.
SNAPSHOT_DATES = ("2025-12-10", "2026-01-14")
#: The release date written into the manifests.
RELEASE = "2026-01-14"


def _gzip_lines(records: list[dict[str, Any]]) -> bytes:
    text = "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in records)
    # mtime=0: the same world always gives the same bytes.
    return gzip.compress(text.encode("utf-8"), mtime=0)


def write_snapshot(bib: Bibliography, root: Path | str, *, per_part: int = 40) -> dict[str, Any]:
    """Write the mini snapshot of *bib* under *root* (``root/data/jsonl/…``); returns its manifest.

    Each entity's records, in id order, go in turn to the older and the newer
    update date, *per_part* records to a part file.
    """
    root = Path(root)
    service = OpenAlexService(bib)
    base = root / "data" / "jsonl"
    entities: dict[str, list[dict[str, Any]]] = {
        "authors": [service._authors[k] for k in sorted(service._authors)],
        "institutions": [service._institutions[k] for k in sorted(service._institutions)],
        "works": [
            dict(service._works[k], is_xpac=False, has_content={"pdf": False, "grobid_xml": False})
            for k in sorted(service._works)
        ],
    }
    # A withdrawn copy of the first indexed work of a world person: in the older partition,
    # and in the deletion log.
    number = 1
    while f"W999{number:07d}" in service._works:
        number += 1
    withdrawn_id = f"W999{number:07d}"
    first = next(w for w in entities["works"] if any(a["author"]["id"] for a in w["authorships"]))
    withdrawn = json.loads(json.dumps(first))
    withdrawn.update(id=ROOT + withdrawn_id, doi=None, title=first["title"] + " (withdrawn)")
    withdrawn["display_name"] = withdrawn["title"]
    withdrawn["ids"] = {"openalex": ROOT + withdrawn_id}
    combined: list[dict[str, Any]] = []
    for entity, records in entities.items():
        folder = base / entity
        by_date: dict[str, list[dict[str, Any]]] = {d: [] for d in SNAPSHOT_DATES}
        for i, record in enumerate(records):
            by_date[SNAPSHOT_DATES[i % len(SNAPSHOT_DATES)]].append(record)
        if entity == "works":
            by_date[SNAPSHOT_DATES[0]].insert(0, withdrawn)
        files = []
        for date, rows in by_date.items():
            for n, start in enumerate(range(0, len(rows), per_part)):
                chunk = rows[start : start + per_part]
                path = folder / f"updated_date={date}" / f"part_{n:04d}.gz"
                path.parent.mkdir(parents=True, exist_ok=True)
                data = _gzip_lines(chunk)
                path.write_bytes(data)
                files.append(
                    {
                        "url": f"s3://openalex/data/jsonl/{entity}/updated_date={date}/{path.name}",
                        "meta": {"content_length": len(data), "record_count": len(chunk)},
                    }
                )
        if entity == "works":
            buf = io.StringIO()
            writer = csv.writer(buf, lineterminator="\n")
            writer.writerow(["work_id", "deleted_date"])
            writer.writerow([ROOT + withdrawn_id, RELEASE])
            (folder / "deleted_ids.csv.gz").write_bytes(
                gzip.compress(buf.getvalue().encode("utf-8"), mtime=0)
            )
        manifest = {
            "date": RELEASE,
            "format": "jsonl",
            "entity": entity,
            "record_count": sum(f["meta"]["record_count"] for f in files),
            "content_length": sum(f["meta"]["content_length"] for f in files),
            "files": files,
        }
        (folder / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        combined.append(
            {
                "entity": entity,
                "meta": {
                    "record_count": manifest["record_count"],
                    "content_length": manifest["content_length"],
                },
                "files": files,
            }
        )
    total = {
        "date": RELEASE,
        "format": "jsonl",
        "meta": {
            "record_count": sum(e["meta"]["record_count"] for e in combined),
            "content_length": sum(e["meta"]["content_length"] for e in combined),
        },
        "entities": combined,
    }
    (base / "manifest.json").write_text(json.dumps(total, indent=2) + "\n", encoding="utf-8")
    (root / "LICENSE.txt").write_text(
        "Synthetic demo data (CC0), laid out like the OpenAlex snapshot.\n", encoding="utf-8"
    )
    return total
