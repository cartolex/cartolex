# SPDX-License-Identifier: MIT
"""Before publishing: the privacy summary of a site, and the checks of what it would show.

:func:`plan` answers what a build with the given options would carry (people
named or pseudonymised, organisations, keywords, themes, texts) and what it
never carries, and the checks, each ``{"code", "level", "params", "fix"}``:

- ``blocker``: nothing can be built (``no_map``);
- ``question``: the build waits for an answer (``names_unanswered``: a people
  atlas asks at each build whether to show names);
- ``warning``: publishable, but worth a look — names shown, projected people's
  names shown (pseudonyms unless chosen: they may be a sensitive set), abstracts
  included, texts or an atlas too large to open quickly, the map or the themes not up to date, themes whose name is the
  same in two languages (probably untranslated), technical names, empty
  themes, a generic title;
- ``info``: what stays out by design (full texts).

``fix`` is the next action the interface offers: ``build`` (with ``scope``),
``open:/themes`` or ``fix-input`` (a field of the build's form).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from .builder import SiteOptions
from .data import SiteTexts, estimate_bytes

if TYPE_CHECKING:
    from cartolex.project import Project

__all__ = ["GENERIC_TITLES", "LARGE_ATLAS_BYTES", "LARGE_TEXTS_BYTES", "plan"]

#: Titles that name no community (compared without case, accents or spaces at the ends).
GENERIC_TITLES = frozenset(
    {"", "untitled", "project", "my project", "new project", "demo", "test", "site", "atlas",
     "map", "sans titre", "projet", "nouveau projet", "carte", "sem titulo", "projeto", "mapa"}
)  # fmt: skip
#: A theme name that looks like an identifier rather than words.
_TECHNICAL = re.compile(
    r"^(?:[a-z]{0,3}[_-]?\d+|.*_.*|(?:cluster|concept|theme|node|group)\s*\d+)$", re.I
)
#: At most this many examples per check.
EXAMPLES = 5
#: Texts adding more than this to a site make it slow to open and hard to send: the plan
#: says so, with the size (a national project's abstracts are gigabytes).
LARGE_TEXTS_BYTES = 500_000_000
#: An atlas whose data read at once (``data/core.js`` and ``data/links.js``) passes this is
#: slow to open on an ordinary computer: the plan says so, with the size (a national
#: project's, about 170,000 people, is estimated near 60 MB).
LARGE_ATLAS_BYTES = 50_000_000


def _fold(text: str) -> str:
    import unicodedata

    norm = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in norm if not unicodedata.combining(c)).strip().lower()


def _pairs(project: Project, cache: Any) -> int:
    """The pairs of co-authors of the project (the app's graph, kept in the project's
    cache), 0 when they cannot be read."""
    try:
        from cartolex.app.coauthors import person_graph

        return int(person_graph(project, cache).pairs)
    except Exception:  # noqa: BLE001 - an estimate never stops the plan
        return 0


def _check(code: str, level: str, fix: dict[str, Any] | None = None, **params: Any) -> dict:
    return {"code": code, "level": level, "params": params, "fix": fix}


def _theme_checks(nodes: list[dict[str, Any]], languages: list[str]) -> list[dict[str, Any]]:
    out = []
    fix = {"action": "open:/themes"}

    def name(n: dict[str, Any]) -> str:
        names = n.get("names") or {}
        return next((names[lang] for lang in languages if names.get(lang)), "") or next(
            (v for v in names.values() if v), ""
        )

    if len(languages) > 1:
        same = [
            n for n in nodes
            if len({_fold((n.get("names") or {}).get(lang, "")) for lang in languages}) == 1
            and _fold((n.get("names") or {}).get(languages[0], ""))
        ]  # fmt: skip
        if same:
            out.append(_check("themes_untranslated", "warning", fix, count=len(same),
                              examples=[name(n) for n in same[:EXAMPLES]]))  # fmt: skip
    technical = [n for n in nodes if not name(n) or _TECHNICAL.match(name(n)) or name(n) == n["id"]]
    if technical:
        out.append(_check("themes_technical", "warning", fix, count=len(technical),
                          examples=[name(n) or n["id"] for n in technical[:EXAMPLES]]))  # fmt: skip
    empty = [n for n in nodes if not n.get("keywords")]
    if empty:
        out.append(_check("themes_empty", "warning", fix, count=len(empty),
                          examples=[name(n) or n["id"] for n in empty[:EXAMPLES]]))  # fmt: skip
    return out


def plan(
    project: Project,
    options: SiteOptions,
    *,
    stale_stages: Iterable[str] = (),
    bundle: dict[str, Any] | None = None,
    extras: dict[str, Any] | None = None,
    cache: Any = None,
) -> dict[str, Any]:
    """The privacy summary and the checks of a build of *project* with *options*.

    *stale_stages* are the stages the site reads that are not up to date (the caller knows
    the build's state). *bundle* and *extras* are the map's bundle and what the atlas adds
    from the tables, when the caller keeps them; *cache* the app's cache of the tables'
    views."""
    from cartolex.app.atlas_layers import map_extras
    from cartolex.app.routes.atlas import build_bundle, lineage
    from cartolex.project.tables import PRIVATE_PARTS, read_source_table

    from .data import project_context

    ctx = project_context(project)
    config = project.config
    title = options.title or config.identity.domain_title or config.name
    checks: list[dict[str, Any]] = []
    runs = lineage(ctx)
    summary: dict[str, Any] = {
        "title": title,
        "names": options.names,
        "names_projected": options.names_projected,
        "texts": options.texts,
        "people": 0,
        "projected": 0,
        "organisations": 0,
        "keywords": 0,
        "themes": 0,
        "full_texts": 0,
        "never": ["full_texts", "identifiers", "people_columns"],
    }
    layout = project.layout
    if layout.table("text_parts").exists():
        import pyarrow as pa
        import pyarrow.compute as pc

        parts = read_source_table(layout.table("text_parts"), "text_parts", ["part"])["part"]
        private = pc.is_in(parts, value_set=pa.array(sorted(PRIVATE_PARTS)))
        summary["full_texts"] = int(pc.sum(private).as_py() or 0)
    if runs["map.layout"] is None:
        checks.append(_check("no_map", "blocker", {"action": "build", "scope": ["map"]}))
        return {"summary": summary, "checks": checks, "ready": False}

    bundle = bundle if bundle is not None else build_bundle(ctx, runs)
    people = [p for p in bundle["people"] if p["person_id"] and p["x"] is not None]
    extras = extras if extras is not None else map_extras(ctx, bundle["people"], cache)
    orgs = extras["organisations"] or bundle["units"]
    summary.update(
        people=len(people),
        projected=sum(1 for o in bundle["overlays"] if o["x"] is not None),
        organisations=len(orgs),
        keywords=sum(1 for k in bundle["keywords"] if k["x"] is not None),
        themes=len(bundle["nodes"]),
    )
    # What the texts would add to the site: the titles counted, the abstracts estimated.
    texts = SiteTexts.of(
        project, {p["person_id"]: f"s{i + 1}" for i, p in enumerate(people)}, "titles", cache
    )
    sizes = texts.estimate() if texts is not None else {"titles": 0, "abstracts": 0}
    summary["text_bytes"] = {"titles": sizes["titles"], "abstracts": sizes["abstracts"]}
    # What the atlas's data would weigh: estimated from the counts and the co-author pairs.
    summary["site_bytes"] = estimate_bytes(
        len(people), summary["keywords"], len(orgs), summary["themes"], _pairs(project, cache)
    )
    if people:
        if options.names is None:
            checks.append(_check("names_unanswered", "question", {"action": "fix-input",
                                                                   "field": "names"}))  # fmt: skip
        elif options.names:
            checks.append(_check("names_shown", "warning", {"action": "fix-input", "field": "names"},
                                 count=len(people)))  # fmt: skip
    if summary["projected"] and options.names_projected:
        checks.append(_check("projected_names_shown", "warning",
                             {"action": "fix-input", "field": "names_projected"},
                             count=summary["projected"]))  # fmt: skip
    if options.texts == "abstracts":
        checks.append(_check("abstracts_included", "warning",
                             {"action": "fix-input", "field": "texts"}))  # fmt: skip
        if sizes["abstracts"] > LARGE_TEXTS_BYTES:
            checks.append(_check("abstracts_large", "warning",
                                 {"action": "fix-input", "field": "texts"},
                                 size=sizes["abstracts"], titles=sizes["titles"]))  # fmt: skip
    elif options.texts == "titles" and sizes["titles"] > LARGE_TEXTS_BYTES:
        checks.append(_check("titles_large", "warning", {"action": "fix-input", "field": "texts"},
                             size=sizes["titles"]))  # fmt: skip
    weight = summary["site_bytes"]
    if weight["atlas"] > LARGE_ATLAS_BYTES:
        total = weight["core"] + weight["links"] + weight["parts"]
        checks.append(_check("site_large", "warning", None, size=weight["atlas"], total=total))
    if summary["full_texts"]:
        checks.append(_check("full_texts_kept", "info", None, count=summary["full_texts"]))
    stale = [s for s in stale_stages]
    if stale:
        checks.append(_check("map_stale", "warning", {"action": "build", "scope": ["map"]},
                             stages=stale))  # fmt: skip
    languages = list(config.languages.display) or ["en"]
    checks += _theme_checks(bundle["nodes"], languages)
    if _fold(title) in GENERIC_TITLES or _fold(title) == _fold(layout.root.name):
        checks.append(_check("title_generic", "warning", {"action": "fix-input", "field": "title"},
                             title=title))  # fmt: skip
    ready = not any(c["level"] in ("blocker", "question") for c in checks)
    return {"summary": summary, "checks": checks, "ready": ready}
