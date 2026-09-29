// SPDX-License-Identifier: MIT
/**
 * The scenes the site's maps draw (see the app's `components/map/core.js`):
 * keywords, people, organisations of one level and projected people, one
 * symbol per kind, coloured by their top-level theme; the selection
 * highlighted and always labelled; theme names at the centre of their
 * keywords, organisations and people named as the zoom grows; lines from a
 * selected person to their real nearest neighbours. The world view places
 * organisations at their address over the outline of the land.
 */
(function () {
  'use strict';

  const S = window.CxSite;
  S.KINDS = ['people', 'keywords', 'orgs', 'projected'];
  S.SHAPE = { people: 'circle', keywords: 'diamond', orgs: 'square', projected: 'ring' };
  S.PALETTE = [...Array.from({ length: 12 }, (_, i) => `--cx-hue-${i + 1}`), '--cx-text-muted'];
  const RADIUS = { people: 4, keywords: 3, orgs: 5.5, projected: 4.5 };
  const ALPHA = { people: 0.95, keywords: 0.75, orgs: 0.95, projected: 0.95 };
  const PICK = { people: 'person', keywords: 'keyword', orgs: 'org', projected: 'projected' };

  function layer(id, n) {
    return {
      id,
      shape: S.SHAPE[id],
      radius: RADIUS[id],
      alpha: ALPHA[id],
      palette: S.PALETTE,
      x: new Float32Array(n),
      y: new Float32Array(n),
      color: new Uint16Array(n),
      rank: new Float32Array(n),
      highlight: new Uint8Array(n),
      highlightCount: 0,
      ref: new Uint32Array(n),
    };
  }

  function light(L, k) {
    L.highlight[k] = 1;
    L.highlightCount += 1;
  }

  /** The organisations' level shown by default: the one with the most placed organisations. */
  S.defaultOrgLevel = function defaultOrgLevel(world) {
    const o = S.ix.core.orgs;
    const counts = new Map();
    o.id.forEach((_, i) => {
      const placed = world ? o.location[i] : o.x[i] !== null;
      if (placed) counts.set(o.level[i], (counts.get(o.level[i]) || 0) + 1);
    });
    let best = '';
    let most = 0;
    for (const [level, n] of counts) {
      if (n > most) {
        best = level;
        most = n;
      }
    }
    return best;
  };

  /** What a selection lights up (sets of indexes per kind), from the core and the details. */
  function lit(sel) {
    const out = { people: new Set(), keywords: new Set(), orgs: new Set(), projected: new Set(), near: [] };
    if (!sel) return out;
    const ix = S.ix;
    const details = S.data.details;
    const terms = (list) => (list || []).map((t) => ix.byTerm.get(t)).filter((i) => i !== undefined);
    if (sel.kind === 'person' && ix.byPerson.has(sel.id)) {
      out.people.add(ix.byPerson.get(sel.id));
      const d = details && details.people[sel.id];
      if (d) {
        terms(d.keywords).forEach((k) => out.keywords.add(k));
        out.near = d.near.map((n) => ix.byPerson.get(n[0])).filter((i) => i !== undefined);
        out.near.forEach((i) => out.people.add(i));
      }
    } else if (sel.kind === 'org' && ix.byOrg.has(sel.id)) {
      out.orgs.add(ix.byOrg.get(sel.id));
      const d = details && details.orgs[sel.id];
      if (d) {
        d.members.forEach((m) => { if (ix.byPerson.has(m)) out.people.add(ix.byPerson.get(m)); });
        terms(d.keywords).forEach((k) => out.keywords.add(k));
      }
    } else if (sel.kind === 'keyword' && ix.byTerm.has(sel.id)) {
      out.keywords.add(ix.byTerm.get(sel.id));
      const users = details && details.used_by[sel.id];
      (users || []).forEach((m) => { if (ix.byPerson.has(m)) out.people.add(ix.byPerson.get(m)); });
    } else if (sel.kind === 'theme' && ix.nodes.has(sel.id)) {
      const under = new Set();
      const todo = [sel.id];
      while (todo.length) {
        const id = todo.pop();
        under.add(id);
        todo.push(...(ix.children.get(id) || []));
      }
      ix.core.keywords.node.forEach((n, i) => { if (n && under.has(n)) out.keywords.add(i); });
      const d = details && details.themes[sel.id];
      if (d) {
        d.people.forEach(([m]) => { if (ix.byPerson.has(m)) out.people.add(ix.byPerson.get(m)); });
        d.orgs.forEach(([m]) => { if (ix.byOrg.has(m)) out.orgs.add(ix.byOrg.get(m)); });
      }
    } else if (sel.kind === 'projected' && ix.byProjected.has(sel.id)) {
      out.projected.add(ix.byProjected.get(sel.id));
    }
    return out;
  }

  /** Ranks from weights: 0 for the heaviest, towards 1 for the lightest. */
  function ranks(weights) {
    const order = weights.map((w, i) => [w || 0, i]).sort((a, b) => b[0] - a[0]);
    const out = new Float32Array(weights.length);
    order.forEach(([, i], r) => { out[i] = r / Math.max(1, weights.length); });
    return out;
  }

  function trimmed(L, n) {
    for (const key of ['x', 'y', 'color', 'rank', 'highlight', 'ref']) L[key] = L[key].subarray(0, n);
    return L;
  }

  /**
   * The scene of the map: *opts* `{show: Set of kinds, org: level, sel}`.
   * Answers `{scene, counts, pick(hit) → selection}`.
   */
  S.mapScene = function mapScene(opts) {
    const ix = S.ix;
    const core = ix.core;
    const show = opts.show;
    const on = lit(opts.sel);
    const layers = [];
    const labels = [];
    const lines = [];
    const counts = {};

    if (show.has('keywords')) {
      const k = core.keywords;
      const L = layer('keywords', k.term.length);
      k.term.forEach((_, i) => {
        L.x[i] = k.x[i];
        L.y[i] = k.y[i];
        L.color[i] = S.colourOf(k.node[i]);
        L.ref[i] = i;
        if (on.keywords.has(i)) light(L, i);
      });
      L.rank = ranks(k.weight);
      L.detail = k.term.length > 400 ? 0.35 : 1;
      layers.push(L);
      counts.keywords = k.term.length;
    }
    if (show.has('projected')) {
      const p = core.projected;
      const L = layer('projected', p.id.length);
      p.id.forEach((_, i) => {
        L.x[i] = p.x[i];
        L.y[i] = p.y[i];
        L.color[i] = S.colourOf(p.top[i]);
        L.ref[i] = i;
        if (on.projected.has(i)) light(L, i);
      });
      layers.push(L);
      counts.projected = p.id.length;
    }
    if (show.has('people')) {
      const p = core.people;
      const L = layer('people', p.id.length);
      p.id.forEach((_, i) => {
        L.x[i] = p.x[i];
        L.y[i] = p.y[i];
        L.color[i] = S.colourOf(p.top[i]);
        L.ref[i] = i;
        if (on.people.has(i)) light(L, i);
        labels.push({ x: p.x[i], y: p.y[i], text: S.personName(i), offset: 12, minZoom: p.id.length > 300 ? 8 : 4 });
      });
      layers.push(L);
      counts.people = p.id.length;
    }
    if (show.has('orgs')) {
      const o = core.orgs;
      const level = opts.org;
      const L = layer('orgs', o.id.length);
      let n = 0;
      o.id.forEach((_, i) => {
        if (o.level[i] !== level || o.x[i] === null) return;
        L.x[n] = o.x[i];
        L.y[n] = o.y[i];
        L.color[n] = S.colourOf(o.top[i]);
        L.ref[n] = i;
        if (on.orgs.has(i)) light(L, n);
        labels.push({ x: o.x[i], y: o.y[i], text: S.orgShort(i), offset: 13, minZoom: 1.5 });
        n += 1;
      });
      layers.push(trimmed(L, n));
      counts.orgs = n;
    }

    // Lines from the selected person to their real nearest neighbours.
    if (opts.sel && opts.sel.kind === 'person' && on.near.length && ix.byPerson.has(opts.sel.id)) {
      const i = ix.byPerson.get(opts.sel.id);
      const x = new Float32Array(on.near.length * 2);
      const y = new Float32Array(on.near.length * 2);
      on.near.forEach((j, k) => {
        x[2 * k] = core.people.x[i];
        y[2 * k] = core.people.y[i];
        x[2 * k + 1] = core.people.x[j];
        y[2 * k + 1] = core.people.y[j];
        labels.push({ x: core.people.x[j], y: core.people.y[j], text: S.personName(j), offset: 12, strong: true });
      });
      lines.push({ id: 'near', x, y, color: '--cx-accent', alpha: 0.85, width: 1.5 });
    }

    // Theme names at the centre of their keywords: the top level, the next with the zoom.
    const centres = new Map();
    core.keywords.node.forEach((node, i) => {
      let at = node;
      while (at && ix.nodes.has(at)) {
        const c = centres.get(at) || [0, 0, 0];
        c[0] += core.keywords.x[i];
        c[1] += core.keywords.y[i];
        c[2] += 1;
        centres.set(at, c);
        at = ix.nodes.get(at).parent;
      }
    });
    const themeLabels = [];
    for (const [id, c] of centres) {
      const node = ix.nodes.get(id);
      if (node.level > 2) continue;
      themeLabels.push({ x: c[0] / c[2], y: c[1] / c[2], text: S.nodeName(id), minZoom: node.level === 1 ? 0 : 2.5,
        weight: (node.weight || 0) + (node.level === 1 ? 1e9 : 0),
        strong: Boolean(opts.sel && opts.sel.kind === 'theme' && opts.sel.id === id) });
    }
    themeLabels.sort((a, b) => b.weight - a.weight);
    const selected = selectionLabel(opts.sel);
    return {
      scene: { layers, lines, regions: [], labels: [...(selected ? [selected] : []), ...themeLabels, ...labels],
        bounds: core.bounds },
      counts,
      pick: (hit) => pickOf(layers, hit),
    };
  };

  function pickOf(layers, hit) {
    if (!hit) return null;
    const L = layers.find((l) => l.id === hit.layer);
    if (!L) return null;
    const i = L.ref[hit.index];
    const core = S.ix.core;
    const ids = { people: core.people.id, keywords: core.keywords.term, orgs: core.orgs.id, projected: core.projected.id };
    return { kind: PICK[L.id], id: ids[L.id][i] };
  }

  /** The label of the selection (always drawn, first). */
  function selectionLabel(sel) {
    if (!sel) return null;
    const ix = S.ix;
    const core = ix.core;
    if (sel.kind === 'person' && ix.byPerson.has(sel.id)) {
      const i = ix.byPerson.get(sel.id);
      return { x: core.people.x[i], y: core.people.y[i], text: S.personName(i), strong: true, offset: 14 };
    }
    if (sel.kind === 'org' && ix.byOrg.has(sel.id)) {
      const i = ix.byOrg.get(sel.id);
      if (core.orgs.x[i] === null) return null;
      return { x: core.orgs.x[i], y: core.orgs.y[i], text: S.orgShort(i), strong: true, offset: 15 };
    }
    if (sel.kind === 'keyword' && ix.byTerm.has(sel.id)) {
      const i = ix.byTerm.get(sel.id);
      return { x: core.keywords.x[i], y: core.keywords.y[i], text: sel.id, strong: true, offset: 13 };
    }
    if (sel.kind === 'projected' && ix.byProjected.has(sel.id)) {
      const i = ix.byProjected.get(sel.id);
      return { x: core.projected.x[i], y: core.projected.y[i], text: S.projectedName(i), strong: true, offset: 14 };
    }
    return null;
  }

  /** Graticule lines every *step* degrees. */
  function graticule(step) {
    const x = [];
    const y = [];
    for (let lon = -180; lon <= 180; lon += step) x.push(lon, lon), y.push(-90, 90);
    for (let lat = -90; lat <= 90; lat += step) x.push(-180, 180), y.push(lat, lat);
    return { id: 'graticule', x: Float32Array.from(x), y: Float32Array.from(y), color: '--cx-rule', alpha: 0.9, width: 1 };
  }

  /** The land's outline as line segments (from `assets/world.js`, Natural Earth). */
  let outline = null;
  function land() {
    if (outline || !S.data.world) return outline;
    const x = [];
    const y = [];
    for (const ring of S.data.world) {
      for (let k = 0; k + 3 < ring.length; k += 2) {
        x.push(ring[k], ring[k + 2]);
        y.push(ring[k + 1], ring[k + 3]);
      }
    }
    outline = { id: 'land', x: Float32Array.from(x), y: Float32Array.from(y), color: '--cx-border', alpha: 0.8, width: 1 };
    return outline;
  }

  /** The world view: organisations of one level at their address (longitude, latitude). */
  S.worldScene = function worldScene(opts) {
    const o = S.ix.core.orgs;
    const on = lit(opts.sel);
    const L = layer('orgs', o.id.length);
    const labels = [];
    let n = 0;
    let box = [Infinity, -Infinity, Infinity, -Infinity];
    o.id.forEach((_, i) => {
      if (o.level[i] !== opts.org || !o.location[i]) return;
      const [lon, lat] = o.location[i];
      L.x[n] = lon;
      L.y[n] = lat;
      L.color[n] = S.colourOf(o.top[i]);
      L.ref[n] = i;
      if (on.orgs.has(i)) light(L, n);
      box = [Math.min(box[0], lon), Math.max(box[1], lon), Math.min(box[2], lat), Math.max(box[3], lat)];
      labels.push({ x: lon, y: lat, text: S.orgShort(i), offset: 14, minZoom: 2,
        strong: Boolean(opts.sel && opts.sel.kind === 'org' && opts.sel.id === o.id[i]) });
      n += 1;
    });
    trimmed(L, n);
    L.radius = 6;
    const pad = (lo, hi, span) => {
      const mid = (lo + hi) / 2;
      const half = Math.max((hi - lo) / 2 + span * 0.15, span / 2);
      return [mid - half, mid + half];
    };
    const [x0, x1] = n ? pad(box[0], box[1], 20) : [-180, 180];
    const [y0, y1] = n ? pad(box[2], box[3], 12) : [-90, 90];
    const span = Math.max(x1 - x0, y1 - y0);
    const lines = [graticule(span > 120 ? 30 : span > 40 ? 10 : 5)];
    if (land()) lines.push(land());
    return {
      scene: { layers: [L], regions: [], lines, labels,
        bounds: { xmin: Math.max(-180, x0), xmax: Math.min(180, x1), ymin: Math.max(-90, y0), ymax: Math.min(90, y1) } },
      counts: { orgs: n },
      pick: (hit) => pickOf([L], hit),
    };
  };
}());
