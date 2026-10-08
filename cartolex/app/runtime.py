# SPDX-License-Identifier: MIT
"""The state of one app: its settings, extensions, sessions, projects, jobs and services.

:func:`cartolex.app.create_app` builds one :class:`Runtime` per app and keeps
it in ``app.state.cartolex``; routes reach it through the request. Nothing is
kept at module level, so two apps in one process share nothing.
"""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .auth import ANONYMOUS, LOCAL_USER, Principal, hosted_authorizer, local_authorizer
from .jobs import LocalJobRunner
from .presence import Presence
from .projects import HostedProjects, LocalProjects, ProjectHost
from .security import Session, SessionStore, same_secret

if TYPE_CHECKING:
    from fastapi import Request

    from cartolex.build import Registry

    from .auth import Authorizer
    from .collection import CollectionService
    from .extensions import Combined
    from .jobs import JobRunner
    from .settings import AppSettings
    from .share import SiteBuilder

__all__ = ["Cache", "Runtime"]


class Cache:
    """A small LRU cache of computed answers, keyed by what they were computed from."""

    def __init__(self, size: int = 16) -> None:
        self._items: OrderedDict[Any, Any] = OrderedDict()
        self._size = size
        self._lock = threading.Lock()
        #: The keys being computed, each with the lock the others wait on.
        self._computing: dict[Any, threading.Lock] = {}

    def get(self, key: Any, compute: Callable[[], Any]) -> Any:
        """The value kept for *key*, else computed once: requests asking for it meanwhile wait
        for that computation instead of making their own."""
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
            computing = self._computing.setdefault(key, threading.Lock())
        with computing:
            with self._lock:
                if key in self._items:
                    self._items.move_to_end(key)
                    return self._items[key]
            try:
                value = compute()
            finally:
                with self._lock:
                    self._computing.pop(key, None)
            with self._lock:
                self._items[key] = value
                self._items.move_to_end(key)
                while len(self._items) > self._size:
                    self._items.popitem(last=False)
        return value

    def peek(self, key: Any) -> Any:
        """The value kept for *key*, or ``None`` (without computing it)."""
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        return None

    def put(self, key: Any, value: Any) -> None:
        """Keep *value* for *key*."""
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self._size:
                self._items.popitem(last=False)

    def items(self) -> list[tuple[Any, Any]]:
        """What is kept, the oldest first (a copy)."""
        with self._lock:
            return list(self._items.items())


class Runtime:
    """Everything one app holds while it runs."""

    def ai_access(self, provider: str = "mistral") -> Any:
        """How the build reaches *provider* now: the launch's access (a model of its own for
        any provider; a key for Mistral only), else that provider's key: its environment
        variable, else the one saved here (on a hosted service, the environment's only). A
        key never goes to another provider than the one it is saved for."""
        from cartolex.lexicon.providers import provider as ai_provider

        given = self.settings.ai_access
        if given is not None and (given.client_factory is not None or provider == "mistral"):
            return given
        service = ai_provider(provider)
        if self.settings.hosted:
            key = os.environ.get(service.env_var, "").strip()
        else:
            key = self.keys.get(service.id)
        if not key:
            return None
        from cartolex.build.engine import AIAccess

        return AIAccess(api_key=key)

    def ai_ready(self, provider: str | None = None) -> bool:
        """Whether the clean-up by API can reach *provider* (``None``: any provider)."""
        from cartolex.lexicon.providers import PROVIDERS

        for p in [provider] if provider else list(PROVIDERS):
            ai = self.ai_access(p)
            if ai is not None and (ai.api_key or ai.client_factory):
                return True
        return False

    def __init__(self, settings: AppSettings, extensions: Combined) -> None:
        from cartolex.build.engine import EngineOptions, engine_registry

        from .collection import UnavailableCollection
        from .share import default_site_builder

        self.settings = settings
        self.extensions = extensions
        #: This app instance: it names the cookies, so two apps never share a session.
        self.instance = secrets.token_hex(4)
        self.launch_token = settings.launch_token or secrets.token_urlsafe(32)
        self._launch_used = False
        self._launch_lock = threading.Lock()
        self.sessions = SessionStore()
        self.authorizer: Authorizer = settings.authorizer or (
            hosted_authorizer if settings.hosted else local_authorizer
        )
        hooks = [e.on_project_open for e in extensions.extensions if e.on_project_open]
        self.projects: ProjectHost = (
            HostedProjects(settings.projects_root, hooks)  # type: ignore[arg-type]
            if settings.hosted
            else LocalProjects(settings.data_dir, hooks)
        )
        self.jobs: JobRunner = settings.job_runner or LocalJobRunner()
        #: The pages of the interface heard from lately (the local app stops when none is open).
        self.presence = Presence()
        self.collection: CollectionService = settings.collection or UnavailableCollection()
        self.site_builder: SiteBuilder = settings.site_builder or default_site_builder()
        from .machine import MachineBudget, MachineKeys, MachineSnapshot
        from .notices import NoticeMemory

        #: The keys saved on this computer (none on a hosted service).
        self.keys = MachineKeys(settings.data_dir if not settings.hosted else None)
        use_saved_keys = getattr(self.collection, "use_saved_keys", None)
        if use_saved_keys is not None and not settings.hosted:
            use_saved_keys(self.keys.get)  # a key saved in the settings serves the collection
        #: The OpenAlex snapshot folder saved on this computer (none on a hosted service).
        self.snapshot = MachineSnapshot(settings.data_dir if not settings.hosted else None)
        #: What the builds may use of this computer (kept in memory on a hosted service).
        self.budget = MachineBudget(settings.data_dir if not settings.hosted else None)
        self.budget.give()
        use_snapshot = getattr(self.collection, "use_snapshot", None)
        if use_snapshot is not None and not settings.hosted:
            use_snapshot(self.snapshot)  # collections may read OpenAlex from it
        #: The rejection cache of this computer (none on a hosted service, or without a folder).
        self.rejects_folder = (
            Path(settings.data_dir) / "rejects"
            if settings.data_dir is not None and not settings.hosted
            else None
        )
        base = settings.registry or engine_registry(
            self.ai_access,
            EngineOptions(
                prompt_dir=extensions.prompt_dir,
                stopword_overlay=extensions.stopword_overlay or None,
                rejects_folder=self.rejects_folder,
            ),
        )
        self.registry: Registry = extensions.registry(base)
        #: The collection notices each person acknowledged (:mod:`cartolex.app.notices`).
        self.notices = NoticeMemory(settings.data_dir)
        #: Preferences per principal when the app has no folder of its own (``/api/me``).
        self.preferences: dict[str, Any] = {}
        self.preferences_lock = threading.Lock()
        #: What was warned about once (a nav entry whose module is missing).
        self.warned: set[tuple[str, str]] = set()
        self.atlas_cache = Cache(12)
        self.table_cache = Cache(16)
        #: The layout previews of the method screen, by what they were drawn from.
        self.preview_cache = Cache(24)
        self._upload_tmp: tempfile.TemporaryDirectory[str] | None = None
        if settings.data_dir is not None:
            self.upload_root = Path(settings.data_dir) / "uploads"
        else:
            self._upload_tmp = tempfile.TemporaryDirectory(prefix="cartolex-uploads-")
            self.upload_root = Path(self._upload_tmp.name)

    # ── cookies and sessions ──
    @property
    def session_cookie(self) -> str:
        return f"cartolex_session_{self.instance}"

    @property
    def csrf_cookie(self) -> str:
        return f"cartolex_csrf_{self.instance}"

    def exchange(self, token: str | None) -> Session | None:
        """Exchange the launch token for a session, once; ``None`` when it is wrong or used."""
        with self._launch_lock:
            if self._launch_used or not same_secret(token, self.launch_token):
                return None
            self._launch_used = True
        return self.sessions.new(LOCAL_USER)

    def session_of(self, request: Request) -> tuple[Session | None, Principal]:
        """The request's session and principal (a host's sign-in opens a session when needed)."""
        session = self.sessions.get(request.cookies.get(self.session_cookie))
        if session is not None:
            return session, session.principal
        if self.settings.authenticate is not None:
            principal = self.settings.authenticate(request)
            if principal is not None and not principal.anonymous:
                session = self.sessions.new(principal)
                self.set_session_cookies(request, session)
                return session, principal
        return None, ANONYMOUS

    def cookie_values(self, session: Session) -> list[str]:
        from .security import cookie_header

        secure = self.settings.secure_cookies
        return [
            cookie_header(self.session_cookie, session.id, http_only=True, secure=secure),
            cookie_header(self.csrf_cookie, session.csrf, http_only=False, secure=secure),
        ]

    def set_session_cookies(self, request: Request, session: Session) -> None:
        """Ask the security layer to set *session*'s cookies on the response."""
        state = request.scope.setdefault("state", {})
        state.setdefault("cartolex.cookies", []).extend(self.cookie_values(session))

    # ── stopping ──
    def shutdown(self) -> None:
        from cartolex.scale import give_budget

        self.jobs.shutdown()
        give_budget(None)
        self.projects.close_all()
        if self._upload_tmp is not None:
            self._upload_tmp.cleanup()

    def uploads_of(self, project_id: str) -> Path:
        folder = self.upload_root / project_id
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def drop_uploads(self, project_id: str, upload_id: str) -> None:
        shutil.rmtree(self.upload_root / project_id / upload_id, ignore_errors=True)
