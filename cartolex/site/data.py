# SPDX-License-Identifier: MIT
"""What an offline site shows, gathered from a built project.

:func:`gather` reads the atlas bundle the app draws (the theme tree, people,
keywords, the engine's units, projected people) and what the atlas adds from
the tables (organisations, current affiliations), then each person's themes,
keywords and **real nearest neighbours** (the closest people in the space the
map is drawn from, not on the drawing), each organisation's themes, keywords
and members, each theme's people, organisations and keywords.

What a site never carries: a full text (only the parts
:func:`cartolex.project.tables.shareable_parts` lets through, and only on
request: titles, or titles and abstracts), the people's extra columns, their
identifiers or their project ids. People get site ids (``s1``, ``s2``…) in an
order of their own: by name when names are shown, shuffled otherwise, so the
order says nothing either.

The answer is split per page (V2-062): ``core`` (the map, the tree, the search)
loads with every page, ``details`` with a person, organisation or theme page,
``texts`` with a person page when texts are included.
"""

from __future__ import annotations

import csv
import random
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cartolex.project import Project

__all__ = ["NEIGHBOURS", "SiteData", "SiteDataError", "gather", "project_context"]

#: Real nearest neighbours listed per person.
NEIGHBOURS = 6
#: Keywords listed per person, organisation and theme.
KEYWORDS = 15
#: The share of a person's (an organisation's) usage above which they count toward a theme.
THEME_SHARE = 0.2
#: Decimals kept for map coordinates and shares.
XY = 4
SHARE = 3


class SiteDataError(Exception):
    """The project cannot give a site yet; ``code`` names why (``no_map``)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class SiteData:
    """A site's data, per file, and its counts (for the privacy summary and ``site.json``)."""

    core: dict[str, Any]
    details: dict[str, Any]
    texts: dict[str, Any] | None
    counts: dict[str, int]


def project_context(project: Project) -> Any:
    """The small context the atlas helpers read (``project``, ``layout``, ``id``)."""
    return SimpleNamespace(project=project, layout=project.layout, id=project.layout.root.name)


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _r(value: Any, digits: int) -> float | None:
    return None if value is None else round(float(value), digits)


def _top(shares: Mapping[str, float], n: int = 3) -> list[list[Any]]:
    ranked = sorted(shares.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
    return [[node, round(share, SHARE)] for node, share in ranked if share > 0]


def _largest(shares: Mapping[str, float]) -> str | None:
    best, value = None, 0.0
    for node, share in shares.items():
        if share > value:
            best, value = node, share
    return best


def neighbours(vectors: dict[str, list[float]], k: int = NEIGHBOURS) -> dict[str, list[list[Any]]]:
    """Each id's *k* nearest ids by cosine similarity of *vectors*: ``{id: [[id, sim], …]}``.

    Computed by blocks of rows, so ten thousand people never make a dense square matrix.
    """
    import numpy as np

    ids = list(vectors)
    if len(ids) < 2:
        return {i: [] for i in ids}
    z = np.asarray([vectors[i] for i in ids], dtype=float)
    norms = np.linalg.norm(z, axis=1, keepdims=True)
    z = z / np.where(norms > 0, norms, 1.0)
    k = min(k, len(ids) - 1)
    out: dict[str, list[list[Any]]] = {}
    for start in range(0, len(ids), 1024):
        block = z[start : start + 1024] @ z.T
        for row in range(block.shape[0]):
            block[row, start + row] = -np.inf
        best = np.argpartition(-block, k, axis=1)[:, :k]
        for row in range(block.shape[0]):
            order = sorted(best[row], key=lambda j, r=row: -block[r, j])
            out[ids[start + row]] = [[ids[j], round(float(block[row, j]), 3)] for j in order]
    return out


def _vectors(ctx: Any, engine_to_person: dict[str, str]) -> dict[str, list[float]]:
    """Each mapped person's coordinates in the space the map is drawn from."""
    out: dict[str, list[float]] = {}
    for r in _rows(ctx.layout.stage("themes.space") / "pca_individuals.csv"):
        pid = engine_to_person.get(r.get("id", ""))
        if not pid:
            continue
        pcs = [float(v) for key, v in r.items() if key.startswith("PC") and v not in ("", None)]
        if pcs:
            out[pid] = pcs
    return out


def _texts(ctx: Any, people: set[str], mode: str) -> dict[str, list[dict[str, Any]]]:
    """The texts of *people*: titles and years (and abstracts, with ``abstracts``); never a
    private part (``shareable_parts``)."""
    from cartolex.project.tables import read_source_table, shareable_parts

    layout = ctx.layout
    if mode == "none" or not layout.table("texts").exists():
        return {}
    by_text: dict[str, list[str]] = defaultdict(list)
    if layout.table("authorships").exists():
        table = read_source_table(layout.table("authorships"), "authorships")
        for a in table.select(["text_id", "person_id"]).to_pylist():
            if a["person_id"] in people:
                by_text[a["text_id"]].append(a["person_id"])
    abstracts: dict[str, str] = {}
    if mode == "abstracts" and layout.table("text_parts").exists():
        parts = shareable_parts(read_source_table(layout.table("text_parts"), "text_parts"))
        for p in parts.select(["text_id", "part", "content"]).to_pylist():
            if p["part"] == "abstract" and p["content"] and p["text_id"] in by_text:
                abstracts.setdefault(p["text_id"], p["content"])
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    texts = read_source_table(layout.table("texts"), "texts").select(["text_id", "title", "year"])
    for t in texts.to_pylist():
        for pid in by_text.get(t["text_id"], ()):
            entry: dict[str, Any] = {"title": t["title"] or "", "year": t["year"]}
            if t["text_id"] in abstracts:
                entry["abstract"] = abstracts[t["text_id"]]
            out[pid].append(entry)
    for items in out.values():
        items.sort(key=lambda e: (-(e["year"] or 0), e["title"]))
    return dict(out)


def _names_of(ctx: Any) -> dict[str, str]:
    """Person id → the name the people's table gives (for projected people)."""
    from cartolex.project.tables import read_source_table

    layout = ctx.layout
    if not layout.table("people").exists():
        return {}
    table = read_source_table(layout.table("people"), "people")
    cols = [c for c in ("person_id", "first_name", "last_name") if c in table.column_names]
    out = {}
    for r in table.select(cols).to_pylist():
        out[r["person_id"]] = f"{r.get('first_name') or ''} {r.get('last_name') or ''}".strip()
    return out


def gather(
    project: Project,
    *,
    names: bool,
    texts: str = "none",
    progress: Callable[[float, str], None] | None = None,
    rng: random.Random | None = None,
) -> SiteData:
    """The data of a site of *project*: names shown when *names*, texts as *texts*
    (``none``, ``titles`` or ``abstracts``). Raises :class:`SiteDataError` without a map."""
    from cartolex.app.atlas_layers import keyword_sets, map_extras
    from cartolex.app.routes.atlas import build_bundle, lineage

    say = progress or (lambda fraction, message: None)
    ctx = project_context(project)
    runs = lineage(ctx)
    if runs["map.layout"] is None:
        raise SiteDataError("no_map")
    say(0.05, "reading the map")
    bundle = build_bundle(ctx, runs)
    extras = map_extras(ctx, bundle["people"])
    depth = int(bundle["depth"] or 0)

    # ── the theme tree ──
    nodes = [
        {
            "id": n["id"],
            "parent": n["parent"],
            "level": n["level"],
            "order": n["order"],
            "names": n["names"],
            "weight": _r(n["weight"], SHARE),
            "share": _r(n["share"], SHARE),
            "keywords": n["keywords"],
        }
        for n in bundle["nodes"]
    ]

    # ── people, in an order that says nothing ──
    say(0.2, "people")
    mapped = [p for p in bundle["people"] if p["person_id"] and p["x"] is not None]
    if names:
        mapped.sort(key=lambda p: (p["name"].split(" ")[-1].lower(), p["name"].lower()))
    else:
        (rng or random.SystemRandom()).shuffle(mapped)
    sid = {p["person_id"]: f"s{k + 1}" for k, p in enumerate(mapped)}
    people_core = {
        "id": [sid[p["person_id"]] for p in mapped],
        "name": [p["name"] if names else None for p in mapped],
        "x": [_r(p["x"], XY) for p in mapped],
        "y": [_r(p["y"], XY) for p in mapped],
        "top": [_largest(p["shares"][0]) if p["shares"] else None for p in mapped],
    }

    # ── organisations, always named ──
    say(0.35, "organisations")
    orgs = [o for o in extras["organisations"]]
    levels = extras["organisation_levels"]
    if not orgs:  # no organisations table: the engine's units stand in
        orgs = [
            {
                "id": u["unit"], "name": u["unit"], "acronym": "", "level": "unit",
                "parents": [], "x": u["x"], "y": u["y"], "members": u["size"],
                "members_ever": u["size"], "location": None,
            }
            for u in bundle["units"]
        ]  # fmt: skip
        levels = [{"id": "unit", "names": {}, "count": len(orgs)}] if orgs else []
    oid = {o["id"]: f"o{k + 1}" for k, o in enumerate(orgs)}
    person_orgs = {pid: info.get("orgs") or [] for pid, info in extras["people"].items()}
    members: dict[str, list[str]] = defaultdict(list)
    member_pids: dict[str, list[str]] = defaultdict(list)
    parents = {o["id"]: list(o.get("parents") or []) for o in orgs}
    for p in mapped:
        todo, seen = list(person_orgs.get(p["person_id"], [])), set()
        while todo:
            o = todo.pop()
            if o in seen or o not in oid:
                continue
            seen.add(o)
            members[o].append(sid[p["person_id"]])
            member_pids[o].append(p["person_id"])
            todo.extend(parents.get(o, []))
    shares_of = {p["person_id"]: p["shares"] for p in mapped}
    org_shares: dict[str, list[dict[str, float]]] = {}
    for o in orgs:
        pids = member_pids[o["id"]]
        levels_sum: list[dict[str, float]] = [defaultdict(float) for _ in range(depth)]
        for pid in pids:
            for lv, shares in enumerate(shares_of[pid][:depth]):
                for node, share in shares.items():
                    levels_sum[lv][node] += share / len(pids)
        org_shares[o["id"]] = [dict(s) for s in levels_sum]
    orgs_core = {
        "id": [oid[o["id"]] for o in orgs],
        "name": [o["name"] for o in orgs],
        "acronym": [o.get("acronym") or "" for o in orgs],
        "level": [o["level"] or "" for o in orgs],
        "parents": [[oid[q] for q in o.get("parents") or [] if q in oid] for o in orgs],
        "x": [_r(o["x"], XY) for o in orgs],
        "y": [_r(o["y"], XY) for o in orgs],
        "members": [len(members[o["id"]]) for o in orgs],
        "location": [
            [o["location"]["lon"], o["location"]["lat"]] if o.get("location") else None
            for o in orgs
        ],
        "top": [_largest(org_shares[o["id"]][0]) if org_shares[o["id"]] else None for o in orgs],
    }

    # ── keywords ──
    keywords = [k for k in bundle["keywords"] if k["x"] is not None]
    kw_core = {
        "term": [k["term"] for k in keywords],
        "x": [_r(k["x"], XY) for k in keywords],
        "y": [_r(k["y"], XY) for k in keywords],
        "node": [k["node"] for k in keywords],
        "weight": [_r(k["weight"] or 0, SHARE) for k in keywords],
    }

    # ── projected people: placed on the finished map, never moving it ──
    projected = [o for o in bundle["overlays"] if o["x"] is not None and o["y"] is not None]
    projected_names = _names_of(ctx) if names and projected else {}
    if not names:
        (rng or random.SystemRandom()).shuffle(projected)
    projected_core = {
        "id": [f"q{k + 1}" for k in range(len(projected))],
        "name": [projected_names.get(o["person_id"]) or None if names else None for o in projected],
        "x": [_r(o["x"], XY) for o in projected],
        "y": [_r(o["y"], XY) for o in projected],
        "top": [_largest(o["shares"][0]) if o["shares"] else None for o in projected],
    }

    # ── details: a person's themes, keywords, organisations and real neighbours ──
    say(0.5, "neighbours")
    # The map's rows carry the engine's ids; the bundle's people are the same rows, in order.
    rows = _rows(ctx.layout.stage("map.layout") / "umap_individuals.csv")
    engine_to_person = {
        r.get("id", ""): p["person_id"] for r, p in zip(rows, bundle["people"], strict=False)
    }
    near = neighbours(_vectors(ctx, engine_to_person))
    say(0.65, "keywords")
    person_terms = keyword_sets(ctx, "person", [p["person_id"] for p in mapped])
    org_terms = keyword_sets(
        ctx, "organisation", list(oid), {"organisations": orgs, "people": extras["people"]}
    )
    people_details = {}
    for p in mapped:
        pid = p["person_id"]
        people_details[sid[pid]] = {
            "themes": [_top(s) for s in p["shares"][:depth]],
            "keywords": person_terms.get(pid, [])[:KEYWORDS],
            "orgs": [oid[o] for o in person_orgs.get(pid, []) if o in oid],
            "near": [[sid[q], sim] for q, sim in near.get(pid, []) if q in sid],
        }
    orgs_details = {
        oid[o["id"]]: {
            "themes": [_top(s) for s in org_shares[o["id"]]],
            "keywords": org_terms.get(o["id"], [])[:KEYWORDS],
            "members": members[o["id"]],
        }
        for o in orgs
    }

    # ── details: a theme's people, organisations and keywords ──
    say(0.8, "themes")
    by_level: dict[int, set[str]] = defaultdict(set)
    for n in nodes:
        by_level[n["level"]].add(n["id"])
    themes_details: dict[str, dict[str, Any]] = {}
    top_keywords = {n["id"]: n["top_keywords"] for n in bundle["nodes"]}
    for n in nodes:
        lv = n["level"] - 1
        ranked_people = sorted(
            (
                (p["shares"][lv].get(n["id"], 0.0), sid[p["person_id"]])
                for p in mapped
                if lv < len(p["shares"]) and p["shares"][lv].get(n["id"], 0.0) >= THEME_SHARE
            ),
            key=lambda sv: (-sv[0], sv[1]),
        )
        ranked_orgs = sorted(
            (
                (org_shares[o["id"]][lv].get(n["id"], 0.0), oid[o["id"]])
                for o in orgs
                if lv < len(org_shares[o["id"]])
                and org_shares[o["id"]][lv].get(n["id"], 0.0) >= THEME_SHARE
            ),
            key=lambda sv: (-sv[0], sv[1]),
        )
        themes_details[n["id"]] = {
            "people": [[s, round(v, SHARE)] for v, s in ranked_people],
            "orgs": [[s, round(v, SHARE)] for v, s in ranked_orgs],
            "keywords": list(top_keywords.get(n["id"]) or [])[:KEYWORDS],
        }

    # Keywords → the people for whom they are among the main ones.
    used_by: dict[str, list[str]] = defaultdict(list)
    for p in mapped:
        for term in person_terms.get(p["person_id"], [])[:KEYWORDS]:
            used_by[term].append(sid[p["person_id"]])

    # ── texts, on request ──
    say(0.9, "texts")
    text_items = _texts(ctx, {p["person_id"] for p in mapped}, texts)
    texts_part = (
        {sid[pid]: items for pid, items in text_items.items() if pid in sid}
        if texts != "none"
        else None
    )

    config = project.config
    core = {
        "map_version": bundle["map_version"],
        "depth": depth,
        "levels": bundle["levels"],
        "languages": list(config.languages.display),
        "nodes": nodes,
        "people": people_core,
        "keywords": kw_core,
        "orgs": orgs_core,
        "org_levels": [{"id": lv["id"], "names": lv["names"]} for lv in levels],
        "projected": projected_core,
        "bounds": bundle["bounds"],
        "names": bool(names),
        "texts": texts,
    }
    details = {
        "people": people_details,
        "orgs": orgs_details,
        "themes": themes_details,
        "used_by": dict(used_by),
    }
    counts = {
        "people": len(mapped),
        "projected": len(projected),
        "organisations": len(orgs),
        "keywords": len(keywords),
        "themes": len(nodes),
        "texts": sum(len(v) for v in (texts_part or {}).values()),
        "abstracts": sum(1 for v in (texts_part or {}).values() for e in v if "abstract" in e),
    }
    return SiteData(core=core, details=details, texts=texts_part, counts=counts)
