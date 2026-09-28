# SPDX-License-Identifier: MIT
"""Demo services: fake bibliographic services on the loopback interface, served from a demo world.

They answer the subset of each real API that cartolex uses, with the real
response shapes, from a bibliographic layer derived from a demo world
(:mod:`cartolex.demo.services.biblio`): homonyms, split and mixed records,
people without records, registry works, affiliation histories and outside
co-authors. Tests, the scenario suite, the browser tests and the demo project
collect from them without any network::

    from cartolex.demo import generate
    from cartolex.demo.services import DemoServices

    with DemoServices(generate("XS", 0)) as services:
        services.endpoints()      # {"openalex": "http://127.0.0.1:PORT/openalex", …}
        services.faults.add("status", service="openalex", status=429, retry_after="1")

or from the command line (serves until Ctrl-C)::

    python -m cartolex.demo services --size S --seed 0 [--port 8765] [--people-list FILE]

OpenAlex and ORCID are served; HAL, SciELO, arXiv, bioRxiv/medRxiv and Europe
PMC are stubs (``501``) until their finders are written: :func:`register_service`
adds a service factory ``(bibliography) -> service``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..model import DemoWorld
from .biblio import Bibliography, build_bibliography
from .http import DemoServer, Fault, FaultPlan, Reply, Request, SeenRequest, Service, json_reply
from .openalex import OpenAlexService
from .orcid import OrcidService

__all__ = [
    "SERVICE_FACTORIES",
    "Bibliography",
    "DemoServices",
    "Fault",
    "FaultPlan",
    "Reply",
    "Request",
    "SeenRequest",
    "Service",
    "StubService",
    "build_bibliography",
    "endpoints_at",
    "json_reply",
    "register_service",
]

#: The path of each service's API under its prefix (the part a real base URL ends with).
API_ROOTS = {"orcid": "v3.0"}


class StubService:
    """A service the demo does not serve yet: every request answers 501."""

    def __init__(self, name: str) -> None:
        self.name = name

    def handle(self, request: Request) -> Reply:
        return json_reply(
            501,
            {"error": f"the demo services do not serve {self.name} yet", "path": request.path},
        )


def _stub(name: str) -> Callable[[Bibliography], Service]:
    return lambda bib: StubService(name)


#: Every service the demo serves, by name: a factory that takes the bibliography.
SERVICE_FACTORIES: dict[str, Callable[[Bibliography], Service]] = {
    "openalex": OpenAlexService,
    "orcid": OrcidService,
    "hal": _stub("hal"),
    "scielo": _stub("scielo"),
    "arxiv": _stub("arxiv"),
    "biorxiv": _stub("biorxiv"),
    "europepmc": _stub("europepmc"),
}


def endpoints_at(base_url: str) -> dict[str, str]:
    """The base URL of each service of demo services already running at *base_url*."""
    base = base_url.rstrip("/")
    return {
        name: f"{base}/{name}" + (f"/{API_ROOTS[name]}" if name in API_ROOTS else "")
        for name in SERVICE_FACTORIES
    }


def register_service(name: str, factory: Callable[[Bibliography], Service]) -> None:
    """Serve *name* with the service *factory* builds (replacing a stub)."""
    SERVICE_FACTORIES[name] = factory


class DemoServices:
    """The demo services of one world, on a loopback port of their own.

    *layer_seed* chooses the bibliographic layer (which people are split,
    mixed…), independently of the world's own seed; *port* 0 takes a free port.
    """

    def __init__(self, world: DemoWorld, *, layer_seed: int = 0, port: int = 0) -> None:
        self.world = world
        self.bibliography = build_bibliography(world, layer_seed)
        services = {name: make(self.bibliography) for name, make in SERVICE_FACTORIES.items()}
        #: The first segment of every URL: which world and layer are served, so that answers
        #: cached from one demo world are never taken for another's.
        self.prefix = (
            f"demo-{world.size.lower()}-{world.seed}-{'-'.join(world.languages)}-{layer_seed}"
        )
        self.server = DemoServer(services, port=port, prefix=self.prefix)

    @property
    def faults(self) -> FaultPlan:
        return self.server.faults

    @property
    def requests(self) -> list[SeenRequest]:
        return self.server.requests

    @property
    def base_url(self) -> str:
        return self.server.base_url

    def url(self, service: str) -> str:
        """The base URL to use for *service* (what replaces its real base URL)."""
        root = API_ROOTS.get(service)
        return f"{self.base_url}/{service}" + (f"/{root}" if root else "")

    def endpoints(self) -> dict[str, str]:
        """Base URL of every service served, by name."""
        return endpoints_at(self.base_url)

    def start(self) -> DemoServices:
        self.server.start()
        return self

    def stop(self) -> None:
        self.server.stop()

    def __enter__(self) -> DemoServices:
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()
