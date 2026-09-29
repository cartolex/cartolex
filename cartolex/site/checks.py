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
  included, the map or the themes not up to date, themes whose name is the
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

if TYPE_CHECKING:
    from cartolex.project import Project

__all__ = ["GENERIC_TITLES", "plan"]

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


def _fold(text: str) -> str:
    import unicodedata

    norm = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in norm if not unicodedata.combining(c)).strip().lower()


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
    project: Project, options: SiteOptions, *, stale_stages: Iterable[str] = ()
) -> dict[str, Any]:
    """The privacy summary and the checks of a build of *project* with *options*.

    *stale_stages* are the stages the site reads that are not up to date (the caller knows
    the build's state)."""
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
        parts = read_source_table(layout.table("text_parts"), "text_parts").column("part")
        summary["full_texts"] = sum(1 for p in parts.to_pylist() if p in PRIVATE_PARTS)
    if runs["map.layout"] is None:
        checks.append(_check("no_map", "blocker", {"action": "build", "scope": ["map"]}))
        return {"summary": summary, "checks": checks, "ready": False}

    bundle = build_bundle(ctx, runs)
    people = [p for p in bundle["people"] if p["person_id"] and p["x"] is not None]
    orgs = map_extras(ctx, bundle["people"])["organisations"] or bundle["units"]
    summary.update(
        people=len(people),
        projected=sum(1 for o in bundle["overlays"] if o["x"] is not None),
        organisations=len(orgs),
        keywords=sum(1 for k in bundle["keywords"] if k["x"] is not None),
        themes=len(bundle["nodes"]),
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
