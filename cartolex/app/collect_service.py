# SPDX-License-Identifier: MIT
"""The real collection behind the app's :class:`~cartolex.app.collection.CollectionService`.

:class:`ServiceCollection` runs the finders of :mod:`cartolex.collect` in a job,
with one :class:`~cartolex.collect.http.HttpClient` per phase (its progress and
cancel wired to the job), and says beforehand what leaves the computer
(:func:`cartolex.collect.privacy.plan_collection`, plus HAL, SciELO and the
text providers). Its actions:

``identify``
    candidate records for the people whose identity waits: OpenAlex searches
    with the ORCID registry as evidence (:func:`~cartolex.collect.resolve.resolve`),
    HAL author forms by name, and, with a SciELO collection, SciELO authors;
``harvest``
    the works of the confirmed people (OpenAlex and ORCID), their HAL deposits
    (by idHAL), and optionally the missing abstracts from the text providers;
``institutions``
    a search of institutions by name, or the people of the institutions chosen
    (a proposal to take);
``collaborators``
    the next rounds of co-authors (:func:`~cartolex.collect.snowball.snowball`);
``retry``
    the collections that failed, for those people only.

Every message has a code for the interface's catalogues beside its English words.
The command line gives the settings (``cartolex app --services demo`` runs the
demo services of a demo world on this computer).
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .collection import BaseCollection, never_leaves
from .errors import ApiError

if TYPE_CHECKING:
    from cartolex.collect.http import HttpClient
    from cartolex.collect.services import CollectSettings
    from cartolex.project import Project

    from .jobs import JobControl

__all__ = ["ACTIONS", "ServiceCollection", "clear_match", "raw_stamp"]

ACTIONS = ("identify", "harvest", "institutions", "collaborators", "retry")
#: What the plan says each service is sent, as codes the interface words.
SEND_CODES = {
    "names": "sends_names",
    "name": "sends_names",
    "identifiers": "sends_identifiers",
    "identifier": "sends_identifiers",
    "institution names": "sends_institution_names",
    "ORCID iDs": "sends_orcids",
    "author identifiers": "sends_author_ids",
    "DOIs": "sends_dois",
    "DOI": "sends_dois",
    "institution identifiers": "sends_institution_ids",
    "your contact address": "sends_contact",
    "contact address": "sends_contact",
    "your API key": "sends_api_key",
    "API key": "sends_api_key",
    "journal identifiers": "sends_journals",
    "text identifiers": "sends_text_ids",
    "links": "sends_links",
}
#: The services a finder or a provider reaches, besides OpenAlex and ORCID.
_FINDER_SERVICES = ("hal", "scielo")


def _send(text: str) -> dict[str, str]:
    return {"code": SEND_CODES.get(text, "sends_other"), "message": text}


def clear_match(candidates: Sequence[Mapping[str, Any]], threshold: float) -> bool:
    """One candidate that can be confirmed, with a score at least *threshold*: a single clear
    match, which a bulk accept takes."""
    usable = [c for c in candidates if c.get("record")]
    return (
        len(usable) == 1
        and isinstance(usable[0].get("score"), int | float)
        and float(usable[0]["score"]) >= threshold
    )


def raw_stamp(project: Project) -> tuple[Any, ...]:
    """What collected records depend on: the raw folders of every slot and ``people.csv``."""
    layout = project.layout
    out: list[Any] = [str(layout.root)]
    for slot in project.config.slots:
        raw = layout.sources / slot.id / "raw"
        if not raw.is_dir():
            continue
        for kind in sorted(p for p in raw.iterdir() if p.is_dir()):
            out.append((slot.id, kind.name, kind.stat().st_mtime_ns))
    try:
        st = layout.people_csv.stat()
        out.append((st.st_size, st.st_mtime_ns))
    except FileNotFoundError:
        out.append(None)
    return tuple(out)


class ServiceCollection(BaseCollection):
    """Collection from the bibliographic services of :mod:`cartolex.collect`.

    *settings* are the jobs' (contact address, keys, endpoints); *local* says the
    services run on this computer (the demo services), which the plan says;
    *scielo* is the SciELO collection searched when an action does not name one.
    """

    available = True
    id = "services"
    name = "bibliographic services"

    def __init__(
        self,
        settings: CollectSettings,
        *,
        local: bool = False,
        label: str | None = None,
        scielo: str | None = None,
    ) -> None:
        self.settings = settings
        self.local = local
        if label:
            self.name = label
        self.scielo = scielo
        self._lock = threading.Lock()
        self._queue: dict[str, Any] = {}

    # ── describing ──
    def describe(self) -> list[dict[str, Any]]:
        out = []
        for name in ("openalex", "orcid", *_FINDER_SERVICES):
            svc = self.settings.service(name)
            out.append(
                {
                    "id": name,
                    "name": svc.label,
                    "host": _host(svc.base_url),
                    "enabled": name != "scielo" or bool(self.scielo),
                    "purpose": {"code": f"purpose_{name}", "message": svc.purpose},
                    "policy": svc.policy,
                    "local": self.local,
                }
            )
        return out

    # ── the plan ──
    def plan(
        self, project: Project, action: str = "identify", options: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        from cartolex.collect.privacy import STORED, plan_collection

        action = "identify" if action == "collect" else action
        if action not in ACTIONS:
            raise ApiError.of("unknown_collection_action", action=action, actions=list(ACTIONS))
        opts = dict(options or {})
        people = opts.get("people") or None
        base_action = {"identify": "resolve", "retry": "coverage"}.get(action, action)
        if action == "institutions" and not (opts.get("search") or opts.get("institutions")):
            raise ApiError.of("institutions_missing")
        base = plan_collection(
            project,
            base_action,
            self.settings,
            people=people,
            institutions=tuple(opts.get("institutions") or ()),
            search=opts.get("search") or None,
            rounds=int(opts.get("rounds") or 1),
            seeds=len(opts["seeds"]) if opts.get("seeds") else None,
            cap=opts.get("cap"),
        )
        hosts = [
            self._host_json(
                h.service,
                h.requests,
                list(h.sends),
                purpose=h.purpose,
                cost=h.cost_usd,
            )
            for h in base.hosts
        ]
        n_people = base.people
        if action in ("identify", "harvest") and opts.get("hal", True):
            n = self._hal_count(project, action, people)
            if n:
                sends = ["names", "identifiers"] if action == "identify" else ["identifiers"]
                hosts.append(self._host_json("hal", n, sends))
        scielo = self._scielo(opts)
        if action == "identify" and scielo:
            hosts.append(
                self._host_json("scielo", 1 + len(opts.get("issns") or ()), ["journal identifiers"])
            )
        if action == "harvest" and opts.get("abstracts"):
            from cartolex.collect.providers import improve_estimate, provider_egress

            # The texts already collected; at most, as if no provider filled one before another.
            asks = improve_estimate(project.layout)
            for entry in provider_egress():
                n = asks.get(entry["provider"], 0)
                same = next((h for h in hosts if h["service"] == entry["service"]), None)
                if same is not None:
                    same["requests"] += n
                    continue
                hosts.append(
                    self._host_json(entry["service"], n, list(entry["sends"]), purpose=None)
                )
        notes = [dict(n) for n in base.coded_notes]
        if self.local:  # nothing is paid to services on this computer
            for h in hosts:
                h["cost_usd"] = None
            notes = [n for n in notes if not n["code"].startswith("note_openalex_budget")]
            notes.insert(
                0,
                {
                    "code": "note_local",
                    "params": {},
                    "message": "the services run on this computer (demo services): nothing "
                    "leaves it",
                },
            )
        for h in hosts:  # the time each host's requests take at its rate
            h["seconds"] = round(h["requests"] / h["rate"], 1) if h["rate"] else None
        seconds = sum(h["seconds"] or 0 for h in hosts)
        return {
            "available": True,
            "code": None,
            "params": {},
            "message": "",
            "action": action,
            "actions": list(ACTIONS),
            "options": {k: v for k, v in opts.items() if k != "consent"},
            "services": self.describe(),
            "people": n_people,
            "leaves_the_computer": hosts,
            "never_leaves": never_leaves()
            + [{"code": "never_lists", "message": base.never_sent[2]}],
            "stored": [
                {"code": f"stored_{i}", "message": text} for i, text in enumerate(STORED, start=1)
            ],
            "notes": notes,
            "consent_needed": True,
            "estimate": {
                "seconds": round(seconds, 1),
                "requests": sum(h["requests"] for h in hosts),
                "cost_usd": sum(h["cost_usd"] or 0 for h in hosts) or None,
            },
        }

    def _host_json(
        self,
        service: str,
        requests: int,
        sends: list[str],
        *,
        purpose: str | None = None,
        cost: float | None = None,
    ) -> dict[str, Any]:
        svc = self.settings.service(service)
        extra = []
        if self.settings.contact and service == "openalex":
            extra.append("your contact address")
        if self.settings.api_key(service):
            extra.append("your API key")
        return {
            "service": service,
            "label": svc.label,
            "host": _host(svc.base_url),
            "local": self.local,
            "purpose": {
                "code": f"purpose_{service}"
                if not purpose or purpose == svc.purpose
                else _purpose_code(purpose),
                "message": purpose or svc.purpose,
            },
            "sends": [_send(s) for s in dict.fromkeys([*sends, *extra])],
            "requests": int(requests),
            "cost_usd": cost,
            "policy": svc.policy,
            "rate": svc.rate.per_second,
        }

    def _hal_count(self, project: Project, action: str, people: Sequence[str] | None) -> int:
        from cartolex.collect.decisions import read_people as decisions_of
        from cartolex.collect.finders import people_refs

        if not project.layout.table("people").exists():
            return 0
        decided = decisions_of(project.layout)
        refs = people_refs(project.layout, person_ids=people)
        n = 0
        for ref in refs:
            row = decided.get(ref.person_id, {})
            if row.get("merged_into") or row.get("role") == "excluded":
                continue
            waiting = row.get("identity", "") in ("", "pending")
            if action == "identify" and waiting and not ref.idhal:
                n += 1
            elif action == "harvest" and ref.idhal:
                n += 1
        return n

    def _scielo(self, opts: Mapping[str, Any]) -> str | None:
        return (opts.get("scielo") or self.scielo or "").strip() or None

    # ── the job ──
    def collect(
        self,
        project: Project,
        control: JobControl,
        action: str = "identify",
        options: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        from cartolex.collect.http import Cancelled, EgressRecord
        from cartolex.project.checkpoints import JobPaused

        from .messages import job_pause

        action = "identify" if action == "collect" else action
        if action not in ACTIONS:
            raise ApiError.of("unknown_collection_action", action=action, actions=list(ACTIONS))
        opts = dict(options or {})
        clients: list[HttpClient] = []
        done: list[str] = []
        runner = getattr(self, f"_run_{action}")
        try:
            result = runner(project, opts, self._client_factory(project, control, clients), done)
        except Cancelled:
            result = {"outcome": "cancelled", "ran": list(done)}
        except JobPaused as paused:
            # Resumable: the result says why, and the checkpoint to resume from.
            result = {"outcome": "paused", "pause": job_pause(paused), "ran": list(done)}
        finally:
            # What left the computer is recorded whatever the end: a failed or paused job
            # sent requests too.
            egress = EgressRecord()
            for c in clients:
                egress.merge(c.egress)
            summary = egress.summary()
            for entry in summary:
                control.event(
                    "egress",
                    service=entry["service"],
                    host=entry["host"],
                    requests=entry["requests"],
                    sends=list(entry["sends"]),
                )
        return {**result, "action": action, "egress": summary}

    def resume_options(self, project: Project, checkpoint: str) -> dict[str, Any] | None:
        """The options of the paused collection *checkpoint*, to resume it (``None``: none)."""
        from cartolex.collect.institutions import checkpoint_options

        found = checkpoint_options(project, checkpoint)
        if found is None:
            return None
        out: dict[str, Any] = {"institutions": list(found.get("institutions") or ())}
        if found.get("years"):
            out["years"] = list(found["years"])
        if found.get("min_works"):
            out["min_works"] = int(found["min_works"])
        return out

    def _client_factory(
        self, project: Project, control: JobControl, clients: list[HttpClient]
    ) -> Callable[[int, int, str], HttpClient]:
        from cartolex.collect.http import HttpClient

        def make(phase: int, phases: int, name: str) -> HttpClient:
            def progress(fraction: float, message: str, **detail: Any) -> None:
                control.progress(
                    {
                        "fraction": (phase + fraction) / phases,
                        "stage": f"corpus.{name}",
                        "stage_fraction": fraction,
                        "phase": phase + 1,
                        "phases": phases,
                        "message": message,
                        "code": detail.get("code"),
                        "params": detail.get("params") or {},
                        "eta_s": detail.get("eta_s"),
                    }
                )

            client = HttpClient(
                self.settings,
                cache_dir=project.layout.cache_http,
                mode="normal",
                progress=progress,
                cancel=control.cancel.is_set,
            )
            clients.append(client)
            return client

        return make

    def _window(self, project: Project, opts: Mapping[str, Any]) -> tuple[str, tuple[int, int]]:
        from cartolex.collect.decisions import slot_window
        from cartolex.collect.people_import import _collection_slot

        slot = _collection_slot(project, None, "collection")
        years = opts.get("years") or slot_window(project.config, slot) or (None, None)
        first = years[0] or 1900
        last = years[1] or datetime.now(timezone.utc).year
        return slot, (int(first), int(last))

    def _run_identify(
        self, project: Project, opts: Mapping[str, Any], client: Any, done: list[str]
    ) -> dict[str, Any]:
        from cartolex.collect.decisions import read_people as decisions_of
        from cartolex.collect.finders import people_refs
        from cartolex.collect.resolve import resolve
        from cartolex.collect.tables import rebuild_sources

        people = opts.get("people") or None
        scielo = self._scielo(opts)
        phases = 1 + bool(opts.get("hal", True)) + bool(scielo)
        report = resolve(project, client(0, phases, "identify"), people=people, auto=False)
        done.append("openalex")
        counts = report.counts
        out: dict[str, Any] = {
            "people": len(report.resolutions),
            "with_candidates": sum(1 for r in report.resolutions if r.candidates),
            "counts": counts,
        }
        decided = decisions_of(project.layout)
        waiting = {
            pid
            for pid, row in decided.items()
            if row.get("identity", "") in ("", "pending") and not row.get("merged_into")
        }
        refs = [
            r
            for r in people_refs(project.layout, person_ids=people)
            if r.person_id in waiting or (people and r.person_id in people)
        ]
        slot, window = self._window(project, opts)
        phase = 1
        if opts.get("hal", True) and refs:
            from cartolex.collect.hal import collect_hal

            hal = collect_hal(
                client(phase, phases, "hal"), project.layout, slot, refs, window=window
            )
            out["hal"] = {"candidates": len(hal.candidates), "works": hal.works,
                          "failed": len(hal.failures)}  # fmt: skip
            done.append("hal")
            phase += 1
        if scielo and refs:
            from cartolex.collect.scielo import collect_scielo

            found = collect_scielo(
                client(phase, phases, "scielo"),
                project.layout,
                slot,
                refs,
                window=window,
                collection=scielo,
                issns=tuple(opts.get("issns") or ()),
            )
            out["scielo"] = {"candidates": len(found.candidates), "works": found.works,
                             "failed": len(found.failures)}  # fmt: skip
            done.append("scielo")
        if len(done) > 1:
            rebuild_sources(project.layout, project.config)
        out["summary"] = (
            f"{out['people']} searched, {out['with_candidates']} with candidate records"
        )
        out["summary_code"] = "identify"
        out["summary_params"] = {"n": out["people"], "found": out["with_candidates"]}
        return out

    def _run_harvest(
        self, project: Project, opts: Mapping[str, Any], client: Any, done: list[str]
    ) -> dict[str, Any]:
        from cartolex.collect.finders import people_refs
        from cartolex.collect.harvest import harvest
        from cartolex.collect.tables import rebuild_sources

        people = opts.get("people") or None
        years = tuple(opts["years"]) if opts.get("years") else None
        phases = 1 + bool(opts.get("hal", True)) + bool(opts.get("abstracts"))
        report = harvest(
            project,
            client(0, phases, "harvest"),
            people=people,
            years=years,  # type: ignore[arg-type]
        )
        done.append("openalex")
        out: dict[str, Any] = {
            "people": report.people,
            "texts": sum(report.works.values()),
            "failed": len(report.failures),
            "stopped": report.stopped,
        }
        if report.cancelled:
            return {**out, "outcome": "cancelled", "ran": list(done)}
        phase = 1
        if opts.get("hal", True):
            from cartolex.collect.hal import collect_hal

            refs = [r for r in people_refs(project.layout, person_ids=people) if r.idhal]
            if refs:
                slot, window = self._window(project, opts)
                hal = collect_hal(
                    client(phase, phases, "hal"),
                    project.layout,
                    slot,
                    refs,
                    window=window,
                    name_fallback=False,
                )
                out["hal"] = {"works": hal.works, "failed": len(hal.failures)}
                rebuild_sources(project.layout, project.config)
                done.append("hal")
            phase += 1
        if opts.get("abstracts"):
            from cartolex.collect.providers import improve_texts

            improved = improve_texts(
                client(phase, phases, "improve"), project.layout, project.config
            )
            out["abstracts"] = _improved(improved)
            done.append("improve")
        out["summary"] = f"{out['people']} people, {out['texts']} texts received"
        out["summary_code"] = "harvest"
        out["summary_params"] = {"n": out["people"], "texts": out["texts"]}
        return out

    def _run_institutions(
        self, project: Project, opts: Mapping[str, Any], client: Any, done: list[str]
    ) -> dict[str, Any]:
        from cartolex.collect.institutions import find_institutions, propose_people
        from cartolex.collect.openalex import OpenAlexApi

        source = OpenAlexApi(client(0, 1, "institutions"))
        if opts.get("search"):
            found = find_institutions(source, str(opts["search"]))
            done.append("search")
            return {
                "search": opts["search"],
                "institutions": found,
                "summary": f"{len(found)} institution(s)",
                "summary_code": "institutions_found",
                "summary_params": {"n": len(found)},
            }
        from cartolex.collect.institutions import CONFIRM_WORKS
        from cartolex.collect.privacy import OPENALEX_BUDGETS, OPENALEX_PRICES
        from cartolex.project.checkpoints import JobPaused

        years = tuple(opts["years"]) if opts.get("years") else None
        api = source.client

        def progress(p: Mapping[str, Any]) -> None:
            total = p.get("total") or 0
            api.progress(
                p["works"] / total if total else 0.0,
                f"{p['works']} of {total} works read, {p['pages']} requests",
                code="institution_works",
                params={k: p[k] for k in ("works", "total", "pages", "authors", "rate")},
                eta_s=p.get("eta_s"),
            )

        try:
            proposal = propose_people(
                project,
                source,
                list(opts.get("institutions") or ()),
                years=years,  # type: ignore[arg-type]
                min_works=int(opts.get("min_works") or 2),
                resume=bool(opts.get("resume")),
                confirm_above=CONFIRM_WORKS,
                progress=progress,
            )
        except JobPaused as paused:
            if paused.code == "collect_size_confirm":
                requests = int(paused.params.get("requests") or 0)
                keyed = bool(self.settings.api_key("openalex"))
                budget = OPENALEX_BUDGETS["with a free key" if keyed else "without a key"]
                cost = requests * OPENALEX_PRICES["list"]
                paused.params.update(
                    cost_usd=None if self.local else round(cost, 2),
                    days=None if self.local else max(1, math.ceil(cost / budget)),
                    keyed=keyed,
                )
            raise
        done.append("proposal")
        return {
            "proposed": len(proposal.people),
            "works": proposal.works,
            "notes": proposal.notes,
            "summary": f"{len(proposal.people)} people proposed",
            "summary_code": "people_proposed",
            "summary_params": {"n": len(proposal.people)},
        }

    def _run_collaborators(
        self, project: Project, opts: Mapping[str, Any], client: Any, done: list[str]
    ) -> dict[str, Any]:
        from cartolex.collect.openalex import OpenAlexApi
        from cartolex.collect.snowball import snowball

        years = tuple(opts["years"]) if opts.get("years") else None
        report = snowball(
            project,
            OpenAlexApi(client(0, 1, "collaborators")),
            rounds=int(opts.get("rounds") or 1),
            seeds=opts.get("seeds") or None,
            years=years,  # type: ignore[arg-type]
            cap=opts.get("cap"),
            max_authors=opts.get("max_authors"),
        )
        done.append("snowball")
        return {
            "collaborators": len(report.collaborators),
            "rounds": len(report.rounds),
            "cut": bool(report.cut),
            "summary": f"{len(report.collaborators)} collaborators proposed",
            "summary_code": "collaborators_proposed",
            "summary_params": {"n": len(report.collaborators)},
        }

    def _run_retry(
        self, project: Project, opts: Mapping[str, Any], client: Any, done: list[str]
    ) -> dict[str, Any]:
        from cartolex.collect.coverage import retry_failed

        reports = retry_failed(project, client(0, 1, "retry"), people=opts.get("people") or None)
        done.append("retry")
        retried = sorted(k for k in reports if k != "not retried")
        return {
            "retried": retried,
            "summary": f"retried: {', '.join(retried) or 'nothing'}",
            "summary_code": "retried" if retried else "retried_nothing",
            "summary_params": {"items": retried},
        }

    # ── the identity queue ──
    def queue(self, project: Project) -> dict[str, list[dict[str, Any]]]:
        """Person id → candidates of every finder (cached while the raw records stay)."""
        from cartolex.collect.resolve import THRESHOLD, identity_queue

        stamp = raw_stamp(project)
        with self._lock:
            cached = self._queue.get(str(project.layout.root))
            if cached is not None and cached[0] == stamp:
                return cached[1]
        if not project.layout.table("people").exists():
            return {}
        found: dict[str, list[dict[str, Any]]] = {}
        for entry in identity_queue(project):
            cands = []
            for c in entry["candidates"]:
                cands.append(
                    {
                        "finder": c["finder"],
                        "record": c.get("record"),
                        "name": c.get("name") or "",
                        "score": c.get("score"),
                        "evidence": c.get("evidence") or [],
                        "evidence_codes": c.get("evidence_codes") or [],
                        "detail": c.get("detail") or "",
                        "detail_code": c.get("detail_code") or "",
                        "detail_params": c.get("detail_params") or {},
                    }
                )
            cands.sort(key=lambda c: (c["record"] is None, -(c["score"] or 0)))
            for c in cands:
                c["clear"] = False
            if clear_match(cands, THRESHOLD):
                next(c for c in cands if c["record"])["clear"] = True
            found[entry["person_id"]] = cands
        with self._lock:
            self._queue[str(project.layout.root)] = (stamp, found)
        return found

    def candidates(
        self, project: Project, person_ids: Sequence[str]
    ) -> dict[str, list[dict[str, Any]]]:
        found = self.queue(project)
        return {pid: found.get(pid, []) for pid in person_ids}


def _host(url: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).netloc


def _purpose_code(text: str) -> str:
    from cartolex.collect.privacy import PURPOSES

    for key, words in PURPOSES.items():
        if words == text:
            return "purpose_" + key.replace(" ", "_")
    return "purpose_other"


def _improved(report: Any) -> dict[str, Any]:
    """Per provider: texts asked, improved, with nothing, failed."""
    return {"texts": report.texts, "providers": dict(report.counts), "stopped": report.stopped}
