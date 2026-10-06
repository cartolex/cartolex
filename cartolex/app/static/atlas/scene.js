// SPDX-License-Identifier: MIT
/**
 * The scene the map draws (see `components/map/core.js`), from the indexed bundle, the
 * state and what was read beside it. The focus drives it: what belongs to the focus is lit
 * in its own colour (the focus itself ringed), the rest fades to a trace. Points are coloured
 * by their top-level theme in the scheme's colours (or keywords by category); organisations
 * are rounded tiles sized by their people; collaborators who are not mapped are small quiet
 * rings, clearer when linked to the focus. The network's links are gentle arcs (`rings.js`),
 * an organisation's members are spanned by a soft hull, a person's time windows are joined
 * in time order. Labels name the themes (in their colour), then, as asked or as the focus
 * needs, people, keywords (in their topic's colour) and organisations. The world view places
 * organisations at their address.
 */
import { convexHull } from '../components/map/core.js';
import {
  matchingPeople, nameIn, nodesUnder, orgLevelOf, periodOf,
} from './data.js';
import { ringLines, ringList } from './rings.js';
import { shade } from './schemes.js';
import { SHAPE_OF } from './state.js';

/** The most regions of people or texts drawn at once. */
export const MAX_REGIONS = 60;
/** The most keyword names offered to the label placement at once (the heaviest first). */
const MAX_KEYWORD_NAMES = 1500;
const HIDDEN = 2;
const RADIUS = { people: 4, keywords: 3, organisations: 5, texts: 3, projected: 3.4, windows: 3.5 };
const ALPHA = { people: 0.95, keywords: 0.78, organisations: 0.92, texts: 0.7, projected: 0.45, windows: 0.8 };
const DIM = { people: 0.1, keywords: 0.07, organisations: 0.1, texts: 0.06, projected: 0.12, windows: 0.12 };
/** The palette's last colour (what no theme holds); the scheme's colours come before it. */
const MAX_COLOURS = 31;

function makeLayer(id, n, palette) {
  return {
    id,
    shape: SHAPE_OF[id],
    radius: RADIUS[id],
    alpha: ALPHA[id],
    dim: DIM[id],
    ringFrom: 2,
    palette,
    x: new Float32Array(n),
    y: new Float32Array(n),
    color: new Uint16Array(n),
    rank: new Float32Array(n),
    highlight: new Uint8Array(n),
    highlightCount: 0,
    ref: new Uint32Array(n),
    shown: 0,
  };
}

/** The alpha of the points the focus leaves out: a trace, fainter the more points overlap
 * (thousands of faint points still add up to a blot). */
function traceAlpha(id, n) {
  return Math.max(0.012, Math.min(DIM[id], DIM[id] * Math.sqrt(400 / Math.max(1, n))));
}

function trim(layer, n) {
  const out = {};
  for (const [k, v] of Object.entries(layer)) out[k] = v && v.subarray && k !== 'palette' ? v.subarray(0, n) : v;
  return out;
}

function mark(layer, k, level) {
  if (!level) return;
  if (!layer.highlight[k]) layer.highlightCount += 1;
  layer.highlight[k] = Math.max(layer.highlight[k], level);
}

/** Ranks from weights: 0 for the heaviest, towards 1 for the lightest. */
function ranksOf(weights) {
  const order = weights.map((w, i) => [w || 0, i]).sort((a, b) => b[0] - a[0]);
  const out = new Float32Array(weights.length);
  order.forEach(([, i], r) => { out[i] = r / Math.max(1, weights.length); });
  return out;
}

/** The hull of keywords (by index), without the farthest fifth when there are many. */
export function keywordRegion(index, terms) {
  const pts = [];
  for (const k of terms) {
    const kw = index.keywords[k];
    if (kw && kw.x !== null && kw.x !== undefined) pts.push([kw.x, kw.y]);
  }
  return hullOf(pts);
}

function hullOf(pts) {
  if (pts.length < 3) return null;
  let kept = pts;
  if (pts.length > 8) {
    const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
    const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
    kept = pts.map((p) => [p, (p[0] - cx) ** 2 + (p[1] - cy) ** 2]).sort((a, b) => a[1] - b[1])
      .slice(0, Math.ceil(pts.length * 0.85)).map((e) => e[0]);
  }
  const polygon = convexHull(kept.map((p) => p[0]), kept.map((p) => p[1]));
  return polygon.length >= 6 ? polygon : null;
}

/** The colour (palette index) of a top-level theme's index. */
function paletteIndex(i) {
  return Math.min(i, MAX_COLOURS);
}

/**
 * What the focus lights: per kind, a map from item index to 1 (lit) or 2 (the focus);
 * `hulls` (lists of people indexes), `path` (a person whose windows are joined), `ringed`
 * (the network's partners).
 */
export function focusOf(index, state, extra) {
  const { texts = null, sets = new Map(), users = null, rings = null } = extra;
  const lit = { people: new Map(), keywords: new Map(), organisations: new Map(), texts: new Map(),
    projected: new Map(), hulls: [], path: null, any: false };
  const on = (kind, i, level = 1) => {
    if (i === undefined || i === null || i < 0) return;
    lit[kind].set(i, Math.max(lit[kind].get(i) || 0, level));
    lit.any = true;
  };
  const termsOf = (key) => (sets.get(key) || []).map((t) => index.byTerm.get(t));
  const side = (sel) => {
    if (!sel) return;
    if (sel.kind === 'person' && index.byPerson.has(sel.id)) {
      on('people', index.byPerson.get(sel.id), 2);
      for (const k of termsOf(`person:${sel.id}`)) on('keywords', k);
      for (const o of index.orgsOfPerson.get(sel.id) || []) on('organisations', index.byOrg.get(o));
      lit.path = sel.id;
      if (texts) texts.people.forEach((ps, i) => { if (ps.includes(sel.id)) on('texts', i); });
    } else if (sel.kind === 'projected' && index.byProjected.has(sel.id)) {
      on('projected', index.byProjected.get(sel.id), 2);
    } else if (sel.kind === 'organisation' && index.byOrg.has(sel.id)) {
      on('organisations', index.byOrg.get(sel.id), 2);
      const members = index.members.get(sel.id) || [];
      for (const i of members) on('people', i);
      lit.hulls.push({ people: members, top: index.orgTop[index.byOrg.get(sel.id)] });
      for (const k of termsOf(`organisation:${sel.id}`)) on('keywords', k);
    } else if (sel.kind === 'text' && texts) {
      const i = texts.id.indexOf(sel.id);
      if (i >= 0) {
        on('texts', i, 2);
        for (const k of texts.terms[i]) on('keywords', k);
        for (const pid of texts.people[i]) on('people', index.byPerson.get(pid));
      }
    } else if (sel.kind === 'theme' && index.nodes.has(sel.id)) {
      const under = nodesUnder(index, sel.id);
      const level = index.nodes.get(sel.id).level || 1;
      index.keywords.forEach((k, i) => { if (k.node && under.has(k.node)) on('keywords', i); });
      index.people.forEach((p, i) => {
        const shares = p.shares && p.shares[level - 1];
        if (shares && (shares[sel.id] || 0) >= 0.2) on('people', i);
      });
      index.orgs.forEach((o, i) => { if ((index.orgSharesAt(o.id, level)[sel.id] || 0) >= 0.2) on('organisations', i); });
    } else if (sel.kind === 'keyword' && index.byTerm.has(sel.id)) {
      const k = index.byTerm.get(sel.id);
      on('keywords', k, 2);
      const node = index.keywords[k].node;
      if (node) for (const j of index.nodeKeywords.get(node) || []) on('keywords', j);
      if (users && !users.error && users.data) for (const i of users.data.at || []) on('people', i);
    }
  };
  side(state.sel);
  if (state.sel && state.with) side(state.with);
  // the network around a person or an organisation
  if (rings && !rings.error) {
    for (const ring of ringList(rings)) {
      for (const item of ring.items || []) {
        if (state.sel.kind === 'organisation') on('organisations', index.byOrg.get(item.id));
        else if (item.place === 'map') on('people', index.byPerson.get(item.id));
        else if (item.place === 'projected') on('projected', index.byProjected.get(item.id));
      }
    }
  }
  return lit;
}

/** Where the network's partners are: `{x, y, projected}` by id, or null. */
function networkPlace(index, orgs) {
  return (id) => {
    if (orgs) {
      const o = index.orgs[index.byOrg.get(id)];
      return o && o.x !== null && o.x !== undefined ? { x: o.x, y: o.y } : null;
    }
    if (index.byPerson.has(id)) {
      const p = index.people[index.byPerson.get(id)];
      return p.x === null || p.x === undefined ? null : { x: p.x, y: p.y };
    }
    if (index.byProjected.has(id)) {
      const p = index.projected[index.byProjected.get(id)];
      return { x: p.x, y: p.y, projected: true };
    }
    return null;
  };
}

/**
 * The scene of the map view, with `counts` per kind (`shown`, `total`) and `notes` (what the
 * atlas should say: regions capped, texts sampled). *extra*: `texts` (columnar, or null),
 * `sets` (`kind:id` → terms), `users` (the people who use the focused keyword), `rings` (the
 * network's answer), `colours` (`{themes: [hex…], neutral, categories, dark}`), `locale`,
 * `nameOf(kind, item)`.
 */
export function mapScene(index, state, extra) {
  const { texts = null, sets = new Map(), rings = null, colours, locale = 'en', nameOf } = extra;
  const palette = [...colours.themes.slice(0, MAX_COLOURS), colours.neutral];
  const NEUTRAL = palette.length - 1;
  const colourOf = (node) => {
    const i = index.colourOf(node);
    return i >= index.tops.length ? NEUTRAL : paletteIndex(i);
  };
  const mask = matchingPeople(index, state);
  const period = periodOf(index, state);
  const inPeriod = (start, end) => !period || (end >= period[0] && start <= period[1]);
  const lit = focusOf(index, state, extra);
  const show = new Set(state.show);
  const named = new Set(state.names);
  const layers = [];
  const regions = [];
  const lines = [];
  const labels = [];
  const counts = {};
  const notes = [];
  const regionsAs = state.as === 'regions';
  const sel = state.sel;

  // the soft hulls of the organisations in focus
  for (const hull of lit.hulls) {
    const polygon = hullOf(hull.people.map((i) => index.people[i]).filter((p) => p.x !== null).map((p) => [p.x, p.y]));
    if (polygon) regions.push({ id: 'hull', polygon, color: palette[colourOf(hull.top)], alpha: 0.1 });
  }

  // the network: arcs under the points
  if (rings && !rings.error && sel) {
    const orgs = sel.kind === 'organisation';
    const from = orgs ? networkPlace(index, true)(sel.id) : networkPlace(index, false)(sel.id);
    if (from) {
      const top = orgs ? index.orgTop[index.byOrg.get(sel.id)] : null;
      lines.push(...ringLines(rings, from, networkPlace(index, orgs),
        { orgs, color: orgs ? palette[colourOf(top)] : '--cx-text' }));
    }
  }

  // Time windows: each person's places per period of years; the focus's joined in time order.
  if (show.has('windows') || lit.path) {
    const all = [];
    for (const [pid, list] of index.windows) {
      const i = index.byPerson.get(pid);
      if (i === undefined || !mask[i]) continue;
      if (!show.has('windows') && pid !== lit.path) continue;
      for (const w of list) if (inPeriod(w.start, w.end)) all.push([w, pid]);
    }
    const L = makeLayer('windows', all.length, palette);
    all.forEach(([w, pid], k) => {
      L.x[k] = w.x;
      L.y[k] = w.y;
      L.color[k] = colourOf(index.personTop[index.byPerson.get(pid)]);
      if (pid === lit.path) mark(L, k, 1);
      L.ref[k] = k;
    });
    L.items = all.map(([w]) => w);
    L.shown = all.length;
    layers.push(L);
    counts.windows = { shown: all.length, total: index.bundle.windows || 0 };
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
      path.forEach((w, k) => labels.push({ x: w.x, y: w.y, text: String(w.start), offset: 12,
        strong: k === 0 || k === path.length - 1 }));
    }
  }

  // Texts: placed by their keywords (or their authors), in the period.
  if (show.has('texts') && texts) {
    const n = texts.id.length;
    const L = makeLayer('texts', n, palette);
    let shown = 0;
    const visible = [];
    for (let i = 0; i < n; i += 1) {
      const year = texts.year[i];
      const keep = !period || year === null || (year >= period[0] && year <= period[1]);
      L.x[i] = texts.x[i];
      L.y[i] = texts.y[i];
      const first = texts.terms[i].length ? index.keywords[texts.terms[i][0]] : null;
      L.color[i] = first && first.node ? colourOf(first.node) : NEUTRAL;
      L.rank[i] = keep ? ((i * 0.6180339887) % 1) : HIDDEN; // a zoom of 2 shows four times as many
      mark(L, i, lit.texts.get(i));
      L.ref[i] = i;
      if (keep) {
        shown += 1;
        visible.push(i);
      }
    }
    L.detail = n > 2000 ? 0.25 : 1;
    L.dim = traceAlpha('texts', shown);
    L.shown = shown;
    counts.texts = { shown, total: n };
    if (texts.sampled) notes.push({ key: 'atlas.note.texts_sample', count: n, total: texts.total });
    if (regionsAs && visible.length <= MAX_REGIONS) {
      for (const i of visible) {
        const polygon = keywordRegion(index, texts.terms[i]);
        if (polygon) regions.push({ id: `text:${texts.id[i]}`, polygon, color: palette[L.color[i]], alpha: 0.08 });
      }
      L.alpha = 0.5;
    } else if (regionsAs) {
      notes.push({ key: 'atlas.note.regions_cap', kind: 'texts', count: visible.length, max: MAX_REGIONS });
    }
    layers.push(L);
  }

  // Keywords: by their top-level theme (or category), the heaviest first as the zoom grows.
  const keywordNames = [];
  if (show.has('keywords')) {
    const ks = index.keywords;
    const L = makeLayer('keywords', ks.length, palette);
    const kinds = new Set(state.kc);
    const byCategory = state.kcol === 'category';
    if (byCategory) L.palette = [...colours.categories, colours.neutral];
    const categoryIndex = { concept: 0, method: 1, object: 2, place: 3, field: 4 };
    let k = 0;
    for (let i = 0; i < ks.length; i += 1) {
      const kw = ks[i];
      if (kw.x === null || kw.x === undefined) continue;
      if (kinds.size && !kinds.has(kw.category || 'none')) continue;
      L.x[k] = kw.x;
      L.y[k] = kw.y;
      L.color[k] = byCategory ? (categoryIndex[kw.category] ?? 5) : colourOf(kw.node);
      L.ref[k] = i;
      mark(L, k, lit.keywords.get(i));
      k += 1;
    }
    const weights = Array.from(L.ref.subarray(0, k), (i) => ks[i].weight || 0);
    const out = trim(L, k);
    out.rank = ranksOf(weights);
    out.detail = k > 400 ? 0.35 : 1;
    out.dim = traceAlpha('keywords', k);
    out.shown = k;
    out.items = ks;
    layers.push(out);
    counts.keywords = { shown: k, total: ks.length };
    // names: every keyword's when asked (the heaviest first; with a focus, its own), and a
    // focused theme's keywords
    const all = named.has('keywords');
    if (all || (sel && sel.kind === 'theme')) {
      const order = [];
      for (let j = 0; j < k; j += 1) if (all ? (!lit.any || out.highlight[j] > 0) : out.highlight[j] > 0) order.push(out.ref[j]);
      order.sort((a, b) => (ks[b].weight || 0) - (ks[a].weight || 0));
      for (const i of order.slice(0, MAX_KEYWORD_NAMES)) {
        const kw = ks[i];
        keywordNames.push({ x: kw.x, y: kw.y, text: kw.term, offset: -11, size: 11,
          color: topicColour(index, kw.node, palette, colourOf, colours.dark),
          minZoom: all && !lit.any ? 1.5 : 0 });
      }
    }
  }

  // People: hidden by the filters; as regions when few enough.
  const peopleNames = [];
  if (show.has('people')) {
    const ps = index.people;
    const L = makeLayer('people', ps.length, palette);
    const shownList = [];
    let k = 0;
    for (let i = 0; i < ps.length; i += 1) {
      if (ps[i].x === null || ps[i].x === undefined) continue;
      L.x[k] = ps[i].x;
      L.y[k] = ps[i].y;
      L.color[k] = colourOf(index.personTop[i]);
      L.rank[k] = mask[i] ? 0 : HIDDEN;
      L.ref[k] = i;
      if (mask[i]) shownList.push(i);
      mark(L, k, mask[i] ? lit.people.get(i) : 0);
      k += 1;
    }
    const out = trim(L, k);
    out.items = ps;
    out.shown = shownList.length;
    out.dim = traceAlpha('people', shownList.length);
    counts.people = { shown: shownList.length, total: k };
    if (regionsAs && shownList.length <= MAX_REGIONS) {
      for (const i of shownList) {
        const terms = sets.get(`person:${ps[i].person_id}`);
        if (!terms) continue;
        const polygon = keywordRegion(index, terms.slice(0, 20).map((t) => index.byTerm.get(t)).filter((t) => t !== undefined));
        if (polygon) regions.push({ id: `person:${ps[i].person_id}`, polygon, color: palette[colourOf(index.personTop[i])], alpha: 0.08 });
      }
    } else if (regionsAs) {
      notes.push({ key: 'atlas.note.regions_cap', kind: 'people', count: shownList.length, max: MAX_REGIONS });
    }
    layers.push(out);
    const few = lit.people.size > 0 && lit.people.size <= 24;
    for (const i of shownList) {
      const on = lit.people.get(i);
      if (!named.has('people') && !(few && on)) continue;
      if (named.has('people') && lit.any && !on) continue;
      const text = nameOf('person', ps[i]);
      if (text) peopleNames.push({ x: ps[i].x, y: ps[i].y, text, offset: 12,
        minZoom: on || shownList.length <= 300 ? 0 : 2.5 });
    }
  }

  // Projected people (collaborators collected but not mapped, and other sets): small quiet
  // rings, placed on the finished map; those of the network show even when the kind is off.
  const projectedOn = show.has('projected');
  if (projectedOn || lit.projected.size) {
    const ps = index.projected;
    const L = makeLayer('projected', ps.length, palette);
    let shown = 0;
    ps.forEach((p, i) => {
      L.x[i] = p.x;
      L.y[i] = p.y;
      L.color[i] = NEUTRAL;
      L.ref[i] = i;
      const level = lit.projected.get(i);
      L.rank[i] = projectedOn || level ? 0 : HIDDEN;
      if (projectedOn || level) shown += 1;
      mark(L, i, level);
      if (level && lit.projected.size <= 24) {
        const text = nameOf('projected', p);
        if (text) peopleNames.push({ x: p.x, y: p.y, text, offset: 11, color: '--cx-text-muted' });
      }
    });
    L.items = ps;
    L.shown = shown;
    layers.push(L);
    counts.projected = { shown, total: ps.length };
  }

  // Organisations of one level: rounded tiles in their main theme's colour, sized by people.
  const level = orgLevelOf(index, state);
  const orgNames = [];
  if (show.has('organisations')) {
    const list = [];
    let most = 1;
    index.orgs.forEach((o, i) => {
      if (o.level === level && o.x !== null && o.x !== undefined) {
        list.push(i);
        most = Math.max(most, (index.members.get(o.id) || []).length);
      }
    });
    const L = makeLayer('organisations', list.length, palette);
    L.size = new Float32Array(list.length);
    L.sizeMax = 2.6;
    list.forEach((i, k) => {
      const o = index.orgs[i];
      L.x[k] = o.x;
      L.y[k] = o.y;
      L.color[k] = colourOf(index.orgTop[i]);
      L.size[k] = 1 + 1.6 * Math.sqrt((index.members.get(o.id) || []).length / most);
      L.ref[k] = i;
      const on = lit.organisations.get(i);
      mark(L, k, on);
      if (named.has('organisations') ? (!lit.any || on) : on) {
        orgNames.push({ x: o.x, y: o.y, text: o.acronym || o.name, offset: -(10 + 6 * L.size[k]),
          strong: on === 2, minZoom: named.has('organisations') && !on && list.length > 30 ? 1.6 : 0 });
      }
      if (regionsAs) {
        const terms = sets.get(`organisation:${o.id}`);
        const polygon = terms && keywordRegion(index, terms.map((t) => index.byTerm.get(t)).filter((t) => t !== undefined));
        if (polygon) regions.push({ id: `organisation:${o.id}`, polygon, color: palette[L.color[k]], alpha: 0.1 });
      }
    });
    L.items = index.orgs;
    L.shown = list.length;
    L.dim = traceAlpha('organisations', list.length);
    layers.push(L);
    counts.organisations = { shown: list.length, total: index.orgs.filter((o) => o.level === level).length };
  }

  // Theme names at the centre of their keywords, in their colour: the top level, the next one
  // with the zoom; a focused theme names its children.
  const themeNames = [];
  const focusTheme = sel && sel.kind === 'theme' && index.nodes.has(sel.id) ? sel.id
    : sel && sel.kind === 'keyword' && index.byTerm.has(sel.id) ? index.keywords[index.byTerm.get(sel.id)].node : null;
  const focusTop = focusTheme ? index.topOf.get(focusTheme) : null;
  for (const [id, c] of index.centres) {
    const node = index.nodes.get(id);
    const lvl = node.level || 1;
    const inFocus = focusTheme && (id === focusTheme || node.parent === focusTheme
      || (lvl === 1 && id === focusTop) || node.parent === index.nodes.get(focusTheme).parent);
    if (lvl > 2 && !inFocus) continue;
    if (focusTheme && !inFocus && lvl > 1) continue;
    const ci = colourOf(id);
    themeNames.push({ x: c.x, y: c.y, text: nameIn(node.names, locale),
      minZoom: lvl === 1 || inFocus ? 0 : 2.5, size: lvl === 1 ? 15 : 12,
      strong: id === focusTheme,
      color: focusTheme && !inFocus ? '--cx-text-muted'
        : shade(palette[ci], colours.dark ? 8 * (lvl === 1 ? 1 : 1.3) : -6 * (lvl === 1 ? 1 : 1.6)),
      weight: (node.weight || 0) + (lvl === 1 ? 1e9 : 0) + (inFocus ? 2e9 : 0) });
  }
  themeNames.sort((a, b) => b.weight - a.weight);
  const selected = selectionLabel(index, state, texts, nameOf);
  return {
    scene: {
      layers,
      regions,
      lines,
      labels: [...(selected ? [selected] : []), ...themeNames, ...orgNames, ...peopleNames, ...keywordNames, ...labels],
      bounds: index.bundle.bounds,
    },
    counts,
    notes,
    lit,
  };
}

/** A keyword's colour: its theme's, shaded by its topic (the node's place among its siblings). */
function topicColour(index, node, palette, colourOf, dark) {
  const base = palette[colourOf(node)];
  if (!node || !index.nodes.has(node)) return base;
  const parent = index.nodes.get(node).parent;
  const siblings = index.children.get(parent || null) || [];
  const j = siblings.indexOf(node);
  return shade(base, (j % 2 ? -7 : 5) + (dark ? 8 : -12));
}

/** The label of the focus (always drawn, first). */
function selectionLabel(index, state, texts, nameOf) {
  const sel = state.sel;
  if (!sel) return null;
  if (sel.kind === 'person' && index.byPerson.has(sel.id)) {
    const p = index.people[index.byPerson.get(sel.id)];
    return p.x === null ? null : { x: p.x, y: p.y, text: nameOf('person', p), strong: true, offset: 14 };
  }
  if (sel.kind === 'projected' && index.byProjected.has(sel.id)) {
    const p = index.projected[index.byProjected.get(sel.id)];
    return { x: p.x, y: p.y, text: nameOf('projected', p), strong: true, offset: 13 };
  }
  if (sel.kind === 'keyword' && index.byTerm.has(sel.id)) {
    const k = index.keywords[index.byTerm.get(sel.id)];
    return k.x === null ? null : { x: k.x, y: k.y, text: k.term, strong: true, offset: 13 };
  }
  if (sel.kind === 'text' && texts) {
    const i = texts.id.indexOf(sel.id);
    if (i >= 0) {
      const title = texts.title[i] || '';
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

const outlines = new WeakMap();
/** The land's outline (rings of `[lon0, lat0, lon1, lat1…]`) as line segments, kept per rings. */
function outlineOf(rings) {
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
 * The world view: organisations of the level at their address (longitude, latitude; an
 * equirectangular projection) on a graticule and the outline of the land (*land*), as tiles
 * sized by their people, coloured by their main theme; the focus lights its own.
 */
export function worldScene(index, state, { land = null, colours }) {
  const palette = [...colours.themes.slice(0, MAX_COLOURS), colours.neutral];
  const NEUTRAL = palette.length - 1;
  const level = orgLevelOf(index, state);
  const list = [];
  let most = 1;
  index.orgs.forEach((o, i) => {
    if (o.level === level && o.location) {
      list.push(i);
      most = Math.max(most, (index.members.get(o.id) || []).length);
    }
  });
  const L = makeLayer('organisations', list.length, palette);
  L.size = new Float32Array(list.length);
  L.sizeMax = 2.6;
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
    const ci = index.colourOf(index.orgTop[i]);
    L.color[k] = ci >= index.tops.length ? NEUTRAL : paletteIndex(ci);
    L.size[k] = 1 + 1.6 * Math.sqrt((index.members.get(o.id) || []).length / most);
    L.ref[k] = i;
    let on = 0;
    if (sel && sel.kind === 'organisation' && sel.id === o.id) on = 2;
    else if (sel && sel.kind === 'theme' && (index.orgSharesAt(o.id, index.nodes.has(sel.id) ? index.nodes.get(sel.id).level : 1)[sel.id] || 0) >= 0.2) on = 1;
    else if (sel && sel.kind === 'person' && (index.orgsOfPerson.get(sel.id) || []).includes(o.id)) on = 1;
    mark(L, k, on);
    labels.push({ x: o.location.lon, y: o.location.lat, text: o.acronym || o.name, offset: 14,
      minZoom: list.length > 30 ? 3 : 0, strong: on === 2 });
  });
  L.items = index.orgs;
  L.shown = list.length;
  const pad = (lo, hi, span) => {
    const mid = (lo + hi) / 2;
    const half = Math.max((hi - lo) / 2 + span * 0.15, span / 2);
    return [mid - half, mid + half];
  };
  const [x0, x1] = list.length ? pad(xmin, xmax, 20) : [-180, 180];
  const [y0, y1] = list.length ? pad(ymin, ymax, 12) : [-90, 90];
  const span = Math.max(x1 - x0, y1 - y0);
  const outline = outlineOf(land);
  return {
    scene: { layers: [L], regions: [], labels,
      lines: [graticule(span > 120 ? 30 : span > 40 ? 10 : 5), ...(outline ? [outline] : [])],
      bounds: { xmin: Math.max(-180, x0), xmax: Math.min(180, x1), ymin: Math.max(-90, y0), ymax: Math.min(90, y1) } },
    counts: { organisations: { shown: list.length, total: index.orgs.filter((o) => o.level === level).length } },
    notes: [],
    lit: { any: false },
  };
}
