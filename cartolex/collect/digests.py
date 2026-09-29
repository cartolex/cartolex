# SPDX-License-Identifier: MIT
"""Digests of raw runs: the costly reading of a run done once, kept in ``cache/sources/``.

A raw run never changes once written, and the tables are rebuilt from every
run after every collection. Reading a harvest is costly: each work is several
kilobytes of JSON, its abstract is rebuilt from the index's inverted index,
its markup stripped, and the language of its title and abstract detected
(most of the time goes there). A **digest** keeps, for every record of a run,
what the kind's reader needs, with those results computed; rebuilding the
tables reads the digests, and only the runs not digested yet (the new ones)
are read whole, several at a time in worker processes when there are many.
A run superseded for everyone it names is not read at all.

::

    cache/sources/index.json                        cartolex-digests/1
    cache/sources/<slot>/<kind>/<run id>.jsonl.gz   one digest per run

The **index** says, for every run digested, the raw run's size and
modification time and the digester's version (when one differs, the run is
digested again), how many records it holds and the people it names: what each
run contributed. The folder is a cache: deleting it costs one full reading
of the runs, nothing else. A digest's records give the same rows as the raw
records they come from, so a rebuild gives the same bytes with or without
them (tested).
"""

from __future__ import annotations

import contextlib
import gzip
import json
import os
import tempfile
from collections.abc import Callable, Iterable, Iterator, Mapping
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from cartolex.project.files import atomic_write_bytes, replace_path
from cartolex.project.layout import ProjectLayout

__all__ = ["DIGESTERS", "INDEX_FORMAT", "DigestCache", "digest_record"]

INDEX_FORMAT = "cartolex-digests/1"
#: Raw runs larger than this in all, when not yet digested, are digested in worker processes.
PARALLEL_BYTES = 32_000_000


# ── the digesters ────────────────────────────────────────────────────────────


def _slim_institution(inst: Mapping[str, Any]) -> dict[str, Any]:
    return {k: inst.get(k) for k in ("id", "display_name", "ror", "country_code", "lineage")}


def _digest_openalex(rec: dict[str, Any]) -> dict[str, Any] | None:
    """A harvest's line: the fields its reader uses, the text's title, abstract and their
    languages computed (:func:`cartolex.collect.harvest.work_text`)."""
    from .harvest import work_text

    record = rec.get("record") or {}
    if rec.get("type") == "author":
        slim = {
            "id": record.get("id"),
            "display_name": record.get("display_name"),
            "works_count": record.get("works_count"),
            "affiliations": [
                {
                    "institution": _slim_institution(a.get("institution") or {}),
                    "years": a.get("years"),
                }
                for a in record.get("affiliations") or []
            ],
        }
        return {**rec, "record": slim}
    if rec.get("type") != "work":
        return rec
    source = ((record.get("primary_location") or {}).get("source") or {}).get("type")
    slim = {
        k: record.get(k)
        for k in (
            "id",
            "doi",
            "title",
            "display_name",
            "publication_year",
            "publication_date",
            "type",
            "language",
            "is_authors_truncated",
        )
    }
    slim["primary_location"] = {"source": {"type": source}}
    slim["authorships"] = [
        {
            "author": {k: (a.get("author") or {}).get(k) for k in ("id", "display_name", "orcid")},
            "raw_author_name": a.get("raw_author_name"),
            "is_corresponding": a.get("is_corresponding"),
            "institutions": [_slim_institution(i) for i in a.get("institutions") or []],
        }
        for a in record.get("authorships") or []
    ]
    return {**rec, "record": slim, "text": work_text(record)}


def _digest_orcid(rec: dict[str, Any]) -> dict[str, Any] | None:
    """A registry answer: only the records (employments) are read; the works lists are not."""
    return rec if rec.get("type") == "record" else None


#: Per kind of run: the digester's version (a new version digests every run again) and the
#: function turning one raw record into what its reader reads (``None``: left out).
DIGESTERS: dict[str, tuple[int, Callable[[dict[str, Any]], dict[str, Any] | None]]] = {
    "openalex": (1, _digest_openalex),
    "orcid": (1, _digest_orcid),
}


def digest_record(kind: str, rec: dict[str, Any]) -> dict[str, Any] | None:
    """The digest of one raw record of a run of *kind*."""
    return DIGESTERS[kind][1](rec)


def _write_digest(raw_path: str, kind: str, out_path: str) -> tuple[int, list[str]]:
    """Digest one raw run into *out_path* (gzip JSON lines, written whole or not at all);
    returns the records read and the people named. Runs in a worker process too."""
    digester = DIGESTERS[kind][1]
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{out.name}.", suffix=".tmp", dir=out.parent)
    os.close(fd)
    people: set[str] = set()
    n = 0
    try:
        with open(raw_path, encoding="utf-8") as fh, gzip.open(tmp, "wt", encoding="utf-8") as gz:
            header = fh.readline()
            gz.write(header)
            named = json.loads(header).get("people")
            if isinstance(named, dict):
                people.update(named)
            for line in fh:
                if not line.strip():
                    continue
                rec = json.loads(line)
                n += 1
                if rec.get("person_id"):
                    people.add(rec["person_id"])
                slim = digester(rec)
                if slim is not None:
                    gz.write(json.dumps(slim, ensure_ascii=False, separators=(",", ":")) + "\n")
        replace_path(tmp, out)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
    return n, sorted(people)


class DigestCache:
    """The digests of a project's raw runs (see the module docstring).

    *jobs* is how many worker processes digest runs when many are new (``None``:
    up to four, when the runs to digest weigh more than :data:`PARALLEL_BYTES`).
    """

    def __init__(
        self, layout: ProjectLayout, *, jobs: int | None = None, write: bool = True
    ) -> None:
        self.layout = layout
        #: Whether digests may be written (``False``: a reader without the project's lock
        #: reads fresh digests, and the raw runs of the others).
        self.write = write
        self.root = layout.cache / "sources"
        self.index_path = self.root / "index.json"
        self.jobs = jobs
        self.index: dict[str, dict[str, Any]] = {}
        self.digested = 0  # runs digested by this rebuild
        self.read = 0  # digests read
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            if data.get("format") == INDEX_FORMAT:
                self.index = dict(data.get("runs") or {})
        except (OSError, ValueError):
            self.index = {}
        self._changed = False

    @staticmethod
    def key(run: Any) -> str:
        return f"{run.slot}/{run.kind}/{run.run_id}"

    def path(self, run: Any) -> Path:
        return self.root / run.slot / run.kind / f"{run.run_id}.jsonl.gz"

    def _stamp(self, run: Any) -> dict[str, Any]:
        st = run.path.stat()
        return {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "version": DIGESTERS[run.kind][0]}

    def fresh(self, run: Any) -> bool:
        """Whether *run* has a digest made from it as it is now."""
        entry = self.index.get(self.key(run))
        if entry is None or not self.path(run).exists():
            return False
        stamp = self._stamp(run)
        return all(entry.get(k) == v for k, v in stamp.items())

    def _record(self, run: Any, n: int, people: list[str]) -> None:
        self.index[self.key(run)] = {**self._stamp(run), "records": n, "people": people}
        self._changed = True
        self.digested += 1

    def prepare(self, runs: Iterable[Any]) -> None:
        """Digest, before the readers run, every run of a digested kind that has no fresh
        digest; many at once in worker processes when they weigh a lot."""
        todo = [r for r in runs if r.kind in DIGESTERS and not self.fresh(r)]
        if not todo:
            return
        size = sum(r.path.stat().st_size for r in todo)
        jobs = self.jobs if self.jobs is not None else (
            min(4, os.cpu_count() or 1) if size > PARALLEL_BYTES else 1
        )  # fmt: skip
        if jobs <= 1 or len(todo) == 1:
            for run in todo:
                self._record(run, *_write_digest(str(run.path), run.kind, str(self.path(run))))
            return
        import multiprocessing

        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=jobs, mp_context=context) as pool:
            futures = [
                (run, pool.submit(_write_digest, str(run.path), run.kind, str(self.path(run))))
                for run in todo
            ]
            for run, future in futures:
                self._record(run, *future.result())

    def records(self, run: Any) -> Iterator[dict[str, Any]]:
        """The digest's records of *run* (digested now if it has none)."""
        if not self.fresh(run):
            if not self.write:
                yield from (r for r in map(DIGESTERS[run.kind][1], run.raw_records()) if r)
                return
            self._record(run, *_write_digest(str(run.path), run.kind, str(self.path(run))))
        self.read += 1
        with gzip.open(self.path(run), "rt", encoding="utf-8") as fh:
            next(fh, None)
            for line in fh:
                if line.strip():
                    yield json.loads(line)

    def save(self, keep: Iterable[Any]) -> None:
        """Write the index, forgetting runs that no longer exist (and removing their digests)."""
        wanted = {self.key(r) for r in keep}
        for key in sorted(set(self.index) - wanted):
            slot, kind, run_id = key.split("/", 2)
            (self.root / slot / kind / f"{run_id}.jsonl.gz").unlink(missing_ok=True)
            del self.index[key]
            self._changed = True
        if not self._changed:
            return
        text = json.dumps(
            {"format": INDEX_FORMAT, "runs": dict(sorted(self.index.items()))},
            ensure_ascii=False,
            indent=1,
        )
        atomic_write_bytes(self.index_path, (text + "\n").encode("utf-8"))
        self._changed = False
