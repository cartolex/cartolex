# SPDX-License-Identifier: MIT
"""What every text provider is: what it can improve, what it sends, what it returns."""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..http import HttpClient, ServiceError, ServiceUnavailable
from ..text import DETECTED_LANGUAGES, detect_language, strip_markup
from .formats import Structured

__all__ = [
    "Found",
    "Progress",
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


#: Called with ``(texts done, texts to do)`` as a provider goes.
Progress = Callable[[int, int], None]
#: A provider whose service fails this many times in a row is not asked again in the job.
MAX_FAILURES_IN_A_ROW = 3


def _not_asked(service: str) -> ServiceUnavailable:
    return ServiceUnavailable(service, None, "not asked", "the service had stopped answering")


class Provider:
    """A text provider: improves texts already found.

    *name* is the ``provider`` its parts carry; *service* the HTTP service it
    calls; *sends* the kinds of data it sends (for the privacy summary) and
    *describes* them in words. :meth:`for_abstract` and :meth:`for_full_text`
    say which texts it can improve; :meth:`abstract` and :meth:`full_text` do it
    (returning ``None`` when the service has nothing for the text).

    A provider whose service answers many texts in one request sets *batch* (how
    many identifiers one request carries) and gives :meth:`lookup` (the
    ``(field, value)`` it asks by), :meth:`fetch` (one request for many values)
    and :meth:`found` (the abstract in a record). What a request learnt is kept
    per text (*item_kind*, in the HTTP cache), so a job cut short is not asked
    again, whatever mix of texts the next one asks about.
    """

    name: str = ""
    service: str = ""
    sends: tuple[str, ...] = ()
    describes: str = ""
    #: What its full texts are: ``jats``, ``latex`` or ``pdf`` (structured ones are tried first).
    full_text_format: str = "pdf"
    #: How many texts one request asks about (1: one request per text).
    batch: int = 1
    #: The cache kind of what is kept per text.
    item_kind: str = ""

    def for_abstract(self, text: TextRef) -> bool:
        return False

    def for_full_text(self, text: TextRef) -> bool:
        return False

    def abstract(self, client: HttpClient, text: TextRef) -> Found | None:
        if self.batch > 1:
            result = self.abstracts(client, [text])[text.text_id]
            if isinstance(result, Exception):
                raise result
            return result
        return None

    def full_text(self, client: HttpClient, text: TextRef, work_dir: Path) -> Found | None:
        return None

    # ── batched lookups ──
    def lookup(self, text: TextRef) -> tuple[str, str] | None:
        """The ``(field, value)`` a batched provider asks about *text* by."""
        return None

    def fetch(self, client: HttpClient, field: str, values: Sequence[str]) -> dict[str, Any]:
        """One request about many *values* of *field*: the record of each value found."""
        raise NotImplementedError

    def found(self, record: Any, text: TextRef) -> Found | None:
        """The abstract (and links) a record gives."""
        return None

    def record(self, client: HttpClient, text: TextRef) -> Any:
        """The record of one text: from the cache kept per text, else asked alone."""
        key = self.lookup(text)
        if key is None:
            return None
        hit, value = client.cached_item(self.service, self.item_kind, f"{key[0]}:{key[1]}")
        if hit:
            return value
        value = self.fetch(client, key[0], [key[1]]).get(key[1])
        client.store_item(self.service, self.item_kind, f"{key[0]}:{key[1]}", value)
        return value

    def requests_for(self, texts: Sequence[TextRef]) -> int:
        """How many requests :meth:`abstracts` sends at most for *texts* (nothing cached)."""
        if self.batch <= 1:
            return len(texts)
        fields: dict[str, set[str]] = {}
        for t in texts:
            key = self.lookup(t)
            if key is not None:
                fields.setdefault(key[0], set()).add(key[1])
        return sum(-(-len(v) // self.batch) for v in fields.values())

    def abstracts(
        self,
        client: HttpClient,
        texts: Sequence[TextRef],
        progress: Progress | None = None,
    ) -> dict[str, Found | None | Exception]:
        """Abstracts of several texts: per text id, what was found, ``None``, or the error.
        *progress* hears ``(done, total)`` after each request."""
        if self.batch > 1:
            return self._batched(client, texts, progress)
        out: dict[str, Any] = {}
        failures = 0
        for n, text in enumerate(texts):
            if failures >= MAX_FAILURES_IN_A_ROW:  # the others are not asked
                out[text.text_id] = _not_asked(self.service)
                continue
            client.check_cancel()
            try:
                out[text.text_id] = self.abstract(client, text)
                failures = 0
            except ServiceUnavailable as exc:
                out[text.text_id] = exc
                failures += 1
            except (ServiceError, ValueError) as exc:  # reported per text by the caller
                out[text.text_id] = exc
            if progress is not None:
                progress(n + 1, len(texts))
        return out

    def _batched(
        self, client: HttpClient, texts: Sequence[TextRef], progress: Progress | None
    ) -> dict[str, Any]:
        out: dict[str, Any] = {}
        by_key: dict[tuple[str, str], list[TextRef]] = {}
        for t in texts:
            key = self.lookup(t)
            if key is None:
                out[t.text_id] = None
            else:
                by_key.setdefault(key, []).append(t)

        def settle(key: tuple[str, str], value: Any) -> None:
            for t in by_key[key]:
                if isinstance(value, Exception) or value is None:
                    out[t.text_id] = value
                    continue
                try:
                    out[t.text_id] = self.found(value, t)
                except (ValueError, KeyError, TypeError) as exc:
                    out[t.text_id] = ValueError(f"unreadable record: {exc}")

        missing: dict[str, list[str]] = {}
        for key in sorted(by_key):
            hit, value = client.cached_item(self.service, self.item_kind, f"{key[0]}:{key[1]}")
            if hit:
                settle(key, value)
            else:
                missing.setdefault(key[0], []).append(key[1])
        total = len(texts)
        if progress is not None:
            progress(len(out), total)
        chunks = [
            (f, values[i : i + self.batch])
            for f, values in missing.items()
            for i in range(0, len(values), self.batch)
        ]
        failures = 0
        for f, chunk in chunks:
            if failures >= MAX_FAILURES_IN_A_ROW:
                for v in chunk:
                    settle((f, v), _not_asked(self.service))
                continue
            client.check_cancel()
            try:
                records = self.fetch(client, f, chunk)
                failures = 0
            except ServiceUnavailable as exc:
                failures += 1
                records = exc
            except (ServiceError, ValueError) as exc:
                records = exc
            for v in chunk:
                if isinstance(records, Exception):
                    settle((f, v), records)
                    continue
                value = records.get(v)
                client.store_item(self.service, self.item_kind, f"{f}:{v}", value)
                settle((f, v), value)
            if progress is not None:
                progress(len(out), total)
        return out
