# SPDX-License-Identifier: MIT
"""Vendor the web interface's JavaScript libraries: fetch, verify, rewrite, hash.

The interface loads native ES modules with no build step and no import map (an
import map would need an inline script, which the app's Content-Security-Policy
forbids). Each vendored file is therefore the library's published ES-module
build with two mechanical changes, both made here and nowhere else:

1. every *bare* import specifier (``"preact"``, ``"preact/hooks"``,
   ``"@preact/signals-core"``) becomes the relative path of the vendored file;
2. the trailing ``//# sourceMappingURL=`` comment is removed (the maps are not
   vendored, and a browser with its tools open would ask for them).

The script checks each package archive against the npm registry's SHA-512
integrity string before reading it, then writes the files and ``VENDOR.md``
beside them: for every file its package, version, source URL, the SHA-256 of
the file as published and the SHA-256 of the vendored result. The tests check
the vendored files against that table (``tests/test_ui_static.py``).

Usage::

    python tools/vendor_ui.py --download          # fetch from registry.npmjs.org
    python tools/vendor_ui.py --from DIR           # archives already in DIR
    python tools/vendor_ui.py --check              # vendored files match VENDOR.md

Only this script touches ``cartolex/app/static/vendor/`` and
``tests/browser/vendor/``; to move to a new version, change the table below and
run it again.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import re
import sys
import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_VENDOR = ROOT / "cartolex" / "app" / "static" / "vendor"
TEST_VENDOR = ROOT / "tests" / "browser" / "vendor"
REGISTRY = "https://registry.npmjs.org"


@dataclass(frozen=True)
class Package:
    """One npm package: where it comes from and which of its files are vendored where."""

    name: str
    version: str
    integrity: str  # the registry's "sha512-<base64>" of the archive
    licence: str  # the package's SPDX licence id
    dest: Path  # the vendor folder the files go to
    files: tuple[tuple[str, str], ...]  # (path inside the package, path under dest)

    @property
    def archive(self) -> str:
        """The archive's file name, as the registry names it."""
        return f"{self.name.rsplit('/', 1)[-1]}-{self.version}.tgz"

    @property
    def url(self) -> str:
        """The archive's URL on the npm registry."""
        return f"{REGISTRY}/{self.name}/-/{self.archive}"


PACKAGES: tuple[Package, ...] = (
    Package(
        "preact",
        "10.29.8",
        "sha512-ej2aVZ+vZ8WO7tvlQWRM9N63A0KzF9q4mWJfDUHgYaIofWY9hu74QdnQrjoPMmZi2/nZ5gN0bJCQF49xQqx09Q==",
        "MIT",
        APP_VENDOR,
        (
            ("dist/preact.module.js", "preact/preact.module.js"),
            ("hooks/dist/hooks.module.js", "preact/hooks.module.js"),
            ("LICENSE", "preact/LICENSE"),
        ),
    ),
    Package(
        "htm",
        "3.1.1",
        "sha512-983Vyg8NwUE7JkZ6NmOqpCZ+sh1bKv2iYTlUkzlWmA5JD2acKoxd4KVxbMmxX/85mtfdnDmTFoNKcg5DGAvxNQ==",
        "Apache-2.0",
        APP_VENDOR,
        (
            ("dist/htm.module.js", "htm/htm.module.js"),
            ("LICENSE", "htm/LICENSE"),
        ),
    ),
    Package(
        "@preact/signals-core",
        "1.14.4",
        "sha512-HNB6HYeYKhQbJ1aKl+YRjrS4+QWHLKX6qKoUsfS/m0vqzsVaEBiZiaKbG/e+NKk2ch5ALQr/ihWaMHxiCuuWHA==",
        "MIT",
        APP_VENDOR,
        (
            ("dist/signals-core.module.js", "signals-core/signals-core.module.js"),
            ("LICENSE", "signals-core/LICENSE"),
        ),
    ),
    Package(
        "@preact/signals",
        "2.11.2",
        "sha512-rVTRTt/T0HIRgbugwS5FigbfF/kfdEFYFtiqxa+lbpqTajepqnR0firuAS2iNdHyejWB7Yc9p9QePkVNBtTAwg==",
        "MIT",
        APP_VENDOR,
        (
            ("dist/signals.module.js", "signals/signals.module.js"),
            ("LICENSE", "signals/LICENSE"),
        ),
    ),
    Package(
        "axe-core",
        "4.13.0",
        "sha512-UzGt8zg7Ny8djbYMhxl2zuEevVa7r2gJjYY5Lwr1xM7+XU2nd6CkIWFTVcCIbAP63vSz71NaVyyuSk9lHKcy0A==",
        "MPL-2.0",
        TEST_VENDOR,
        (
            ("axe.min.js", "axe-core/axe.min.js"),
            ("LICENSE", "axe-core/LICENSE"),
            ("LICENSE-3RD-PARTY.txt", "axe-core/LICENSE-3RD-PARTY.txt"),
        ),
    ),
)

#: Bare specifiers and the vendored file each one names (relative to APP_VENDOR).
SPECIFIERS: dict[str, str] = {
    "preact": "preact/preact.module.js",
    "preact/hooks": "preact/hooks.module.js",
    "@preact/signals-core": "signals-core/signals-core.module.js",
}

_IMPORT = re.compile(r"""(\bfrom\s*|\bimport\s*\(?\s*)(["'])([^"'./][^"']*)\2""")
_SOURCE_MAP = re.compile(r"\n?//# sourceMappingURL=\S+\s*$")
_ROW = re.compile(
    r"^\| `(?P<file>[^`]+)` \| (?P<package>\S+) \| (?P<version>\S+) \| (?P<licence>\S+) "
    r"\| (?P<source>\S+) \| `(?P<upstream>[0-9a-f]{64})` \| `(?P<vendored>[0-9a-f]{64})` \|$"
)


def sha256(data: bytes) -> str:
    """Hex SHA-256 of *data*."""
    return hashlib.sha256(data).hexdigest()


def check_integrity(data: bytes, integrity: str) -> None:
    """Refuse an archive whose SHA-512 differs from the registry's integrity string."""
    algo, _, expected = integrity.partition("-")
    if algo != "sha512":
        raise ValueError(f"unsupported integrity algorithm {algo!r}")
    actual = base64.b64encode(hashlib.sha512(data).digest()).decode()
    if actual != expected:
        raise ValueError(f"integrity mismatch: expected {expected}, got {actual}")


def rewrite(source: str, dest: str) -> str:
    """The ES module *source* vendored at *dest*: relative specifiers, no source-map comment."""

    def relative(match: re.Match[str]) -> str:
        spec = match.group(3)
        if spec not in SPECIFIERS:
            raise ValueError(f"{dest}: unknown bare specifier {spec!r}; add it to SPECIFIERS")
        depth = dest.count("/")
        target = "../" * depth + SPECIFIERS[spec]
        if depth == 0:
            target = "./" + target
        # Two files in the same folder: "./x.js", not "../folder/x.js".
        folder = dest.rsplit("/", 1)[0] + "/" if "/" in dest else ""
        if folder and SPECIFIERS[spec].startswith(folder):
            target = "./" + SPECIFIERS[spec][len(folder) :]
        return f"{match.group(1)}{match.group(2)}{target}{match.group(2)}"

    out = _IMPORT.sub(relative, source)
    out = _SOURCE_MAP.sub("", out)
    return out if out.endswith("\n") else out + "\n"


def vendor(package: Package, archive: bytes) -> list[dict[str, str]]:
    """Write *package*'s files from its *archive*; return one table row per file."""
    check_integrity(archive, package.integrity)
    rows = []
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for inside, dest in package.files:
            member = tar.extractfile(f"package/{inside}")
            if member is None:
                raise ValueError(f"{package.name}: {inside} not in the archive")
            upstream = member.read()
            data = upstream
            if dest.endswith(".module.js"):
                data = rewrite(upstream.decode("utf-8"), dest).encode("utf-8")
            target = package.dest / dest
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            rows.append(
                {
                    "file": dest,
                    "package": package.name,
                    "version": package.version,
                    "licence": package.licence,
                    "source": f"{package.url}#{inside}",
                    "upstream": sha256(upstream),
                    "vendored": sha256(data),
                }
            )
    return rows


HEADER = """# Vendored {what}

Written by `tools/vendor_ui.py`; do not edit by hand. Each file is the
package's published file, taken from the npm registry archive after checking
the archive's SHA-512 integrity. ES-module files have their bare import
specifiers rewritten to relative paths and their source-map comment removed;
nothing else changes. `upstream` is the SHA-256 of the published file,
`vendored` the SHA-256 of the file here.

| file | package | version | licence | source | upstream SHA-256 | vendored SHA-256 |
| --- | --- | --- | --- | --- | --- | --- |
"""


def write_table(folder: Path, what: str, rows: list[dict[str, str]]) -> None:
    """Write ``VENDOR.md`` in *folder* with one line per vendored file."""
    lines = [
        f"| `{r['file']}` | {r['package']} | {r['version']} | {r['licence']} | {r['source']} "
        f"| `{r['upstream']}` | `{r['vendored']}` |"
        for r in rows
    ]
    (folder / "VENDOR.md").write_text(HEADER.format(what=what) + "\n".join(lines) + "\n")


def read_table(folder: Path) -> list[dict[str, str]]:
    """The rows of ``VENDOR.md`` in *folder*."""
    text = (folder / "VENDOR.md").read_text(encoding="utf-8")
    return [m.groupdict() for line in text.splitlines() if (m := _ROW.match(line))]


def check(folder: Path) -> list[str]:
    """Problems with the vendored files of *folder*: missing, changed or unlisted files."""
    rows = read_table(folder)
    problems = []
    listed = set()
    for row in rows:
        path = folder / row["file"]
        listed.add(path)
        if not path.is_file():
            problems.append(f"{row['file']}: listed in VENDOR.md but missing")
        elif sha256(path.read_bytes()) != row["vendored"]:
            problems.append(f"{row['file']}: SHA-256 differs from VENDOR.md")
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.name != "VENDOR.md" and path not in listed:
            problems.append(f"{path.relative_to(folder)}: not listed in VENDOR.md")
    if not rows:
        problems.append(f"{folder}: VENDOR.md lists no file")
    return problems


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--download", action="store_true", help="fetch from registry.npmjs.org")
    group.add_argument("--from", dest="source", type=Path, help="folder holding the archives")
    group.add_argument("--check", action="store_true", help="check the vendored files")
    args = parser.parse_args(argv)
    folders = {APP_VENDOR: "front-end libraries", TEST_VENDOR: "browser-test libraries"}
    if args.check:
        problems = [p for folder in folders for p in check(folder)]
        for problem in problems:
            print(f"vendor: {problem}")
        print(f"vendor: {'FAIL' if problems else 'ok'}")
        return 1 if problems else 0
    rows: dict[Path, list[dict[str, str]]] = {folder: [] for folder in folders}
    for package in PACKAGES:
        if args.download:
            with urllib.request.urlopen(package.url, timeout=60) as response:  # noqa: S310
                archive = response.read()
        else:
            archive = (args.source / package.archive).read_bytes()
        rows[package.dest] += vendor(package, archive)
        print(f"vendor: {package.name} {package.version}")
    for folder, what in folders.items():
        write_table(folder, what, rows[folder])
    return 0


if __name__ == "__main__":
    sys.exit(main())
