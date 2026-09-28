# SPDX-License-Identifier: MIT
"""Shared pytest fixtures for the cartolex test suite.

All synthetic data used here is fabricated — no real researcher names,
corpora, or evaluation materials appear in this file or in tests/fixtures/.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# No test may reach the network. Loopback stays open for local servers and
# Unix sockets for multiprocessing; anything else raises at connect time.
# ---------------------------------------------------------------------------

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _allowed(sock: socket.socket, address: Any) -> bool:
    if getattr(socket, "AF_UNIX", None) is not None and sock.family == socket.AF_UNIX:
        return True
    host = address[0] if isinstance(address, tuple) and address else address
    return isinstance(host, str) and host in _LOOPBACK


def _guarded_connect(self: socket.socket, address: Any) -> None:
    if not _allowed(self, address):
        raise RuntimeError(f"network access is blocked in tests (tried {address!r})")
    return _real_connect(self, address)


def _guarded_connect_ex(self: socket.socket, address: Any) -> int:
    if not _allowed(self, address):
        raise RuntimeError(f"network access is blocked in tests (tried {address!r})")
    return _real_connect_ex(self, address)


socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]

# Make langdetect deterministic across the whole suite (its default seed is a
# PRNG, which would make language-detection assertions flaky).
try:  # pragma: no cover - only when langdetect is installed
    from langdetect import DetectorFactory

    DetectorFactory.seed = 0
except Exception:  # pragma: no cover
    pass

# ---------------------------------------------------------------------------
# Language models. A test that parses texts is marked ``models`` (optionally
# with the languages it needs). Without the models it is skipped, unless the
# run passes ``--require-models`` (tools/check.py does): then it fails.
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--require-models",
        action="store_true",
        default=False,
        help="fail, instead of skipping, tests marked 'models' whose language models are missing",
    )
    parser.addoption(
        "--heavy",
        action="store_true",
        default=False,
        help="also run the tests marked 'heavy' (large measures; run them under a memory cap)",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "models(*langs): the test parses texts with the pinned language models of these "
        "languages (default: every supported language)",
    )
    config.addinivalue_line(
        "markers",
        "heavy: a large measure (minutes, gigabytes), run only with --heavy: the full "
        "check runs it once, under the machine's memory-capped runner",
    )


def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.get_closest_marker("heavy") and not item.config.getoption("--heavy"):
        pytest.skip("a heavy measure: run with --heavy")
    marker = item.get_closest_marker("models")
    if marker is None:
        return
    from cartolex.lexicon import language_models

    langs = marker.args or language_models.supported_languages()
    missing = [
        language_models.spec(lang).name for lang in langs if not language_models.installed(lang)
    ]
    if not missing:
        return
    message = f"language model(s) not installed: {', '.join(missing)}"
    if item.config.getoption("--require-models"):
        pytest.fail(message + " (see tools/requirements-models.txt)", pytrace=False)
    pytest.skip(message)


# ---------------------------------------------------------------------------
# Workspace fixture — a temporary directory wired up as a project root
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """Create a minimal project workspace in a temporary directory.

    The directory tree mirrors the production layout but contains only
    synthetic placeholder files.
    """
    (tmp_path / "automatic_data").mkdir()
    (tmp_path / "manual_data").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "lexical_analysis" / "models").mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def synthetic_corpus(workspace: Path) -> tuple[Path, Path]:
    """Write two small synthetic text corpora (FR and EN) and matching indexes.

    The French documents sit in a ``reports`` slot, the English ones in the
    ``manual`` slot. Returns (reports_index_csv, manual_index_csv) paths.
    All author IDs and text content are fabricated.
    """
    corpus_dir = workspace / "automatic_data" / "corpus_reports"
    corpus_dir.mkdir(parents=True, exist_ok=True)

    # Synthetic French-language documents
    fr_docs = [
        (
            "synth_001.txt",
            "apprentissage automatique réseaux de neurones classification supervisée",
        ),
        ("synth_002.txt", "physique quantique intrication photons polarisation mesure"),
        ("synth_003.txt", "biologie moléculaire protéines expression génique séquençage"),
    ]
    for fname, content in fr_docs:
        (corpus_dir / fname).write_text(content, encoding="utf-8")

    # Write the synthetic index of the reports slot.
    # first_name / last_name are required by the pipeline schema but are
    # completely fabricated — they do NOT correspond to real researchers.
    index_lines = ["last_name,first_name,unit,txt_path"]
    synthetic_names = [
        ("SynthA", "ResearcherOne"),
        ("SynthB", "ResearcherTwo"),
        ("SynthC", "ResearcherThree"),
    ]
    for (fname, _), (last, first) in zip(fr_docs, synthetic_names, strict=True):
        rel = Path("automatic_data") / "corpus_reports" / fname
        index_lines.append(f"{last},{first},LAB_SYNTH,{rel}")
    reports_index = workspace / "reports_index.csv"
    reports_index.write_text("\n".join(index_lines), encoding="utf-8")

    # English-language documents
    en_corpus_dir = workspace / "automatic_data" / "corpus_manual"
    en_corpus_dir.mkdir(parents=True, exist_ok=True)
    en_docs = [
        ("synth_en_001.txt", "machine learning neural networks supervised classification deep"),
        ("synth_en_002.txt", "quantum physics entanglement photons polarization measurement"),
        ("synth_en_003.txt", "molecular biology proteins gene expression sequencing genomics"),
    ]
    for fname, content in en_docs:
        (en_corpus_dir / fname).write_text(content, encoding="utf-8")

    manual_index_lines = ["last_name,first_name,unit,txt_path"]
    manual_synthetic_names = [
        ("SynthD", "ResearcherFour"),
        ("SynthE", "ResearcherFive"),
        ("SynthF", "ResearcherSix"),
    ]
    for (fname, _), (last, first) in zip(en_docs, manual_synthetic_names, strict=True):
        rel = Path("automatic_data") / "corpus_manual" / fname
        manual_index_lines.append(f"{last},{first},LAB_SYNTH,{rel}")
    manual_index = workspace / "manual_index.csv"
    manual_index.write_text("\n".join(manual_index_lines), encoding="utf-8")

    # An empty index of a third slot the settings do not declare.
    (workspace / "unused_index.csv").write_text(
        "last_name,first_name,unit,txt_path\n", encoding="utf-8"
    )

    return reports_index, manual_index


@pytest.fixture()
def minimal_config():
    """Return a small KeywordsConfig for the synthetic workspace."""
    from cartolex.lexicon.config import KeywordsConfig

    return KeywordsConfig(
        corpus_slots=("reports", "manual"),
        ngram_range=(1, 2),
        min_df=1,
        max_df=0.95,
        max_features=500,
        length_bonus_alpha=1.0,
        nested_threshold=1.1,
        top_n_researcher=5,
        top_n_unit=10,
        top_n_domain=20,
    )


@pytest.fixture()
def minimal_context(workspace: Path, minimal_config):
    """Return the run context of the synthetic workspace with :func:`minimal_config`."""
    from cartolex.context import RunContext

    return RunContext.for_workspace(workspace, minimal_config, now_year=2026)
