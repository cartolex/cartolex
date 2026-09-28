# SPDX-License-Identifier: MIT
"""The HAL finder: the works people deposited in the HAL open archive.

A person is searched by their **idHAL** (``authIdHal_s``), over the project's
publication-year window (``producedDateY_i``), with HAL's cursor paging
(``cursorMark``, sorted on ``docid``). Each deposit gives a text: its document
type, year, titles and abstracts per language, its **DOI link**
(``doiId_s``), its arXiv and PubMed identifiers, its authors, and the
structures each author stated, fetched with their parents from HAL's
structure referential. A person without an idHAL is searched by name, and what
comes back is only **proposed** (a run of candidates, grouped by the author
forms HAL has, with the idHAL a form carries): a name never accepts a work.

What HAL returned is stored as received in ``sources/<slot>/raw/hal/``;
:func:`read_hal_runs` builds the table rows from it. See
``docs/dev/collection.md`` for HAL's access policy.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from cartolex.project.layout import ProjectLayout

from .finders import (
    FinderReport,
    PersonRef,
    Window,
    add_parts,
    check_window,
    fold,
    name_matches,
    normalise_doi,
)
from .http import (
    Cancelled,
    CursorPaging,
    HttpClient,
    IncompleteResults,
    ServiceError,
    ServiceUnavailable,
)
from .tables import RawRun, RawWriter, SourceBuilder, iso, parse_time

__all__ = [
    "HAL_DOC_TYPES",
    "HAL_SKIPPED_TYPES",
    "HAL_FIELDS",
    "HAL_PAGING",
    "HalWork",
    "collect_hal",
    "parse_hal_doc",
    "read_hal_candidate_runs",
    "read_hal_runs",
]

#: The languages whose titles and abstracts are asked for (HAL's ``<lang>_title_s`` fields).
HAL_LANGUAGES = ("en", "fr", "es", "pt", "de", "it")
#: The fields asked for each deposit.
HAL_FIELDS: tuple[str, ...] = (
    "docid",
    "halId_s",
    "uri_s",
    "docType_s",
    "language_s",
    "title_s",
    "abstract_s",
    "producedDate_s",
    "producedDateY_i",
    "publicationDateY_i",
    "doiId_s",
    "arxivId_s",
    "pubmedId_s",
    "authFullName_s",
    "authIdHal_s",
    "authIdHalFullName_fs",
    "authIdHasStructure_fs",
    "authORCIDIdExt_s",
    "structId_i",
    "fileMain_s",
    "openAccess_bool",
    *(f"{lang}_title_s" for lang in HAL_LANGUAGES),
    *(f"{lang}_abstract_s" for lang in HAL_LANGUAGES),
)
STRUCTURE_FIELDS = (
    "docid",
    "name_s",
    "acronym_s",
    "type_s",
    "valid_s",
    "country_s",
    "parentDocid_i",
    "ror_s",
)
#: HAL document types → cartolex's. A type not listed is kept as ``other``, except those of
#: :data:`HAL_SKIPPED_TYPES`.
HAL_DOC_TYPES: Mapping[str, str] = {
    "ART": "article",
    "COMM": "communication",
    "POSTER": "communication",
    "PROCEEDINGS": "book",
    "OUV": "book",
    "DOUV": "book",
    "COUV": "chapter",
    "THESE": "thesis",
    "ETABTHESE": "thesis",
    "MEM": "thesis",
    "REPORT": "report",
    "CREPORT": "report",
    "OTHERREPORT": "report",
    "REPACT": "report",
    "SYNTHESE": "report",
    "UNDEFINED": "preprint",
    "PREPRINT": "preprint",
    "WORKINGPAPER": "preprint",
    "LECTURE": "other",
    "ISSUE": "other",
    "NOTICE": "other",
    "TRAD": "other",
    "BLOG": "other",
    "OTHER": "other",
}
#: Types that carry no text to read (images, videos, sound, maps, software, patents): skipped.
HAL_SKIPPED_TYPES = frozenset({"IMG", "VIDEO", "SON", "MAP", "SOFTWARE", "PATENT"})
_FACET = "_FacetSep_"
_JOIN = "_JoinSep_"
#: Rows asked per page; HAL allows up to 10,000.
PAGE_ROWS = 200
#: Structures asked per request, and how many levels of parents are followed.
STRUCTURE_BATCH = 50
PARENT_LEVELS = 4
#: Consecutive people whose search fails before the job stops asking.
MAX_FAILURES_IN_A_ROW = 3

HAL_PAGING = CursorPaging(
    items=lambda d: d["response"]["docs"],
    next_cursor=lambda d: d.get("nextCursorMark"),
    total=lambda d: int(d["response"]["numFound"]),
    cursor_param="cursorMark",
    first="*",
)


def _validate(data: Any) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("response"), dict):
        raise ValueError("not a HAL search answer")
    if not isinstance(data["response"].get("docs"), list):
        raise ValueError("a HAL answer without docs")


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, list | tuple) else [value]


@dataclass(frozen=True)
class HalAuthorForm:
    """One author as a deposit lists them: name, rank, idHAL when known, structures stated."""

    rank: int
    full_name: str
    idhal: str | None
    structures: tuple[tuple[int, str], ...]  # (structure docid, name)


@dataclass(frozen=True)
class HalWork:
    """A deposit, read: what a text is built from."""

    hal_id: str
    doc_type: str  # cartolex's type
    hal_type: str
    year: int | None
    date: str | None
    doi: str | None
    ids: dict[str, str]
    title: str
    language: str | None
    titles: list[tuple[str | None, str]]
    abstracts: list[tuple[str | None, str]]
    authors: tuple[HalAuthorForm, ...]
    file: str | None

    def form_of(self, idhal: str) -> HalAuthorForm | None:
        return next((a for a in self.authors if a.idhal == idhal), None)


def parse_hal_doc(doc: Mapping[str, Any]) -> HalWork | None:
    """A deposit as HAL gives it → :class:`HalWork`; ``None`` for a type that carries no text.

    Raises ``ValueError`` when the record lacks what every deposit has (its id, a title).
    """
    hal_id = doc.get("halId_s")
    titles_all = [str(t) for t in _list(doc.get("title_s")) if t]
    if not hal_id or not titles_all:
        raise ValueError("a HAL record without its id or a title")
    hal_type = str(doc.get("docType_s") or "")
    if hal_type in HAL_SKIPPED_TYPES:
        return None
    doc_type = HAL_DOC_TYPES.get(hal_type, "other")
    languages = [str(x) for x in _list(doc.get("language_s")) if x]
    language = languages[0] if languages else None
    titles: list[tuple[str | None, str]] = []
    abstracts: list[tuple[str | None, str]] = []
    for lang in HAL_LANGUAGES:
        titles += [(lang, str(t)) for t in _list(doc.get(f"{lang}_title_s")) if t][:1]
        abstracts += [(lang, str(t)) for t in _list(doc.get(f"{lang}_abstract_s")) if t][:1]
    if not titles:
        titles = [(language if i == 0 else None, t) for i, t in enumerate(titles_all)]
    if not abstracts:
        abstracts = [
            (language if i == 0 else None, str(t))
            for i, t in enumerate(_list(doc.get("abstract_s")))
            if t
        ]
    names = [str(n) for n in _list(doc.get("authFullName_s"))]
    idhal_of: dict[str, str] = {}
    for item in _list(doc.get("authIdHalFullName_fs")):
        idhal, _, full = str(item).partition(_FACET)
        if full and idhal:
            idhal_of.setdefault(fold(full), idhal)
    structs: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for item in _list(doc.get("authIdHasStructure_fs")):
        author, _, structure = str(item).partition(_JOIN)
        _aid, _, full = author.partition(_FACET)
        sid, _, sname = structure.partition(_FACET)
        if full and sid.strip().isdigit():
            structs[fold(full)].append((int(sid), sname))
    authors = tuple(
        HalAuthorForm(
            rank=i,
            full_name=name,
            idhal=idhal_of.get(fold(name)),
            structures=tuple(dict.fromkeys(structs.get(fold(name), []))),
        )
        for i, name in enumerate(names, start=1)
    )
    ids = {"hal": str(hal_id)}
    if doc.get("arxivId_s"):
        ids["arxiv"] = str(_list(doc["arxivId_s"])[0])
    if doc.get("pubmedId_s"):
        ids["pmid"] = str(_list(doc["pubmedId_s"])[0])
    year = doc.get("producedDateY_i") or doc.get("publicationDateY_i")
    date = doc.get("producedDate_s")
    return HalWork(
        hal_id=str(hal_id),
        doc_type=doc_type,
        hal_type=hal_type,
        year=int(year) if year else None,
        date=str(date)[:10] if date else None,
        doi=normalise_doi(_list(doc.get("doiId_s"))[0] if doc.get("doiId_s") else None),
        ids=ids,
        title=titles[0][1] if titles and titles[0][0] == language else titles_all[0],
        language=language,
        titles=titles,
        abstracts=abstracts,
        authors=authors,
        file=str(doc["fileMain_s"]) if doc.get("fileMain_s") else None,
    )


# ── collecting ───────────────────────────────────────────────────────────────


def _search(
    client: HttpClient, q: str, window: Window, *, kind: str, sends: Sequence[str]
) -> list[dict[str, Any]]:
    params = {
        "q": q,
        "fq": f"producedDateY_i:[{window[0]} TO {window[1]}]",
        "fl": ",".join(HAL_FIELDS),
        "rows": PAGE_ROWS,
        "sort": "docid asc",
        "wt": "json",
    }
    try:
        return list(
            client.get_all(
                "hal",
                "search/",
                params,
                kind=kind,
                sends=sends,
                paging=HAL_PAGING,
                validate=_validate,
            ).data
        )
    except IncompleteResults:
        # A page cut short: the whole list is asked once more before giving up.
        return list(
            client.get_all(
                "hal",
                "search/",
                params,
                kind=kind,
                sends=sends,
                paging=HAL_PAGING,
                validate=_validate,
            ).data
        )


def _quoted(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _structures(client: HttpClient, wanted: Iterable[int]) -> list[dict[str, Any]]:
    """The structures *wanted* and their parents, level by level."""
    found: dict[int, dict[str, Any]] = {}
    todo = sorted(set(wanted))
    for _level in range(PARENT_LEVELS + 1):
        todo = [s for s in todo if s not in found]
        if not todo:
            break
        parents: set[int] = set()
        for i in range(0, len(todo), STRUCTURE_BATCH):
            batch = todo[i : i + STRUCTURE_BATCH]
            data = client.get_json(
                "hal",
                "ref/structure/",
                {
                    "q": "docid:(" + " OR ".join(str(s) for s in batch) + ")",
                    "fl": ",".join(STRUCTURE_FIELDS),
                    "rows": len(batch),
                    "wt": "json",
                },
                kind="hal_structures",
                sends=["identifier"],
                validate=_validate,
            ).data
            for doc in data["response"]["docs"]:
                if isinstance(doc, dict) and str(doc.get("docid", "")).isdigit():
                    found[int(doc["docid"])] = doc
                    parents.update(
                        int(p) for p in _list(doc.get("parentDocid_i")) if str(p).isdigit()
                    )
        todo = sorted(parents)
    return [found[k] for k in sorted(found)]


def collect_hal(
    client: HttpClient,
    layout: ProjectLayout,
    slot: str,
    people: Sequence[PersonRef],
    *,
    window: Window,
    name_fallback: bool = True,
    now: Callable[[], datetime] | None = None,
) -> FinderReport:
    """Collect the HAL deposits of *people* in *window* into *slot*'s raw folder.

    People with an idHAL are searched by it and their deposits kept; the others
    (with *name_fallback*) are searched by name, and the author forms that
    match are only proposed, in a ``hal_candidates`` run. Progress, cancel and
    the egress record go through *client*. A person whose search fails is
    reported and the job goes on; after three failures in a row the service is
    taken to be down and the job stops asking (what was found is kept).
    """
    window = check_window(window)
    clock = now or (lambda: datetime.now(timezone.utc))
    report = FinderReport("hal", window, people=len(people))
    header = {"service": "hal", "window": list(window), "people": len(people)}
    wanted_structures: set[int] = set()
    failures_in_a_row = 0
    proposals: list[dict[str, Any]] = []
    with RawWriter(layout, slot, "hal", header) as run:
        for n, person in enumerate(people):
            client.progress(n / max(1, len(people)), f"HAL: person {n + 1} of {len(people)}")
            if failures_in_a_row >= MAX_FAILURES_IN_A_ROW:
                report.stopped = "HAL stopped answering; the other people were not searched"
                report.skip("not searched", len(people) - n)
                break
            try:
                if person.idhal:
                    _by_idhal(client, person, window, run, report, clock, wanted_structures)
                elif name_fallback:
                    _by_name(client, person, window, proposals, report)
                else:
                    report.skip("no idHAL")
                failures_in_a_row = 0
            except Cancelled:
                raise
            except ServiceError as exc:
                if _budget_spent(client, exc):
                    raise
                failures_in_a_row += 1
                report.failures.append({"person_id": person.person_id, "error": str(exc)})
        if wanted_structures:
            client.progress(1.0, "HAL: structures")
            try:
                for doc in _structures(client, wanted_structures):
                    run.add({"type": "structure", "retrieved_at": iso(clock()), "doc": doc})
            except ServiceError as exc:
                if _budget_spent(client, exc):
                    raise
                report.failures.append({"person_id": "", "error": f"structures: {exc}"})
    report.runs.append(run.path)
    if proposals:
        with RawWriter(layout, slot, "hal_candidates", header) as run:
            for candidate in proposals:
                run.add(candidate)
        report.runs.append(run.path)
    return report


def _budget_spent(client: HttpClient, exc: ServiceError) -> bool:
    """Whether the service asked for a wait longer than a job may block (stop the job)."""
    limit = client.settings.retry.max_retry_after
    return isinstance(exc, ServiceUnavailable) and (exc.retry_after or 0) > limit


def _by_idhal(
    client: HttpClient,
    person: PersonRef,
    window: Window,
    run: RawWriter,
    report: FinderReport,
    clock: Callable[[], datetime],
    wanted_structures: set[int],
) -> None:
    for idhal in person.idhal:
        docs = _search(
            client, f"authIdHal_s:{_quoted(idhal)}", window, kind="hal_works", sends=["identifier"]
        )
        at = iso(clock())
        for doc in docs:
            try:
                work = parse_hal_doc(doc)
            except ValueError:
                report.skip("malformed record")
                continue
            if work is None:
                report.skip("type without text")
                continue
            form = work.form_of(idhal)
            if form is None:  # the idHAL is on the record but not tied to a name
                report.skip("idHAL without a name")
                continue
            wanted_structures.update(s for s, _ in form.structures)
            run.add(
                {
                    "type": "work",
                    "person_id": person.person_id,
                    "how": "idhal",
                    "idhal": idhal,
                    "retrieved_at": at,
                    "doc": doc,
                }
            )
            report.works += 1
            report.found[person.person_id] = report.found.get(person.person_id, 0) + 1


def _by_name(
    client: HttpClient,
    person: PersonRef,
    window: Window,
    proposals: list[dict[str, Any]],
    report: FinderReport,
) -> None:
    forms: dict[str, dict[str, Any]] = {}
    for last, first in person.names():
        full = " ".join(x for x in (first, last) if x)
        docs = _search(
            client,
            f"authFullName_t:{_quoted(full)}",
            window,
            kind="hal_name_search",
            sends=["name"],
        )
        for doc in docs:
            try:
                work = parse_hal_doc(doc)
            except ValueError:
                continue
            if work is None:
                continue
            for form in work.authors:
                first_f, _, last_f = form.full_name.rpartition(" ")
                if not name_matches(person, first_f, last_f):
                    continue
                key = form.idhal or f"name:{fold(form.full_name)}"
                entry = forms.setdefault(
                    key,
                    {
                        "idhal": form.idhal,
                        "full_name": form.full_name,
                        "works": [],
                        "structures": [],
                    },
                )
                if work.hal_id not in entry["works"]:
                    entry["works"].append(work.hal_id)
                for _sid, sname in form.structures:
                    if sname not in entry["structures"]:
                        entry["structures"].append(sname)
                entry.setdefault("years", [])
                if work.year and work.year not in entry["years"]:
                    entry["years"].append(work.year)
    for key in sorted(forms):
        entry = forms[key]
        entry["years"] = sorted(entry.get("years", []))
        candidate = {"type": "candidate", "person_id": person.person_id, **entry}
        proposals.append(candidate)
        report.candidates.append(candidate)


# ── reading ──────────────────────────────────────────────────────────────────


def _structure_org(builder: SourceBuilder, slot: str, doc: Mapping[str, Any], at: datetime) -> str:
    docid = int(doc["docid"])
    ids = {"hal": str(docid)}
    if doc.get("ror_s"):
        ids["ror"] = str(_list(doc["ror_s"])[0]).rsplit("/", 1)[-1]
    country = str(doc["country_s"]).upper()[:2] if doc.get("country_s") else None
    return builder.organisation(
        slot=slot,
        keys=[f"hal-structure:{docid}"],
        name=str(doc.get("name_s") or doc.get("label_s") or docid),
        acronym=str(doc["acronym_s"]) if doc.get("acronym_s") else None,
        parent_keys=[
            f"hal-structure:{int(p)}" for p in _list(doc.get("parentDocid_i")) if str(p).isdigit()
        ],
        ids=ids,
        country=country,
        source="hal",
        retrieved_at=at,
    )


def read_hal_runs(runs: list[RawRun], builder: SourceBuilder) -> None:
    """Rows from the HAL runs of one slot: organisations for the structures, a text per
    deposit with its parts, and the authorship of the person it was found for."""
    for run in runs:
        records = list(run.records())
        for rec in records:
            if rec.get("type") == "structure":
                _structure_org(builder, run.slot, rec["doc"], parse_time(rec["retrieved_at"]))
        for rec in records:
            if rec.get("type") != "work":
                continue
            at = parse_time(rec["retrieved_at"])
            try:
                work = parse_hal_doc(rec["doc"])
            except ValueError:
                builder.count("hal: malformed record")
                continue
            if work is None:
                continue
            tid = builder.text(
                slot=run.slot,
                keys=[f"hal:{work.hal_id}"],
                title=work.title,
                doc_type=work.doc_type,
                source="hal",
                retrieved_at=at,
                year=work.year,
                date=work.date,
                doi=work.doi,
                ids=work.ids,
                n_authors=len(work.authors),
            )
            add_parts(builder, tid, "title", work.titles, provider="hal", retrieved_at=at)
            add_parts(builder, tid, "abstract", work.abstracts, provider="hal", retrieved_at=at)
            pid = rec.get("person_id")
            form = work.form_of(rec.get("idhal") or "")
            if not pid or form is None or not builder.known_person(pid):
                continue
            orgs = []
            for sid, sname in form.structures:
                key = f"hal-structure:{sid}"
                oid = builder.registry.lookup("organisations", [key], run.slot)
                if oid is None or oid not in builder.orgs:
                    oid = builder.organisation(
                        slot=run.slot,
                        keys=[key],
                        name=sname or str(sid),
                        ids={"hal": str(sid)},
                        source="hal",
                        retrieved_at=at,
                    )
                orgs.append(oid)
                builder.affiliation(pid, oid, work.year, work.year, "stated")
            builder.authorship(
                tid,
                pid,
                position=form.rank,
                orgs=orgs,
                last=form.rank == len(work.authors),
            )
            builder.count("hal: authorships")


def read_hal_candidate_runs(runs: list[RawRun], builder: SourceBuilder) -> None:
    """Candidates are proposals kept for review: no table row is built from them."""
    return None
