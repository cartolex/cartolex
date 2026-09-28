# SPDX-License-Identifier: MIT
"""The JSON Schema of the app's manifest, generated from :class:`cartolex.app.manifest.Manifest`.

::

    python -m cartolex.app.schemas --write   # regenerate after a model change
    python -m cartolex.app.schemas --check   # fail when the stored schema is out of date

A test runs the check, so a model change without its regenerated schema fails.
"""

from __future__ import annotations

import argparse
import json
import sys

from ..manifest import SCHEMA_PATH, manifest_schema

__all__ = ["main", "schema_text"]


def schema_text() -> str:
    """The manifest's schema as it is stored."""
    return json.dumps(manifest_schema(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write or check the manifest's JSON Schema.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    text = schema_text()
    if args.write:
        SCHEMA_PATH.write_text(text, encoding="utf-8")
        print(f"wrote {SCHEMA_PATH.name}")
        return 0
    stored = SCHEMA_PATH.read_text(encoding="utf-8") if SCHEMA_PATH.exists() else ""
    if stored != text:
        print(f"{SCHEMA_PATH.name} is out of date: run python -m cartolex.app.schemas --write")
        return 1
    print("the manifest's schema is up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
