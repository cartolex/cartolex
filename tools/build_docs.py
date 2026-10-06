# SPDX-License-Identifier: MIT
"""Build the documentation into the package, for the app to serve at ``/static/docs/``.

Usage::

    python tools/build_docs.py            # docs/ → cartolex/app/static/docs/

A strict Sphinx build (warnings are errors) of ``docs/`` as HTML, without the pages'
sources, replaces ``cartolex/app/static/docs/`` (not tracked by git; a wheel built
afterwards carries it, and ``tools/package_check.py`` accepts it). The release build
runs it before building the wheel; a checkout without it shows, at the same address,
how to build it. Needs the development extras (Sphinx, MyST, Furo).
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: Where the app serves the documentation from (``/static/docs/``).
TARGET = ROOT / "cartolex" / "app" / "static" / "docs"


def build(target: Path = TARGET) -> Path:
    """Build the HTML documentation into *target* (replaced whole); answers *target*."""
    with tempfile.TemporaryDirectory(prefix="cartolex-docs-") as work:
        out = Path(work) / "html"
        subprocess.run(
            [
                sys.executable, "-m", "sphinx", "-b", "html", "-W", "--keep-going", "-q",
                "-D", "html_copy_source=0", "-D", "html_show_sourcelink=0",
                str(ROOT / "docs"), str(out),
            ],
            check=True,
        )  # fmt: skip
        for stray in (".buildinfo", "objects.inv", ".doctrees", "_sources"):
            path = out / stray
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        for page in out.rglob("*.html"):
            up = "../" * (len(page.relative_to(out).parts) - 1)
            text = page.read_text(encoding="utf-8")
            page.write_text(strict(_styles_to_files(text, out, up)), encoding="utf-8")
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(out, target)
    return target


#: What the app's Content-Security-Policy refuses in Furo's pages (no inline script or
#: style), and what does the same within it: the saved light or dark choice is then
#: read by the theme's own script, the hidden icons and the half-tones are SVG
#: attributes.
_STRICT = (
    (re.compile(r"\s*<script>\s*document\.body\.dataset\.theme = [^<]*</script>"), ""),
    (re.compile(r' style="display: none;"'), ' display="none"'),
    (re.compile(r' style="opacity: 50%"'), ' opacity="0.5"'),
)


def _styles_to_files(page: str, out: Path, up: str) -> str:
    """*page* with each ``<style>`` block in a file of ``_static/`` (the same block, the
    same file) and linked instead."""

    def to_file(match: re.Match[str]) -> str:
        css = match.group(1)
        name = f"_static/inline-{hashlib.sha256(css.encode()).hexdigest()[:12]}.css"
        (out / name).write_text(css, encoding="utf-8")
        return f'<link rel="stylesheet" type="text/css" href="{up}{name}" />'

    return re.sub(r"<style>(.*?)</style>", to_file, page, flags=re.S)


def strict(page: str) -> str:
    """*page* without what a strict Content-Security-Policy refuses; raises
    ``ValueError`` when an inline script or style is left (a new theme's)."""
    for pattern, replacement in _STRICT:
        page = pattern.sub(replacement, page)
    left = re.search(r"<script(?![^>]*\bsrc=)[^>]*>|<style|\sstyle=\"|\son[a-z]+=\"", page)
    if left:
        raise ValueError(f"inline script or style left in the documentation: {left.group(0)!r}")
    return page


def main() -> int:
    target = build()
    files = [p for p in target.rglob("*") if p.is_file()]
    size = sum(p.stat().st_size for p in files)
    print(f"docs: {len(files)} files, {size / 1e6:.1f} MB in {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
