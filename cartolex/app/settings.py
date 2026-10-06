# SPDX-License-Identifier: MIT
"""The settings of one app: how it runs, where it keeps its own files, and its services.

:class:`AppSettings` is immutable and passed to :func:`cartolex.app.create_app`;
nothing in the app reads an environment variable or a module-level setting, so
two apps in one process (two projects, a test's) share nothing. The command
line builds the settings (``cartolex app``, ``cartolex api``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from fastapi import Request

    from cartolex.build import Registry
    from cartolex.build.engine import AIAccess

    from .auth import Authorizer, Principal
    from .collection import CollectionService
    from .jobs import JobRunner
    from .share import SiteBuilder

__all__ = ["LOOPBACK_NAMES", "AppSettings"]

#: The host names a local app answers to.
LOOPBACK_NAMES = ("127.0.0.1", "localhost", "::1")


@dataclass(frozen=True)
class AppSettings:
    """How one app runs.

    **Mode.** ``local``: one person on their own computer, one project open at
    a time (:attr:`project` opens at start), loopback host names only.
    ``hosted``: a service, many projects under :attr:`projects_root`, chosen by
    the route (``/api/projects/<id>/…``) or by the principal; the host names in
    :attr:`allowed_hosts` only.

    **Own files.** :attr:`data_dir` is the app's folder outside any project
    (recent projects, uploads waiting for a confirmation); ``None`` keeps them
    in memory and a temporary folder.

    **Security.** :attr:`launch_token` is exchanged once for a session
    (``None``: a random one per app). :attr:`authenticate` (hosted) turns a
    request into a principal, for a host's own sign-in; :attr:`authorizer`
    decides every request (default: :func:`cartolex.app.auth.local_authorizer`,
    or :func:`~cartolex.app.auth.hosted_authorizer` when hosted).

    **Services.** :attr:`job_runner`, :attr:`collection`, :attr:`site_builder`
    default to the local runner and the stubs; :attr:`registry` to cartolex's
    stages with :attr:`ai_access` (the AI key given to the build). A build of
    cartolex's stages runs in a process of its own (:mod:`cartolex.app.build_run`),
    so that its memory goes back to the computer when it ends; :attr:`build_in_child`
    false keeps it in a thread of the app.

    **The last project.** :attr:`reopen_last` (local only, without :attr:`project`):
    the project opened last is opened again at start when it is still there and no
    other app holds it (``cartolex app`` without a folder).

    **Stopping when unused.** :attr:`idle_stop_s` (local only): the server stops
    once no page of the interface has been open for that many seconds and no
    job runs (:mod:`cartolex.app.presence`); ``None`` keeps it running.
    """

    mode: Literal["local", "hosted"] = "local"
    project: Path | None = None
    projects_root: Path | None = None
    data_dir: Path | None = None
    allowed_hosts: tuple[str, ...] = ()
    launch_token: str | None = None
    secure_cookies: bool = False
    static_dir: Path | None = None
    locales: tuple[str, ...] = ("en", "fr", "pt-BR")
    default_locale: str = "en"
    max_upload_mb: float = 50.0
    #: A copilot's result: about 260 bytes a decision, so 256 MB holds about a million.
    max_result_mb: float = 256.0
    max_archive_members: int = 20_000
    max_archive_mb: float = 2_000.0
    max_request_kb: float = 16_384.0
    authenticate: Callable[[Request], Principal | None] | None = None
    authorizer: Authorizer | None = None
    job_runner: JobRunner | None = None
    collection: CollectionService | None = None
    site_builder: SiteBuilder | None = None
    registry: Registry | None = None
    ai_access: AIAccess | None = None
    build_budget_mb: float | None = None
    build_year: int | None = None
    build_in_child: bool = True
    heartbeat_s: float = 5.0
    idle_stop_s: float | None = None
    reopen_last: bool = False

    def __post_init__(self) -> None:
        if self.mode not in ("local", "hosted"):
            raise ValueError(f"mode is local or hosted, not {self.mode!r}")
        if self.mode == "hosted":
            if self.projects_root is None:
                raise ValueError("a hosted app needs projects_root, the folder of its projects")
            if not self.allowed_hosts:
                raise ValueError("a hosted app needs allowed_hosts, the names it answers to")
        if self.default_locale not in self.locales:
            raise ValueError(f"the default locale {self.default_locale!r} is not in locales")
        if (
            self.max_upload_mb <= 0
            or self.max_archive_mb <= 0
            or self.max_request_kb <= 0
            or self.max_result_mb <= 0
        ):
            raise ValueError("size limits are positive")
        if self.idle_stop_s is not None and (self.hosted or self.idle_stop_s <= 0):
            raise ValueError("idle_stop_s is a positive number of seconds, for a local app only")

    @property
    def hosted(self) -> bool:
        return self.mode == "hosted"

    def host_allowed(self, hostname: str) -> bool:
        """Whether a request's ``Host`` name (without the port) is one this app answers to."""
        name = hostname.lower().strip("[]")
        allowed = {h.lower().strip("[]") for h in self.allowed_hosts}
        if self.mode == "local":
            allowed |= set(LOOPBACK_NAMES)
        return name in allowed
