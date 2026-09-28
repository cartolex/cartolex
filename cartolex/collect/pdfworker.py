# SPDX-License-Identifier: MIT
"""PDF text in a worker process, with a timeout: one file that hangs cannot stall a job.

Reading a PDF can take a very long time, or never end, on a file whose
structure loops or whose fonts are broken. :class:`PdfWorker` reads each file
in a separate process started once per job (``spawn``: nothing of the job's
threads or connections is copied into it) and waits at most *timeout* seconds
for each; a file that takes longer is left out with the reason, the process is
stopped, and the next file gets a new one. A file that cannot be read is
reported the same way, and the job goes on.

The folder import (:func:`cartolex.collect.people_import.import_folder`) and
the text providers (:func:`cartolex.collect.providers.improve_texts`) read
every PDF this way.
"""

from __future__ import annotations

import contextlib
import importlib
import multiprocessing
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from pathlib import Path
from typing import Any

__all__ = ["DEFAULT_TIMEOUT", "PdfError", "PdfWorker", "extract_pdf", "pdf_worker"]

#: Seconds a file may take before it is left out.
DEFAULT_TIMEOUT = 120.0
#: Seconds a new worker may take to start (it loads the reader first; files wait for it).
STARTUP_TIMEOUT = 120.0
#: The function that reads a file's text, as ``module:function``.
EXTRACTOR = "cartolex.lexicon.pdf_text:extract_text"


class PdfError(Exception):
    """A PDF whose text could not be read: why, in words."""


def _load(spec: str) -> Callable[[Path], str]:
    module, _, name = spec.partition(":")
    return getattr(importlib.import_module(module), name)


def _serve(conn: Any, extractor: str) -> None:  # pragma: no cover - runs in the worker
    """The worker's loop: say it is ready, then read a path, answer ``("ok", text)`` or
    ``("error", reason)``."""
    extract = _load(extractor)
    import pypdf  # noqa: F401 - loaded before the first file, not during its time

    conn.send(("ready", None))
    while True:
        try:
            message = conn.recv()
        except EOFError:
            return
        if message is None:
            return
        path, strict = message
        try:
            if strict:  # a file that is not a PDF fails here, with its reason
                import pypdf

                with open(path, "rb") as fh:
                    len(pypdf.PdfReader(fh, strict=True).pages)
            conn.send(("ok", extract(Path(path))))
        except Exception as exc:  # noqa: BLE001 - every failure is an answer
            conn.send(("error", f"{type(exc).__name__}: {str(exc)[:160]}"))


class PdfWorker:
    """Reads PDF files in a worker process, at most *timeout* seconds each.

    Use it as a context manager (the process stops at the end); the process
    starts with the first file. *extractor* (``module:function``) replaces the
    reading function (tests).
    """

    def __init__(self, timeout: float = DEFAULT_TIMEOUT, *, extractor: str | None = None) -> None:
        if not timeout > 0:
            raise ValueError("a timeout is a positive number of seconds")
        self.timeout = float(timeout)
        self.extractor = extractor or EXTRACTOR
        self._process: Any = None
        self._conn: Any = None
        #: How many workers were stopped because a file took too long.
        self.stopped = 0

    def _start(self) -> None:
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(
            target=_serve, args=(child, self.extractor), name="cartolex-pdf", daemon=True
        )
        process.start()
        child.close()
        self._process, self._conn = process, parent
        try:
            ready = parent.poll(STARTUP_TIMEOUT) and parent.recv()[0] == "ready"
        except (EOFError, OSError):
            ready = False
        if not ready:
            self._stop()
            raise PdfError("the PDF reader could not start")

    def extract(self, path: Path, *, strict: bool = False) -> str:
        """The text of the PDF at *path*; :class:`PdfError` says why when there is none.

        With *strict*, a file whose structure is not a PDF's is refused first.
        """
        if self._process is None or not self._process.is_alive():
            self._start()
        try:
            self._conn.send((str(path), strict))
            ready = self._conn.poll(self.timeout)
        except (BrokenPipeError, EOFError, OSError) as exc:
            self._stop()
            raise PdfError(f"the reader stopped ({type(exc).__name__})") from None
        if not ready:
            self._stop()
            self.stopped += 1
            raise PdfError(f"no text after {self.timeout:g} s: the file was left out")
        try:
            status, value = self._conn.recv()
        except (EOFError, OSError):
            self._stop()
            raise PdfError("the reader stopped while reading the file") from None
        if status != "ok":
            raise PdfError(value)
        return value

    def _stop(self) -> None:
        if self._process is not None:
            with contextlib.suppress(Exception):
                self._process.kill()
            with contextlib.suppress(Exception):
                self._process.join(5)
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
        self._process = self._conn = None

    def close(self) -> None:
        """Stop the worker process."""
        if self._conn is not None and self._process is not None and self._process.is_alive():
            with contextlib.suppress(Exception):
                self._conn.send(None)
                self._process.join(5)
        self._stop()

    def __enter__(self) -> PdfWorker:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


_CURRENT: ContextVar[PdfWorker | None] = ContextVar("cartolex_pdf_worker", default=None)


@contextlib.contextmanager
def pdf_worker(
    timeout: float = DEFAULT_TIMEOUT, *, extractor: str | None = None
) -> Iterator[PdfWorker]:
    """A worker for the PDFs of one job, used by :func:`extract_pdf` while the block runs."""
    worker = PdfWorker(timeout, extractor=extractor)
    token = _CURRENT.set(worker)
    try:
        yield worker
    finally:
        _CURRENT.reset(token)
        worker.close()


def extract_pdf(path: Path, *, strict: bool = False) -> str:
    """The text of one PDF, through the job's worker (or a worker of its own)."""
    worker = _CURRENT.get()
    if worker is not None:
        return worker.extract(path, strict=strict)
    with PdfWorker() as own:
        return own.extract(path, strict=strict)
