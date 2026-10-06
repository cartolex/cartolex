# SPDX-License-Identifier: MIT
"""What an offline site shows, gathered from a built project.

The site mounts the app's atlas (``cartolex/app/static/atlas/``, see ``docs/dev/atlas.md``)
over a data source of its own, which answers from the site's files what the app answers
from its server. :func:`gather` makes those files' contents:

- ``core`` (``data/core.js``, read when the site opens): the atlas bundle of
  ``GET /api/atlas`` (``cartolex-atlas/3``) as columns: the theme tree, the people (their
  place, their themes' shares per level, their organisations), the keywords, the
  organisations, the projected people, the years;
- ``people`` (``data/people/<n>.js``, a part loaded with the person it holds): each
  person's keywords and their vector in the space of the themes, for « Compare »;
- ``orgs`` (``data/orgs.js``): the same for the organisations;
- ``keywords`` (``data/keywords/<n>.js``): who uses each keyword most, with the share of
  their use it holds;
- ``links`` (``data/links.js``, loaded when the network is asked for): who writes with
  whom, as sparse lists (people, and organisations per level);
- ``texts`` (``data/texts/<n>.js``, only on request): the titles, or titles and abstracts.

What a site never carries: a full text (only the parts
:func:`cartolex.project.tables.shareable_parts` lets through, and only on request), the
people's extra columns, their roles, their identifiers or their project ids, the name of a
set of projected people. People get site ids (``s1``, ``s2``…) in an order of their own: by
name when names are shown, shuffled otherwise, so the order says nothing either; projected
people get ``q1``, ``q2``… and are left out of the links unless their names are shown.
"""

from __future__ import annotations

import base64
import csv
import random
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cartolex.project import Project

__all__ = [
    "ABSTRACT_BYTES",
    "KEYWORD_USERS",
    "SiteData",
    "SiteDataError",
    "SiteTexts",
    "gather",
    "project_context",
]

#: Keywords kept per person and organisation, the most used first.
KEYWORDS = 15
#: People kept per keyword (its users, the share of their use it holds the largest first).
KEYWORD_USERS = 100
#: Theme shares kept per person and level, the largest first.
SHARES_KEPT = 12
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
    people: dict[str, Any]
    orgs: dict[str, Any]
    keywords: dict[str, Any]
    links: dict[str, Any] | None
    texts: SiteTexts | None
    counts: dict[str, int] = field(default_factory=dict)


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


def _shares(levels: list[dict[str, float]], code: Mapping[str, int]) -> list[list[int]]:
    """A person's shares per level as ``[node code, thousandths, …]``, the largest first,
    at most :data:`SHARES_KEPT` per level (a share under a thousandth is left out)."""
    out = []
    for shares in levels:
        ranked = sorted(shares.items(), key=lambda kv: (-kv[1], kv[0]))[:SHARES_KEPT]
        flat: list[int] = []
        for node, share in ranked:
            permille = round(share * 1000)
            if permille >= 1 and node in code:
                flat += [code[node], permille]
        out.append(flat)
    return out


def _vector(v: Any) -> str:
    """A vector as base64 bytes (int8, its largest component ±127): a cosine does not
    depend on the scale, and the site reads it as ``Int8Array``."""
    import numpy as np

    v = np.asarray(v, dtype=np.float64)
    top = float(np.abs(v).max()) if len(v) else 0.0
    q = np.zeros(len(v), np.int8) if top <= 0 else np.round(v / top * 127).astype(np.int8)
    return base64.b64encode(q.tobytes()).decode("ascii")


def _ints(values: Any, dtype: str = "<i4") -> str:
    """Integers as base64 bytes (little-endian; the site reads them as a typed array)."""
    import numpy as np

    return base64.b64encode(np.asarray(values).astype(dtype).tobytes()).decode("ascii")


#: A text's entry in a site, beyond its title's bytes (``{"title":"","year":2020},``).
ENTRY_BYTES = 26
#: An abstract's bytes in a site, on average: 1,120 measured on 1.9 million abstracts of a
#: national sample. It estimates a site before it is built; the build counts them.
ABSTRACT_BYTES = 1_120


@dataclass
class SiteTexts:
    """The texts a site carries, an entry per text and mapped author, as two arrays: the
    author's site number (``s<number>``) and the text's row in the texts' view
    (:mod:`cartolex.app.texts_view`), never an object per text. The abstracts are read when
    the site is written, a row group at a time (:meth:`abstracts_of`), and each part of
    the site is made from its people's entries alone (:meth:`entries`)."""

    view: Any
    number: Any
    row: Any
    abstracts: bool

    @classmethod
    def of(
        cls, project: Project, sids: Mapping[str, str], mode: str, cache: Any = None
    ) -> SiteTexts | None:
        """The texts of the people of *sids* (person id → site id) as *mode* carries them;
        ``None`` for ``none``. *cache*: the app's, which keeps the texts' view."""
        import numpy as np

        from cartolex.app.texts_view import texts_view

        if mode == "none":
            return None
        none = np.zeros(0, dtype=np.int64)
        if not project.layout.table("texts").exists():
            return cls(None, none, none, mode == "abstracts")
        view = texts_view(project, cache)
        number_of = np.full(len(view.person_ids), -1, dtype=np.int64)
        code = {pid: i for i, pid in enumerate(view.person_ids)}
        for pid, sid in sids.items():
            if pid in code:
                number_of[code[pid]] = int(sid[1:])
        authors = view.authors
        number = number_of[np.asarray(authors["person"], dtype=np.int64)] if len(authors) else none
        keep = number >= 0
        row = np.asarray(authors["text"], dtype=np.int64)[keep] if len(authors) else none
        return cls(view, number[keep], row, mode == "abstracts")

    @property
    def count(self) -> int:
        return len(self.row)

    def estimate(self) -> dict[str, int]:
        """The bytes the texts add to a site with their titles (counted), and with their
        abstracts too (estimated, :data:`ABSTRACT_BYTES` each), and how many entries have
        an abstract or a full text."""
        import numpy as np
        import pyarrow.compute as pc

        if not len(self.row):
            return {"titles": 0, "abstracts": 0, "with_abstract": 0}
        lengths = pc.binary_length(self.view.table["title"]).fill_null(0).to_numpy()
        titles = int(lengths[self.row].sum()) + ENTRY_BYTES * len(self.row)
        with_abstract = int(np.count_nonzero(self.view.content[self.row] >= 1))
        return {
            "titles": titles,
            "abstracts": titles + ABSTRACT_BYTES * with_abstract,
            "with_abstract": with_abstract,
        }

    def abstracts_of(self, project: Project, scratch: Path) -> Any:
        """The abstract of each text an entry names (its first abstract part, never a
        private part), as a table ``row``, ``abstract`` in row order: written to *scratch*
        a row group of ``text_parts`` at a time, and mapped back from there."""
        import numpy as np
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.ipc as ipc
        import pyarrow.parquet as pq

        from cartolex.project.tables import check_source_file, find_ids, id_keys

        schema = pa.schema([("row", pa.int64()), ("abstract", pa.string())])
        target = scratch / "abstracts.arrow"
        path = project.layout.table("text_parts")
        with pa.OSFile(str(target), "wb") as sink, ipc.new_file(sink, schema) as writer:
            if self.view is not None and len(self.row) and path.exists():
                wanted = np.zeros(self.view.n, dtype=bool)
                wanted[self.row] = True
                keys = id_keys(self.view.table["text_id"])
                check_source_file(path, "text_parts")
                pf = pq.ParquetFile(path)
                last = -1
                try:
                    for group in range(pf.num_row_groups):
                        part = pf.read_row_group(group, columns=["text_id", "part", "content"])
                        part = part.filter(
                            pc.and_(
                                pc.equal(part["part"], "abstract"),
                                pc.greater(pc.binary_length(part["content"]), 0),
                            )
                        )
                        rows = find_ids(keys, part["text_id"])
                        at = np.flatnonzero((rows >= 0) & wanted[np.maximum(rows, 0)])
                        rows = rows[at]
                        if not len(rows):
                            continue
                        first = np.empty(len(rows), dtype=bool)  # a text's first abstract
                        first[0] = rows[0] != last
                        first[1:] = rows[1:] != rows[:-1]
                        last = int(rows[-1])
                        writer.write_table(
                            pa.table(
                                {
                                    "row": rows[first],
                                    "abstract": part["content"].take(pa.array(at[first])),
                                },
                                schema=schema,
                            )
                        )
                finally:
                    pf.close()
        return ipc.open_file(pa.memory_map(str(target), "r")).read_all()

    def entries(self, select: Any, abstracts: Any = None) -> dict[str, list[dict[str, Any]]]:
        """The entries at *select* (indices) by site id, in site order, each person's texts
        the newest first, then by title (with their abstracts from :meth:`abstracts_of`)."""
        import numpy as np

        rows, numbers = self.row[select], self.number[select]
        if not len(rows):
            return {}
        texts = np.unique(rows)
        titles = _take_sorted(self.view.table["title"], texts)
        years = np.where(self.view.has_year[texts], self.view.year[texts], 0).tolist()
        found: dict[int, str] = {}
        if abstracts is not None and abstracts.num_rows:
            have = abstracts["row"].to_numpy()
            at = np.minimum(np.searchsorted(have, texts), len(have) - 1)
            hit = np.flatnonzero(have[at] == texts)
            body = _take_sorted(abstracts["abstract"], at[hit])
            found = dict(zip(texts[hit].tolist(), body, strict=True))
        index = {r: i for i, r in enumerate(texts.tolist())}
        out: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for number, row in zip(numbers.tolist(), rows.tolist(), strict=True):
            i = index[row]
            entry: dict[str, Any] = {"title": titles[i] or "", "year": years[i] or None}
            if row in found:
                entry["abstract"] = found[row]
            out[number].append(entry)
        for items in out.values():
            items.sort(key=lambda e: (-(e["year"] or 0), e["title"]))
        return {f"s{n}": out[n] for n in sorted(out)}


def _take_sorted(column: Any, rows: Any) -> list[Any]:
    """The values of the chunked *column* at the sorted *rows*, taken chunk by chunk (one
    take across many chunks is far slower)."""
    import numpy as np
    import pyarrow as pa

    chunks = column.chunks if isinstance(column, pa.ChunkedArray) else [column]
    starts = np.cumsum([0, *(len(c) for c in chunks)])
    bounds = np.searchsorted(rows, starts)
    out: list[Any] = []
    for i, chunk in enumerate(chunks):
        mine = rows[bounds[i] : bounds[i + 1]]
        if len(mine):
            out += chunk.take(pa.array(mine - starts[i])).to_pylist()
    return out


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


#: The most partners kept per person or organisation in the site's links, the strongest
#: first: the network's rings are found in the browser from these lists.
MAX_LINKS = 200


def _graph_lists(graph: Any, codes: list[int | None], index: Any) -> dict[str, Any]:
    """The links of *graph* between the site's entities, as CSR arrays over the site's
    order: entity *i* is ``codes[i]`` in the graph (``None``: not in it), *index* maps a
    graph code to a site position (``-1``: not in the site). Answers ``ptr``, ``nbr``,
    ``cnt`` (texts together), ``texts`` (each one's texts counted), ``outside`` (co-authors
    outside the project, and those in the project but not in the site)."""
    import numpy as np

    n = len(codes)
    ptr = np.zeros(n + 1, np.int64)
    nbr: list[Any] = []
    cnt: list[Any] = []
    texts = np.zeros(n, np.int64)
    outside = np.zeros(n, np.int64)
    for i, code in enumerate(codes):
        if code is None:
            ptr[i + 1] = ptr[i]
            continue
        partners, together = graph.links(code)
        at = index[partners]
        keep = at >= 0
        texts[i] = graph.stat("texts", code)
        outside[i] = graph.stat("outside", code) + int((~keep).sum())
        mine = at[keep][:MAX_LINKS]
        nbr.append(mine)
        cnt.append(together[keep][:MAX_LINKS])
        ptr[i + 1] = ptr[i] + len(mine)
    flat = np.concatenate(nbr) if nbr else np.zeros(0, np.int64)
    weights = np.concatenate(cnt) if cnt else np.zeros(0, np.int64)
    return {
        "ptr": _ints(ptr),
        "nbr": _ints(flat),
        "cnt": _ints(np.minimum(weights, 65535), "<u2"),
        "texts": _ints(texts),
        "outside": _ints(outside),
        "max_authors": int(graph.max_authors),
    }


def _links(
    project: Project, pids: list[str], orgs: list[dict[str, Any]], levels: list[str]
) -> dict[str, Any] | None:
    """Who writes with whom among the site's people (*pids*, in site order) and among its
    organisations of each level (``None`` when the project cannot say)."""
    import numpy as np

    try:
        from cartolex.app.coauthors import org_graph, person_graph
    except ImportError:  # the co-authorship graph is not part of this version
        return None
    graph = person_graph(project)
    index = np.full(len(graph.ids), -1, np.int64)
    codes = [graph.code(pid) for pid in pids]
    for i, code in enumerate(codes):
        if code is not None:
            index[code] = i
    out: dict[str, Any] = {"people": _graph_lists(graph, codes, index), "orgs": {}}
    position = {o["id"]: k for k, o in enumerate(orgs)}
    for level in levels:
        mine = [o["id"] for o in orgs if o["level"] == level]
        if not mine:
            continue
        g = org_graph(project, level)
        index = np.full(len(g.ids), -1, np.int64)
        codes = [g.code(o) for o in mine]
        for o, code in zip(mine, codes, strict=True):
            if code is not None:
                index[code] = position[o]
        lists = _graph_lists(g, codes, index)
        lists["ids"] = [position[o] for o in mine]
        out["orgs"][level] = lists
    return out


def _space(ctx: Any, bundle: dict[str, Any], extras: dict[str, Any]) -> Any:
    """The space of the themes with the map's people (``None`` without one)."""
    from cartolex.app.space_index import space_run, space_view

    run = space_run(ctx.layout)
    if run is None:
        return None
    try:
        return space_view(ctx, run, bundle, extras)
    except (OSError, ValueError, KeyError):
        return None


def gather(
    project: Project,
    *,
    names: bool,
    names_projected: bool = False,
    texts: str = "none",
    progress: Callable[[float, str], None] | None = None,
    rng: random.Random | None = None,
) -> SiteData:
    """The data of a site of *project*: names shown when *names* (projected people's when
    *names_projected*: pseudonyms otherwise, in a shuffled order), texts as *texts*
    (``none``, ``titles`` or ``abstracts``). Raises :class:`SiteDataError` without a map."""
    import numpy as np

    from cartolex.app.atlas_layers import keyword_sets, map_extras, terms_of_people
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
            "color": n.get("color"),
            "weight": _r(n["weight"], SHARE),
            "share": _r(n["share"], SHARE),
            "keywords": n["keywords"],
            "keywords_counted": n.get("keywords_counted", 0),
            "top_keywords": list(n.get("top_keywords") or []),
            "x": _r(n.get("x"), XY),
            "y": _r(n.get("y"), XY),
        }
        for n in bundle["nodes"]
    ]
    code = {n["id"]: k for k, n in enumerate(nodes)}

    # ── people, in an order that says nothing ──
    say(0.15, "people")
    at_of = {id(p): k for k, p in enumerate(bundle["people"])}  # the bundle's index
    mapped = [p for p in bundle["people"] if p["person_id"] and p["x"] is not None]
    if names:
        mapped.sort(key=lambda p: (p["name"].split(" ")[-1].lower(), p["name"].lower()))
    else:
        (rng or random.SystemRandom()).shuffle(mapped)
    pids = [p["person_id"] for p in mapped]
    sid = {pid: f"s{k + 1}" for k, pid in enumerate(pids)}
    site_of_bundle = np.full(len(bundle["people"]), -1, np.int64)
    for k, p in enumerate(mapped):
        site_of_bundle[at_of[id(p)]] = k

    # ── organisations, always named ──
    say(0.25, "organisations")
    orgs = list(extras["organisations"])
    levels = extras["organisation_levels"]
    person_orgs = {pid: info.get("orgs") or [] for pid, info in extras["people"].items()}
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
        person_orgs = {p["person_id"]: [p["unit"]] for p in mapped if p.get("unit")}
    position = {o["id"]: k for k, o in enumerate(orgs)}
    oid = {o["id"]: f"o{k + 1}" for k, o in enumerate(orgs)}

    people_core = {
        "id": [sid[pid] for pid in pids],
        "name": [p["name"] if names else None for p in mapped],
        "x": [_r(p["x"], XY) for p in mapped],
        "y": [_r(p["y"], XY) for p in mapped],
        "shares": [_shares(p["shares"][:depth], code) for p in mapped],
        "orgs": [[position[o] for o in person_orgs.get(pid, []) if o in position] for pid in pids],
    }
    orgs_core = {
        "id": [oid[o["id"]] for o in orgs],
        "name": [o["name"] for o in orgs],
        "acronym": [o.get("acronym") or "" for o in orgs],
        "level": [o["level"] or "" for o in orgs],
        "parents": [[position[q] for q in o.get("parents") or [] if q in position] for o in orgs],
        "x": [_r(o["x"], XY) for o in orgs],
        "y": [_r(o["y"], XY) for o in orgs],
        "members": [int(o.get("members") or 0) for o in orgs],
        "members_ever": [int(o.get("members_ever") or 0) for o in orgs],
        "location": [
            [o["location"]["lon"], o["location"]["lat"]] if o.get("location") else None
            for o in orgs
        ],
    }

    # ── keywords ──
    keywords = [k for k in bundle["keywords"] if k["x"] is not None]
    kw_core = {
        "term": [k["term"] for k in keywords],
        "x": [_r(k["x"], XY) for k in keywords],
        "y": [_r(k["y"], XY) for k in keywords],
        "node": [code.get(k["node"], -1) if k["node"] else -1 for k in keywords],
        "level": [k.get("level") for k in keywords],
        "counts_to": [k.get("counts_to") or 0 for k in keywords],
        "weight": [_r(k["weight"] or 0, SHARE) for k in keywords],
        "share": [_r(k.get("share") or 0, SHARE) for k in keywords],
        "category": [k.get("category") for k in keywords],
    }

    # ── projected people: placed on the finished map, never moving it ──
    projected = [o for o in bundle["overlays"] if o["x"] is not None and o["y"] is not None]
    projected_names = _names_of(ctx) if names_projected and projected else {}
    if not names_projected:
        (rng or random.SystemRandom()).shuffle(projected)
    projected_core = {
        "id": [f"q{k + 1}" for k in range(len(projected))],
        "name": [
            projected_names.get(o["person_id"]) or None if names_projected else None
            for o in projected
        ],
        "x": [_r(o["x"], XY) for o in projected],
        "y": [_r(o["y"], XY) for o in projected],
        "shares": [_shares(o["shares"][:depth], code) for o in projected],
    }

    # ── what a person, an organisation and a keyword add when they are in focus ──
    say(0.4, "keywords")
    by_person = terms_of_people(ctx)
    person_terms = keyword_sets(ctx, "person", pids, by_person=by_person)
    org_terms = keyword_sets(
        ctx, "organisation", list(position), {"organisations": orgs, "people": extras["people"]},
        by_person=by_person,
    )  # fmt: skip
    say(0.55, "the space of the themes")
    space = _space(ctx, bundle, extras)
    people_details: dict[str, dict[str, Any]] = {}
    for pid in pids:
        entry: dict[str, Any] = {"k": person_terms.get(pid, [])[:KEYWORDS]}
        row = space.row_of.get(pid) if space is not None else None
        if row is not None:
            entry["v"] = _vector(space.space.vectors[row])
        people_details[sid[pid]] = entry
    orgs_details: dict[str, dict[str, Any]] = {}
    for o in orgs:
        entry = {"k": org_terms.get(o["id"], [])[:KEYWORDS]}
        vector = space.org_vector(o["id"]) if space is not None else None
        if vector is not None:
            entry["v"] = _vector(vector)
        orgs_details[oid[o["id"]]] = entry
    users: dict[str, list[int]] = {}
    if space is not None:
        say(0.65, "who uses each keyword")
        for k, term in enumerate(kw_core["term"]):
            col = space.space.column.get(term.strip().casefold())
            if col is None:
                continue
            rows, share = space.space.users_of(col)
            at = space.at[rows]
            keep = at >= 0
            mine = site_of_bundle[at[keep]]
            share = np.asarray(share)[keep]
            keep = mine >= 0
            mine, share = mine[keep], share[keep]
            order = np.lexsort((mine, -share))[:KEYWORD_USERS]
            flat = np.empty(2 * len(order), np.int64)
            flat[0::2] = mine[order]
            flat[1::2] = np.maximum(1, np.round(share[order] * 1000))
            users[str(k)] = [int(len(mine)), *flat.tolist()]

    # ── who writes with whom ──
    say(0.75, "co-authors")
    links = _links(project, pids, orgs, [lv["id"] for lv in levels])

    # ── texts, on request ──
    say(0.9, "texts")
    texts_part = SiteTexts.of(project, sid, texts)

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
        "years": extras.get("years") or {},
        "names": bool(names),
        "names_projected": bool(names_projected),
        "texts": texts,
        "has": {
            "vectors": space is not None,
            "users": bool(users),
            "links": links is not None,
        },
    }
    counts = {
        "people": len(mapped),
        "projected": len(projected),
        "organisations": len(orgs),
        "keywords": len(keywords),
        "themes": len(nodes),
        "texts": texts_part.count if texts_part is not None else 0,
        "abstracts": 0,  # counted as the site is written
    }
    return SiteData(
        core=core,
        people=people_details,
        orgs=orgs_details,
        keywords=users,
        links=links,
        texts=texts_part,
        counts=counts,
    )
