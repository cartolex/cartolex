# SPDX-License-Identifier: MIT
"""A page opened from ``file://`` that mounts the shared atlas from its classic script, as
the offline site does: a small synthetic bundle, a sparse list of co-author links whose
rings `ringsOf` computes in the browser, a translator over the app's catalogue. No server.

Variants: a map version in three dimensions (`dimensions=3`), two built versions (a flat
one, pinned, and one in 3D, read through the source's `version`), the time windows of
each person (the trajectory), the French catalogue, and a large bundle generated in the
browser (`generate=`: people, texts, keywords, organisations) for the frame budget."""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

GENERATE = """
(function () {
  // a large synthetic bundle, made here rather than shipped as a file of tens of megabytes
  const size = window.FAKE_GENERATE;
  if (!size) return;
  let seed = 7;
  const rnd = () => {
    seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const gauss = () => Math.sqrt(-2 * Math.log(rnd() + 1e-12)) * Math.cos(2 * Math.PI * rnd());
  const centre = (i) => [Math.cos(i * 0.8) * 6, Math.sin(i * 0.8) * 6, ((i % 3) - 1) * 5];
  const nodes = [];
  for (let i = 0; i < 8; i += 1) {
    nodes.push({ id: `t${i}`, parent: null, level: 1, order: i, names: { en: `theme ${i}` }, weight: 1, share: 0.125 });
  }
  const keywords = [];
  for (let k = 0; k < size.keywords; k += 1) {
    const c = centre(k % 8);
    keywords.push({ term: `term ${k}`, x: c[0] + gauss(), y: c[1] + gauss(), z: c[2] + gauss(), node: `t${k % 8}`,
      weight: rnd() / 50 });
  }
  const people = [];
  const extra = {};
  for (let p = 0; p < size.people; p += 1) {
    const c = centre(p % 8);
    people.push({ person_id: `p${p}`, name: `Person ${p}`, unit: '', x: c[0] + gauss() * 1.3, y: c[1] + gauss() * 1.3,
      z: c[2] + gauss() * 1.3, shares: [{ [`t${p % 8}`]: 1 }] });
    extra[`p${p}`] = { role: 'mapped', orgs: [`o${p % size.orgs}`] };
  }
  const orgs = [];
  for (let o = 0; o < size.orgs; o += 1) {
    const c = centre(o % 8);
    orgs.push({ id: `o${o}`, name: `Organisation ${o}`, acronym: `O${o}`, level: 'lab', parents: [],
      x: c[0] + gauss() * 0.5, y: c[1] + gauss() * 0.5, z: c[2] + gauss() * 0.5, location: null });
  }
  const n = size.texts;
  const texts = { id: [], title: [], year: [], x: [], y: [], z: [], by: [], terms: [], people: [], total: n, sampled: false };
  for (let i = 0; i < n; i += 1) {
    const c = centre(i % 8);
    texts.id.push(`w${i}`);
    texts.title.push(`Text ${i}`);
    texts.year.push(2000 + (i % 20));
    texts.x.push(c[0] + gauss() * 1.5);
    texts.y.push(c[1] + gauss() * 1.5);
    texts.z.push(c[2] + gauss() * 1.5);
    texts.by.push('keywords');
    texts.terms.push([i % size.keywords]);
    texts.people.push([`p${i % size.people}`]);
  }
  window.FAKE_TEXTS = texts;
  window.FAKE_LINKS = [];
  window.FAKE_BUNDLE = { format: 'cartolex-atlas/3', available: true, map_version: 'v3d', pinned_version: 'v3d',
    dimensions: 3, versions: [{ id: 'v3d', dimensions: 3, method: 'umap', note: '', pinned: true }],
    nodes, people, keywords, organisations: orgs, organisation_levels: [{ id: 'lab', names: { en: 'Lab' } }],
    people_extra: extra, columns: [], overlays: [],
    bounds: { xmin: -11, xmax: 11, ymin: -11, ymax: 11, zmin: -10, zmax: 10 } };
})();
"""

MAIN = """
(function () {
  const A = window.CartolexAtlas;
  const b = window.FAKE_BUNDLE;
  // the other built versions: their places over the first one's (the static site's way)
  const layouts = window.FAKE_LAYOUTS || {};
  const versionOf = (version) => {
    const over = version && layouts[version];
    if (!over) return b;
    const at = (list, places) => list.map((item, i) => ({ ...item, ...places[i] }));
    return { ...b, map_version: version, dimensions: over.dimensions, bounds: over.bounds,
      people: at(b.people, over.people), keywords: at(b.keywords, over.keywords),
      organisations: at(b.organisations, over.organisations) };
  };
  window.FAKE_READS = [];
  const links = new Map();
  for (const [a, c, n] of window.FAKE_LINKS) {
    for (const [x, y] of [[a, c], [c, a]]) {
      if (!links.has(x)) links.set(x, []);
      links.get(x).push([y, n]);
    }
  }
  const names = new Map(b.people.map((p) => [p.person_id, p.name]));
  const graph = { neighbours: (id) => links.get(id) || [], placed: (id) => (names.has(id) ? 'map' : null),
    describe: (ids) => ids.map((id) => ({ id, name: names.get(id) || null })) };
  const source = {
    layouts: true,
    bundle: ({ version } = {}) => {
      window.FAKE_READS.push(['bundle', version || '']);
      return Promise.resolve(versionOf(version));
    },
    coauthors: ({ id, circle, pages }) => Promise.resolve(A.ringsOf(graph, id, circle, pages)) };
  if (window.FAKE_TEXTS) source.texts = () => Promise.resolve(window.FAKE_TEXTS);
  if (window.FAKE_WINDOWS) {
    source.windows = ({ person, version } = {}) => {
      window.FAKE_READS.push(['windows', person || '', version || '']);
      const w = window.FAKE_WINDOWS;
      const keep = w.person.map((p, k) => k).filter((k) => !person || b.people[w.person[k]].person_id === person);
      const pick = (col) => (w[col] ? keep.map((k) => w[col][k]) : undefined);
      const z = versionOf(version).dimensions === 3 ? pick('z') : undefined;
      return Promise.resolve({ person: pick('person'), start: pick('start'), end: pick('end'), texts: pick('texts'),
        x: pick('x'), y: pick('y'), ...(z ? { z } : {}), top: pick('top') });
    };
  }
  const kept = {};
  const lang = document.documentElement.lang || 'en';
  const host = { t: A.createTranslator(window.FAKE_MESSAGES, null, lang), locale: lang, title: 'Synthetic field',
    prefs: { get: (k) => kept[k], set: (k, v) => { kept[k] = v; } },
    address: { read: () => new URLSearchParams(window.location.hash.slice(1)),
      write: (q) => window.history.replaceState(null, '', `#${q.toString()}`) } };
  window.ATLAS = A.mountAtlas(document.getElementById('root'), { source, host });
})();
"""

INDEX = """<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><title>Atlas</title>
<link rel="stylesheet" href="tokens.css"><link rel="stylesheet" href="atlas.css"><link rel="stylesheet" href="page.css">
</head><body><div id="root" class="root"></div>
<script src="atlas.js"></script><script src="messages.js"></script><script src="data.js"></script>
<script src="generate.js"></script><script src="main.js"></script></body></html>"""


def _depth(i: int) -> float:
    """The z of theme *i*'s centre on a map in three dimensions: three layers apart."""
    return ((i % 3) - 1) * 5.0


def synthetic_bundle(
    people: int, keywords: int, orgs: int, seed: int = 0, dimensions: int = 2
) -> tuple[dict, list]:
    """An atlas bundle of *people*, *keywords* and *orgs* (one level), 8 themes of 3 topics,
    and co-author links inside each organisation; with *dimensions* 3, every place has a z
    (the themes three layers apart) and the bundle says so."""
    rnd = random.Random(seed)
    space = dimensions == 3
    nodes = []
    for i in range(8):
        cx, cy = math.cos(i * 0.8) * 6, math.sin(i * 0.8) * 6
        nodes.append(
            {
                "id": f"t{i}",
                "parent": None,
                "level": 1,
                "order": i,
                "names": {"en": f"theme {i}"},
                "weight": 1 + i / 10,
                "share": 0.125,
                "x": cx,
                "y": cy,
            }
        )
        nodes += [
            {
                "id": f"t{i}_{j}",
                "parent": f"t{i}",
                "level": 2,
                "order": j,
                "names": {"en": f"topic {i}.{j}"},
                "weight": 0.3 + j / 10,
                "share": None,
            }
            for j in range(3)
        ]
    kws = []
    for k in range(keywords):
        i, j = k % 8, (k // 8) % 3
        kws.append(
            {
                "term": f"term {k}",
                "x": math.cos(i * 0.8) * 6 + rnd.gauss(0, 1),
                "y": math.sin(i * 0.8) * 6 + rnd.gauss(0, 1),
                "node": f"t{i}_{j}",
                "weight": rnd.random() / 50,
            }
        )
        if space:
            kws[-1]["z"] = _depth(i) + rnd.gauss(0, 1)
    org_list = [
        {
            "id": f"o{k}",
            "name": f"Organisation {k}",
            "acronym": f"O{k}",
            "level": "lab",
            "parents": [],
            "x": None,
            "y": None,
            "location": None,
        }
        for k in range(orgs)
    ]
    ppl, extra, sums, members = [], {}, {}, {}
    for p in range(people):
        i, j = p % 8, p % 3
        x, y = math.cos(i * 0.8) * 6 + rnd.gauss(0, 1.3), math.sin(i * 0.8) * 6 + rnd.gauss(0, 1.3)
        z = _depth(i) + rnd.gauss(0, 1.3)
        pid = f"p{p}"
        ppl.append(
            {
                "person_id": pid,
                "name": f"Person {p}",
                "unit": "",
                "x": x,
                "y": y,
                "shares": [{f"t{i}": 0.75, f"t{(i + 1) % 8}": 0.25}, {f"t{i}_{j}": 1.0}],
            }
        )
        if space:
            ppl[-1]["z"] = z
        org = f"o{p % orgs}"
        extra[pid] = {
            "role": "mapped",
            "columns": {"site": "north" if p % 2 else "south"},
            "orgs": [org],
        }
        s = sums.setdefault(org, [0.0, 0.0, 0, 0.0])
        s[0] += x
        s[1] += y
        s[2] += 1
        s[3] += z
        members.setdefault(org, []).append(pid)
    for o in org_list:
        s = sums.get(o["id"])
        if s:
            o["x"], o["y"] = s[0] / s[2], s[1] / s[2]
            if space:
                o["z"] = s[3] / s[2]
    links = []
    for ids in members.values():
        for a in ids:
            links.append([a, ids[rnd.randrange(len(ids))], 1 + rnd.randrange(4)])
    links = [lk for lk in links if lk[0] != lk[1]]
    bundle = {
        "format": "cartolex-atlas/3",
        "available": True,
        "map_version": "v1",
        "nodes": nodes,
        "people": ppl,
        "keywords": kws,
        "organisations": org_list,
        "organisation_levels": [{"id": "lab", "names": {"en": "Lab"}}],
        "people_extra": extra,
        "columns": [
            {
                "column": "site",
                "values": [
                    {"value": "north", "count": people // 2},
                    {"value": "south", "count": people - people // 2},
                ],
            }
        ],
        "bounds": {"xmin": -10, "xmax": 10, "ymin": -10, "ymax": 10},
        "overlays": [],
    }
    if space:
        bundle["dimensions"] = 3
        bundle["bounds"].update(zmin=-9, zmax=9)
    return bundle, links


def two_versions(bundle: dict, people: int, keywords: int, orgs: int) -> dict:
    """Make *bundle* (flat) the pinned one of two built versions; answers the places of the
    other one, in three dimensions (`FAKE_LAYOUTS`), in the bundle's row order."""
    other, _ = synthetic_bundle(people, keywords, orgs, seed=1, dimensions=3)
    bundle["pinned_version"] = bundle["map_version"]
    bundle["versions"] = [
        {
            "id": bundle["map_version"],
            "dimensions": 2,
            "method": "umap",
            "note": "flat",
            "pinned": True,
        },
        {"id": "v3d", "dimensions": 3, "method": "umap", "note": "", "pinned": False},
    ]

    def places(items: list) -> list:
        return [{k: item.get(k) for k in ("x", "y", "z")} for item in items]

    return {
        "v3d": {
            "dimensions": 3,
            "bounds": other["bounds"],
            "people": places(other["people"]),
            "keywords": places(other["keywords"]),
            "organisations": places(other["organisations"]),
        }
    }


def time_windows(bundle: dict, per_person: int = 4, seed: int = 2) -> dict:
    """The time windows of every person, columnar (`GET /api/atlas/windows`): *per_person*
    periods each, drifting from their place; `z` too, for a 3D version."""
    rnd = random.Random(seed)
    cols: dict[str, list] = {
        k: [] for k in ("person", "start", "end", "texts", "x", "y", "z", "top")
    }
    for i, p in enumerate(bundle["people"]):
        for k in range(per_person):
            cols["person"].append(i)
            cols["start"].append(2000 + 5 * k)
            cols["end"].append(2004 + 5 * k)
            cols["texts"].append(1 + rnd.randrange(5))
            cols["x"].append(p["x"] + (k - 1.5) * 0.8 + rnd.gauss(0, 0.2))
            cols["y"].append(p["y"] + rnd.gauss(0, 0.5))
            cols["z"].append(p.get("z", 0) + (k - 1.5) * 0.6)
            cols["top"].append(None)
    bundle["windows"] = len(cols["person"])
    bundle["window_years"] = {"min": 2000, "max": 2004 + 5 * (per_person - 1)}
    return cols


def write_atlas_page(
    folder: Path,
    *,
    people: int = 60,
    keywords: int = 120,
    orgs: int = 6,
    dimensions: int = 2,
    versions: bool = False,
    windows: bool = False,
    lang: str = "en",
    generate: dict | None = None,
) -> Path:
    """Write the page and its scripts in *folder*; answers its ``index.html``. *dimensions*
    3: a map version in three dimensions; *versions*: two built versions (flat, pinned, and
    3D); *windows*: every person's time windows; *lang*: the catalogue's language;
    *generate*: `{people, texts, keywords, orgs}`, a 3D bundle made in the browser."""
    from cartolex.app.static_files import ATLAS_MODULES, PACKAGE_STATIC, classic_script

    folder.mkdir(parents=True, exist_ok=True)
    data = ""
    if generate:
        data = f"window.FAKE_GENERATE = {json.dumps(generate)};\n"
    else:
        bundle, links = synthetic_bundle(people, keywords, orgs, dimensions=dimensions)
        layouts = two_versions(bundle, people, keywords, orgs) if versions else {}
        cols = time_windows(bundle) if windows else None
        data = (
            f"window.FAKE_BUNDLE = {json.dumps(bundle)};\nwindow.FAKE_LINKS = {json.dumps(links)};\n"
            f"window.FAKE_LAYOUTS = {json.dumps(layouts)};\nwindow.FAKE_WINDOWS = {json.dumps(cols)};\n"
        )
    messages = json.loads((PACKAGE_STATIC / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
    files = {
        "atlas.js": classic_script([PACKAGE_STATIC / m for m in ATLAS_MODULES], "CartolexAtlas"),
        "messages.js": "window.FAKE_MESSAGES = "
        + json.dumps({k: v for k, v in messages.items() if k.startswith("atlas.")})
        + ";\n",
        "data.js": data,
        "generate.js": GENERATE,
        "main.js": MAIN,
        "tokens.css": (PACKAGE_STATIC / "css" / "tokens.css").read_text(encoding="utf-8"),
        "atlas.css": (PACKAGE_STATIC / "css" / "atlas.css").read_text(encoding="utf-8"),
        "page.css": "html, body { margin: 0; height: 100%; background: var(--cx-bg); } .root { height: 100vh; }\n",
        "index.html": INDEX.replace("{lang}", lang),
    }
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    return folder / "index.html"
