# SPDX-License-Identifier: MIT
"""What every text provider is: what it can improve, what it sends, what it returns."""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..http import HttpClient, ServiceError, ServiceUnavailable
from ..text import DETECTED_LANGUAGES, detect_language, strip_markup
from .formats import Structured

__all__ = [
    "Found",
    "Provided",
    "Provider",
    "TextRef",
    "UnsafeLink",
    "check_link",
    "is_local",
    "parts_from",
    "plain_abstracts",
]


@dataclass(frozen=True)
class TextRef:
    """A text already found, as a provider sees it (from the source tables)."""

    text_id: str
    slot: str
    doc_type: str
    title: str
    year: int | None = None
    doi: str | None = None
    ids: Mapping[str, str] = field(default_factory=dict)
    has_abstract: bool = False
    has_full_text: bool = False


@dataclass(frozen=True)
class Provided:
    """One part a provider gives: ``abstract``, ``body`` or ``full``, its language and format."""

    part: str
    language: str
    format: str
    content: str


@dataclass
class Found:
    """What a provider found for one text: parts, links it states, and what it read.

    *links* are ``(relation, value)``: ``("version_of_doi", doi)`` when the text
    is a preprint whose published version has that DOI. *error* says why a
    file could not be read (the text is then left as it was).
    """

    parts: list[Provided] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)
    read: str = ""  # "jats", "latex", "pdf", "api"
    error: str | None = None


class UnsafeLink(ValueError):
    """A link a service gave that cartolex does not follow."""


def check_link(url: str, *, local_ok: bool) -> str:
    """*url* when it is an ``http(s)`` link cartolex may follow, else :class:`UnsafeLink`.

    A link to this computer or a private network is followed only when the job's
    services themselves are local (*local_ok*, the demo services).
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeLink(f"not a web link: {url[:80]!r}")
    host = parts.hostname
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    private = host == "localhost" or (
        address is not None and (address.is_private or address.is_loopback or address.is_link_local)
    )
    if private and not local_ok:
        raise UnsafeLink(f"a link to a private address is not followed: {host}")
    return url


def is_local(client: HttpClient, service: str) -> bool:
    """Whether *service* is pointed at this computer (the demo services)."""
    host = urlsplit(client.service(service).base_url).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1")


def _language(declared: str | None, content: str) -> str:
    code = (declared or "").lower()[:2]
    return code if code in DETECTED_LANGUAGES else detect_language(content)


def parts_from(doc: Structured, *, fmt: str) -> list[Provided]:
    """The parts of a structured document (*fmt* ``jats`` or ``latex``): its abstracts and
    bodies, one per language; a language not declared is detected."""
    out: list[Provided] = []
    seen: set[tuple[str, str]] = set()
    for part, items in (("abstract", doc.abstracts), ("body", doc.bodies)):
        for lang, text in items:
            content = text.strip()
            if not content:
                continue
            language = _language(lang, content)
            if (part, language) in seen:
                continue
            seen.add((part, language))
            out.append(Provided(part, language, fmt, content))
    return out


def plain_abstracts(items: Iterable[tuple[str | None, str]]) -> list[Provided]:
    """Abstracts an API gives as text (markup possible): cleaned, one per language."""
    out: list[Provided] = []
    seen: set[str] = set()
    for lang, raw in items:
        content, fmt = strip_markup(raw)
        if not content:
            continue
        language = _language(lang, content)
        if language in seen:
            continue
        seen.add(language)
        out.append(Provided("abstract", language, fmt, content))
    return out


class Provider:
    """A text provider: improves texts already found.

    *name* is the ``provider`` its parts carry; *service* the HTTP service it
    calls; *sends* the kinds of data it sends (for the privacy summary) and
    *describes* them in words. :meth:`for_abstract` and :meth:`for_full_text`
    say which texts it can improve; :meth:`abstract` and :meth:`full_text` do it
    (returning ``None`` when the service has nothing for the text).
    """

    name: str = ""
    service: str = ""
    sends: tuple[str, ...] = ()
    describes: str = ""
    #: What its full texts are: ``jats``, ``latex`` or ``pdf`` (structured ones are tried first).
    full_text_format: str = "pdf"

    def for_abstract(self, text: TextRef) -> bool:
        return False

    def for_full_text(self, text: TextRef) -> bool:
        return False

    def abstract(self, client: HttpClient, text: TextRef) -> Found | None:
        return None

    def full_text(self, client: HttpClient, text: TextRef, work_dir: Path) -> Found | None:
        return None

    def abstracts(
        self, client: HttpClient, texts: Sequence[TextRef]
    ) -> dict[str, Found | None | Exception]:
        """Abstracts of several texts; a provider whose service answers many at once
        overrides it. Returns, per text id, what was found, ``None``, or the error."""
        out: dict[str, Any] = {}
        failures = 0
        for text in texts:
            if failures >= 3:  # the service stopped answering: the others are not asked
                out[text.text_id] = ServiceUnavailable(
                    self.service, None, "not asked", "the service had stopped answering"
                )
                continue
            try:
                out[text.text_id] = self.abstract(client, text)
                failures = 0
            except ServiceUnavailable as exc:
                out[text.text_id] = exc
                failures += 1
            except (ServiceError, ValueError) as exc:  # reported per text by the caller
                out[text.text_id] = exc
        return out
