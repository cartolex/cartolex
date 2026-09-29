// SPDX-License-Identifier: MIT
/**
 * The scene the MapFrame draws, from the indexed bundle and the page's
 * state: one layer per kind shown (each with its own symbol), coloured by
 * top-level theme; the filters and the period hide what they leave out (rank
 * 2); the keywords and texts are revealed by the zoom (their ranks); the
 * selection is highlighted, its keywords too, a person's time windows joined
 * by a line; regions span the keywords of organisations, people or texts;
 * labels name the themes, then organisations and people as the zoom grows,
 * and the selection always. The world view places organisations at their
 * address, on a graticule.
 */
import { convexHull } from '../../components/index.js';
import { PALETTE, NEUTRAL, matching, orgName, periodOf, themeName, under } from './model.js';
import { SHAPE_OF } from './state.js';

/** The most regions of people or texts drawn at once. */
export const MAX_REGIONS = 60;
const HIDDEN = 2;

const RADIUS = { people: 4, keywords: 3, organisations: 5.5, texts: 3, projected: 4.5, windows: 3.5 };
const ALPHA = { people: 0.95, keywords: 0.75, organisations: 0.95, texts: 0.7, projected: 0.95, windows: 0.8 };

function layer(id, n) {
  return {
    id,
    shape: SHAPE_OF[id],
    radius: RADIUS[id],
    alpha: ALPHA[id],
    palette: PALETTE,
    x: new Float32Array(n),
    y: new Float32Array(n),
    color: new Uint16Array(n),
    rank: new Float32Array(n),
    highlight: new Uint8Array(n),
    highlightCount: 0,
    ref: new Uint32Array(n), // the index of each point's item in its list
    shown: 0,
  };
}

function trimmed(n, fill) {
  const out = {};
  for (const [k, v] of Object.entries(fill)) {
    out[k] = v && v.subarray && k !== 'palette' ? v.subarray(0, n) : v;
  }
  return out;
}

/** The hull of keywords (by index), without the farthest fifth when there are many. */
export function keywordRegion(index, terms) {
  const pts = [];
  for (const k of terms) {
    const kw = index.keywords[k];
    if (kw && kw.x !== null && kw.y !== null) pts.push([kw.x, kw.y]);
  }
  if (pts.length < 3) return null;
  let kept = pts;
  if (pts.length > 8) {
    const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
    const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
    kept = pts.map((p) => [p, (p[0] - cx) ** 2 + (p[1] - cy) ** 2]).sort((a, b) => a[1] - b[1])
      .slice(0, Math.ceil(pts.length * 0.8)).map((e) => e[0]);
  }
  const polygon = convexHull(kept.map((p) => p[0]), kept.map((p) => p[1]));
  return polygon.length >= 6 ? polygon : null;
}

/** What the selection lights up: sets of item indexes per kind, and its keywords. */
function selection(index, state, texts, sets) {
  const lit = { people: new Set(), keywords: new Set(), organisations: new Set(), texts: new Set(),
    projected: new Set(), windows: null, path: null };
  const sel = state.sel;
  if (!sel) return lit;
  const termIndexes = (list) => (list || []).map((t) => index.byTerm.get(t)).filter((i) => i !== undefined);
  if (sel.kind === 'person' && index.byPerson.has(sel.id)) {
    lit.people.add(index.byPerson.get(sel.id));
    for (const k of termIndexes(sets.get(`person:${sel.id}`))) lit.keywords.add(k);
    lit.windows = sel.id;
    lit.path = sel.id;
    if (texts) texts.people.forEach((ps, i) => { if (ps.includes(sel.id)) lit.texts.add(i); });
  } else if (sel.kind === 'organisation' && index.byOrg.has(sel.id)) {
    lit.organisations.add(index.byOrg.get(sel.id));
    for (const i of index.members.get(sel.id) || []) lit.people.add(i);
    for (const k of termIndexes(sets.get(`organisation:${sel.id}`))) lit.keywords.add(k);
  } else if (sel.kind === 'text' && texts) {
    const i = texts.id.indexOf(sel.id);
    if (i >= 0) {
      lit.texts.add(i);
      for (const k of texts.terms[i]) lit.keywords.add(k);
      for (const pid of texts.people[i]) if (index.byPerson.has(pid)) lit.people.add(index.byPerson.get(pid));
    }
  } else if (sel.kind === 'theme' && index.nodes.has(sel.id)) {
    const nodes = under(index, sel.id);
    const level = index.nodes.get(sel.id).level;
    index.keywords.forEach((k, i) => { if (k.node && nodes.has(k.node)) lit.keywords.add(i); });
    index.people.forEach((p, i) => {
      const shares = p.shares && p.shares[level - 1];
      if (shares && (shares[sel.id] || 0) >= 0.2) lit.people.add(i);
    });
    index.orgs.forEach((o, i) => { if ((index.orgShares.get(o.id)[sel.id] || 0) >= 0.2) lit.organisations.add(i); });
  } else if (sel.kind === 'keyword' && index.byTerm.has(sel.id)) {
    lit.keywords.add(index.byTerm.get(sel.id));
  } else if (sel.kind === 'projected') {
    const i = index.projected.findIndex((o) => o.person_id === sel.id);
    if (i >= 0) lit.projected.add(i);
  }
  return lit;
}

/** Ranks from weights: 0 for the heaviest, towards 1 for the lightest. */
function ranksByWeight(weights) {
  const order = weights.map((w, i) => [w || 0, i]).sort((a, b) => b[0] - a[0]);
  const out = new Float32Array(weights.length);
  order.forEach(([, i], r) => { out[i] = r / Math.max(1, weights.length); });
  return out;
}

/**
 * The scene of the map view. *texts* is the columnar texts answer (or null
 * until read), *sets* the keywords of people and organisations read so far
 * (`kind:id` → terms). Answers `{scene, counts, notes}`: `counts` per kind
 * (`shown`, `total`), `notes` what the page should say (regions capped).
 */
export function mapScene(index, state, { texts = null, sets = new Map(), locale = 'en' } = {}) {
  const mask = matching(index, state);
  const period = periodOf(index, state);
  const inPeriod = (start, end) => !period || (end >= period[0] && start <= period[1]);
  const lit = selection(index, state, texts, sets);
  const show = new Set(state.show);
  const layers = [];
  const regions = [];
  const lines = [];
  const labels = [];
  const counts = {};
  const notes = [];
  const regionsOf = state.as === 'regions';

  // Time windows: each person's places per period of years, in the period; the selected
  // person's are joined by a line in time order.
  if (show.has('windows') || lit.path) {
    const all = [];
    for (const [pid, list] of index.windows) {
      const i = index.byPerson.get(pid);
      if (i === undefined || !mask[i]) continue;
      if (!show.has('windows') && pid !== lit.path) continue;
      for (const w of list) if (inPeriod(w.start, w.end)) all.push([w, pid]);
    }
    const L = layer('windows', all.length);
    all.forEach(([w, pid], k) => {
      L.x[k] = w.x;
      L.y[k] = w.y;
      L.color[k] = index.colourOf(index.personTop[index.byPerson.get(pid)]);
      if (pid === lit.path) {
        L.highlight[k] = 1;
        L.highlightCount += 1;
      }
      L.ref[k] = k;
    });
    L.items = all.map(([w]) => w);
    L.shown = all.length;
    layers.push(L);
    counts.windows = { shown: all.length, total: (index.atlas.trajectories || []).length };
    if (lit.path) {
      const path = all.filter(([, pid]) => pid === lit.path).map(([w]) => w);
      if (path.length > 1) {
        const x = new Float32Array((path.length - 1) * 2);
        const y = new Float32Array((path.length - 1) * 2);
        for (let k = 0; k + 1 < path.length; k += 1) {
          x[2 * k] = path[k].x;
          y[2 * k] = path[k].y;
          x[2 * k + 1] = path[k + 1].x;
          y[2 * k + 1] = path[k + 1].y;
        }
        lines.push({ id: 'path', x, y, color: '--cx-accent', alpha: 0.9, width: 2 });
        path.forEach((w, k) => labels.push({ x: w.x, y: w.y, text: String(w.start), offset: 12, strong: k === 0 || k === path.length - 1 }));
      }
    }
  }

  // Texts: placed by their keywords (or their authors), in the period.
  if (show.has('texts') && texts) {
    const n = texts.id.length;
    const L = layer('texts', n);
    const dense = n > 2000;
    let shown = 0;
    const visible = [];
    for (let i = 0; i < n; i += 1) {
      const year = texts.year[i];
      const keep = !period || year === null || (year >= period[0] && year <= period[1]);
      L.x[i] = texts.x[i];
      L.y[i] = texts.y[i];
      const first = texts.terms[i].length ? index.keywords[texts.terms[i][0]] : null;
      L.color[i] = first && first.node ? index.colourOf(first.node) : NEUTRAL;
      // An even spread of ranks: a zoom of 2 shows four times as many texts.
      L.rank[i] = keep ? ((i * 0.6180339887) % 1) : HIDDEN;
      if (lit.texts.has(i)) {
        L.highlight[i] = 1;
        L.highlightCount += 1;
      }
      L.ref[i] = i;
      if (keep) {
        shown += 1;
        visible.push(i);
      }
    }
    L.detail = dense ? 0.25 : 1;
    L.shown = shown;
    counts.texts = { shown, total: n };
    if (regionsOf && visible.length <= MAX_REGIONS) {
      for (const i of visible) {
        const polygon = keywordRegion(index, texts.terms[i]);
        if (polygon) regions.push({ id: `text:${texts.id[i]}`, polygon, color: PALETTE[L.color[i]], alpha: 0.08 });
      }
      L.alpha = 0.5;
    } else if (regionsOf) {
      notes.push({ key: 'map.note.regions_cap', kind: 'texts', count: visible.length });
    }
    layers.push(L);
  }

  // Keywords: coloured by their top-level theme, the heaviest first as the zoom grows.
  if (show.has('keywords')) {
    const ks = index.keywords;
    const L = layer('keywords', ks.length);
    let k = 0;
    for (let i = 0; i < ks.length; i += 1) {
      if (ks[i].x === null || ks[i].y === null) continue;
      L.x[k] = ks[i].x;
      L.y[k] = ks[i].y;
      L.color[k] = index.colourOf(ks[i].node);
      L.ref[k] = i;
      if (lit.keywords.has(i)) {
        L.highlight[k] = 1;
        L.highlightCount += 1;
      }
      k += 1;
    }
    const weights = Array.from(L.ref.subarray(0, k), (i) => ks[i].weight || 0);
    const out = trimmed(k, L);
    out.rank = ranksByWeight(weights);
    out.detail = k > 400 ? 0.35 : 1;
    out.shown = k;
    out.items = ks;
    layers.push(out);
    counts.keywords = { shown: k, total: ks.length };
  }

  // People: hidden by the filters; as regions when few enough.
  const peopleShown = [];
  if (show.has('people')) {
    const ps = index.people;
    const L = layer('people', ps.length);
    let k = 0;
    for (let i = 0; i < ps.length; i += 1) {
      if (ps[i].x === null || ps[i].y === null) continue;
      L.x[k] = ps[i].x;
      L.y[k] = ps[i].y;
      L.color[k] = index.colourOf(index.personTop[i]);
      L.rank[k] = mask[i] ? 0 : HIDDEN;
      L.ref[k] = i;
      if (mask[i]) peopleShown.push(i);
      if (lit.people.has(i)) {
        L.highlight[k] = 1;
        L.highlightCount += 1;
      }
      k += 1;
    }
    const out = trimmed(k, L);
    out.items = ps;
    out.shown = peopleShown.length;
    counts.people = { shown: peopleShown.length, total: k };
    if (regionsOf && peopleShown.length <= MAX_REGIONS) {
      for (const i of peopleShown) {
        const terms = sets.get(`person:${ps[i].person_id}`);
        if (!terms) continue;
        const polygon = keywordRegion(index, terms.slice(0, 20).map((t) => index.byTerm.get(t)).filter((t) => t !== undefined));
        if (polygon) regions.push({ id: `person:${ps[i].person_id}`, polygon, color: PALETTE[index.colourOf(index.personTop[i])], alpha: 0.08 });
      }
    } else if (regionsOf) {
      notes.push({ key: 'map.note.regions_cap', kind: 'people', count: peopleShown.length });
    }
    layers.push(out);
    for (const i of peopleShown) {
      labels.push({ x: ps[i].x, y: ps[i].y, text: ps[i].name, offset: 12, minZoom: ps.length > 400 ? 8 : 3 });
    }
  }

  // Projected people: placed on the finished map, never moving it.
  if (show.has('projected')) {
    const ps = index.projected;
    const L = layer('projected', ps.length);
    ps.forEach((p, i) => {
      L.x[i] = p.x;
      L.y[i] = p.y;
      L.color[i] = index.colourOf(largest0(p.shares));
      L.ref[i] = i;
      if (lit.projected.has(i)) {
        L.highlight[i] = 1;
        L.highlightCount += 1;
      }
    });
    L.items = ps;
    L.shown = ps.length;
    layers.push(L);
    counts.projected = { shown: ps.length, total: ps.length };
  }

  // Organisations of one level: at the mean of their current members, or as regions.
  const orgLevel = state.org || (index.levels[0] && index.levels[0].id) || '';
  if (show.has('organisations')) {
    const list = [];
    index.orgs.forEach((o, i) => {
      if (o.level === orgLevel && o.x !== null && o.y !== null) list.push(i);
    });
    const L = layer('organisations', list.length);
    list.forEach((i, k) => {
      const o = index.orgs[i];
      L.x[k] = o.x;
      L.y[k] = o.y;
      L.color[k] = index.colourOf(index.orgTop[i]);
      L.ref[k] = i;
      if (lit.organisations.has(i)) {
        L.highlight[k] = 1;
        L.highlightCount += 1;
      }
      labels.push({ x: o.x, y: o.y, text: orgName(o), offset: 13, minZoom: list.length > 30 ? 2 : 1.2 });
      if (regionsOf) {
        const terms = sets.get(`organisation:${o.id}`);
        const polygon = terms && keywordRegion(index, terms.map((t) => index.byTerm.get(t)).filter((t) => t !== undefined));
        if (polygon) regions.push({ id: `organisation:${o.id}`, polygon, color: PALETTE[L.color[k]], alpha: 0.1 });
      }
    });
    L.items = index.orgs;
    L.shown = list.length;
    if (regionsOf) L.alpha = 0.6;
    layers.push(L);
    counts.organisations = { shown: list.length, total: index.orgs.filter((o) => o.level === orgLevel).length };
  }

  // Theme names at the centre of their keywords: the top level first, the next one with the zoom.
  const centres = new Map();
  for (const k of index.keywords) {
    if (!k.node || k.x === null) continue;
    let at = k.node;
    while (at && index.nodes.has(at)) {
      const c = centres.get(at) || [0, 0, 0];
      c[0] += k.x;
      c[1] += k.y;
      c[2] += 1;
      centres.set(at, c);
      at = index.nodes.get(at).parent;
    }
  }
  const themeLabels = [];
  for (const [id, c] of centres) {
    const node = index.nodes.get(id);
    if (!node || node.level > 2) continue;
    themeLabels.push({ x: c[0] / c[2], y: c[1] / c[2], text: themeName(index, id, locale),
      minZoom: node.level === 1 ? 0 : 2.5, weight: (node.weight || 0) + (node.level === 1 ? 1e9 : 0),
      strong: state.sel && state.sel.kind === 'theme' && state.sel.id === id });
  }
  themeLabels.sort((a, b) => b.weight - a.weight);
  const selected = selectionLabel(index, state, texts);
  return {
    scene: {
      layers,
      regions,
      lines,
      labels: [...(selected ? [selected] : []), ...themeLabels, ...labels],
      bounds: index.atlas.bounds,
    },
    counts,
    notes,
    lit,
  };
}

function largest0(shares) {
  let best = null;
  let value = 0;
  for (const [id, v] of Object.entries((shares && shares[0]) || {})) {
    if (v > value) {
      best = id;
      value = v;
    }
  }
  return best;
}

/** The label of the selection (always drawn, first). */
function selectionLabel(index, state, texts) {
  const sel = state.sel;
  if (!sel) return null;
  if (sel.kind === 'person' && index.byPerson.has(sel.id)) {
    const p = index.people[index.byPerson.get(sel.id)];
    return p.x === null ? null : { x: p.x, y: p.y, text: p.name, strong: true, offset: 14 };
  }
  if (sel.kind === 'organisation' && index.byOrg.has(sel.id)) {
    const o = index.orgs[index.byOrg.get(sel.id)];
    return o.x === null ? null : { x: o.x, y: o.y, text: orgName(o), strong: true, offset: 15 };
  }
  if (sel.kind === 'keyword' && index.byTerm.has(sel.id)) {
    const k = index.keywords[index.byTerm.get(sel.id)];
    return k.x === null ? null : { x: k.x, y: k.y, text: k.term, strong: true, offset: 13 };
  }
  if (sel.kind === 'text' && texts) {
    const i = texts.id.indexOf(sel.id);
    if (i >= 0) {
      const title = texts.title[i];
      return { x: texts.x[i], y: texts.y[i], text: title.length > 48 ? `${title.slice(0, 47)}…` : title, strong: true, offset: 13 };
    }
  }
  return null;
}

/** Graticule lines every *step* degrees, for the world view. */
function graticule(step) {
  const x = [];
  const y = [];
  for (let lon = -180; lon <= 180; lon += step) {
    x.push(lon, lon);
    y.push(-90, 90);
  }
  for (let lat = -90; lat <= 90; lat += step) {
    x.push(-180, 180);
    y.push(lat, lat);
  }
  return { id: 'graticule', x: Float32Array.from(x), y: Float32Array.from(y), color: '--cx-border', alpha: 0.35, width: 1 };
}

/** The land's outline (rings of `[lon0, lat0, lon1, lat1…]`) as line segments, kept per rings. */
const outlines = new WeakMap();
function outline(rings) {
  if (!rings) return null;
  if (!outlines.has(rings)) {
    const x = [];
    const y = [];
    for (const ring of rings) {
      for (let k = 0; k + 3 < ring.length; k += 2) {
        x.push(ring[k], ring[k + 2]);
        y.push(ring[k + 1], ring[k + 3]);
      }
    }
    outlines.set(rings, { id: 'land', x: Float32Array.from(x), y: Float32Array.from(y), color: '--cx-border', alpha: 0.8, width: 1 });
  }
  return outlines.get(rings);
}

/**
 * The world view: organisations of the level at their address (longitude,
 * latitude; an equirectangular projection) on a graticule and the outline of
 * the land (*land*: its rings, when read), sized alike, coloured by the
 * top-level theme of their members.
 */
export function worldScene(index, state, land = null) {
  const orgLevel = state.org || (index.levels[0] && index.levels[0].id) || '';
  const list = [];
  index.orgs.forEach((o, i) => {
    if (o.level === orgLevel && o.location) list.push(i);
  });
  const L = layer('organisations', list.length);
  let xmin = Infinity;
  let xmax = -Infinity;
  let ymin = Infinity;
  let ymax = -Infinity;
  const labels = [];
  const sel = state.sel;
  list.forEach((i, k) => {
    const o = index.orgs[i];
    L.x[k] = o.location.lon;
    L.y[k] = o.location.lat;
    xmin = Math.min(xmin, L.x[k]);
    xmax = Math.max(xmax, L.x[k]);
    ymin = Math.min(ymin, L.y[k]);
    ymax = Math.max(ymax, L.y[k]);
    L.color[k] = index.colourOf(index.orgTop[i]);
    L.ref[k] = i;
    const on = sel && ((sel.kind === 'organisation' && sel.id === o.id)
      || (sel.kind === 'theme' && (index.orgShares.get(o.id)[sel.id] || 0) >= 0.2)
      || (sel.kind === 'person' && (index.extra[sel.id] || { orgs: [] }).orgs.includes(o.id)));
    if (on) {
      L.highlight[k] = 1;
      L.highlightCount += 1;
    }
    labels.push({ x: o.location.lon, y: o.location.lat, text: orgName(o), offset: 14, minZoom: list.length > 30 ? 3 : 0,
      strong: Boolean(sel && sel.kind === 'organisation' && sel.id === o.id) });
  });
  L.items = index.orgs;
  L.radius = 6;
  L.shown = list.length;
  const pad = (lo, hi, span) => {
    const mid = (lo + hi) / 2;
    const half = Math.max((hi - lo) / 2 + span * 0.15, span / 2);
    return [mid - half, mid + half];
  };
  const [x0, x1] = list.length ? pad(xmin, xmax, 20) : [-180, 180];
  const [y0, y1] = list.length ? pad(ymin, ymax, 12) : [-90, 90];
  const span = Math.max(x1 - x0, y1 - y0);
  return {
    scene: { layers: [L], regions: [], labels,
      lines: [graticule(span > 120 ? 30 : span > 40 ? 10 : 5), ...(land ? [outline(land)] : [])],
      bounds: { xmin: Math.max(-180, x0), xmax: Math.min(180, x1), ymin: Math.max(-90, y0), ymax: Math.min(90, y1) } },
    counts: { organisations: { shown: list.length, total: index.orgs.filter((o) => o.level === orgLevel).length } },
    notes: [],
  };
}
