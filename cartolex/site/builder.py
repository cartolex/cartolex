# SPDX-License-Identifier: MIT
"""Writing an offline site: versioned builds, the ``latest`` marker, the stale flag.

A build is a folder ``outputs/sites/<date>_<time>/`` written under a hidden
name and renamed when complete, so a build is never half-written and never
replaces an earlier one (a second build in the same second gets ``-2``). The
file ``outputs/sites/latest`` names the newest build. Each build keeps its
record in ``site.json``: the options it was built with, what it holds, and a
fingerprint of what it was built from (the runs of the stages it reads, the
tables and the decision files); a build whose fingerprint differs from the
project's now is **stale**.

The folder::

    index.html          the one page (no inline script or style)
    README.txt          « unzip the whole folder first », then what the site holds
    site.json           the build's record (not read by the page)
    assets/             tokens.css, atlas.css, site.css, atlas.js (the app's atlas as one
                        classic script), site.js, i18n.js, world.js, cloud-light.svg and
                        cloud-dark.svg (the lexicon's word cloud, on the home page)
    data/               core.js (every page: the atlas bundle as columns), orgs.js,
                        people/<n>.js, keywords/<n>.js, links.js (the co-authors) and
                        texts/<n>.js (on request), loaded on demand

Data files are classic scripts (``window.CX_SITE[<part>] = …``): a page opened from
``file://`` can load a script but cannot read a JSON file. A person's details and texts
are in the part ``(number − 1) mod n`` of their site id (``s<number>``), a keyword's users
in the part ``index mod n`` of its place in the core's keywords, *n* in ``core.shards``
chosen so that a part holds about :data:`SHARD_BYTES`: a national site's texts are
gigabytes, a page loads what it shows.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import tempfile
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from .data import gather

if TYPE_CHECKING:
    from cartolex.project import Project

__all__ = [
    "FORMAT",
    "LANGUAGES",
    "TEXT_MODES",
    "OfflineSiteBuilder",
    "SiteOptions",
    "atlas_sources",
    "build_site",
    "inputs_fingerprint",
    "list_builds",
    "site_zip",
]

FORMAT = "cartolex-site/3"
#: The site's languages (its catalogues); the build chooses the one it opens in.
LANGUAGES = ("en", "fr", "pt-BR")
#: What a site may carry of the texts: nothing (the default), titles, titles and abstracts.
TEXT_MODES = ("none", "titles", "abstracts")
#: The stages whose results a site reads: their runs are part of its fingerprint.
READS = (
    "corpus.assemble",
    "keywords.build",
    "themes.space",
    "themes.apply",
    "map.layout",
    "map.trajectories",
    "overlays.position",
)

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
CATALOGUES = HERE / "i18n"
#: The app's style tokens, which the site's copy (``assets/tokens.css``) must equal.
APP_TOKENS = HERE.parent / "app" / "static" / "css" / "tokens.css"
#: The outline of the land masses (Natural Earth, public domain), shared with the app.
WORLD = HERE.parent / "app" / "static" / "data" / "world-land-110m.json"
#: The atlas's style sheet, shared with the app.
ATLAS_CSS = HERE.parent / "app" / "static" / "css" / "atlas.css"
#: The app's catalogues: the site carries their ``atlas.*`` messages.
APP_CATALOGUES = HERE.parent / "app" / "static" / "i18n"
#: The prefixes of the app's messages the atlas speaks with.
ATLAS_MESSAGES = ("atlas.",)
#: The site's own classic scripts, joined into ``assets/site.js`` in this order.
SITE_SCRIPTS = (
    "core.js",
    "source.js",
    "atlas.js",
    "pages.js",
    "person.js",
    "main.js",
)


@dataclass(frozen=True)
class SiteOptions:
    """What the person building chose: names or pseudonyms (asked at each build of a people
    atlas), names of the projected people (pseudonyms unless chosen explicitly: they may be a
    sensitive set), the texts carried, the title and the language the site opens in."""

    names: bool | None = None
    names_projected: bool = False
    texts: str = "none"
    title: str = ""
    language: str = "en"

    @classmethod
    def of(cls, raw: Mapping[str, Any]) -> SiteOptions:
        """Options from a request's values; unknown values fall back to the defaults."""
        names = raw.get("names")
        if isinstance(names, str):
            names = {"names": True, "pseudonyms": False}.get(names)
        names_projected = raw.get("names_projected") in (True, "names")
        texts = raw.get("texts") if raw.get("texts") in TEXT_MODES else "none"
        language = raw.get("language") if raw.get("language") in LANGUAGES else "en"
        title = str(raw.get("title") or "").strip()[:120]
        return cls(
            names=names if isinstance(names, bool) else None,
            names_projected=names_projected,
            texts=str(texts),
            title=title,
            language=str(language),
        )


def _catalogue(language: str) -> dict[str, str]:
    doc = json.loads((CATALOGUES / f"{language}.json").read_text(encoding="utf-8"))
    return {k: v for k, v in doc.items() if not k.startswith("$")}


def _messages(language: str) -> dict[str, str]:
    """The site's catalogue of *language* and the atlas's messages of the app's
    (:data:`ATLAS_MESSAGES`)."""
    app = json.loads((APP_CATALOGUES / f"{language}.json").read_text(encoding="utf-8"))
    atlas = {k: v for k, v in app.items() if k.startswith(ATLAS_MESSAGES)}
    return {**atlas, **_catalogue(language)}


_FROM = re.compile(
    r"""^(?:import|export)\s[^;]*?\sfrom\s+'((?:\./|(?:\.\./)+)[\w/-]+\.js)';""", re.M
)


def atlas_sources(listed: Sequence[str], root: Path) -> list[str]:
    """The modules of the atlas in the order a classic script needs them: those *listed*
    (``ATLAS_MODULES``, paths under *root*) and every module they import, each after the
    modules it imports (the listed order otherwise). A cycle is refused (``ValueError``)."""
    order: list[str] = []
    state: dict[str, str] = {}

    def visit(name: str) -> None:
        if state.get(name) == "done":
            return
        if state.get(name) == "open":
            raise ValueError(f"{name}: its imports come back to it")
        state[name] = "open"
        text = (root / name).read_text(encoding="utf-8")
        for spec in _FROM.findall(text):
            visit(_normal((PurePosixPath(name).parent / spec).as_posix()))
        state[name] = "done"
        order.append(name)

    for name in listed:
        visit(_normal(name))
    return order


def _normal(name: str) -> str:
    """``atlas/../components/map/core.js`` → ``components/map/core.js``."""
    parts: list[str] = []
    for part in PurePosixPath(name).parts:
        if part == "..":
            parts.pop()
        elif part != ".":
            parts.append(part)
    return "/".join(parts)


def _atlas_assets() -> dict[str, bytes]:
    """The app's atlas as the site loads it: its modules as one classic script
    (``window.CartolexAtlas``) and its style sheet."""
    from cartolex.app.static_files import ATLAS_MODULES, PACKAGE_STATIC, classic_script

    modules = atlas_sources(ATLAS_MODULES, PACKAGE_STATIC)
    return {
        "assets/atlas.js": classic_script(
            [PACKAGE_STATIC / m for m in modules], "CartolexAtlas"
        ).encode(),
        "assets/atlas.css": ATLAS_CSS.read_bytes(),
    }


#: The looks the home page's word cloud is drawn for (``assets/cloud-<look>.svg``).
CLOUD_LOOKS = ("light", "dark")


def _clouds(project: Project, language: str) -> dict[str, bytes]:
    """The home page's word cloud: the lexicon's, drawn by the app
    (:func:`cartolex.app.lexicon_view.word_cloud`: its most important keywords, coloured by
    their top-level theme in the interface's hues, the atlas's default scheme) for the light
    and the dark look, in *language* when the project displays it, else in its first display
    language. Nothing when the project has no lexicon or the lexicon no keyword."""
    from types import SimpleNamespace

    from cartolex.app.lexicon_view import word_cloud
    from cartolex.app.runtime import Cache

    from .data import project_context

    runtime = SimpleNamespace(table_cache=Cache(4))  # the lexicon is read once for both looks
    ctx = project_context(project)
    out = {}
    for look in CLOUD_LOOKS:
        svg = word_cloud(runtime, ctx, theme=look, language=language.split("-")[0])
        if svg is None or "<text" not in svg:
            return {}
        out[f"assets/cloud-{look}.svg"] = svg.encode()
    return out


def _script(name: str, value: Any) -> bytes:
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return (
        f"window.CX_SITE = window.CX_SITE || {{}};\nwindow.CX_SITE[{json.dumps(name)}] = {body};\n"
    ).encode()


#: The bytes a part of the people's details or texts holds, about.
SHARD_BYTES = 2 << 20


def _shard_count(by_key: Mapping[str, Any]) -> int:
    total = sum(
        len(json.dumps(v, ensure_ascii=False, separators=(",", ":"))) for v in by_key.values()
    )
    return max(1, -(-total // SHARD_BYTES))


def _part_of(key: str) -> int:
    """The number a part is chosen by: a site id's (``s12`` → 11), a keyword's index."""
    return int(key[1:]) - 1 if key[:1].isalpha() else int(key)


def _write_shards(folder: Path, name: str, by_key: Mapping[str, Any], n: int) -> dict[str, int]:
    """*by_key* (keyed by site ids ``s1``, ``s2``…, or keyword indexes) written as *n* parts
    ``data/<name>/<i>.js`` (see :func:`_part_of`), one at a time; their sizes by file name."""
    buckets: list[list[str]] = [[] for _ in range(n)]
    for key in by_key:
        buckets[_part_of(key) % n].append(key)
    sizes = {}
    for i, keys in enumerate(buckets):
        body = _script(f"{name}/{i}", {key: by_key[key] for key in keys})
        path = folder / "data" / name / f"{i}.js"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        sizes[f"data/{name}/{i}.js"] = len(body)
    return sizes


def _write_texts(project: Project, folder: Path, texts: Any) -> tuple[int, dict[str, int], int]:
    """The texts (:class:`~cartolex.site.data.SiteTexts`) written as parts
    ``data/texts/<i>.js`` of about :data:`SHARD_BYTES`, each made from its people's
    entries alone, the abstracts read once into a scratch file beside *folder*: the
    number of parts, their sizes by file name, and the entries with an abstract."""
    import numpy as np
    import pyarrow.compute as pc

    scratch = folder.with_name(folder.name + ".scratch")
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        abstracts = texts.abstracts_of(project, scratch) if texts.abstracts else None
        total = texts.estimate()["titles"]
        with_abstract = 0
        if abstracts is not None and abstracts.num_rows and texts.count:
            have = abstracts["row"].to_numpy()
            at = np.minimum(np.searchsorted(have, texts.row), len(have) - 1)
            hit = have[at] == texts.row
            lengths = pc.binary_length(abstracts["abstract"]).to_numpy()
            total += int(lengths[at[hit]].sum()) + 14 * int(hit.sum())  # ,"abstract":""
            with_abstract = int(hit.sum())
        n = max(1, -(-total // SHARD_BYTES))
        part = (texts.number - 1) % n
        order = np.argsort(part, kind="stable")
        bounds = np.searchsorted(part[order], np.arange(n + 1))
        sizes = {}
        for i in range(n):
            body = _script(f"texts/{i}", texts.entries(order[bounds[i] : bounds[i + 1]], abstracts))
            path = folder / "data" / "texts" / f"{i}.js"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            sizes[f"data/texts/{i}.js"] = len(body)
        del abstracts
        return n, sizes, with_abstract
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inputs_fingerprint(project: Project) -> str:
    """What a site is built from, as one digest: the runs of the stages it reads, the tables
    and every decision file (their content). A site is stale when it changes."""
    from cartolex.build.records import read_record

    layout = project.layout
    parts: list[Any] = []
    for stage in READS:
        record = read_record(layout, stage)
        parts.append([stage, record.run_id if record else None])
    if layout.tables.is_dir():
        for path in sorted(layout.tables.glob("*.parquet")):
            st = path.stat()
            # Whole seconds: a copy of the folder may keep no finer time.
            parts.append([path.name, st.st_size, int(st.st_mtime)])
    decisions = [layout.project_json]
    if layout.decisions.is_dir():
        decisions += sorted(
            p
            for p in layout.decisions.rglob("*")
            if p.is_file() and not p.name.startswith(".") and layout.history not in p.parents
        )
    for path in decisions:
        parts.append([path.relative_to(layout.root).as_posix(), _sha(path.read_bytes())])
    return _sha(json.dumps(parts).encode())[:32]


def _sites(project: Project) -> Path:
    return project.layout.outputs / "sites"


def _latest(folder: Path) -> str:
    marker = folder / "latest"
    return marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""


def list_builds(project: Project) -> list[dict[str, Any]]:
    """The builds in ``outputs/sites/``, the newest first, each with its record and whether it
    is the ``latest`` and ``stale``."""
    folder = _sites(project)
    if not folder.is_dir():
        return []
    latest = _latest(folder)
    now = None
    out = []
    for path in sorted((p for p in folder.iterdir() if p.is_dir()), reverse=True):
        if path.name.startswith("."):
            continue
        entry: dict[str, Any] = {"id": path.name, "latest": path.name == latest}
        try:
            record = json.loads((path / "site.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            record = None
        if isinstance(record, dict):
            if now is None:
                now = inputs_fingerprint(project)
            entry.update(
                {
                    "built_at": record.get("built_at"),
                    "title": record.get("options", {}).get("title"),
                    "names": record.get("options", {}).get("names"),
                    "names_projected": record.get("options", {}).get("names_projected", False),
                    "texts": record.get("options", {}).get("texts"),
                    "language": record.get("options", {}).get("language"),
                    "counts": record.get("counts", {}),
                    "size": record.get("size", 0),
                    "stale": record.get("inputs") != now,
                }
            )
        out.append(entry)
    return out


def _new_id(folder: Path, now: datetime) -> str:
    base = now.strftime("%Y-%m-%d_%H%M%S")
    name, n = base, 1
    while (folder / name).exists():
        n += 1
        name = f"{base}-{n}"
    return name


def _index_html(title: str, language: str, words: Mapping[str, str], world: bool) -> str:
    scripts = ["assets/i18n.js", "data/core.js", "assets/atlas.js"]
    if world:
        scripts.append("assets/world.js")
    scripts.append("assets/site.js")
    tags = "\n".join(f'<script src="{s}"></script>' for s in scripts)
    return f"""<!doctype html>
<html lang="{html.escape(language)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="assets/tokens.css">
<link rel="stylesheet" href="assets/atlas.css">
<link rel="stylesheet" href="assets/site.css">
</head>
<body>
<div id="cx-missing" class="cx-missing" role="alert">
<h1>{html.escape(words["missing.title"])}</h1>
<p>{html.escape(words["missing.text"])}</p>
</div>
<div id="cx-site" class="cx-site" hidden></div>
{tags}
</body>
</html>
"""


def _readme(title: str, words: Mapping[str, str], options: SiteOptions, counts: Mapping[str, int]):
    lines = [
        words["readme.first"],
        "",
        title,
        "=" * max(4, len(title)),
        "",
        words["readme.open"],
        "",
        words["readme.names" if options.names else "readme.pseudonyms"],
        words[f"readme.texts.{options.texts}"],
        words["readme.never"],
        "",
    ]
    return "\n".join(lines).replace("{people}", str(counts.get("people", 0)))


def build_site(
    project: Project,
    options: SiteOptions,
    *,
    progress: Callable[[float, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Write a new build of *project*'s site; answers its record (``id``, ``counts``, ``size``…).

    Returns ``{"cancelled": True}`` (and writes nothing) when *cancelled* says so between
    phases."""
    from cartolex.project.files import atomic_write_bytes
    from cartolex.project.project import cartolex_version

    say = progress or (lambda fraction, message: None)
    stop = cancelled or (lambda: False)
    fingerprint = inputs_fingerprint(project)
    data = gather(
        project,
        names=bool(options.names),
        names_projected=options.names_projected,
        texts=options.texts,
        progress=lambda f, m: say(0.8 * f, m),
    )
    if stop():
        return {"cancelled": True}
    say(0.85, "writing the site")
    config = project.config
    title = options.title or config.identity.domain_title or config.name
    words = _catalogue(options.language)
    at = now or datetime.now(timezone.utc)
    world = any(loc for loc in data.core["orgs"]["location"])

    folder = _sites(project)
    folder.mkdir(parents=True, exist_ok=True)
    build_id = _new_id(folder, at.astimezone())
    staging = folder / f".building-{build_id}"
    if staging.exists():
        shutil.rmtree(staging)
    shards = {"people": _shard_count(data.people), "keywords": _shard_count(data.keywords)}
    sizes: dict[str, int] = {}
    try:
        if data.texts is not None:
            say(0.87, "writing the texts")
            n, written, data.counts["abstracts"] = _write_texts(project, staging, data.texts)
            shards["texts"] = n
            sizes.update(written)
        say(0.88, "drawing the word cloud")
        clouds = _clouds(project, options.language)
        core = {
            **data.core,
            "has": {**data.core["has"], "cloud": bool(clouds)},
            "title": title,
            "built_at": at.isoformat(timespec="seconds"),
            "language": options.language,
            "shards": shards,
        }
        files: dict[str, bytes] = {
            "index.html": _index_html(title, options.language, words, world).encode(),
            "README.txt": _readme(title, words, options, data.counts).encode(),
            "assets/tokens.css": (ASSETS / "tokens.css").read_bytes(),
            "assets/site.css": (ASSETS / "site.css").read_bytes(),
            "assets/site.js": b"\n".join((ASSETS / name).read_bytes() for name in SITE_SCRIPTS),
            "assets/i18n.js": _script("i18n", {code: _messages(code) for code in LANGUAGES}),
            "data/core.js": _script("core", core),
            "data/orgs.js": _script("orgs", data.orgs),
            **_atlas_assets(),
            **clouds,
        }
        files["data/links.js"] = _script("links", data.links)
        if world:
            files["assets/world.js"] = _script(
                "world", json.loads(WORLD.read_text(encoding="utf-8"))["rings"]
            )
        record = {
            "format": FORMAT,
            "id": build_id,
            "built_at": at.isoformat(timespec="seconds"),
            "cartolex": cartolex_version(),
            "map_version": data.core["map_version"],
            "options": {**asdict(options), "title": title},
            "inputs": fingerprint,
            "counts": data.counts,
        }
        sizes.update({name: len(body) for name, body in files.items()})
        sizes.update(_write_shards(staging, "people", data.people, shards["people"]))
        sizes.update(_write_shards(staging, "keywords", data.keywords, shards["keywords"]))
        record["files"] = dict(sorted(sizes.items()))
        record["size"] = sum(sizes.values())
        files["site.json"] = (json.dumps(record, indent=2, ensure_ascii=False) + "\n").encode()
        for name, body in files.items():
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        if stop():
            shutil.rmtree(staging)
            return {"cancelled": True}
        os.replace(staging, folder / build_id)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    atomic_write_bytes(folder / "latest", f"{build_id}\n".encode())
    say(1.0, "done")
    return record


def site_zip(project: Project, build_id: str) -> tuple[Path, str] | None:
    """A build as a zip whose README comes first (« unzip first »), and the zip's file name;
    ``None`` when there is no such build. The zip is written once, file by file, to
    ``outputs/sites/.zips/<build>.zip`` (a national site is gigabytes: never held in
    memory), and served from there."""
    folder = _sites(project)
    if not build_id or build_id.startswith(".") or "/" in build_id or "\\" in build_id:
        return None
    path = folder / build_id
    if not path.is_dir():
        return None
    top = f"site-{build_id}"
    target = folder / ".zips" / f"{build_id}.zip"
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{build_id}.", suffix=".tmp", dir=target.parent)
        os.close(fd)
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
                files = sorted(p for p in path.rglob("*") if p.is_file())
                files.sort(key=lambda p: p.name != "README.txt")
                for p in files:
                    zf.write(p, f"{top}/{p.relative_to(path).as_posix()}")
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    return target, f"{top}.zip"


class OfflineSiteBuilder:
    """The site builder of cartolex (:class:`cartolex.app.share.SiteBuilder`)."""

    available = True

    def builds(self, project: Project) -> list[dict[str, Any]]:
        return list_builds(project)

    def build(self, project: Project, options: Mapping[str, Any], control: Any) -> dict[str, Any]:
        opts = SiteOptions.of(options)

        def progress(fraction: float, message: str) -> None:
            control.progress(
                {"fraction": round(fraction, 3), "stage": "share.site", "message": message}
            )

        record = build_site(project, opts, progress=progress, cancelled=lambda: control.cancelled)
        if record.get("cancelled"):
            return {"summary": "nothing changed", "summary_code": "site_cancelled"}
        return {
            "id": record["id"],
            "size": record["size"],
            "counts": record["counts"],
            "summary": f"site {record['id']} built",
            "summary_code": "site_built",
            "summary_params": {"id": record["id"]},
        }

    def folder(self, project: Project, build_id: str) -> Path | None:
        """The folder of a build, or ``None``."""
        if not build_id or build_id.startswith(".") or "/" in build_id or "\\" in build_id:
            return None
        path = _sites(project) / build_id
        return path if path.is_dir() else None
