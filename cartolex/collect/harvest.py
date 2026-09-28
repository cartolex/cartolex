# SPDX-License-Identifier: MIT
"""Harvest: the works of every confirmed person, into the source tables.

For each person whose identity is confirmed or accepted automatically, the
records in ``decisions/people.csv`` say where their works are:

* ``openalex:A…`` — every work of these author records, in the year window,
  through cursor pages of 100, and the records themselves (their
  affiliations, with years);
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
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from cartolex.project import Project
from cartolex.project.tables import read_source_table

from .decisions import read_people
from .http import Cancelled, HttpClient
from .names import name_similarity, words
from .openalex import author, bare_doi, doc_type, short_id, works_by_authors, works_by_dois
from .orcid import declared_works, employments, registry_record
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

__all__ = ["HarvestReport", "harvest", "read_openalex_runs", "read_orcid_runs"]


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

    def lines(self) -> list[str]:
        out = [f"{self.people} person(s) harvested, {sum(self.works.values())} work(s) received"]
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
        records = [r for r in (dec.get("records") or "").split(";") if r]
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
) -> HarvestReport:
    """Collect the works of every confirmed person (or of *people*) and rebuild the tables.

    *years* is the window ``(first, last)``, inclusive, either end open when
    ``None``. A cancel keeps the people harvested before it; the person being
    harvested when it came is left out whole.
    """
    now = now or datetime.now(timezone.utc)
    layout = project.layout
    if not layout.table("people").exists():
        raise FileNotFoundError("the project has no people yet: import a list first")
    slot = _collection_slot(project, slot, "collection")
    targets = _targets(project, people)
    report = HarvestReport()
    window = list(years) if years else None
    oa_out = RawWriter(layout, slot, "openalex", {"years": window, "people": {}}, now=now)
    orcid_out = RawWriter(
        layout, slot, "orcid", {"years": window, "people": {}}, run_id=oa_out.run_id
    )
    report.run_id = oa_out.run_id
    try:
        for k, (person, records) in enumerate(targets):
            client.check_cancel()
            pid = person["person_id"]
            client.progress(k / max(1, len(targets)), f"person {k + 1} of {len(targets)}")
            oa_lines, orcid_lines, meta = _harvest_person(client, person, records, years, report)
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
    if report.people:
        report.rebuild = rebuild_sources(layout, project.config)
    client.progress(1.0, "harvest done")
    if report.cancelled:
        raise Cancelled(
            f"harvest cancelled after {report.people} of {len(targets)} people; their works are kept"
        )
    return report


def _harvest_person(
    client: HttpClient,
    person: dict[str, Any],
    records: list[str],
    years: tuple[int | None, int | None] | None,
    report: HarvestReport,
) -> tuple[list[dict], list[dict], dict[str, Any]]:
    pid = person["person_id"]
    oa_ids = [r.split(":", 1)[1] for r in records if r.startswith("openalex:")]
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
        fetched = author(client, aid)
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
        window = None if years is None else (years[0] or 0, years[1] or 0)
        fetched = works_by_authors(client, oa_ids, years=window)
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
        got = declared_works(client, orcid)
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
        for work, answer in works_by_dois(client, dois):
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


def _current(runs: list[RawRun]) -> tuple[dict[str, dict], dict[str, list[tuple[RawRun, dict]]]]:
    """Each person's latest run's header entry, and the lines of those runs, by person."""
    latest: dict[str, str] = {}
    meta: dict[str, dict] = {}
    for run in runs:
        for pid, entry in (run.header.get("people") or {}).items():
            latest[pid] = run.run_id
            meta[pid] = entry
    lines: dict[str, list[tuple[RawRun, dict]]] = defaultdict(list)
    for run in runs:
        if run.run_id not in set(latest.values()):
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
    dois_of = {pid: set(entry.get("dois") or []) for pid, entry in meta.items()}
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
                _link(builder, run.slot, tid, rec, owners, dois_of, orcid_of, names_of)
                builder.count("works read")


def _text(builder: SourceBuilder, slot: str, pid: str, rec: dict[str, Any]) -> str | None:
    work = rec["record"]
    at = parse_time(rec["retrieved_at"])
    title, title_format = strip_markup(work.get("title") or work.get("display_name") or "")
    wid = short_id(work.get("id"))
    if not title or not wid:
        builder.warnings.append(f"a work without a title or an id was left out ({wid})")
        return None
    try:
        raw_abstract = abstract_from_inverted_index(work.get("abstract_inverted_index"))
    except ValueError:
        builder.warnings.append(f"{wid}: its abstract could not be rebuilt; left out")
        raw_abstract = ""
    abstract, abstract_format = strip_markup(raw_abstract)
    doi = bare_doi(work.get("doi"))
    year = work.get("publication_year") if isinstance(work.get("publication_year"), int) else None
    keys = ([f"doi:{doi}"] if doi else []) + [
        f"openalex:{wid}",
        f"title:{pid}:{' '.join(words(title))}|{year}",
    ]
    lang_title, lang_abstract = _languages(title, abstract, work.get("language"))
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
    dois_of: dict[str, set[str]],
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
        for pid, dois in sorted(dois_of.items()):
            if pid in at_rank or doi not in dois:
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
