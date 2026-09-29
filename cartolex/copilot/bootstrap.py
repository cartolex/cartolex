# SPDX-License-Identifier: MIT
"""Unpack the cartolex kit of this bundle and say how to use it: no network, no install.

Run it from the bundle's folder::

    python setup/bootstrap.py

It checks the wheel against ``bundle.json``, unpacks it with the standard
library into ``setup/site`` (nothing is installed on the system, nothing is
downloaded), checks the scientific libraries the kit needs, and prints the
line that makes the kit importable. It uses the standard library only.
"""

import hashlib
import importlib
import json
import sys
import zipfile
from pathlib import Path

NEEDED = ("numpy", "pandas", "scipy", "sklearn", "matplotlib")


def main() -> int:
    here = Path(__file__).resolve().parent
    root = here.parent
    wheels = sorted(here.glob("cartolex-*.whl"))
    if len(wheels) != 1:
        print(f"expected one cartolex wheel in {here}, found {len(wheels)}")
        return 1
    wheel = wheels[0]
    manifest = json.loads((root / "bundle.json").read_text(encoding="utf-8"))
    expected = manifest.get("files", {}).get(f"setup/{wheel.name}")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if expected and expected != digest:
        print(f"{wheel.name} does not match bundle.json: the bundle is damaged")
        return 1
    site = here / "site"
    stamp = site / ".unpacked"
    if not stamp.is_file() or stamp.read_text(encoding="utf-8") != digest:
        with zipfile.ZipFile(wheel) as zf:
            for member in zf.namelist():
                target = (site / member).resolve()
                if site.resolve() not in target.parents:
                    print(f"refused a member outside the kit's folder: {member}")
                    return 1
            zf.extractall(site)
        stamp.write_text(digest, encoding="utf-8")
    missing = []
    for name in NEEDED:
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)
    if missing:
        print("missing libraries (the kit needs them): " + ", ".join(missing))
        return 1
    try:
        importlib.import_module("umap")
        umap = "UMAP available"
    except ImportError:
        umap = "UMAP not available: use the 'tree' or 'tsne' layout"
    print(f"cartolex kit unpacked in {site} ({umap}).")
    print("Use it with:")
    print(f"import sys; sys.path.insert(0, {str(site)!r})")
    print("from cartolex.copilot import open_bundle")
    print(f"session = open_bundle({str(root)!r}); print(session.summary())")
    return 0


if __name__ == "__main__":
    sys.exit(main())
