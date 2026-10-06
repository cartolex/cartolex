# SPDX-License-Identifier: MIT
"""A page opened from ``file://`` that mounts the shared atlas from its classic script, as
the offline site does: a small synthetic bundle, a sparse list of co-author links whose
rings `ringsOf` computes in the browser, a translator over the app's catalogue. No server."""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

MAIN = """
(function () {
  const A = window.CartolexAtlas;
  const b = window.FAKE_BUNDLE;
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
  const source = { bundle: () => Promise.resolve(b),
    coauthors: ({ id, circle, pages }) => Promise.resolve(A.ringsOf(graph, id, circle, pages)) };
  const kept = {};
  const host = { t: A.createTranslator(window.FAKE_MESSAGES, null, 'en'), locale: 'en', title: 'Synthetic field',
    prefs: { get: (k) => kept[k], set: (k, v) => { kept[k] = v; } },
    address: { read: () => new URLSearchParams(window.location.hash.slice(1)),
      write: (q) => window.history.replaceState(null, '', `#${q.toString()}`) } };
  window.ATLAS = A.mountAtlas(document.getElementById('root'), { source, host });
})();
"""

INDEX = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Atlas</title>
<link rel="stylesheet" href="tokens.css"><link rel="stylesheet" href="atlas.css"><link rel="stylesheet" href="page.css">
</head><body><div id="root" class="root"></div>
<script src="atlas.js"></script><script src="messages.js"></script><script src="data.js"></script>
<script src="main.js"></script></body></html>"""


def synthetic_bundle(people: int, keywords: int, orgs: int, seed: int = 0) -> tuple[dict, list]:
    """An atlas bundle of *people*, *keywords* and *orgs* (one level), 8 themes of 3 topics,
    and co-author links inside each organisation."""
    rnd = random.Random(seed)
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
        org = f"o{p % orgs}"
        extra[pid] = {
            "role": "mapped",
            "columns": {"site": "north" if p % 2 else "south"},
            "orgs": [org],
        }
        s = sums.setdefault(org, [0.0, 0.0, 0])
        s[0] += x
        s[1] += y
        s[2] += 1
        members.setdefault(org, []).append(pid)
    for o in org_list:
        s = sums.get(o["id"])
        if s:
            o["x"], o["y"] = s[0] / s[2], s[1] / s[2]
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
    return bundle, links


def write_atlas_page(folder: Path, *, people: int = 60, keywords: int = 120, orgs: int = 6) -> Path:
    """Write the page and its scripts in *folder*; answers its ``index.html``."""
    from cartolex.app.static_files import ATLAS_MODULES, PACKAGE_STATIC, classic_script

    folder.mkdir(parents=True, exist_ok=True)
    bundle, links = synthetic_bundle(people, keywords, orgs)
    en = json.loads((PACKAGE_STATIC / "i18n" / "en.json").read_text(encoding="utf-8"))
    files = {
        "atlas.js": classic_script([PACKAGE_STATIC / m for m in ATLAS_MODULES], "CartolexAtlas"),
        "messages.js": "window.FAKE_MESSAGES = "
        + json.dumps({k: v for k, v in en.items() if k.startswith("atlas.")})
        + ";\n",
        "data.js": f"window.FAKE_BUNDLE = {json.dumps(bundle)};\nwindow.FAKE_LINKS = {json.dumps(links)};\n",
        "main.js": MAIN,
        "tokens.css": (PACKAGE_STATIC / "css" / "tokens.css").read_text(encoding="utf-8"),
        "atlas.css": (PACKAGE_STATIC / "css" / "atlas.css").read_text(encoding="utf-8"),
        "page.css": "html, body { margin: 0; height: 100%; background: var(--cx-bg); } .root { height: 100vh; }\n",
        "index.html": INDEX,
    }
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    return folder / "index.html"
