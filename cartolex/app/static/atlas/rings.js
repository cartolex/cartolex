// SPDX-License-Identifier: MIT
/**
 * The network around the focus: who a person writes with (and who those write with, up to
 * three rings), or the organisations an organisation's people write with. The answers come
 * from the source's `coauthors()` (the shape of `GET /api/atlas/coauthors`, see
 * `docs/dev/atlas.md`); `ringsOf()` makes that shape from a sparse list of links, for a host
 * that has no server (the offline site). On the map a link is a gentle arc: strong for the
 * first ring (wider for more texts together), thinner and fainter further out and towards
 * collaborators who are not mapped. Lines are drawn for real links only, never for a
 * similarity.
 */

/** The names of the rings after the first, in an answer. */
export const RING_KEYS = ['second', 'third'];
/** How many of each ring the atlas asks for (and lists, then « and N more »). */
export const RING_PAGE = [200, 100, 100];

/**
 * The rings around *id* in *graph* (`neighbours(id) → [[id, texts]…]`, `describe(ids) →
 * [{id, name, …}]`, `placed(id) → 'map' | 'projected' | null`, optional `stats(id) →
 * {texts, outside}`), *depth* rings (1 to 3), each paged by *pages* (`[[offset, limit]…]`):
 * the answer of the source's `coauthors()`.
 */
export function ringsOf(graph, id, depth = 1, pages = []) {
  const stats = (graph.stats && graph.stats(id)) || {};
  const out = { id, circle: depth, texts: stats.texts || 0, outside: stats.outside || 0 };
  const seen = new Set([id]);
  let frontier = [id];
  for (let d = 0; d < depth; d += 1) {
    const next = new Map();
    const edges = [];
    for (const a of frontier) {
      for (const [b, n] of graph.neighbours(a) || []) {
        if (seen.has(b)) continue;
        let e = next.get(b);
        if (!e) {
          e = { id: b, texts: 0, via: [] };
          next.set(b, e);
        }
        e.texts += n;
        if (d) e.via.push(a);
        edges.push([a, b, n]);
      }
    }
    for (const b of next.keys()) seen.add(b);
    const ring = [...next.values()].sort((x, y) => y.texts - x.texts || (x.id < y.id ? -1 : 1));
    const [offset, limit] = pages[d] || [0, 50];
    const rows = ring.slice(offset, offset + limit);
    const described = graph.describe(rows.map((r) => r.id));
    const items = rows.map((r, k) => ({ ...described[k], id: r.id, place: graph.placed(r.id), texts: r.texts,
      ...(d ? { paths: r.via.length, via: r.via } : {}) }));
    const drawn = (x) => x === id || Boolean(graph.placed(x));
    const lines = edges.filter(([a, b]) => drawn(a) && drawn(b)).sort((x, y) => y[2] - x[2])
      .map(([a, b, n]) => (d ? [a, b, n] : [b, n]));
    const part = { count: ring.length, placed: ring.filter((r) => graph.placed(r.id)).length, items, lines,
      partial: false, offset, limit };
    if (d === 0) Object.assign(out, part);
    else out[RING_KEYS[d - 1]] = part;
    frontier = ring.map((r) => r.id);
  }
  return out;
}

/** The rings of an answer as a list: `[{count, items, lines}]`, the first ring first. */
export function ringList(answer) {
  if (!answer) return [];
  const out = [answer];
  for (const key of RING_KEYS) if (answer[key]) out.push(answer[key]);
  return out.slice(0, answer.circle || out.length);
}

/**
 * A reader of the network around the focus, one answer per focus and number of rings, kept:
 * `get(sel, circle)` answers what is known now (null while it is read, `{error}` when it
 * failed) and reads it when it is not; *onAnswer* is called when an answer arrives.
 */
export function createRingReader(source, onAnswer) {
  const answers = new Map();
  let alive = true;
  return {
    available: Boolean(source && source.coauthors),
    get(sel, circle) {
      if (!this.available || !sel || circle < 1) return null;
      if (sel.kind !== 'person' && sel.kind !== 'organisation' && sel.kind !== 'projected') return null;
      const kind = sel.kind === 'organisation' ? 'organisation' : 'person';
      const key = `${kind}:${sel.id}:${circle}`;
      if (answers.has(key)) return answers.get(key);
      answers.set(key, null);
      Promise.resolve()
        .then(() => source.coauthors({ kind, id: sel.id, circle,
          pages: RING_PAGE.slice(0, circle).map((limit) => [0, limit]) }))
        .then((data) => (data && data.error ? { error: data.error } : data))
        .catch((error) => ({ error: { message: String(error && error.message ? error.message : error) } }))
        .then((data) => {
          if (!alive) return;
          answers.set(key, data || { error: {} });
          if (onAnswer) onAnswer();
        });
      return null;
    },
    clear() {
      answers.clear();
    },
    dispose() {
      alive = false;
    },
  };
}

/** The segments of a gentle arc from (x1, y1) to (x2, y2): a quadratic curve bent by a
 * fifth of its length to the left, as pairs of points appended to *xs*, *ys*. With *zs* (a
 * map in three dimensions), from z1 to z2: bent by a fifth of its length in space, across
 * the segment and level (its left on the flat map seen from the front), and *zs* gets the
 * z of each point. */
export function arcInto(xs, ys, x1, y1, x2, y2, steps = 12, zs = null, z1 = 0, z2 = 0) {
  let cx = (x1 + x2) / 2 - (y2 - y1) * 0.18;
  let cy = (y1 + y2) / 2 + (x2 - x1) * 0.18;
  const cz = (z1 + z2) / 2;
  if (zs) {
    const length = Math.hypot(x2 - x1, y2 - y1, z2 - z1);
    const flat = Math.hypot(x2 - x1, y2 - y1);
    const [ux, uy] = flat > length * 1e-3 ? [-(y2 - y1) / flat, (x2 - x1) / flat] : [1, 0];
    cx = (x1 + x2) / 2 + ux * length * 0.18;
    cy = (y1 + y2) / 2 + uy * length * 0.18;
  }
  let px = x1;
  let py = y1;
  let pz = z1;
  for (let k = 1; k <= steps; k += 1) {
    const s = k / steps;
    const u = 1 - s;
    const x = u * u * x1 + 2 * u * s * cx + s * s * x2;
    const y = u * u * y1 + 2 * u * s * cy + s * s * y2;
    xs.push(px, x);
    ys.push(py, y);
    if (zs) {
      const z = u * u * z1 + 2 * u * s * cz + s * s * z2;
      zs.push(pz, z);
      pz = z;
    }
    px = x;
    py = y;
  }
}

/** The look of a ring's links: width in pixels and opacity, the first ring first. */
const RING_STYLE = [[1, 0.75], [0.6, 0.34], [0.45, 0.18]];

/**
 * The scene's lines of the rings of *answer* around the focus at *from* (`{x, y}`): one line
 * per look (width and opacity); *placeOf(id)* gives where each partner is (`{x, y,
 * projected}`) or null. For organisations (*orgs*), the links are wider and in *color*. In
 * three dimensions (*space*), the places carry `z` and so do the lines.
 */
export function ringLines(answer, from, placeOf, { orgs = false, color = '--cx-text', space = false } = {}) {
  const groups = new Map();
  const add = (a, b, width, alpha) => {
    const w = Math.round(width * 2) / 2;
    const key = `${w}|${alpha}`;
    let g = groups.get(key);
    if (!g) {
      g = { xs: [], ys: [], zs: space ? [] : null, width: w, alpha };
      groups.set(key, g);
    }
    arcInto(g.xs, g.ys, a.x, a.y, b.x, b.y, 12, g.zs, a.z || 0, b.z || 0);
  };
  ringList(answer).forEach((ring, d) => {
    const [base, alpha] = RING_STYLE[d] || RING_STYLE[2];
    for (const line of ring.lines || []) {
      const [a, b, n] = d === 0 ? [null, line[0], line[1]] : line;
      const pa = d === 0 ? from : placeOf(a);
      const pb = placeOf(b);
      if (!pa || !pb) continue;
      const faint = pb.projected || pa.projected ? 0.55 : 1;
      const width = (orgs ? base * 1.6 : base) + (d === 0 ? Math.min(n, 12) * (orgs ? 0.15 : 0.4) : 0);
      add(pa, pb, Math.max(0.5, width), Math.round(alpha * faint * 100) / 100);
    }
  });
  return [...groups.values()].map((g, k) => ({ id: `ring-${k}`, x: Float32Array.from(g.xs), y: Float32Array.from(g.ys),
    ...(g.zs ? { z: Float32Array.from(g.zs) } : {}), color, alpha: g.alpha, width: g.width }));
}
