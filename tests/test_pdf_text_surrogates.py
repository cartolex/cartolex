"""``extract_text`` must never return a string that cannot be UTF-8 encoded.

A document whose font maps a supplementary-plane character to a lone
surrogate made pypdf emit ``\\ud835``, and a whole corpus build died on
``UnicodeEncodeError: surrogates not allowed`` at the first ``f.write``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cartolex.lexicon import pdf_text


def test_repair_surrogates_recombines_split_pairs_and_drops_lone_halves() -> None:
    # "𝑥" is U+1D465 MATHEMATICAL ITALIC SMALL X split in two units.
    broken = "f(𝑥) = \ud835 sin x"
    fixed = pdf_text.repair_surrogates(broken)
    fixed.encode("utf-8")  # must not raise
    assert "\U0001d465" in fixed
    assert "\ud835" not in fixed
    assert fixed.startswith("f(") and fixed.endswith(" sin x")


def test_repair_surrogates_is_identity_on_clean_text() -> None:
    clean = "Résumé — 𝑥 ∈ ℝ, ﬁnal"
    assert pdf_text.repair_surrogates(clean) is clean
    assert pdf_text.repair_surrogates("") == ""


def test_extract_text_output_is_always_encodable(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "dossier.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub")
    long_enough = "Rapport annuel " * 10 + "\ud835 sur la théorie"
    monkeypatch.setattr(pdf_text, "_extract_with_pypdf", lambda _p: long_enough)
    monkeypatch.setattr(
        pdf_text, "_extract_with_pdfminer", lambda _p: pytest.fail("fallback not expected")
    )
    out = pdf_text.extract_text(pdf)
    out.encode("utf-8")
    assert "\ud835" not in out
    assert out.endswith(" sur la théorie")


def test_extract_text_fallback_path_is_also_repaired(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub")
    monkeypatch.setattr(pdf_text, "_extract_with_pypdf", lambda _p: "")
    monkeypatch.setattr(pdf_text, "_extract_with_pdfminer", lambda _p: "texte du repli \udc00 " * 8)
    out = pdf_text.extract_text(pdf)
    out.encode("utf-8")
    assert "\udc00" not in out
