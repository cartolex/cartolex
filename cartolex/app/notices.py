# SPDX-License-Identifier: MIT
"""How much of the collection notice a planned collection shows, and what a person
acknowledged.

The data cartolex collects are public bibliographic records; the notice says
what leaves the computer before a collection. It has three levels, chosen on
the server and enforced there too:

``none``
    nothing personal is sent (an institution searched or read by its name or
    identifier) and the work fits OpenAlex's free daily budget: the collection
    starts at once, with a one-line note;
``brief``
    people's names or identifiers are sent and the person has already
    acknowledged the notice of this kind of collection, with the same content:
    a compact confirmation, the full notice one click away;
``full``
    the first time for this kind, a notice whose content changed since it was
    acknowledged (another service, other kinds of data, a new
    :data:`NOTICE_VERSION`), or a collection beyond the free daily budget: the
    whole notice, with « Don't show this again ».

A collection at ``brief`` or ``full`` starts only with ``consent: true``.
Acknowledgements are kept per person in the app's own folder
(``<data dir>/users/<digest of the principal's id>.notices.json``, never in a
project), in memory when the app has no folder; Settings › Privacy resets them.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["NOTICE_VERSION", "PERSONAL_SENDS", "NoticeMemory", "notice_level"]

#: The notice's version: raised when what it says changes enough to be read again.
NOTICE_VERSION = 1
FORMAT = "cartolex-notices/1"
#: What the plan says a host receives that names or identifies a person (a code of
#: :data:`cartolex.app.collect_service.SEND_CODES`; an unknown kind counts as personal).
PERSONAL_SENDS = frozenset(
    {"sends_names", "sends_identifiers", "sends_orcids", "sends_author_ids", "sends_other"}
)
#: What is the person's own and says nothing of the people mapped: left out of the content.
_OWN_SENDS = frozenset({"sends_contact", "sends_api_key"})


def _sends(plan: Mapping[str, Any]) -> list[tuple[str, str, tuple[str, ...]]]:
    return sorted(
        (
            str(h.get("service")),
            str(h.get("host") or ""),
            tuple(sorted({s["code"] for s in h.get("sends", ()) if s["code"] not in _OWN_SENDS})),
        )
        for h in plan.get("leaves_the_computer", ())
    )


def notice_digest(plan: Mapping[str, Any]) -> str:
    """The notice's content in a few characters: its version, each host and the kinds of data
    it receives (not the counts, the options or the person's own contact address and key)."""
    text = json.dumps([NOTICE_VERSION, _sends(plan)], separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def notice_level(
    plan: Mapping[str, Any], acknowledged: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """The notice a *plan* (a collection's plan, as the API gives it) asks, given the kinds
    *acknowledged* (``{kind: {"digest", "at"}}``).

    Returns ``{"level", "kind", "digest", "personal", "reasons", "acknowledged_at"}``;
    ``reasons`` are codes: ``nothing_sent``, ``not_personal``, ``acknowledged``,
    ``first_time``, ``changed``, ``over_budget``.
    """
    kind = str(plan.get("action") or "collect")
    digest = notice_digest(plan)
    hosts = list(plan.get("leaves_the_computer", ()))
    personal = any(
        s["code"] in PERSONAL_SENDS for h in hosts for s in h.get("sends", ()) if "code" in s
    )
    budget = plan.get("budget")
    over = bool(budget) and not budget.get("fits", True)
    seen = acknowledged.get(kind)
    reasons: list[str] = []
    if over:
        reasons.append("over_budget")
    if not hosts:
        level, reasons = "none", ["nothing_sent"]
    elif not personal and not over:
        level, reasons = "none", ["not_personal"]
    elif seen is not None and seen.get("digest") == digest and not over:
        level, reasons = "brief", ["acknowledged"]
    else:
        level = "full"
        if personal:
            reasons.append("changed" if seen is not None else "first_time")
    return {
        "level": level,
        "kind": kind,
        "digest": digest,
        "personal": personal,
        "reasons": reasons,
        "acknowledged_at": seen.get("at") if seen is not None else None,
    }


class NoticeMemory:
    """The kinds of collection each person acknowledged (« Don't show this again »)."""

    def __init__(self, data_dir: str | Path | None) -> None:
        self.folder = Path(data_dir) / "users" if data_dir is not None else None
        self._memory: dict[str, dict[str, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def _file(self, principal: str) -> Path | None:
        if self.folder is None:
            return None
        digest = hashlib.sha256(principal.encode("utf-8")).hexdigest()[:32]
        return self.folder / f"{digest}.notices.json"

    def read(self, principal: str) -> dict[str, dict[str, Any]]:
        """``{kind: {"digest", "at"}}`` of what *principal* acknowledged."""
        path = self._file(principal)
        if path is None:
            return {k: dict(v) for k, v in self._memory.get(principal, {}).items()}
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        kinds = doc.get("acknowledged") if isinstance(doc, dict) else None
        if not isinstance(kinds, dict):
            return {}
        return {
            str(k): {"digest": str(v.get("digest", "")), "at": v.get("at")}
            for k, v in kinds.items()
            if isinstance(v, dict)
        }

    def _write(self, principal: str, kinds: dict[str, dict[str, Any]]) -> None:
        path = self._file(principal)
        if path is None:
            self._memory[principal] = kinds
            return
        from cartolex.project.files import atomic_write_bytes, json_bytes

        if not kinds:
            path.unlink(missing_ok=True)
            return
        atomic_write_bytes(path, json_bytes({"format": FORMAT, "acknowledged": kinds}))

    def acknowledge(self, principal: str, kind: str, digest: str) -> None:
        """Remember that *principal* read the notice of *kind* with this content."""
        at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            kinds = self.read(principal)
            kinds[kind] = {"digest": digest, "at": at}
            self._write(principal, kinds)

    def reset(self, principal: str) -> int:
        """Forget every acknowledgement of *principal*; how many there were."""
        with self._lock:
            n = len(self.read(principal))
            self._write(principal, {})
        return n
