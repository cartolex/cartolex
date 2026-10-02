# SPDX-License-Identifier: MIT
"""Banned-terms linter: keep project-specific words out of files, commits and tags.

The list of banned terms is private to whoever runs the scan. It is read from a
file given by ``--list``, by ``$CARTOLEX_DENYLIST``, or from
``~/.config/cartolex-dev/deny-list.txt``. The file holds one regular expression
per line (``#`` starts a comment); each is matched against accent-stripped,
lower-cased text. A match is reported by the pattern's number and its place —
never by the matched text — so a public log reveals nothing about the list.

Exceptions:

* inline: a line containing ``vocab-allow: <reason>``, or preceded by a line
  that does, is not reported;
* per file: ``tools/vocab-allow.toml`` lists ``[[allow]]`` tables with ``path``
  (a glob), ``patterns`` (numbers) and ``reason``.

Commit messages get one more rule, public and independent of the list: a commit
has one author, so a ``Co-Authored-By`` trailer is reported even when no list is
found.

Usage::

    python tools/vocab_scan.py                      # the working tree
    python tools/vocab_scan.py --commits A..B       # also these commit messages
    python tools/vocab_scan.py --tags               # also tag names and messages

Exit status: 0 clean, 1 matches found, 2 usage error (e.g. ``--require-list``
without a list).
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10
    tomllib = None

DEFAULT_LIST = Path.home() / ".config" / "cartolex-dev" / "deny-list.txt"
ALLOW_FILE = Path("tools") / "vocab-allow.toml"
INLINE_MARK = "vocab-allow:"
_JOINERS = re.compile(r"[_./\\-]+")
_TRAILER = re.compile(r"co-authored-by\s*:", re.IGNORECASE)
SKIP_DIRS = {".git", ".venvs", ".cache", "build", "dist", "docs/_build", "__pycache__"}


@dataclass(frozen=True)
class Hit:
    """One match: where it is and which pattern matched (by number)."""

    where: str
    line: int
    pattern: int

    def render(self) -> str:
        """Format the hit without revealing the matched text."""
        loc = f"{self.where}:{self.line}" if self.line else self.where
        return f"{loc}: banned term #{self.pattern}"


def normalize(text: str) -> str:
    """Strip accents and lower-case, so patterns match any spelling variant."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def load_patterns(path: Path) -> list[tuple[int, re.Pattern[str]]]:
    """Read the numbered patterns; numbering counts non-comment, non-blank lines."""
    patterns: list[tuple[int, re.Pattern[str]]] = []
    number = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        number += 1
        patterns.append((number, re.compile(normalize(line))))
    return patterns


def load_file_allows(root: Path) -> list[tuple[str, set[int]]]:
    """Read per-file exceptions from ``tools/vocab-allow.toml`` (if present)."""
    path = root / ALLOW_FILE
    if not path.is_file() or tomllib is None:
        return []
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    allows = []
    for entry in data.get("allow", []):
        if not entry.get("reason"):
            raise SystemExit(f"{ALLOW_FILE}: every [[allow]] needs a reason")
        allows.append((str(entry["path"]), {int(n) for n in entry.get("patterns", [])}))
    return allows


def scan_text(
    text: str,
    where: str,
    patterns: list[tuple[int, re.Pattern[str]]],
    allowed: set[int] | None = None,
    honour_inline: bool = True,
) -> list[Hit]:
    """Scan *text* line by line and return the hits not covered by an exception."""
    hits: list[Hit] = []
    previous = ""
    for lineno, line in enumerate(text.splitlines(), start=1):
        if honour_inline and (INLINE_MARK in line or INLINE_MARK in previous):
            previous = line
            continue
        norm = normalize(line)
        # Also match with word joiners turned into spaces, so a banned word
        # inside snake_case, dotted or dashed names is caught by \b patterns.
        split = _JOINERS.sub(" ", norm)
        for number, rx in patterns:
            if allowed and number in allowed:
                continue
            if rx.search(norm) or rx.search(split):
                hits.append(Hit(where, lineno, number))
        previous = line
    return hits


def tracked_files(root: Path) -> list[Path]:
    """Files git knows about, plus untracked files that are not ignored."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    files = []
    for rel in sorted({p for p in out.decode("utf-8").split("\0") if p}):
        if any(rel == d or rel.startswith(d + "/") for d in SKIP_DIRS):
            continue
        if (root / rel).is_file():
            files.append(Path(rel))
    return files


def is_binary(data: bytes) -> bool:
    """Treat a file with a NUL byte in its first 8 KiB as binary."""
    return b"\0" in data[:8192]


def scan_tree(
    root: Path,
    patterns: list[tuple[int, re.Pattern[str]]],
    honour_inline: bool = True,
    cache: Path | None = None,
) -> list[Hit]:
    """Scan file paths and text contents of the working tree.

    With *cache* (a JSON file), a file whose path and content did not change since the
    last scan with the same patterns, exceptions and mode keeps the hits it had then (the
    file holds content digests, places and pattern numbers, never a term).
    """
    allows = load_file_allows(root)
    setting = hashlib.sha256(
        json.dumps(
            [[rx.pattern for _n, rx in patterns], sorted(map(str, allows)), honour_inline]
        ).encode("utf-8")
    ).hexdigest()
    memo: dict[str, list] = {}
    if cache is not None and cache.is_file():
        try:
            saved = json.loads(cache.read_text(encoding="utf-8"))
            if saved.get("setting") == setting:
                memo = saved.get("files") or {}
        except ValueError:
            memo = {}
    kept: dict[str, list] = {}
    hits: list[Hit] = []
    for rel in tracked_files(root):
        rel_s = rel.as_posix()
        data = (root / rel).read_bytes()
        key = hashlib.sha256(rel_s.encode("utf-8") + b"\0" + data).hexdigest()
        if key in memo:
            found = [Hit(*h) for h in memo[key]]
        else:
            found = _scan_file(rel_s, data, patterns, allows, honour_inline)
        kept[key] = [[h.where, h.line, h.pattern] for h in found]
        hits += found
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"setting": setting, "files": kept}), encoding="utf-8")
    return hits


def _scan_file(
    rel_s: str,
    data: bytes,
    patterns: list[tuple[int, re.Pattern[str]]],
    allows: list[tuple[str, set[int]]],
    honour_inline: bool,
) -> list[Hit]:
    allowed: set[int] = set()
    for glob, numbers in allows:
        if fnmatch.fnmatch(rel_s, glob):
            allowed |= numbers
    hits = [
        Hit(rel_s + " (path)", 0, h.pattern)
        for h in scan_text(rel_s, rel_s, patterns, allowed, honour_inline=False)
    ]
    if not is_binary(data):
        hits += scan_text(
            data.decode("utf-8", errors="replace"), rel_s, patterns, allowed, honour_inline
        )
    return hits


def scan_paths(paths: list[Path], patterns: list[tuple[int, re.Pattern[str]]]) -> list[Hit]:
    """Scan every text file under *paths* (outside git: generated data, build outputs)."""
    hits: list[Hit] = []
    for base in paths:
        files = [base] if base.is_file() else sorted(f for f in base.rglob("*") if f.is_file())
        for f in files:
            where = f.as_posix()
            hits += [
                Hit(where + " (path)", 0, h.pattern)
                for h in scan_text(f.name, where, patterns, honour_inline=False)
            ]
            data = f.read_bytes()
            if not is_binary(data):
                hits += scan_text(data.decode("utf-8", errors="replace"), where, patterns)
    return hits


def _commit_messages(root: Path, rev_range: str) -> list[tuple[str, str]]:
    """The (sha, message) pairs of the commits in *rev_range*."""
    out = subprocess.run(
        ["git", "log", "--format=%H%x00%B%x1e", rev_range],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    pairs: list[tuple[str, str]] = []
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if record:
            sha, _, body = record.partition("\0")
            pairs.append((sha, body))
    return pairs


def scan_commits(root: Path, rev_range: str, patterns) -> list[Hit]:
    """Scan the messages of the commits in *rev_range* (no inline exceptions)."""
    hits: list[Hit] = []
    for sha, body in _commit_messages(root, rev_range):
        hits += scan_text(body, f"commit {sha[:10]}", patterns, honour_inline=False)
    return hits


def scan_trailers(root: Path, rev_range: str) -> list[str]:
    """Report every ``Co-Authored-By`` trailer in *rev_range*: a commit has one author."""
    found: list[str] = []
    for sha, body in _commit_messages(root, rev_range):
        for number, line in enumerate(body.splitlines(), start=1):
            if _TRAILER.match(line.strip()):
                found.append(f"commit {sha[:10]}:{number}: co-author trailer")
    return found


def scan_tags(root: Path, patterns) -> list[Hit]:
    """Scan tag names and annotation messages (no inline exceptions)."""
    out = subprocess.run(
        ["git", "for-each-ref", "refs/tags", "--format=%(refname:short)%00%(contents)%1e"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    hits: list[Hit] = []
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        name, _, body = record.partition("\0")
        hits += scan_text(name + "\n" + body, f"tag {name}", patterns, honour_inline=False)
    return hits


def resolve_list(explicit: str | None) -> Path | None:
    """Find the private list: argument, then environment, then the default path."""
    for candidate in (explicit, os.environ.get("CARTOLEX_DENYLIST"), str(DEFAULT_LIST)):
        if candidate and Path(candidate).expanduser().is_file():
            return Path(candidate).expanduser()
    return None


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--list", help="path to the private list of banned patterns")
    parser.add_argument("--require-list", action="store_true", help="fail if no list is found")
    parser.add_argument("--commits", metavar="RANGE", help="also scan these commit messages")
    parser.add_argument("--tags", action="store_true", help="also scan tag names and messages")
    parser.add_argument(
        "--strict", action="store_true", help="ignore inline exceptions (count what they hide)"
    )
    parser.add_argument("--root", default=".", help="repository root (default: .)")
    parser.add_argument(
        "--paths", nargs="+", metavar="PATH", help="scan these files or folders instead of the tree"
    )
    parser.add_argument(
        "--cache",
        type=Path,
        metavar="FILE",
        help="keep each file's result here: unchanged files are not scanned again",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    trailers = scan_trailers(root, args.commits) if args.commits else []
    for line in trailers:
        print(line)
    list_path = resolve_list(args.list)
    if list_path is None:
        msg = "vocab: no banned-terms list found; scan skipped"
        if args.require_list:
            print(msg, file=sys.stderr)
            return 2
        print(msg)
        return 1 if trailers else 0
    patterns = load_patterns(list_path)
    if args.paths:
        hits = scan_paths([Path(x) for x in args.paths], patterns)
    else:
        hits = scan_tree(root, patterns, honour_inline=not args.strict, cache=args.cache)
    if args.commits:
        hits += scan_commits(root, args.commits, patterns)
    if args.tags:
        hits += scan_tags(root, patterns)
    for hit in hits:
        print(hit.render())
    where = {h.where.split(" (path)")[0] for h in hits}
    print(f"vocab: {len(hits)} match(es) in {len(where)} place(s), {len(patterns)} patterns")
    if trailers:
        print(f"vocab: {len(trailers)} co-author trailer(s); a commit has one author")
    return 1 if hits or trailers else 0


if __name__ == "__main__":
    raise SystemExit(main())
