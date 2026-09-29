# SPDX-License-Identifier: MIT
"""Collection behind a small protocol: importing people, collecting their texts, identities.

The app calls a :class:`CollectionService` for everything that brings people
and texts into a project: an imported list (a CSV or a pasted list, a mapping
proposal, then a confirmation), the plan of a collection with what leaves the
computer, the collection itself (a job), and the candidate records of each
person for the identity queue. The real services (bibliographic sources) plug
in behind the same calls; until they land, two implementations exist:

- :class:`UnavailableCollection` (the default): importing a list works (it is
  local), collecting says it is not available;
- :class:`DemoCollection`: a stand-in that « collects » the texts of the demo
  world (``cartolex.demo``) for the people imported from it. Nothing leaves
  the computer. The tests and the demo use it.
"""

from __future__ import annotations

import secrets
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .errors import ApiError
from .importing import confirm_list, propose_list
from .messages import message
from .people_io import people_rows, read_people

if TYPE_CHECKING:
    from cartolex.demo.model import DemoWorld
    from cartolex.project import Project

    from .jobs import JobControl

__all__ = [
    "BaseCollection",
    "CollectionService",
    "DemoCollection",
    "UnavailableCollection",
]

#: What cartolex never sends anywhere during a collection: a code and its English words.
NEVER_SENT = (
    ("never_texts", "the texts already in the project"),
    ("never_decisions", "your decisions (roles, identities, keywords, themes)"),
    ("never_ai", "AI answers and keys"),
    ("never_emails", "e-mail addresses (they are never stored)"),
)


def never_leaves() -> list[dict[str, str]]:
    """What never leaves the computer, as ``{code, message}``."""
    return [{"code": code, "message": text} for code, text in NEVER_SENT]


class CollectionService(Protocol):
    """What the app asks of collection. ``docs/dev/api.md`` describes each call."""

    #: Whether texts can be collected (the manifest's ``capabilities.collection``).
    available: bool

    def describe(self) -> list[dict[str, Any]]:
        """The services, for the settings: ``[{"id", "name", "enabled", "sends"}]``."""
        ...

    def propose_import(
        self, project: Project, folder: Path, filename: str, data: bytes
    ) -> dict[str, Any]:
        """Keep an uploaded list in *folder* and propose how its columns map to fields."""
        ...

    def confirm_import(
        self,
        project: Project,
        folder: Path,
        mapping: Mapping[str, str],
        *,
        role: str,
        set_id: str,
        expected_people: str | None,
    ) -> dict[str, Any]:
        """Add the people of the list kept in *folder*, with the confirmed mapping."""
        ...

    def plan(
        self, project: Project, action: str = "collect", options: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """What *action* would do: people, services, what leaves the computer and what never
        does. ``actions`` lists the actions the service offers."""
        ...

    def collect(
        self,
        project: Project,
        control: JobControl,
        action: str = "collect",
        options: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        """Run *action* (in a job): progress and cancel through *control*."""
        ...

    def candidates(
        self, project: Project, person_ids: Sequence[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """Candidate records of each person, with their evidence, best first."""
        ...


class BaseCollection:
    """The local part every service shares: importing a list of people."""

    available = False
    id = "none"
    name = "no collection service"

    def describe(self) -> list[dict[str, Any]]:
        return [{"id": self.id, "name": self.name, "enabled": self.available, "sends": []}]

    def propose_import(
        self, project: Project, folder: Path, filename: str, data: bytes
    ) -> dict[str, Any]:
        return propose_list(project, folder, filename, data)

    def confirm_import(
        self,
        project: Project,
        folder: Path,
        mapping: Mapping[str, str],
        *,
        role: str,
        set_id: str,
        expected_people: str | None,
    ) -> dict[str, Any]:
        return confirm_list(project, folder, mapping, role=role, set_id=set_id)

    def plan(
        self, project: Project, action: str = "collect", options: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        return {
            "available": False,
            **message("collection_unavailable"),
            "action": action,
            "actions": [],
            "services": self.describe(),
            "people": 0,
            "leaves_the_computer": [],
            "never_leaves": never_leaves(),
            "stored": [],
            "notes": [],
            "consent_needed": False,
        }

    def collect(
        self,
        project: Project,
        control: JobControl,
        action: str = "collect",
        options: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        raise ApiError.of("collection_unavailable")

    def candidates(
        self, project: Project, person_ids: Sequence[str]
    ) -> dict[str, list[dict[str, Any]]]:
        return {}


class UnavailableCollection(BaseCollection):
    """No collection service: people can be imported, texts cannot be collected."""


def _fold(text: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).casefold()
    return "".join(c for c in text if not unicodedata.combining(c)).strip()


class DemoCollection(BaseCollection):
    """A stand-in service answering from a demo world; nothing leaves the computer.

    A person of the project matches a person of the world by their names. The
    collection writes the matched people's texts (titles and abstracts) into
    the project's first collection slot, with their organisations and
    affiliations; each matched person has one candidate record, the world's.
    """

    available = True
    id = "demo"
    name = "demo world (a stand-in inside cartolex)"

    def __init__(self, world: DemoWorld) -> None:
        self.world = world
        self._people = {(_fold(p.last_name), _fold(p.first_name)): p for p in world.people}

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "id": self.id,
                "name": self.name,
                "enabled": True,
                "sends": ["people's names and ORCID iDs (they stay on this computer)"],
            }
        ]

    def _matches(self, project: Project) -> dict[str, Any]:
        out = {}
        for row in people_rows(project):
            found = self._people.get((_fold(row["last_name"]), _fold(row["first_name"])))
            if found is not None:
                out[row["person_id"]] = found
        return out

    def plan(
        self, project: Project, action: str = "collect", options: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        if action != "collect":
            raise ApiError.of("unknown_collection_action", action=action, actions=["collect"])
        people, _ = read_people(project)
        wanted = [p for p in people if p["role"] in ("mapped", "context", "projected")]
        return {
            "available": True,
            "code": None,
            "params": {},
            "message": "",
            "action": action,
            "actions": ["collect"],
            "services": self.describe(),
            "people": len(wanted),
            "leaves_the_computer": [
                {
                    "service": self.id,
                    "label": self.name,
                    "host": "",
                    "local": True,
                    "purpose": {"code": "purpose_demo", "message": "a stand-in inside cartolex"},
                    "sends": [{"code": "sends_names", "message": "names"}],
                    "requests": len(wanted),
                    "cost_usd": None,
                    "policy": "",
                }
            ],
            "never_leaves": never_leaves(),
            "stored": [],
            "notes": [
                {"code": "note_local", "message": "a stand-in: nothing leaves this computer"}
            ],
            "consent_needed": False,
            "estimate": {"seconds": max(1, len(wanted) // 20)},
        }

    def collect(
        self,
        project: Project,
        control: JobControl,
        action: str = "collect",
        options: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        import pyarrow as pa

        from cartolex.project.tables import (
            SOURCE_SCHEMAS,
            read_source_table,
            write_source_table,
        )

        slot = next((s for s in project.config.slots if s.kind == "collection"), None)
        slot = slot or (project.config.slots[0] if project.config.slots else None)
        if slot is None:
            raise ApiError.of("no_slot")
        matches = self._matches(project)
        world_to_project = {p.person_id: pid for pid, p in matches.items()}
        works = [w for w in self.world.works if any(a in world_to_project for a in w.authors)]
        works.sort(key=lambda w: (w.year, w.work_id))
        stamp = datetime.now(timezone.utc).replace(microsecond=0)
        groups = {g.group_id: g for g in self.world.groups}
        layout = project.layout
        texts, parts, authorships = [], [], []
        for i, w in enumerate(works):
            if i % 50 == 0:
                control.progress(
                    {"fraction": i / max(1, len(works)), "message": "reading the demo world"}
                )
                if control.cancelled:
                    return {"outcome": "cancelled", "texts": 0}
            texts.append(
                {
                    "text_id": w.work_id,
                    "slot": slot.id,
                    "position": i,
                    "year": w.year,
                    "doc_type": w.doc_type,
                    "title": w.title,
                    "doi": w.doi or None,
                    "ids": [],
                    "n_authors": len(w.authors),
                    "source": "demo",
                    "retrieved_at": stamp,
                }
            )
            for part, content in (("title", w.title), ("abstract", w.abstract)):
                parts.append(
                    {
                        "text_id": w.work_id,
                        "part": part,
                        "language": w.language,
                        "provider": "demo",
                        "format": "plain",
                        "content": content,
                        "retrieved_at": stamp,
                    }
                )
            for rank, author in enumerate(w.authors, start=1):
                if author in world_to_project:
                    person = next(p for p in self.world.people if p.person_id == author)
                    authorships.append(
                        {
                            "text_id": w.work_id,
                            "person_id": world_to_project[author],
                            "position": rank,
                            "orgs": [person.group],
                            "last": rank == len(w.authors),
                        }
                    )

        def keep_others(name: str, rows: list[dict]) -> list[dict]:
            path = layout.table(name)
            if not path.exists():
                return rows
            old = read_source_table(path, name).to_pylist()
            if name == "texts":
                old = [r for r in old if r["slot"] != slot.id]
            else:
                ours = {t["text_id"] for t in texts}
                gone = (
                    {
                        r["text_id"]
                        for r in read_source_table(
                            layout.table("texts"), "texts", ["text_id", "slot"]
                        ).to_pylist()
                        if r["slot"] == slot.id
                    }
                    if layout.table("texts").exists()
                    else set()
                )
                old = [r for r in old if r["text_id"] not in ours | gone]
            return [_plain(r) for r in old] + rows

        institutions = sorted({groups[p.group].institution for p in matches.values()})
        inst_id = {name: f"i{n + 1:03d}" for n, name in enumerate(institutions)}
        orgs = [
            {
                "org_id": inst_id[name],
                "name": name,
                "level": project.config.levels[-1].id if len(project.config.levels) > 1 else None,
                "parents": [],
                "ids": [],
                "source": "demo",
                "retrieved_at": stamp,
            }
            for name in institutions
        ]
        for gid in sorted({p.group for p in matches.values()}):
            g = groups[gid]
            orgs.append(
                {
                    "org_id": g.group_id,
                    "name": g.name,
                    "acronym": g.acronym,
                    "level": project.config.levels[0].id if project.config.levels else None,
                    "parents": [inst_id[g.institution]],
                    "ids": [],
                    "location": {"lat": g.lat, "lon": g.lon},
                    "source": "demo",
                    "retrieved_at": stamp,
                }
            )
        affiliations = [
            {"person_id": pid, "org_id": p.group, "source": "demo"} for pid, p in matches.items()
        ]

        def merged(name: str, rows: list[dict], key: tuple[str, ...]) -> list[dict]:
            path = layout.table(name)
            old = (
                [_plain(r) for r in read_source_table(path, name).to_pylist()]
                if path.exists()
                else []
            )
            seen = {tuple(r.get(k) for k in key) for r in rows}
            return rows + [r for r in old if tuple(r.get(k) for k in key) not in seen]

        control.progress({"fraction": 0.9, "message": "writing the tables"})
        tables = {
            "texts": keep_others("texts", texts),
            "text_parts": keep_others("text_parts", parts),
            "authorships": keep_others("authorships", authorships),
            "organisations": merged("organisations", orgs, ("org_id",)),
            "affiliations": merged(
                "affiliations", affiliations, ("person_id", "org_id", "start_year", "source")
            ),
        }
        for name, rows in tables.items():
            write_source_table(
                layout.table(name), name, pa.Table.from_pylist(rows, schema=SOURCE_SCHEMAS[name])
            )
        control.progress({"fraction": 1.0, "message": "done"})
        control.event("collected", people=len(matches), texts=len(texts))
        return {"people": len(matches), "texts": len(texts), "slot": slot.id}

    def candidates(
        self, project: Project, person_ids: Sequence[str]
    ) -> dict[str, list[dict[str, Any]]]:
        matches = self._matches(project)
        texts: dict[str, int] = {}
        for w in self.world.works:
            for a in w.authors:
                texts[a] = texts.get(a, 0) + 1
        groups = {g.group_id: g for g in self.world.groups}
        out: dict[str, list[dict[str, Any]]] = {}
        for pid in person_ids:
            p = matches.get(pid)
            if p is None:
                out[pid] = []
                continue
            record = f"openalex:{p.openalex_id}" if p.openalex_id else f"demo:{p.person_id}"
            out[pid] = [
                {
                    "record": record,
                    "name": f"{p.first_name} {p.last_name}",
                    "score": 0.95,
                    "evidence": {
                        "texts": texts.get(p.person_id, 0),
                        "organisation": groups[p.group].acronym if p.group in groups else "",
                        "orcid": p.orcid or None,
                        "same_name": True,
                    },
                }
            ]
        return out


def _plain(row: dict[str, Any]) -> dict[str, Any]:
    """A row read from a table, ready to be written again (maps as key-value lists)."""
    out = dict(row)
    for key, value in row.items():
        if isinstance(value, dict) and key in ("ids", "columns"):
            out[key] = list(value.items())
    return out


def new_import_id() -> str:
    return "imp-" + secrets.token_hex(6)
