# SPDX-License-Identifier: MIT
"""Harvest: the works of every confirmed person, into the source tables.

For each person whose identity is confirmed or accepted automatically, the
records in ``decisions/people.csv`` say where their works are:

* ``openalex:A…`` — every work of these author records, in the year window,
  through cursor pages of 100, and the records themselves (their
  affiliations, with years). From the API, the records of several people
  are asked for together, up to 50 a request, and each person gets the works
  their own records sign: what a request per person would give, in fewer
  requests;
* ``orcid:…`` — the works the person declared in the registry, fetched from
  the index by DOI, 50 at a time, and the registry record (employments).

A person's works from all their records are united, duplicates removed by DOI,
then by normalised title and year. Each work becomes a text with its title and
abstract (rebuilt from the inverted index), its language detected per part; its
authorships name every project person on it, at their rank, with ``last`` and
``corresponding`` when the service says so, and with the institutions the work
states for them, which also date their affiliations.

Everything received is kept in ``sources/<slot>/raw/openalex/`` and
``raw/orcid/``, one run per harvest; the tables are rebuilt from these runs, a
person's latest run replacing their earlier ones.

The years default to the slot's window (``years`` in ``project.json``). The
OpenAlex part comes from the API, or from a downloaded snapshot
(:mod:`cartolex.collect.snapshot`), read in one pass for the whole job: the
runs written are the same. A person whose collection fails is recorded with
the cause (:mod:`cartolex.collect.outcomes`) and the others go on; after three
failures in a row the harvest stops, keeps what it collected, and raises the
last error.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from cartolex.project import Project
from cartolex.project.tables import read_source_table

from .decisions import read_people, slot_window
from .http import CacheMiss, Cancelled, CollectError, Fetched, HttpClient, ServiceError
from .names import name_similarity, words
from .openalex import (
    AUTHOR_BATCH,
    OpenAlexApi,
    OpenAlexSource,
    Years,
    _work_authors,
    author_batches,
    bare_doi,
    doc_type,
    short_id,
)
from .orcid import declared_works, employments, registry_record
from .outcomes import MAX_FAILURES_IN_A_ROW, failure_record, stops_the_job, write_failures
from .people_import import _collection_slot
from .tables import (
    RawRun,
    RawWriter,
    RebuildReport,
    SourceBuilder,
    iso,
    parse_time,
    rebuild_sources,
)
from .text import DETECTED_LANGUAGES, abstract_from_inverted_index, detect_language, strip_markup

__all__ = [
    "HarvestReport",
    "current_runs",
    "harvest",
    "read_openalex_runs",
    "read_orcid_runs",
    "work_text",
]


@dataclass
class HarvestReport:
    """What a harvest did."""

    people: int = 0
    works: dict[str, int] = field(default_factory=dict)
    out_of_window: int = 0
    declared_dois: int = 0
    dois_not_indexed: int = 0
    notes: list[str] = field(default_factory=list)
    rebuild: RebuildReport | None = None
    cancelled: bool = False
    run_id: str = ""
    #: People whose harvest failed, with the cause (see :mod:`cartolex.collect.outcomes`).
    failures: list[dict[str, Any]] = field(default_factory=list)
    #: Why the harvest stopped before the end, if it did.
    stopped: str | None = None
    #: Where OpenAlex's records came from: ``api`` or ``snapshot``.
    source: str = "api"

    def lines(self) -> list[str]:
        out = [f"{self.people} person(s) harvested, {sum(self.works.values())} work(s) received"]
        out += [f"failed: {f['person_id']}: {f['cause']}" for f in self.failures]
        if self.stopped:
            out.append(self.stopped)
        if self.declared_dois:
            out.append(
                f"{self.declared_dois} DOI(s) declared in the registry, "
                f"{self.dois_not_indexed} not in the index"
            )
        if self.out_of_window:
            out.append(f"{self.out_of_window} declared work(s) outside the years asked for")
        if self.rebuild is not None:
            out.append(
                "tables: " + ", ".join(f"{n} {k}" for k, n in sorted(self.rebuild.rows.items()))
            )
            out += self.rebuild.warnings[:20]
        return out + self.notes


def _in_window(work: dict[str, Any], years: tuple[int | None, int | None] | None) -> bool:
    if years is None:
        return True
    year = work.get("publication_year")
    if not isinstance(year, int):
        return True
    first, last = years
    return (first is None or year >= first) and (last is None or year <= last)


class _BatchedWorks:
    """An OpenAlex source whose people's works are asked for a batch of people at a time.

    :meth:`load` asks for every work of a batch's records in one list; each person's
    question is then answered with the works their own records sign, in the list's order.
    A batch whose list fails, or holds a work none of its records signs (a record merged
    into another), is not kept: its people are asked for one by one, and a failure is
    recorded with the person. Every other question goes to the source.
    """

    def __init__(self, source: OpenAlexSource) -> None:
        self.source = source
        self.label = source.label
        self._ids: frozenset[str] = frozenset()
        self._years: Years = None
        self._works: list[tuple[dict[str, Any], set[str]]] = []
        self._fetched: Fetched | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.source, name)

    def load(self, author_ids: Iterable[str], years: Years) -> None:
        """Ask for the works of *author_ids* within *years* in one list (not for one record)."""
        self._ids, self._works, self._fetched = frozenset(), [], None
        ids = frozenset(a for a in author_ids if a)
        if len(ids) < 2:
            return
        try:
            fetched = self.source.works_by_authors(sorted(ids), years)
        except (ServiceError, CacheMiss):
            return
        works = [(w, _work_authors(w) & ids) for w in fetched.data]
        if any(not signed for _w, signed in works):
            return
        self._ids, self._years, self._works, self._fetched = ids, years, works, fetched

    def works_by_authors(self, author_ids: Sequence[str], years: Years) -> Fetched:
        wanted = {a for a in author_ids if a}
        if self._fetched is None or not wanted <= self._ids or years != self._years:
            return self.source.works_by_authors(author_ids, years)
        return replace(self._fetched, data=[w for w, signed in self._works if signed & wanted])


def _openalex_ids(records: Sequence[str]) -> list[str]:
    return [r.split(":", 1)[1] for r in records if r.startswith("openalex:")]


def _targets(project: Project, people: Sequence[str] | None) -> list[tuple[dict, list[str]]]:
    rows = read_source_table(project.layout.table("people"), "people").to_pylist()
    decisions = read_people(project.layout)
    wanted = set(people) if people is not None else None
    out = []
    for row in rows:
        dec = decisions.get(row["person_id"], {})
        if wanted is not None and row["person_id"] not in wanted:
            continue
        if dec.get("merged_into") or dec.get("role") == "excluded":
            continue
        if dec.get("identity") not in ("confirmed", "auto"):
            continue
        records = [
            r
            for r in (dec.get("records") or "").split(";")
            if r.startswith(("openalex:", "orcid:"))
        ]
        if records:
            out.append((row, records))
    return out


def harvest(
    project: Project,
    client: HttpClient,
    *,
    people: Sequence[str] | None = None,
    years: tuple[int | None, int | None] | None = None,
    slot: str | None = None,
    now: datetime | None = None,
    source: OpenAlexSource | None = None,
    batch: int = AUTHOR_BATCH,
) -> HarvestReport:
    """Collect the works of every confirmed person (or of *people*) and rebuild the tables.

    *years* is the window ``(first, last)``, inclusive, either end open when
    ``None``; by default the slot's ``years``. *source* answers the OpenAlex
    requests (default: the API through *client*; a
    :class:`~cartolex.collect.snapshot.SnapshotSource` reads a snapshot); the
    registry is always asked through *client*. From a source asked request by
    request, the works of up to *batch* records of consecutive people are asked
    for in one list (``1``: each person's on their own). A cancel keeps the people
    harvested before it; the person being harvested when it came is left out
    whole. A person whose collection fails is recorded and the others go on.
    """
    now = now or datetime.now(timezone.utc)
    layout = project.layout
    if not layout.table("people").exists():
        raise FileNotFoundError("the project has no people yet: import a list first")
    slot = _collection_slot(project, slot, "collection")
    if years is None:
        years = slot_window(project.config, slot)
    targets = _targets(project, people)
    report = HarvestReport()
    source = source or OpenAlexApi(client)
    report.source = source.label
    registry: dict[str, Any] = {}
    if hasattr(source, "prefetch") and targets:
        _prefetch(client, source, targets, registry)
    batched = _BatchedWorks(source) if batch > 1 and not hasattr(source, "prefetch") else None
    starts: dict[int, list[str]] = {}
    if batched is not None:
        groups = [_openalex_ids(records) for _person, records in targets]
        starts = {b[0]: [a for i in b for a in groups[i]] for b in author_batches(groups, batch)}
    asked = batched if batched is not None else source
    window = list(years) if years else None
    oa_out = RawWriter(
        layout, slot, "openalex", {"years": window, "people": {}, "source": source.label}, now=now
    )
    orcid_out = RawWriter(
        layout, slot, "orcid", {"years": window, "people": {}}, run_id=oa_out.run_id
    )
    report.run_id = oa_out.run_id
    in_a_row = 0
    started = time.monotonic()
    last_error: CollectError | None = None
    try:
        for k, (person, records) in enumerate(targets):
            client.check_cancel()
            pid = person["person_id"]
            _report_progress(client, k, len(targets), report, started)
            if batched is not None and k in starts:
                batched.load(starts[k], years)
            try:
                oa_lines, orcid_lines, meta = _harvest_person(
                    client, asked, person, records, years, report, registry
                )
            except (ServiceError, CacheMiss) as exc:
                report.failures.append(failure_record(pid, "harvest", exc, now=now))
                in_a_row += 1
                last_error = exc
                if stops_the_job(client, exc) or in_a_row >= MAX_FAILURES_IN_A_ROW:
                    report.stopped = (
                        f"the harvest stopped after {in_a_row} failure(s) in a row; "
                        f"{len(targets) - k - 1} person(s) were not asked for"
                    )
                    break
                continue
            in_a_row = 0
            for line in oa_lines:
                oa_out.add(line)
            for line in orcid_lines:
                orcid_out.add(line)
            oa_out.header["people"][pid] = meta
            if meta["orcids"]:
                orcid_out.header["people"][pid] = {"orcids": meta["orcids"]}
            report.people += 1
            report.works[pid] = sum(1 for x in oa_lines if x["type"] == "work")
    except Cancelled:
        report.cancelled = True
    except BaseException:
        oa_out.discard()
        orcid_out.discard()
        raise
    for writer in (oa_out, orcid_out):
        if writer.header["people"]:
            writer.close()
        else:
            writer.discard()
    write_failures(layout, slot, "harvest", report.failures, now=now)
    if report.people:
        report.rebuild = rebuild_sources(layout, project.config)
    client.progress(1.0, "harvest done")
    if report.cancelled:
        raise Cancelled(
            f"harvest cancelled after {report.people} of {len(targets)} people; their works are kept"
        )
    if report.stopped and last_error is not None:
        raise last_error
    return report


def _report_progress(
    client: HttpClient, done: int, total: int, report: HarvestReport, started: float
) -> None:
    """How far the harvest is: the people done, the texts received, the requests sent, and
    the time left at the pace so far."""
    elapsed = time.monotonic() - started
    texts = sum(report.works.values())
    client.progress(
        done / max(1, total),
        f"person {done + 1} of {total}, {texts} texts received",
        code="harvest_people",
        params={
            "n": done,
            "total": total,
            "texts": texts,
            "requests": sum(client.egress.requests.values()),
        },
        eta_s=round(elapsed / done * (total - done)) if done and elapsed >= 5 else None,
    )


def _prefetch(
    client: HttpClient,
    source: Any,
    targets: list[tuple[dict, list[str]]],
    registry: dict[str, Any],
) -> None:
    """For a source read in passes (the snapshot): the registry first, for the DOIs people
    declared, then one pass for every record and work of the job."""
    author_ids: list[str] = []
    dois: set[str] = set()
    for _person, records in targets:
        for record in records:
            scheme, value = record.split(":", 1)
            if scheme == "openalex":
                author_ids.append(value)
            elif scheme == "orcid" and value not in registry:
                client.check_cancel()
                try:
                    registry[value] = declared_works(client, value)
                except (ServiceError, CacheMiss):
                    continue  # asked again with the person, where a failure is recorded
                if registry[value] is not None:
                    dois |= {w.doi for w in registry[value][0] if w.doi}
    source.prefetch(author_ids=author_ids, dois=dois)


def _harvest_person(
    client: HttpClient,
    source: OpenAlexSource,
    person: dict[str, Any],
    records: list[str],
    years: tuple[int | None, int | None] | None,
    report: HarvestReport,
    registry: dict[str, Any],
) -> tuple[list[dict], list[dict], dict[str, Any]]:
    pid = person["person_id"]
    oa_ids = _openalex_ids(records)
    orcids = [r.split(":", 1)[1] for r in records if r.startswith("orcid:")]
    names = [[person["last_name"], person["first_name"] or ""]] + [
        [a["last_name"], a["first_name"] or ""] for a in person.get("aliases") or []
    ]
    meta: dict[str, Any] = {
        "records": records,
        "names": names,
        "orcid": person.get("orcid") or (orcids[0] if orcids else None),
        "orcids": orcids,
        "dois": [],
    }
    oa_lines: list[dict] = []
    orcid_lines: list[dict] = []
    for aid in oa_ids:
        fetched = source.author(aid)
        if fetched is None:
            report.notes.append(f"{pid}: the record {aid} no longer exists")
            continue
        oa_lines.append(
            {
                "type": "author",
                "person_id": pid,
                "retrieved_at": iso(fetched.retrieved_at),
                "record": fetched.data,
            }
        )
    if oa_ids:
        fetched = source.works_by_authors(oa_ids, years)
        for work in fetched.data:
            oa_lines.append(
                {
                    "type": "work",
                    "via": "openalex",
                    "person_id": pid,
                    "retrieved_at": iso(fetched.retrieved_at),
                    "record": work,
                }
            )
    for orcid in orcids:
        got = registry[orcid] if orcid in registry else declared_works(client, orcid)
        if got is None:
            report.notes.append(f"{pid}: the registry has no record for {orcid}")
            continue
        declared, fetched = got
        orcid_lines.append(
            {
                "type": "works",
                "person_id": pid,
                "orcid": orcid,
                "retrieved_at": iso(fetched.retrieved_at),
                "record": fetched.data,
            }
        )
        rec = registry_record(client, orcid)
        if rec is not None:
            orcid_lines.append(
                {
                    "type": "record",
                    "person_id": pid,
                    "orcid": orcid,
                    "retrieved_at": iso(rec.retrieved_at),
                    "record": rec.data,
                }
            )
        dois = sorted({w.doi for w in declared if w.doi})
        meta["dois"] += [d for d in dois if d not in meta["dois"]]
        report.declared_dois += len(dois)
        found = set()
        for work, answer in source.works_by_dois(dois):
            doi = bare_doi(work.get("doi"))
            found.add(doi)
            if not _in_window(work, years):
                report.out_of_window += 1
                continue
            oa_lines.append(
                {
                    "type": "work",
                    "via": "orcid",
                    "person_id": pid,
                    "retrieved_at": iso(answer.retrieved_at),
                    "record": work,
                }
            )
        report.dois_not_indexed += len(set(dois) - found)
    return oa_lines, orcid_lines, meta


# ── readers ──────────────────────────────────────────────────────────────────


def current_runs(runs: list[RawRun]) -> set[str]:
    """The ids of the runs that are some person's latest (the others are superseded whole)."""
    latest: dict[str, str] = {}
    for run in runs:
        for pid in run.header.get("people") or {}:
            latest[pid] = run.run_id
    return set(latest.values())


def _current(runs: list[RawRun]) -> tuple[dict[str, dict], dict[str, list[tuple[RawRun, dict]]]]:
    """Each person's latest run's header entry, and the lines of those runs, by person."""
    latest: dict[str, str] = {}
    meta: dict[str, dict] = {}
    for run in runs:
        for pid, entry in (run.header.get("people") or {}).items():
            latest[pid] = run.run_id
            meta[pid] = entry
    lines: dict[str, list[tuple[RawRun, dict]]] = defaultdict(list)
    current = set(latest.values())
    for run in runs:
        if run.run_id not in current:
            continue
        for rec in run.records():
            if latest.get(rec.get("person_id")) == run.run_id:
                lines[rec["person_id"]].append((run, rec))
    return meta, lines


def _org(builder: SourceBuilder, slot: str, inst: dict[str, Any], at: datetime) -> str | None:
    iid = short_id(inst.get("id"))
    name = inst.get("display_name")
    if not iid or not name:
        return None
    ids = {"openalex": iid}
    if inst.get("ror"):
        ids["ror"] = str(inst["ror"]).rsplit("/", 1)[-1]
    parents = [short_id(x) for x in inst.get("lineage") or []]
    return builder.organisation(
        slot=slot,
        keys=[f"openalex:{iid}"],
        name=name,
        ids=ids,
        country=inst.get("country_code") or None,
        parent_keys=[f"openalex:{p}" for p in parents if p and p != iid],
        source="openalex",
        retrieved_at=at,
    )


def _languages(title: str, abstract: str, declared: str | None) -> tuple[str, str]:
    """The language of the title and of the abstract: the abstract is detected; the title, too
    short to tell alone, takes the abstract's; the service's own language fills a gap."""
    declared = (declared or "").lower()
    fallback = declared if declared in DETECTED_LANGUAGES else "und"
    lang_abstract = detect_language(abstract) if abstract else "und"
    if lang_abstract == "und":
        lang_abstract = fallback
    if abstract:
        return lang_abstract, lang_abstract
    lang_title = detect_language(title)
    return (lang_title if lang_title != "und" else fallback), lang_abstract


def _alphabetical(authorships: list[dict[str, Any]]) -> bool:
    """Four authors or more in alphabetical order of surname: first and last mean nothing."""
    if len(authorships) < 4:
        return False
    surnames = []
    for a in authorships:
        name = (a.get("author") or {}).get("display_name") or a.get("raw_author_name") or ""
        parts = words(name)
        surnames.append(parts[-1] if parts else "")
    return surnames == sorted(surnames)


def read_openalex_runs(runs: list[RawRun], builder: SourceBuilder) -> None:
    """The reader of harvests: texts, parts, authorships, organisations, dated affiliations."""
    meta, lines = _current(runs)
    owners: dict[str, set[str]] = defaultdict(set)
    for pid, entry in meta.items():
        for record in entry.get("records") or []:
            if record.startswith("openalex:"):
                owners[record.split(":", 1)[1]].add(pid)
    # Who declared each DOI, in person order: a work's DOI finds them at once.
    doi_owners: dict[str, list[str]] = defaultdict(list)
    for pid in sorted(meta):
        for doi in sorted(set(meta[pid].get("dois") or [])):
            doi_owners[doi].append(pid)
    orcid_of = {pid: entry.get("orcid") for pid, entry in meta.items()}
    names_of = {pid: [tuple(n) for n in entry.get("names") or []] for pid, entry in meta.items()}
    for pid in sorted(meta):
        if not builder.known_person(pid):
            builder.warnings.append(f"{pid} is no longer in the tables; their harvest is left out")
            continue
        own = lines.get(pid, [])
        for run, rec in own:
            if rec["type"] != "author":
                continue
            at = parse_time(rec["retrieved_at"])
            for aff in rec["record"].get("affiliations") or []:
                oid = _org(builder, run.slot, aff.get("institution") or {}, at)
                years = [y for y in aff.get("years") or [] if isinstance(y, int)]
                if oid:
                    builder.affiliation(
                        pid,
                        oid,
                        min(years) if years else None,
                        max(years) if years else None,
                        "openalex",
                    )
        works = [(run, rec) for run, rec in own if rec["type"] == "work"]
        works.sort(key=lambda x: (not x[1]["record"].get("doi"), x[1]["record"].get("id") or ""))
        for run, rec in works:
            tid = _text(builder, run.slot, pid, rec)
            if tid is not None:
                _link(builder, run.slot, tid, rec, owners, doi_owners, orcid_of, names_of)
                builder.count("works read")


def work_text(work: dict[str, Any]) -> dict[str, Any]:
    """A work's title and abstract as a text holds them: markup stripped, the abstract rebuilt
    from the inverted index, each part's language (the costly part of reading a harvest,
    kept in the run's digest, :mod:`cartolex.collect.digests`)."""
    title, title_format = strip_markup(work.get("title") or work.get("display_name") or "")
    error = False
    try:
        raw_abstract = abstract_from_inverted_index(work.get("abstract_inverted_index"))
    except ValueError:
        error = True
        raw_abstract = ""
    abstract, abstract_format = strip_markup(raw_abstract)
    lang_title, lang_abstract = (
        _languages(title, abstract, work.get("language")) if title else ("und", "und")
    )
    return {
        "title": title,
        "title_format": title_format,
        "abstract": abstract,
        "abstract_format": abstract_format,
        "abstract_error": error,
        "lang_title": lang_title,
        "lang_abstract": lang_abstract,
    }


def _text(builder: SourceBuilder, slot: str, pid: str, rec: dict[str, Any]) -> str | None:
    work = rec["record"]
    at = parse_time(rec["retrieved_at"])
    text = rec.get("text") or work_text(work)
    title, title_format = text["title"], text["title_format"]
    wid = short_id(work.get("id"))
    if not title or not wid:
        builder.warnings.append(f"a work without a title or an id was left out ({wid})")
        return None
    if text["abstract_error"]:
        builder.warnings.append(f"{wid}: its abstract could not be rebuilt; left out")
    abstract, abstract_format = text["abstract"], text["abstract_format"]
    doi = bare_doi(work.get("doi"))
    year = work.get("publication_year") if isinstance(work.get("publication_year"), int) else None
    keys = ([f"doi:{doi}"] if doi else []) + [
        f"openalex:{wid}",
        f"title:{pid}:{' '.join(words(title))}|{year}",
    ]
    lang_title, lang_abstract = text["lang_title"], text["lang_abstract"]
    tid = builder.text(
        slot=slot,
        keys=keys,
        title=title,
        doc_type=doc_type(work),
        source="openalex" if rec.get("via") == "openalex" else "orcid",
        retrieved_at=at,
        year=year,
        date=work.get("publication_date") or None,
        doi=doi,
        ids={"openalex": wid},
        n_authors=len(work.get("authorships") or []),
    )
    builder.part(
        tid,
        part="title",
        language=lang_title,
        provider="openalex",
        content=title,
        format=title_format,
        retrieved_at=at,
    )
    builder.part(
        tid,
        part="abstract",
        language=lang_abstract,
        provider="openalex",
        content=abstract,
        format=abstract_format,
        retrieved_at=at,
    )
    return tid


def _link(
    builder: SourceBuilder,
    slot: str,
    tid: str,
    rec: dict[str, Any],
    owners: dict[str, set[str]],
    doi_owners: dict[str, list[str]],
    orcid_of: dict[str, str | None],
    names_of: dict[str, list[tuple[str, str]]],
) -> None:
    """Every project person on this work, at their rank: by their records, else by their
    registry (the work's DOI declared, and the ORCID or the name on the authorship)."""
    work = rec["record"]
    at = parse_time(rec["retrieved_at"])
    auths = work.get("authorships") or []
    n = len(auths)
    unknown_last = bool(work.get("is_authors_truncated")) or _alphabetical(auths)
    flagged = any(a.get("is_corresponding") for a in auths)
    year = work.get("publication_year") if isinstance(work.get("publication_year"), int) else None
    doi = bare_doi(work.get("doi"))
    at_rank: dict[str, int] = {}
    for k, a in enumerate(auths, start=1):
        aid = short_id((a.get("author") or {}).get("id"))
        for pid in sorted(owners.get(aid or "", ())):
            at_rank.setdefault(pid, k)
    if doi:
        for pid in doi_owners.get(doi, ()):
            if pid in at_rank:
                continue
            for k, a in enumerate(auths, start=1):
                shown = a.get("author") or {}
                orcid = (shown.get("orcid") or "").rsplit("/", 1)[-1] or None
                name = shown.get("display_name") or a.get("raw_author_name") or ""
                if (orcid and orcid == orcid_of.get(pid)) or name_similarity(
                    names_of.get(pid, []), name
                )[0] >= 0.75:
                    at_rank[pid] = k
                    break
            else:
                builder.warnings.append(
                    f"{pid}: a work declared in the registry does not show them among its authors"
                )
    for pid, k in sorted(at_rank.items(), key=lambda kv: kv[1]):
        if not builder.known_person(pid):
            continue
        a = auths[k - 1]
        orgs = [o for o in (_org(builder, slot, i, at) for i in a.get("institutions") or []) if o]
        builder.authorship(
            tid,
            pid,
            position=k,
            orgs=orgs,
            last=None if unknown_last else k == n,
            corresponding=bool(a.get("is_corresponding")) if flagged else None,
        )
        for oid in orgs:
            builder.affiliation(pid, oid, year, year, "stated")


def read_orcid_runs(runs: list[RawRun], builder: SourceBuilder) -> None:
    """The reader of registry answers: the employments people declared, as dated affiliations
    to the organisations of the same name they are already affiliated with."""
    meta, lines = _current(runs)
    for pid in sorted(meta):
        if not builder.known_person(pid):
            continue
        for _run, rec in lines.get(pid, []):
            if rec["type"] != "record":
                continue
            for emp in employments(rec["record"]):
                target = words(emp.organisation)
                mine = [
                    (source != "openalex", oid)
                    for oid, name, source in builder.affiliated_orgs(pid)
                    if words(name) == target
                ]
                oid = min(mine)[1] if mine else builder.org_by_name(emp.organisation)
                if oid is None:
                    builder.count("employments without a known organisation")
                    continue
                builder.affiliation(pid, oid, emp.start, emp.end, "orcid")
