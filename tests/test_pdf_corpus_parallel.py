# SPDX-License-Identifier: MIT
"""PDF corpus assembly: working pdfminer fallback + parallel extraction parity.

``_extract_with_pdfminer`` used to call ``pdfminer.high_level.extract_text``
with an ``output_string`` kwarg that only exists on ``extract_text_to_fp`` —
the fallback always raised ``TypeError`` and short/scanned PDFs were silently
dropped.  ``build_pdf_corpus`` gains ``n_jobs``: extraction fans out to worker
processes but everything else (cleanup, txt writes, hook, index) stays in the
parent, in index order, so parallel output is byte-identical to serial.
"""

from __future__ import annotations

import csv
from pathlib import Path

from cartolex.lexicon.pdf_corpus import build_pdf_corpus
from cartolex.lexicon.pdf_text import _extract_with_pdfminer, extract_text


def _minimal_pdf(text: str) -> bytes:
    """Build a one-page PDF with *text* in a single uncompressed Tj stream."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_at,
    )
    return bytes(out)


# Comfortably above pdf_text._MIN_TEXT_LEN so the pypdf path never falls back.
_TEXTS = [
    "Physics of active matter and stochastic thermodynamics in living systems.",
    "Sociologie des mondes ruraux et des transformations du travail agricole.",
    "Deep-sea microbial ecology and biogeochemical cycles of the abyssal plain.",
    "Histoire des sciences au dix-neuvieme siecle et circulation des savoirs.",
]


def _write_fixture_corpus(root: Path) -> tuple[Path, Path]:
    """Write 4 text PDFs + 1 blank-page PDF + an index CSV referencing them,
    plus one index row whose file is missing on disk."""
    pdf_dir = root / "pdfs"
    pdf_dir.mkdir()
    rows = []
    for i, text in enumerate(_TEXTS):
        name = f"doc_{i}.pdf"
        (pdf_dir / name).write_bytes(_minimal_pdf(text))
        rows.append(name)
    (pdf_dir / "blank.pdf").write_bytes(_minimal_pdf(" "))
    rows.append("blank.pdf")
    rows.append("missing.pdf")  # listed in the index, absent on disk

    index_csv = root / "pdf_index.csv"
    with index_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "last_name",
                "first_name",
                "unit",
                "rank",
                "start_year",
                "doc_year",
                "doc_type",
                "pdf_index",
                "pdf_filename",
                "pdf_url",
            ],
        )
        writer.writeheader()
        for i, name in enumerate(rows):
            writer.writerow(
                {
                    "last_name": f"Name{i}",
                    "first_name": f"First{i}",
                    "unit": "GRP0000",
                    "rank": "senior",
                    "start_year": "2020",
                    "doc_year": "2025",
                    "doc_type": "annual_report",
                    "pdf_index": str(i),
                    "pdf_filename": name,
                    "pdf_url": "",
                }
            )
    return pdf_dir, index_csv


def test_pdfminer_extractor_returns_text(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(_minimal_pdf(_TEXTS[0]))
    text = _extract_with_pdfminer(pdf)
    assert "stochastic thermodynamics" in text


def test_extract_text_falls_back_to_pdfminer(tmp_path, monkeypatch) -> None:
    """When pypdf yields (near-)empty text, the pdfminer fallback must rescue."""
    import cartolex.lexicon.pdf_text as pdf_text

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(_minimal_pdf(_TEXTS[0]))
    monkeypatch.setattr(pdf_text, "_extract_with_pypdf", lambda _p: "")
    text = extract_text(pdf)
    assert "stochastic thermodynamics" in text


def _run_build(root: Path, pdf_dir: Path, index_csv: Path, n_jobs: int):
    out_dir = root / f"corpus_{n_jobs}"
    out_index = root / f"index_{n_jobs}.csv"
    hook_rows: list[tuple[str, str]] = []
    build_pdf_corpus(
        pdf_dir=pdf_dir,
        pdf_index_csv=index_csv,
        out_dir=out_dir,
        index_csv=out_index,
        label="test",
        per_doc_hook=lambda raw, row: hook_rows.append((row["pdf_filename"], raw[:40])),
        n_jobs=n_jobs,
    )
    txts = {p.name: p.read_text(encoding="utf-8") for p in sorted(out_dir.glob("*.txt"))}
    index_rows = list(csv.DictReader(out_index.open(encoding="utf-8")))
    for row in index_rows:
        row["txt_path"] = Path(row["txt_path"]).name  # out dirs differ by n_jobs
    return txts, index_rows, hook_rows


def test_build_pdf_corpus_parallel_matches_serial(tmp_path: Path) -> None:
    pdf_dir, index_csv = _write_fixture_corpus(tmp_path)
    serial = _run_build(tmp_path, pdf_dir, index_csv, n_jobs=1)
    parallel = _run_build(tmp_path, pdf_dir, index_csv, n_jobs=2)

    assert serial == parallel
    txts, index_rows, hook_rows = serial
    # 4 text docs extracted; the blank page and the missing file are skipped.
    assert len(txts) == 4
    assert [r["last_name"] for r in index_rows] == ["Name0", "Name1", "Name2", "Name3"]
    # Person attributes pass through in their order; the file columns (pdf_*) do not.
    assert list(index_rows[0]) == [
        "last_name",
        "first_name",
        "unit",
        "rank",
        "start_year",
        "doc_year",
        "doc_type",
        "source",
        "txt_path",
    ]
    assert {r["rank"] for r in index_rows} == {"senior"}
    assert {r["start_year"] for r in index_rows} == {"2020"}
    # The hook runs in the parent, once per extracted doc, in index order.
    assert [name for name, _ in hook_rows] == [f"doc_{i}.pdf" for i in range(4)]
    assert "active matter" in txts["doc_0.txt"]


def test_build_pdf_corpus_isolates_one_bad_document(tmp_path: Path, monkeypatch) -> None:
    """A cleanup/write failure on one row is logged and skipped, not fatal."""
    from cartolex.lexicon import pdf_corpus

    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    for name in ("a", "b", "c"):
        (pdf_dir / f"{name}.pdf").write_bytes(_minimal_pdf(f"Document {name} text " * 5))
    index = tmp_path / "pdf_index.csv"
    with index.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["last_name", "first_name", "unit", "pdf_filename"])
        w.writeheader()
        for name in ("a", "b", "c"):
            w.writerow(
                {
                    "last_name": name.upper(),
                    "first_name": "x",
                    "unit": "U",
                    "pdf_filename": f"{name}.pdf",
                }
            )

    real_clean = pdf_corpus.clean_text

    def _clean(text: str) -> str:
        if "Document b" in text:
            raise ValueError("boom on b")
        return real_clean(text)

    monkeypatch.setattr(pdf_corpus, "clean_text", _clean)
    hook_calls: list[str] = []

    def _hook(raw: str, row: dict) -> None:
        hook_calls.append(row["last_name"])
        if row["last_name"] == "C":
            raise RuntimeError("hook exploded on c")

    out_index = tmp_path / "corpus_index.csv"
    pdf_corpus.build_pdf_corpus(
        pdf_dir=pdf_dir,
        pdf_index_csv=index,
        out_dir=tmp_path / "txt",
        index_csv=out_index,
        per_doc_hook=_hook,
    )
    with out_index.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["last_name"] for r in rows] == ["A", "C"]  # b skipped, c kept despite hook
    assert hook_calls == ["A", "C"]
