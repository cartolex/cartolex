# SPDX-License-Identifier: MIT
"""The banned-terms linter: numbering, normalisation, exceptions, commits, privacy."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "vocab_scan", Path(__file__).resolve().parent.parent / "tools" / "vocab_scan.py"
)
assert _SPEC and _SPEC.loader
vocab_scan = importlib.util.module_from_spec(_SPEC)
sys.modules["vocab_scan"] = vocab_scan
_SPEC.loader.exec_module(vocab_scan)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.org", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    return root


@pytest.fixture()
def deny(tmp_path: Path) -> Path:
    path = tmp_path / "deny.txt"
    path.write_text("# comment\n\n\\bzorblax\\b\nquuxcorp\n", encoding="utf-8")
    return path


def test_patterns_are_numbered_without_comments(deny: Path) -> None:
    numbers = [n for n, _ in vocab_scan.load_patterns(deny)]
    assert numbers == [1, 2]


def test_accents_and_case_are_ignored(deny: Path) -> None:
    patterns = vocab_scan.load_patterns(deny)
    hits = vocab_scan.scan_text("A ZÖRBLAX appears", "f.txt", patterns)
    assert [(h.line, h.pattern) for h in hits] == [(1, 1)]


def test_inline_exception_covers_its_line_and_the_next(deny: Path) -> None:
    patterns = vocab_scan.load_patterns(deny)
    text = "zorblax  # vocab-allow: fixture\n# vocab-allow: next line\nquuxcorp\nzorblax\n"
    hits = vocab_scan.scan_text(text, "f.py", patterns)
    assert [(h.line, h.pattern) for h in hits] == [(4, 1)]


def test_report_never_contains_the_matched_text(repo: Path, deny: Path, capsys) -> None:
    (repo / "a.txt").write_text("quuxcorp\n", encoding="utf-8")
    code = vocab_scan.main(["--root", str(repo), "--list", str(deny)])
    out = capsys.readouterr().out
    assert code == 1
    assert "a.txt:1: banned term #2" in out
    assert "quuxcorp" not in out


def test_file_names_and_binary_files(repo: Path, deny: Path) -> None:
    (repo / "zorblax_notes.txt").write_text("clean\n", encoding="utf-8")
    (repo / "blob.bin").write_bytes(b"\0quuxcorp")
    hits = vocab_scan.scan_tree(repo, vocab_scan.load_patterns(deny))
    assert [(h.where, h.pattern) for h in hits] == [("zorblax_notes.txt (path)", 1)]


def test_file_allow_needs_a_reason_and_limits_numbers(repo: Path, deny: Path) -> None:
    if vocab_scan.tomllib is None:
        pytest.skip("tomllib needs Python 3.11")
    (repo / "tools").mkdir()
    (repo / "meta.json").write_text('{"a": "zorblax quuxcorp"}\n', encoding="utf-8")
    (repo / "tools" / "vocab-allow.toml").write_text(
        '[[allow]]\npath = "meta.json"\npatterns = [1]\nreason = "metadata"\n', encoding="utf-8"
    )
    hits = vocab_scan.scan_tree(repo, vocab_scan.load_patterns(deny))
    assert [(h.where, h.pattern) for h in hits] == [("meta.json", 2)]


def test_commit_messages_are_scanned(repo: Path, deny: Path) -> None:
    (repo / "a.txt").write_text("clean\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "docs: mention quuxcorp")
    hits = vocab_scan.scan_commits(repo, "HEAD", vocab_scan.load_patterns(deny))
    assert [h.pattern for h in hits] == [2]


def test_co_author_trailers_are_reported_even_without_a_list(repo: Path, monkeypatch) -> None:
    (repo / "a.txt").write_text("clean\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(
        repo, "commit", "-q", "-m", "feat: one\n\nBody.\n\nco-authored-by: Someone <s@example.org>"
    )
    _git(repo, "commit", "-q", "--allow-empty", "-m", "fix: two, co-authored-by nobody in the text")
    assert [x.split(":", 1)[1] for x in vocab_scan.scan_trailers(repo, "HEAD")] == [
        "5: co-author trailer"
    ]
    monkeypatch.delenv("CARTOLEX_DENYLIST", raising=False)
    monkeypatch.setattr(vocab_scan, "DEFAULT_LIST", repo / "absent.txt")
    assert vocab_scan.main(["--root", str(repo), "--commits", "HEAD"]) == 1
    assert vocab_scan.main(["--root", str(repo), "--commits", "HEAD~1..HEAD"]) == 0


def test_missing_list_skips_or_fails_on_request(repo: Path, monkeypatch) -> None:
    monkeypatch.delenv("CARTOLEX_DENYLIST", raising=False)
    monkeypatch.setattr(vocab_scan, "DEFAULT_LIST", repo / "absent.txt")
    assert vocab_scan.main(["--root", str(repo)]) == 0
    assert vocab_scan.main(["--root", str(repo), "--require-list"]) == 2


def test_paths_scan_outside_git(tmp_path: Path, deny: Path) -> None:
    world = tmp_path / "world"
    (world / "sub").mkdir(parents=True)
    (world / "sub" / "groups.csv").write_text("g1,ZORBLAX-TEAM\n", encoding="utf-8")
    (world / "quuxcorp.txt").write_text("clean\n", encoding="utf-8")
    hits = vocab_scan.scan_paths([world], vocab_scan.load_patterns(deny))
    assert sorted((Path(h.where).name, h.pattern) for h in hits) == [
        ("groups.csv", 1),
        ("quuxcorp.txt (path)", 2),
    ]


def test_strict_mode_counts_what_inline_exceptions_hide(repo: Path, deny: Path, capsys) -> None:
    (repo / "a.py").write_text("x = 'zorblax'  # vocab-allow: fixture\n", encoding="utf-8")
    assert vocab_scan.main(["--root", str(repo), "--list", str(deny)]) == 0
    assert vocab_scan.main(["--root", str(repo), "--list", str(deny), "--strict"]) == 1
    assert "a.py:1: banned term #1" in capsys.readouterr().out
