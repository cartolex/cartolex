# SPDX-License-Identifier: MIT
"""Write a demo world as a cartolex project (format ``cartolex-project/1``).

The project holds the same corpus the engine reads from :func:`write_corpus`:
``cartolex.project.corpus.assemble_corpus`` on this project gives the same
index rows and the same texts, which is what lets a project build reproduce
the numeric reference. Every value is derived from the world, and the times
are fixed, so writing the same world twice gives the same tables.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa

from cartolex.project import Project
from cartolex.project.files import write_decision
from cartolex.project.models import Level, Overlay, Slot
from cartolex.project.tables import SOURCE_SCHEMAS, decision_csv_bytes, write_source_table

from .model import COHORT, DemoWorld

__all__ = ["DOMAIN_DESCRIPTION", "DOMAIN_TITLE", "SLOT", "write_project"]

DOMAIN_TITLE = "Coastal and marine systems"
DOMAIN_DESCRIPTION = (
    "An invented research community working on coastal and marine systems: physical "
    "processes, ecology, hazards, resources and management."
)
#: The corpus slot of the demo, named as the engine's default slot.
SLOT = "manual"
#: The fixed time stamped on every row, so the tables do not depend on the clock.
STAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)
LEVELS = (
    Level(id="lab", names={"en": "Lab", "fr": "Laboratoire", "pt": "Laboratório"}),
    Level(
        id="institution", names={"en": "Institution", "fr": "Établissement", "pt": "Instituição"}
    ),
)


def _table(name: str, rows: list[dict]) -> pa.Table:
    schema = SOURCE_SCHEMAS[name]
    return pa.table({f.name: [r.get(f.name) for r in rows] for f in schema}, schema=schema)


def write_project(world: DemoWorld, root: Path | str, *, name: str | None = None) -> Project:
    """Create a project in *root* (empty or missing) from *world*; return it, open for writing."""
    languages = sorted({w.language for w in world.works}, key=lambda lang: (lang != "fr", lang))
    project = Project.init(
        Path(root),
        name=name or f"Demo world {world.size}, seed {world.seed}",
        domain_title=DOMAIN_TITLE,
        domain_description=DOMAIN_DESCRIPTION,
        corpus_languages=tuple(languages),
        reference_language="en",
        slots=(Slot(id=SLOT, kind="corpus", fit=True, trajectory=True),),
    )
    config = project.config.model_copy(
        update={
            "levels": list(LEVELS),
            "overlays": [Overlay(id=s, trajectory=False) for s in sorted(world.overlay_sets)],
        }
    )
    project.save_config(config, action="demo levels and overlays")
    layout = project.layout

    institutions = sorted({g.institution for g in world.groups})
    inst_id = {inst: f"i{i + 1:03d}" for i, inst in enumerate(institutions)}
    orgs = [
        {
            "org_id": inst_id[inst],
            "name": inst,
            "level": "institution",
            "parents": [],
            "ids": [],
            "source": "import",
            "retrieved_at": STAMP,
        }
        for inst in institutions
    ] + [
        {
            "org_id": g.group_id,
            "name": g.name,
            "acronym": g.acronym,
            "level": "lab",
            "parents": [inst_id[g.institution]],
            "ids": [],
            "location": {"lat": g.lat, "lon": g.lon},
            "source": "import",
            "retrieved_at": STAMP,
        }
        for g in world.groups
    ]
    people = [
        {
            "person_id": p.person_id,
            "last_name": p.last_name,
            "first_name": p.first_name,
            "orcid": p.orcid or None,
            "ids": [
                (scheme, [value])
                for scheme, value in (("idhal", p.idhal), ("openalex", p.openalex_id))
                if value
            ],
            "source": "import",
            "columns": [("career_stage", p.career_stage), ("site", p.site)],
            "retrieved_at": STAMP,
        }
        for p in world.people
    ]
    affiliations = [
        {"person_id": p.person_id, "org_id": p.group, "source": "import"} for p in world.people
    ]
    ordered = sorted(world.works, key=lambda w: (w.year, w.work_id))
    position = {w.work_id: i for i, w in enumerate(ordered)}
    group_of = {p.person_id: p.group for p in world.people}
    texts, parts, authorships = [], [], []
    for w in world.works:
        texts.append(
            {
                "text_id": w.work_id,
                "slot": SLOT,
                "position": position[w.work_id],
                "year": w.year,
                "doc_type": w.doc_type,
                "title": w.title,
                "doi": w.doi or None,
                "ids": [],
                "n_authors": len(w.authors),
                "source": "import",
                "retrieved_at": STAMP,
            }
        )
        for part, content in (("title", w.title), ("abstract", w.abstract)):
            parts.append(
                {
                    "text_id": w.work_id,
                    "part": part,
                    "language": w.language,
                    "provider": "import",
                    "format": "plain",
                    "content": content,
                    "retrieved_at": STAMP,
                }
            )
        for rank, pid in enumerate(w.authors, start=1):
            authorships.append(
                {
                    "text_id": w.work_id,
                    "person_id": pid,
                    "position": rank,
                    "orgs": [group_of[pid]],
                    "last": rank == len(w.authors),
                }
            )

    for table_name, rows in (
        ("organisations", orgs),
        ("people", people),
        ("affiliations", affiliations),
        ("texts", texts),
        ("text_parts", parts),
        ("authorships", authorships),
    ):
        write_source_table(layout.table(table_name), table_name, _table(table_name, rows))

    decided = STAMP.strftime("%Y-%m-%dT%H:%M:%SZ")
    roles = [
        {
            "person_id": p.person_id,
            "role": "mapped" if p.role == COHORT else "projected",
            "set": "" if p.role == COHORT else p.role.split(":", 1)[1],
            "identity": "confirmed",
            "decided_at": decided,
        }
        for p in world.people
    ]
    write_decision(
        layout,
        layout.people_csv,
        decision_csv_bytes("people", roles),
        expected=None,
        action="demo roles",
    )
    return project
