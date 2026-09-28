# SPDX-License-Identifier: MIT
"""PDF text in a worker process: a file that hangs is left out with its reason, and the job
goes on; folder imports and text providers read their PDFs this way."""

from __future__ import annotations

import time

import pytest

from cartolex.collect import pdfworker
from cartolex.collect.pdfworker import PdfError, PdfWorker
from cartolex.demo.services.render import render_pdf

TEXT = (
    "Sediment transport on tidal flats was measured over two winters, with wave gauges "
    "and turbidity sensors along a sheltered estuary shore."
)


def _pdf(path, text=TEXT):
    path.write_bytes(render_pdf([text]))
    return path


def test_a_file_that_hangs_is_left_out_and_the_next_one_is_read(tmp_path) -> None:
    good = _pdf(tmp_path / "good.pdf")
    slow = _pdf(tmp_path / "slow.pdf")
    with PdfWorker(1.5, extractor="_pdf_fakes:hangs_on_slow") as worker:
        assert "tidal flats" in worker.extract(good)
        started = time.monotonic()
        with pytest.raises(PdfError, match="no text after 1.5 s"):
            worker.extract(slow)
        assert time.monotonic() - started < 10
        assert worker.stopped == 1
        assert "tidal flats" in worker.extract(good)  # a new worker took over


def test_what_cannot_be_read_says_why(tmp_path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4 this is not a real document")
    with PdfWorker(30) as worker:
        with pytest.raises(PdfError, match="PdfReadError|Error"):
            worker.extract(broken, strict=True)
        assert "tidal flats" in worker.extract(_pdf(tmp_path / "ok.pdf"))
    with pytest.raises(ValueError):
        PdfWorker(0)


def test_a_folder_import_leaves_a_hanging_file_out(tmp_path, monkeypatch) -> None:
    from cartolex.collect.people_import import import_folder, import_people
    from cartolex.project import Project

    monkeypatch.setattr(pdfworker, "EXTRACTOR", "_pdf_fakes:hangs_on_slow")
    project = Project.init(tmp_path / "p", name="Docs", domain_title="Coasts")
    import_people(project, "last_name,first_name\nTavelin,Ada\n")
    docs = tmp_path / "docs" / "Ada Tavelin"
    docs.mkdir(parents=True)
    _pdf(docs / "report-2021.pdf")
    _pdf(docs / "slow-2022.pdf")
    (docs / "notes-2020.txt").write_text("Wave climate along a sandy coast.", encoding="utf-8")
    report = import_folder(project, tmp_path / "docs", pdf_timeout=1.5)
    assert report.texts == 2
    assert dict(report.refused) == {
        "Ada Tavelin/slow-2022.pdf": "could not be read (no text after 1.5 s: the file was left out)"
    }
    project.close()
