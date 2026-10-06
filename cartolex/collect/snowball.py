# SPDX-License-Identifier: MIT
"""Collaborators: the co-authors of confirmed seeds, proposed round by round.

A field is more than its list of people: the people they write with carry
part of its vocabulary. :func:`snowball` proposes them **round by round**:

* round 1 are the co-authors of the **seeds** (the confirmed, mapped people
  of the project with an OpenAlex record, or the people named), round 2 the
  co-authors of round 1 (those not refused), and so on;
* a work with more than ``max_authors`` authors (25 by default) is left out of
  the co-author graph: a large collaboration says little about who works with
  whom;
* **whole rounds only**: rounds are taken while the people proposed stay within
  the **cap**; the round that would pass it is left out whole, and named in a
  warning. Both are parameters of ``params.json``
  (``collect.snowball.cap``, 200 by default, and
  ``collect.snowball.max_authors``);
* each collaborator comes with its evidence: the joint works, the people of
  the round before they wrote with (the seeds, in round 1), the **path** back
  to a seed, the last joint year, the organisation stated on the latest joint
  work, and the **topical fit** (:func:`topical_fit`).

**Topical fit.** The cosine similarity between the words of the candidate's
titles and abstracts in the window (their works other than the joint ones, which
say what they work on beyond the collaboration; the joint ones when they have
no other) and the words of the seeds' titles and abstracts: words of three letters or more,
folded (case and accents aside), function words left out, each weighted by
``(1 + ln tf) × idf`` with ``idf = 1 + ln((1 + N) / (1 + df))`` over the N
texts of the seeds and the round's candidates; every seed weighs the same in
the seeds' profile. 1 means the same vocabulary in the same proportions, 0 no
word in common.

The collaborators proposed enter the tables as people (source
``collaborators``) with their records confirmed and the role **context**
(their texts shape the lexicon with a weight; they are not on the map), and a
row each in ``decisions/snowball.csv`` with the decision ``context``;
:func:`decide_collaborators` changes it (``mapped``, ``projected``, ``no``,
``later``). What each round received is kept in ``sources/<slot>/raw/snowball/``.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from importlib.resources import files
from typing import Any

from cartolex.project import Project
from cartolex.project.files import StaleWrite, fingerprint, write_decision
from cartolex.project.identity import merge_roots, merged_groups
from cartolex.project.tables import decision_csv_bytes, read_decision_csv

from .decisions import collect_params, decided_now, read_people, slot_window, update_people
from .names import split_full_name, words
from .openalex import OpenAlexSource, Years, short_id
from .people_import import _collection_slot, _registry_ids
from .tables import RawRun, RawWriter, SourceBuilder, iso, parse_time, rebuild_sources
from .text import abstract_from_inverted_index

__all__ = [
    "DECISIONS",
    "FIT_MEASURE",
    "Collaborator",
    "SnowballReport",
    "decide_collaborators",
    "read_snowball",
    "read_snowball_runs",
    "snowball",
    "topical_fit",
]

#: What a collaborator's decision makes of them in ``decisions/people.csv``.
DECISIONS = {
    "mapped": "mapped",
    "context": "context",
    "projected": "projected",
    "no": "excluded",
    "later": "undecided",
}
FIT_MEASURE = (
    "cosine similarity of the words of titles and abstracts (three letters or more, folded, "
    "function words left out), weighted by (1 + ln tf) × idf over the round's texts, "
    "between the candidate's works other than the joint ones (the joint ones when there is "
    "no other) and the seeds' works (every seed weighing the same)"
)
KIND = "snowball"


# ── topical fit ──────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _function_words() -> frozenset[str]:
    """The packaged function words of every language, folded."""
    root = files("cartolex._data") / "stopwords"
    out: set[str] = set()
    core = json.loads((root / "core.json").read_text(encoding="utf-8"))
    for key, values in core.items():
        if key.startswith(("midwords_", "blacklist_", "connectives_", "header_noise_lead_")):
            out.update(w for v in values for w in words(v))
    function = json.loads((root / "function_words.json").read_text(encoding="utf-8"))
    for key, values in function.items():
        if not key.startswith("_"):
            out.update(w for v in values for w in words(v))
    return frozenset(out)


def _terms(text: str) -> list[str]:
    stop = _function_words()
    return [w for w in words(text) if len(w) >= 3 and not w.isdigit() and w not in stop]


def _text_of(work: Mapping[str, Any]) -> str:
    title = work.get("title") or work.get("display_name") or ""
    try:
        abstract = abstract_from_inverted_index(work.get("abstract_inverted_index"))
    except ValueError:
        abstract = ""
    return f"{title}\n{abstract}"


def topical_fit(
    seeds: Mapping[str, Sequence[Mapping[str, Any]]],
    candidates: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, float]:
    """The fit of each candidate to the seeds, from their works (see the module docstring).

    *seeds* and *candidates* map a key to works in the index's shape (a title
    and an inverted-index abstract). Returns candidate → fit, rounded to three
    decimals; a candidate without words, or seeds without words, fit 0.
    """
    docs: dict[str, Counter[str]] = {}
    owners: dict[str, dict[str, list[str]]] = {"seed": {}, "cand": {}}
    for side, group in (("seed", seeds), ("cand", candidates)):
        for key, works in group.items():
            ids = []
            for w in works:
                wid = short_id(w.get("id")) or f"{side}:{key}:{len(ids)}"
                if wid not in docs:
                    docs[wid] = Counter(_terms(_text_of(w)))
                ids.append(wid)
            owners[side][key] = ids
    n = len(docs)
    df: Counter[str] = Counter()
    for counts in docs.values():
        df.update(counts.keys())
    idf = {t: 1.0 + math.log((1 + n) / (1 + d)) for t, d in df.items()}

    def vector(doc_ids: Iterable[str]) -> dict[str, float]:
        tf: Counter[str] = Counter()
        for d in dict.fromkeys(doc_ids):
            tf.update(docs[d])
        vec = {t: (1.0 + math.log(c)) * idf[t] for t, c in tf.items()}
        norm = math.sqrt(sum(v * v for v in vec.values()))
        return {t: v / norm for t, v in vec.items()} if norm else {}

    profile: dict[str, float] = defaultdict(float)
    seed_vectors = [vector(ids) for ids in owners["seed"].values()]
    seed_vectors = [v for v in seed_vectors if v]
    for vec in seed_vectors:
        for t, v in vec.items():
            profile[t] += v / len(seed_vectors)
    norm = math.sqrt(sum(v * v for v in profile.values()))
    out = {}
    for key, ids in owners["cand"].items():
        vec = vector(ids)
        dot = sum(v * profile.get(t, 0.0) for t, v in vec.items())
        out[key] = round(dot / norm, 3) if norm and vec else 0.0
    return out


# ── the rounds ───────────────────────────────────────────────────────────────


@dataclass
class Collaborator:
    """One collaborator proposed, with the evidence to decide."""

    round: int
    record: str
    name: str
    orcid: str | None
    parents: dict[str, int]
    path: list[str]
    joint_texts: int
    last_joint_year: int | None
    fit: float
    organisation: str | None
    works: int
    person_id: str | None = None

    def describe(self) -> str:
        via = " > ".join(self.path)
        org = f", {self.organisation}" if self.organisation else ""
        return (
            f"{self.person_id or self.record}  {self.name}  fit {self.fit:.3f}  "
            f"{self.joint_texts} joint work(s), last {self.last_joint_year}{org}\n"
            f"      path {via}"
        )


@dataclass
class SnowballReport:
    """What :func:`snowball` did."""

    seeds: list[str] = field(default_factory=list)
    rounds: list[dict[str, Any]] = field(default_factory=list)
    collaborators: list[Collaborator] = field(default_factory=list)
    cap: int = 0
    max_authors: int = 0
    already: int = 0
    cut: str | None = None
    large_works: int = 0
    run_id: str = ""
    fit_measure: str = FIT_MEASURE

    def lines(self, show: int | None = 20) -> list[str]:
        out = [
            f"seeds: {len(self.seeds)}; "
            + "; ".join(f"round {r['round']}: {r['proposed']} collaborator(s)" for r in self.rounds)
            if self.rounds
            else f"seeds: {len(self.seeds)}; no round taken"
        ]
        if self.large_works:
            out.append(
                f"{self.large_works} work(s) with more than {self.max_authors} authors left out "
                "of the co-author graph"
            )
        if self.cut:
            out.append("warning: " + self.cut)
        ranked = sorted(self.collaborators, key=lambda c: (c.round, -c.fit, c.record))
        out += ["  " + c.describe() for c in ranked[:show]]
        return out

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def read_snowball(project: Project) -> list[dict[str, str]]:
    """The rows of ``decisions/snowball.csv`` (empty when there is none)."""
    return read_decision_csv(project.layout.snowball_csv, "snowball")


def _write_snowball(
    project: Project, changes: Mapping[tuple[str, str], Mapping[str, str]], *, action: str
) -> None:
    """Merge *changes* ((round, person id) → columns) into ``decisions/snowball.csv``."""
    layout = project.layout
    for attempt in (1, 2):
        expected = fingerprint(layout.snowball_csv)
        rows = {(r["round"], r["person_id"]): r for r in read_snowball(project)}
        for key, values in changes.items():
            row = dict(rows.get(key) or {"round": key[0], "person_id": key[1]})
            row.update({k: str(v) for k, v in values.items()})
            rows[key] = row
        try:
            write_decision(
                layout,
                layout.snowball_csv,
                decision_csv_bytes("snowball", list(rows.values())),
                expected=expected,
                action=action,
            )
            return
        except StaleWrite:
            if attempt == 2:
                raise


def _seed_people(project: Project, seeds: Sequence[str] | None) -> dict[str, list[str]]:
    """Seed person id → their OpenAlex records: the named people, else every confirmed,
    mapped person with one."""
    rows = read_people(project.layout)
    roots = merge_roots(rows)
    groups = merged_groups(roots)
    out = {}
    for pid, row in sorted(rows.items()):
        if seeds is not None and pid not in seeds:
            continue
        if pid in roots or row.get("role") == "excluded":
            continue
        if seeds is None and row.get("role") != "mapped":
            continue
        # their records and those of the rows merged into them, when accepted
        records = [
            r.split(":", 1)[1]
            for one in (pid, *groups.get(pid, ()))
            if (rows.get(one) or {}).get("identity") in ("confirmed", "auto")
            for r in ((rows.get(one) or {}).get("records") or "").split(";")
            if r.startswith("openalex:")
        ]
        if records:
            out[pid] = list(dict.fromkeys(records))
    if seeds is not None:
        missing = sorted(set(seeds) - set(out))
        if missing:
            raise ValueError("these seeds have no confirmed OpenAlex record: " + ", ".join(missing))
    return out


def _known_records(project: Project) -> dict[str, str]:
    out = {}
    for pid, row in read_people(project.layout).items():
        for record in (row.get("records") or "").split(";"):
            if record.startswith("openalex:"):
                out.setdefault(record.split(":", 1)[1], pid)
    return out


def _known_orcids(project: Project) -> set[str]:
    """The ORCIDs of the project's people: an index record showing one of them is that
    person, even when it is not among their confirmed records (a record mixing two people)."""
    out = set()
    for row in read_people(project.layout).values():
        for record in (row.get("records") or "").split(";"):
            if record.startswith("orcid:"):
                out.add(record.split(":", 1)[1])
    path = project.layout.table("people")
    if path.exists():
        from cartolex.project.tables import read_source_table

        out |= {o for o in read_source_table(path, "people", ["orcid"])["orcid"].to_pylist() if o}
    return out


def snowball(
    project: Project,
    source: OpenAlexSource,
    *,
    rounds: int = 1,
    seeds: Sequence[str] | None = None,
    years: Years = None,
    cap: int | None = None,
    max_authors: int | None = None,
    slot: str | None = None,
    now: datetime | None = None,
) -> SnowballReport:
    """Propose the next *rounds* rounds of collaborators (see the module docstring).

    The first call starts from the seeds (*seeds*, or every confirmed mapped
    person with an OpenAlex record); a later call goes on from the last round
    in ``decisions/snowball.csv``, from its collaborators not refused. *cap*
    and *max_authors* default to ``params.json``'s (``collect.snowball``), the
    years to the slot's window.
    """
    if rounds < 1:
        raise ValueError("rounds must be at least 1")
    now = now or datetime.now(timezone.utc)
    params = collect_params(project, "snowball")
    cap = params["cap"] if cap is None else cap
    max_authors = params["max_authors"] if max_authors is None else max_authors
    slot = _collection_slot(project, slot, "collection")
    if years is None:
        years = slot_window(project.config, slot)
    previous = read_snowball(project)
    last_round = max((int(r["round"]) for r in previous), default=0)
    known = _known_records(project)
    known_orcids = _known_orcids(project)
    report = SnowballReport(cap=cap, max_authors=max_authors, already=len(previous))
    seed_people = _first_seeds(project) if last_round else None
    if seed_people is None:
        seed_people = _seed_people(project, seeds)
    report.seeds = sorted(seed_people)
    if not seed_people:
        raise ValueError("no seed: confirm the records of mapped people first")
    # The people each round starts from, as (key, author records): person ids.
    if last_round == 0:
        parents = {pid: records for pid, records in seed_people.items()}
        paths = {pid: [pid] for pid in parents}
    else:
        parents, paths = {}, {}
        rows = read_people(project.layout)
        for r in previous:
            if int(r["round"]) != last_round or r["decision"] == "no":
                continue
            records = [
                x.split(":", 1)[1]
                for x in (rows.get(r["person_id"], {}).get("records") or "").split(";")
                if x.startswith("openalex:")
            ]
            if records:
                parents[r["person_id"]] = records
                paths[r["person_id"]] = r["path"].split(">") if r["path"] else [r["person_id"]]
    proposed_before = len({r["person_id"] for r in previous})
    seen = set(known) | {a for recs in parents.values() for a in recs}
    seed_works = source.works_of_authors(
        sorted({a for recs in seed_people.values() for a in recs}), years
    )
    seed_texts = {
        pid: _dedupe(w for a in records for w in seed_works.get(a, ()))
        for pid, records in seed_people.items()
    }
    records_out: list[dict[str, Any]] = []
    total = proposed_before
    for k in range(last_round + 1, last_round + rounds + 1):
        if not parents:
            break
        parent_works = (
            seed_works
            if k == 1
            else source.works_of_authors(
                sorted({a for recs in parents.values() for a in recs}), years
            )
        )
        owner = {a: key for key, recs in parents.items() for a in recs}
        found: dict[str, dict[str, Any]] = {}
        large: set[str] = set()
        for aid in sorted(parent_works):
            for work in parent_works[aid]:
                wid = short_id(work.get("id")) or ""
                auths = work.get("authorships") or []
                if len(auths) > max_authors:
                    large.add(wid)
                    continue
                present = {short_id((a.get("author") or {}).get("id")) for a in auths}
                mine = sorted({owner[x] for x in present if x in owner})
                year = (
                    work.get("publication_year")
                    if isinstance(work.get("publication_year"), int)
                    else None
                )
                for a in auths:
                    shown = a.get("author") or {}
                    cid = short_id(shown.get("id"))
                    if not cid or cid in seen or cid in owner:
                        continue
                    if (shown.get("orcid") or "").rsplit("/", 1)[-1] in known_orcids:
                        continue  # a record of someone already in the project
                    entry = found.setdefault(
                        cid,
                        {
                            "name": shown.get("display_name") or a.get("raw_author_name") or cid,
                            "orcid": (shown.get("orcid") or "").rsplit("/", 1)[-1] or None,
                            "parents": Counter(),
                            "joint": {},
                        },
                    )
                    if wid not in entry["joint"]:
                        entry["joint"][wid] = {
                            "id": wid,
                            "year": year,
                            "institutions": [i for i in a.get("institutions") or [] if i.get("id")],
                        }
                        for p in mine:
                            entry["parents"][p] += 1
        report.large_works += len(large)
        if total + len(found) > cap:
            report.cut = (
                f"round {k} ({len(found)} collaborator(s)) would take the people proposed "
                f"past the cap of {cap} ({total} already): it is left out whole; raise "
                "collect.snowball.cap in params.json to take it"
            )
            break
        cand_works = source.works_of_authors(sorted(found), years) if found else {}
        fits = topical_fit(
            seed_texts,
            {c: _own_texts(cand_works.get(c, ()), parent_works, found[c]) for c in found},
        )
        next_parents: dict[str, list[str]] = {}
        for cid in sorted(found):
            entry = found[cid]
            best = min(entry["parents"], key=lambda p: (-entry["parents"][p], p))
            joint = sorted(entry["joint"].values(), key=lambda j: (j["year"] or 0, j["id"]))
            years_known = [j["year"] for j in joint if j["year"] is not None]
            latest = joint[-1]
            org = latest["institutions"][0] if latest["institutions"] else None
            key = f"openalex:{cid}"
            record = {
                "type": "collaborator",
                "round": k,
                "key": key,
                "record": key,
                "name": entry["name"],
                "orcid": entry["orcid"],
                "parents": dict(sorted(entry["parents"].items())),
                "path": [*paths[best], key],
                "joint_texts": len(joint),
                "last_joint_year": max(years_known) if years_known else None,
                "fit": fits.get(cid, 0.0),
                "organisation": org,
                "joint": joint,
                "works": len(cand_works.get(cid, ())),
                "retrieved_at": iso(now),
            }
            records_out.append(record)
            next_parents[key] = [cid]
            paths[key] = record["path"]
            seen.add(cid)
        report.rounds.append({"round": k, "proposed": len(found)})
        total += len(found)
        parents = next_parents
    header = {
        "seeds": {pid: records for pid, records in sorted(seed_people.items())},
        "years": list(years) if years else None,
        "cap": cap,
        "max_authors": max_authors,
        "rounds": report.rounds,
        "cut": report.cut,
        "fit": FIT_MEASURE,
        "source": source.label,
    }
    # Written even when nothing was proposed: the run records the seeds and the cut.
    with RawWriter(project.layout, slot, KIND, header, now=now) as out:
        for rec in records_out:
            out.add(rec)
    report.run_id = out.run_id
    if records_out:
        rebuild_sources(project.layout, project.config)
        pid_of = _registry_ids(project, slot, [r["key"] for r in records_out])

        def person(key: str) -> str:
            return pid_of.get(key) or key

        people_changes = {}
        rows = {}
        stamp = decided_now(now)
        for rec in records_out:
            pid = person(rec["key"])
            people_changes[pid] = {
                "role": "context",
                "identity": "confirmed",
                "records": rec["record"],
                "note": f"collaborator, round {rec['round']}",
            }
            rows[(str(rec["round"]), pid)] = {
                "seeds": ";".join(person(p) for p in rec["parents"]),
                "path": ">".join(person(p) for p in rec["path"]),
                "joint_texts": str(rec["joint_texts"]),
                "last_joint_year": ""
                if rec["last_joint_year"] is None
                else str(rec["last_joint_year"]),
                "fit": f"{rec['fit']:.3f}",
                "decision": "context",
                "decided_at": stamp,
            }
            report.collaborators.append(
                Collaborator(
                    round=rec["round"],
                    record=rec["record"],
                    name=rec["name"],
                    orcid=rec["orcid"],
                    parents={person(p): n for p, n in rec["parents"].items()},
                    path=[person(p) for p in rec["path"]],
                    joint_texts=rec["joint_texts"],
                    last_joint_year=rec["last_joint_year"],
                    fit=rec["fit"],
                    organisation=(rec["organisation"] or {}).get("display_name"),
                    works=rec["works"],
                    person_id=pid,
                )
            )
        update_people(project.layout, people_changes, action="collaborators proposed", now=now)
        _write_snowball(project, rows, action="collaborators proposed")
    return report


def _first_seeds(project: Project) -> dict[str, list[str]] | None:
    """The seeds of the first snowball run of the project (later rounds keep them)."""
    from .tables import read_runs

    for slot in (s.id for s in project.config.slots):
        runs = read_runs(project.layout, slot, KIND)
        if runs:
            seeds = runs[0].header.get("seeds") or {}
            return {pid: list(records) for pid, records in seeds.items()}
    return None


def _dedupe(works: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for w in works:
        out.setdefault(short_id(w.get("id")) or str(len(out)), w)
    return list(out.values())


def _own_texts(
    works: Sequence[Mapping[str, Any]],
    parent_works: Mapping[str, Sequence[Mapping[str, Any]]],
    entry: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    """The texts a candidate's fit is measured on: their works other than the joint ones,
    or the joint ones when they have no other."""
    joint = set(entry["joint"])
    own = [w for w in _dedupe(works) if short_id(w.get("id")) not in joint]
    if own:
        return own
    return _dedupe(w for ws in parent_works.values() for w in ws if short_id(w.get("id")) in joint)


def decide_collaborators(
    project: Project, decisions: Mapping[str, str], *, now: datetime | None = None
) -> int:
    """Record decisions about collaborators (person id → ``mapped``, ``context``,
    ``projected``, ``no`` or ``later``): in ``decisions/snowball.csv`` and, as their
    role, in ``decisions/people.csv``. Returns how many were changed."""
    rows = {r["person_id"]: r for r in read_snowball(project)}
    for pid, decision in decisions.items():
        if decision not in DECISIONS:
            raise ValueError(f"{decision!r} is not a decision ({', '.join(DECISIONS)})")
        if pid not in rows:
            raise ValueError(f"{pid} is not a collaborator proposed")
    stamp = decided_now(now)
    changes = {
        (rows[pid]["round"], pid): {"decision": decision, "decided_at": stamp}
        for pid, decision in decisions.items()
    }
    _write_snowball(project, changes, action="collaborators decided")
    roles = {
        pid: {
            "role": DECISIONS[decision],
            "set": "collaborators" if decision == "projected" else "",
        }
        for pid, decision in decisions.items()
    }
    if any(d == "projected" for d in decisions.values()):
        from .people_import import _overlays

        _overlays(project, {"collaborators"})
    return update_people(project.layout, roles, action="collaborators decided", now=now)


def read_snowball_runs(runs: list[RawRun], builder: SourceBuilder) -> None:
    """The reader of collaborators' rounds: a person per collaborator (source
    ``collaborators``), the organisations stated on their joint works, dated affiliations."""
    for run in runs:
        for rec in run.records():
            if rec.get("type") != "collaborator":
                continue
            at = parse_time(rec["retrieved_at"])
            last, first = split_full_name(rec["name"])
            aid = rec["record"].split(":", 1)[1]
            pid = builder.person(
                slot=run.slot,
                keys=[rec["key"]],
                last_name=last or rec["name"],
                first_name=first or None,
                orcid=rec.get("orcid"),
                ids={"openalex": [aid]},
                source="collaborators",
                retrieved_at=at,
            )
            spans: dict[str, list[int]] = {}
            for joint in rec.get("joint") or []:
                for inst in joint.get("institutions") or []:
                    iid = short_id(inst.get("id"))
                    name = inst.get("display_name")
                    if not iid or not name:
                        continue
                    lineage = [short_id(x) for x in inst.get("lineage") or []]
                    ids = {"openalex": iid}
                    if inst.get("ror"):
                        ids["ror"] = str(inst["ror"]).rsplit("/", 1)[-1]
                    oid = builder.organisation(
                        slot=run.slot,
                        keys=[f"openalex:{iid}"],
                        name=name,
                        ids=ids,
                        country=inst.get("country_code") or None,
                        parent_keys=[f"openalex:{p}" for p in lineage if p and p != iid],
                        source="openalex",
                        retrieved_at=at,
                    )
                    if joint.get("year") is not None:
                        span = spans.setdefault(oid, [joint["year"], joint["year"]])
                        span[0], span[1] = min(span[0], joint["year"]), max(span[1], joint["year"])
            for oid, (start, end) in sorted(spans.items()):
                builder.affiliation(pid, oid, start, end, "stated")
