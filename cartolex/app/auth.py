# SPDX-License-Identifier: MIT
"""Who is asking, and whether they may (4g).

Every route calls :func:`authorize` with the request's :class:`Principal`, the
route's **action** (``keywords.write``, ``build.start``…) and the
:class:`Resource` it touches; ``tests/test_app_security.py`` lists the routes
and fails if one does not. Locally there is one trusted principal, the person
who started cartolex (:data:`LOCAL_USER`); a host passes its own
:data:`Authorizer` in the settings.

A denied request gets **403** with a plain message; a request without a
session gets **401** (open the app again from the command).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

__all__ = [
    "ANONYMOUS",
    "LOCAL_USER",
    "PUBLIC_ACTIONS",
    "Authorizer",
    "Decision",
    "Principal",
    "Resource",
    "authorize",
    "hosted_authorizer",
    "local_authorizer",
]


@dataclass(frozen=True)
class Principal:
    """The person (or service) a request acts for.

    ``id`` names it in authorisation decisions, never in logs. ``trusted`` is
    the local user, allowed everything. ``projects`` limits a hosted principal
    to some projects (``None``: any), and ``project`` is the project it works
    on when the route does not name one.
    """

    id: str
    name: str = ""
    roles: frozenset[str] = field(default_factory=frozenset)
    trusted: bool = False
    projects: frozenset[str] | None = None
    project: str | None = None

    @property
    def anonymous(self) -> bool:
        return self.id == ANONYMOUS_ID


ANONYMOUS_ID = "anonymous"
#: A request without a session.
ANONYMOUS = Principal(ANONYMOUS_ID)
#: The one person using a local cartolex: whoever started it (and so holds the launch link).
LOCAL_USER = Principal("local", "local user", frozenset({"owner"}), trusted=True)


@dataclass(frozen=True)
class Resource:
    """What a route acts on: ``app``, ``static``, ``project``, ``job`` or ``extension``.

    ``project`` is the id of the project the request works on, when there is
    one; ``id`` the resource's own id (a job's, an extension's).
    """

    kind: str
    id: str | None = None
    project: str | None = None


@dataclass(frozen=True)
class Decision:
    """An authorisation answer: allowed, or denied with the reason shown to the person."""

    allowed: bool
    reason: str = ""


#: Decides whether *principal* may do *action* on *resource*: ``True``, ``False`` or a
#: :class:`Decision` with the reason.
Authorizer = Callable[[Principal, str, Resource], "bool | Decision"]

#: Actions anyone may do, even without a session: the health check, the launch
#: link, and the interface's files (which hold no project data).
PUBLIC_ACTIONS = frozenset({"app.health", "app.launch", "static.read"})


def local_authorizer(principal: Principal, action: str, resource: Resource) -> bool | Decision:
    """Locally: the public actions for anyone, everything for the trusted local user."""
    if action in PUBLIC_ACTIONS:
        return True
    return principal.trusted


def hosted_authorizer(principal: Principal, action: str, resource: Resource) -> bool | Decision:
    """A default for a hosted service: signed-in principals, on the projects they may open.

    Creating projects and listing them need a signed-in principal; a project's
    routes need the project to be among ``principal.projects`` (when set).
    Hosts with roles of their own pass their own :data:`Authorizer`.
    """
    if action in PUBLIC_ACTIONS:
        return True
    if principal.anonymous:
        return False
    if principal.trusted:
        return True
    if resource.project is not None and principal.projects is not None:
        if resource.project not in principal.projects:
            return Decision(False, "this project is not one you may open")
    return True


def authorize(
    authorizer: Authorizer, principal: Principal, action: str, resource: Resource
) -> Decision:
    """Ask *authorizer*; a plain ``False`` becomes a denial with a generic reason."""
    answer = authorizer(principal, action, resource)
    if isinstance(answer, Decision):
        return answer
    if answer:
        return Decision(True)
    return Decision(False, f"you may not do this here ({action})")
