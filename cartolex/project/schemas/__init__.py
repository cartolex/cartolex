# SPDX-License-Identifier: MIT
"""The JSON Schemas of a project's JSON files, generated from :mod:`cartolex.project.models`.

The ``*.schema.json`` files beside this module are shipped with the package and
published with the format docs. They are generated, never edited::

    python -m cartolex.project.schemas --write   # regenerate
    python -m cartolex.project.schemas --check   # fail if a stored schema is out of date

A test runs the check, so a model change without its regenerated schema fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..models import MapsFile, ParamsFile, ProjectFile, RunRecord, StopwordsFile, ThemesFile

__all__ = ["SCHEMAS", "schema_bytes", "schema_of"]

#: File name (without ``.schema.json``) → model.
SCHEMAS = {
    "project": ProjectFile,
    "params": ParamsFile,
    "run": RunRecord,
    "themes": ThemesFile,
    "maps": MapsFile,
    "stopwords": StopwordsFile,
}

HERE = Path(__file__).resolve().parent
BASE_URI = "https://cartolex.github.io/schemas/1/"


def schema_of(name: str) -> dict:
    """The JSON Schema of file kind *name*."""
    model = SCHEMAS[name]
    schema = model.model_json_schema(by_alias=True, mode="validation")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{BASE_URI}{name}.schema.json",
        **schema,
    }


def schema_bytes(name: str) -> bytes:
    return (
        json.dumps(schema_of(name), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate or check the format's JSON Schemas.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    stale = []
    for name in SCHEMAS:
        path = HERE / f"{name}.schema.json"
        data = schema_bytes(name)
        if args.write:
            path.write_bytes(data)
        elif not path.exists() or path.read_bytes() != data:
            stale.append(path.name)
    if stale:
        print(
            "out of date: " + ", ".join(stale) + " (run python -m cartolex.project.schemas --write)"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
