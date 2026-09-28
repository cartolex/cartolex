# SPDX-License-Identifier: MIT
"""Helpers for the collection tests: a project holding a demo world's people, and a client."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa

from cartolex.collect import HttpClient, RetryPolicy, Timeouts, local_settings
from cartolex.demo.model import DemoWorld
from cartolex.project import Project
from cartolex.project.models import Slot
from cartolex.project.tables import SOURCE_SCHEMAS, write_source_table

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
SLOT = "collected"


def project_with_people(root: Path, world: DemoWorld, *, orcids: bool = True) -> Project:
    """A project with one collection slot and the world's people in its tables (as an
    import would give them: names, ORCID, idHAL)."""
    project = Project.init(
        root,
        name="Collection test",
        domain_title="Invented field",
        slots=(Slot(id=SLOT, kind="collection"),),
    )
    rows = [
        {
            "person_id": p.person_id,
            "last_name": p.last_name,
            "first_name": p.first_name,
            "orcid": (p.orcid or None) if orcids else None,
            "ids": [("idhal", [p.idhal])] if p.idhal else [],
            "source": "import",
            "columns": [],
            "aliases": [],
            "retrieved_at": T0,
        }
        for p in world.people
    ]
    schema = SOURCE_SCHEMAS["people"]
    table = pa.table({f.name: [r.get(f.name) for r in rows] for f in schema}, schema=schema)
    write_source_table(project.layout.table("people"), "people", table)
    return project


def client_for(services, project: Project | None = None, **kw) -> HttpClient:
    """A client of the demo services: no pacing, quick retries, the project's cache."""
    settings = local_settings(
        services.endpoints(),
        timeouts=kw.pop("timeouts", Timeouts(connect=2.0, read=5.0)),
        retry=kw.pop("retry", RetryPolicy(max_attempts=3, base_delay=0.01, max_delay=0.05)),
    )
    cache = project.layout.cache_http if project is not None else None
    return HttpClient(settings, cache_dir=cache, **kw)
