# SPDX-License-Identifier: MIT
"""Figures, tables and files to take away: the map as PNG or SVG, the theme table as CSV,
the map bundle and the project as one zip.

- :func:`map_figure` draws the map (people and keywords, coloured by their
  top-level theme, the top-level themes named at the centre of their
  keywords, a legend) at a chosen size in pixels, light or dark, with the
  app's colour tokens. People are never named on a figure.
- :func:`theme_table` is the tree as CSV: each node's level, parent, names in
  the display languages, keywords, weight, share and top keywords.
- :func:`write_map_bundle` writes the portable map bundle
  (:mod:`cartolex.atlas.map_bundle`; people named by their project ids only).
- :func:`write_project_zip` writes the project folder as one zip without its
  caches (``cache/``, the staging area, the lock) and without earlier exports.

Files that take time are written into ``outputs/exports/`` under dated names,
never over an earlier one.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .data import project_context

if TYPE_CHECKING:
    from cartolex.project import Project

__all__ = [
    "EXPORT_KINDS",
    "exports_folder",
    "list_exports",
    "map_figure",
    "theme_table",
    "write_map_bundle",
    "write_project_zip",
]

#: The exports written as files (by a job): their file name's stem.
EXPORT_KINDS = {"map_bundle": "map-bundle", "project": "project"}
#: What the project's zip leaves out (folder names at any depth, then paths from the root).
_SKIP_NAMES = {".lock", ".lock.takeover", ".staging", ".journal.json", "__pycache__"}
_SKIP_PATHS = ("cache", "outputs/exports")


def exports_folder(project: Project) -> Path:
    return project.layout.outputs / "exports"


def list_exports(project: Project) -> list[dict[str, Any]]:
    """The files in ``outputs/exports/``, the newest first."""
    folder = exports_folder(project)
    if not folder.is_dir():
        return []
    items = []
    for path in folder.iterdir():
        if path.is_file() and not path.name.startswith("."):
            kind = next((k for k, stem in EXPORT_KINDS.items() if path.name.startswith(stem)), "")
            st = path.stat()
            items.append({"name": path.name, "kind": kind, "size": st.st_size,
                          "made_at": datetime.fromtimestamp(st.st_mtime, timezone.utc)
                          .isoformat(timespec="seconds")})  # fmt: skip
    items.sort(key=lambda e: e["made_at"], reverse=True)
    return items


def _dated(project: Project, stem: str, suffix: str) -> Path:
    folder = exports_folder(project)
    folder.mkdir(parents=True, exist_ok=True)
    base = f"{stem}-{datetime.now().strftime('%Y-%m-%d_%H%M%S')}"
    path, n = folder / f"{base}{suffix}", 1
    while path.exists():
        n += 1
        path = folder / f"{base}-{n}{suffix}"
    return path


# ── the map as a figure ─────────────────────────────────────────────────────


def _tokens(theme: str) -> dict[str, str]:
    """The colour tokens of a theme (``light`` or ``dark``) from the app's tokens.css."""
    from .builder import APP_TOKENS

    css = APP_TOKENS.read_text(encoding="utf-8")
    light = css.split("/* Light */", 1)[1].split("}", 1)[0]
    dark = css.split(":root[data-theme='dark'] {", 1)[1].split("}", 1)[0]
    block = light if theme == "light" else dark
    out = dict(re.findall(r"--(cx-[\w-]+):\s*(#[0-9a-fA-F]{6})", light))
    out.update(re.findall(r"--(cx-[\w-]+):\s*(#[0-9a-fA-F]{6})", block))
    for key, value in re.findall(r"--(cx-[\w-]+):\s*var\([^,]+,\s*(#[0-9a-fA-F]{6})\)", block):
        out[key] = value
    return out


def map_figure(
    project: Project,
    *,
    fmt: str = "png",
    width: int = 1600,
    height: int = 1200,
    theme: str = "light",
    language: str = "",
) -> bytes:
    """The map as a PNG or SVG image of *width* × *height* pixels."""
    import matplotlib

    matplotlib.use("Agg", force=False)
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D

    from cartolex.app.routes.atlas import build_bundle, lineage

    ctx = project_context(project)
    runs = lineage(ctx)
    if runs["map.layout"] is None:
        raise LookupError("no_map")
    bundle = build_bundle(ctx, runs)
    colours = _tokens(theme)
    nodes = {n["id"]: n for n in bundle["nodes"]}
    tops = sorted((n for n in bundle["nodes"] if not n["parent"] or n["parent"] not in nodes),
                  key=lambda n: (n["order"], n["id"]))  # fmt: skip
    top_of: dict[str, str] = {}
    for n in bundle["nodes"]:
        at = n["id"]
        while nodes.get(at, {}).get("parent") in nodes:
            at = nodes[at]["parent"]
        top_of[n["id"]] = at
    hue = {t["id"]: colours.get(f"cx-hue-{k % 12 + 1}", "#808080") for k, t in enumerate(tops)}
    muted = colours.get("cx-text-muted", "#808080")
    lang = language[:2] or (list(project.config.languages.display) or ["en"])[0]

    def name(node: dict[str, Any]) -> str:
        names = node.get("names") or {}
        return names.get(lang) or names.get("en") or next(iter(names.values()), "") or node["id"]

    def colour_of(node: str | None) -> str:
        return hue.get(top_of.get(node or "", ""), muted)

    dpi = 100
    fig = Figure(figsize=(width / dpi, height / dpi), dpi=dpi, facecolor=colours.get("cx-bg"))
    ax = fig.add_axes((0.01, 0.01, 0.74, 0.98))
    ax.set_facecolor(colours.get("cx-bg"))
    ax.set_axis_off()
    kw = [k for k in bundle["keywords"] if k["x"] is not None]
    ax.scatter([k["x"] for k in kw], [k["y"] for k in kw], s=10, marker="D", linewidths=0,
               c=[colour_of(k["node"]) for k in kw], alpha=0.6)  # fmt: skip
    people = [p for p in bundle["people"] if p["x"] is not None]

    def top_share(p: dict[str, Any]) -> str | None:
        shares = p["shares"][0] if p["shares"] else {}
        return max(shares, key=lambda n: shares[n]) if shares else None

    ax.scatter([p["x"] for p in people], [p["y"] for p in people], s=26, linewidths=0,
               c=[colour_of(top_share(p)) for p in people], alpha=0.95)  # fmt: skip
    for t in tops:
        pts = [(k["x"], k["y"]) for k in kw if top_of.get(k["node"] or "") == t["id"]]
        if pts:
            ax.text(sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts), name(t),
                    ha="center", va="center", fontsize=9, color=colours.get("cx-text"),
                    bbox={"boxstyle": "round,pad=0.2", "fc": colours.get("cx-bg"), "ec": "none",
                          "alpha": 0.75})  # fmt: skip
    handles = [
        Line2D([], [], marker="o", ls="", color=hue[t["id"]], label=name(t)) for t in tops[:12]
    ]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.76, 0.98), frameon=False,
               fontsize=9, labelcolor=colours.get("cx-text"))  # fmt: skip
    buffer = io.BytesIO()
    fig.savefig(buffer, format=fmt, dpi=dpi, facecolor=fig.get_facecolor(),
                metadata={"Software": None} if fmt == "png" else {"Date": None})  # fmt: skip
    return buffer.getvalue()


# ── the theme table ─────────────────────────────────────────────────────────


def theme_table(project: Project) -> str:
    """The theme tree as CSV (UTF-8), one row per node, parents before their children."""
    from cartolex.app.routes.atlas import build_bundle, lineage

    ctx = project_context(project)
    runs = lineage(ctx)
    if runs["map.layout"] is None:
        raise LookupError("no_map")
    bundle = build_bundle(ctx, runs)
    languages = list(project.config.languages.display) or ["en"]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["id", "level", "parent", *(f"name_{lang}" for lang in languages),
                     "keywords", "weight", "share", "top_keywords"])  # fmt: skip
    for n in sorted(bundle["nodes"], key=lambda n: (n["level"], n["order"], n["id"])):
        writer.writerow([n["id"], n["level"], n["parent"] or "",
                         *((n["names"] or {}).get(lang, "") for lang in languages),
                         n["keywords"], n["weight"] if n["weight"] is not None else "",
                         n["share"] if n["share"] is not None else "",
                         "; ".join(n["top_keywords"])])  # fmt: skip
    return out.getvalue()


# ── files: the map bundle, the project ──────────────────────────────────────


def write_map_bundle(project: Project) -> Path:
    """Write the project's map bundle as a zip in ``outputs/exports/``; answers its path."""
    from cartolex.atlas.map_bundle import write_bundle
    from cartolex.build.bundle import project_bundle

    bundle = project_bundle(project, build_date=datetime.now(timezone.utc).date().isoformat())
    return write_bundle(bundle, _dated(project, EXPORT_KINDS["map_bundle"], ".zip"))


def _skipped(rel: Path) -> bool:
    posix = rel.as_posix()
    if any(part in _SKIP_NAMES for part in rel.parts):
        return True
    return any(posix == p or posix.startswith(p + "/") for p in _SKIP_PATHS)


def write_project_zip(project: Project, *, cancelled: Any = None) -> Path | None:
    """Write the project folder as one zip (no caches, no earlier exports) in
    ``outputs/exports/``; answers its path, or ``None`` when *cancelled()* stopped it."""
    root = project.layout.root
    target = _dated(project, EXPORT_KINDS["project"], ".zip")
    partial = target.with_name(f".{target.name}.part")
    top = root.name or "project"
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(root.rglob("*")):
                rel = path.relative_to(root)
                if not path.is_file() or _skipped(rel):
                    continue
                if cancelled and cancelled():
                    raise InterruptedError
                zf.write(path, f"{top}/{rel.as_posix()}")
        partial.replace(target)
    except InterruptedError:
        partial.unlink(missing_ok=True)
        return None
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return target
