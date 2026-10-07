// SPDX-License-Identifier: MIT
/**
 * Distances' computations, without the DOM: how alike people and organisations are, by the
 * measures a browser can compute from what a host gives (`docs/dev/atlas.md`, « Distances »):
 *
 * - `space`: the cosine of their vectors in the space of the themes (the source's
 *   `vectors(kind)`: int8 rows over the bundle's people or organisations);
 * - `themes`: the themes they share, Σ min of their top-level theme shares (the bundle's).
 *
 * Both go from 0 (nothing in common) to 1 (the same), as the app's measures of the same
 * names (`cartolex/app/similarity.py`). Long loops give the page back every so often
 * (`distPause`), so a national field (10⁵ people) is ranked without freezing the page;
 * nothing ever holds a people × people matrix but within a capped selection.
 */

/** The measures the browser computes, in the order offered. */
export const DIST_MEASURES = ['space', 'themes'];
/** Multiply-adds between two pauses. */
const DIST_STEP = 4e6;

/** A pause that lets the page draw and hear the keyboard; rejects once *signal* is aborted. */
export function distPause(signal) {
  return new Promise((resolve, reject) => {
    setTimeout(() => (signal && signal.aborted ? reject(new Error('aborted')) : resolve()), 0);
  });
}

/** A pause at most every *ms* milliseconds: `await tick()` in a loop gives the page back
 * that often, however long each turn. */
export function distTicker(signal, ms = 40) {
  let last = performance.now();
  return async () => {
    if (performance.now() - last < ms) return;
    await distPause(signal);
    last = performance.now();
  };
}

/** The dot product of rows *a* and *b* (offsets) of *V*, *D* wide, four at a time. */
function dotRows(V, a, b, D) {
  let d0 = 0;
  let d1 = 0;
  let d2 = 0;
  let d3 = 0;
  let k = 0;
  for (; k + 3 < D; k += 4) {
    d0 += V[a + k] * V[b + k];
    d1 += V[a + k + 1] * V[b + k + 1];
    d2 += V[a + k + 2] * V[b + k + 2];
    d3 += V[a + k + 3] * V[b + k + 3];
  }
  let d = d0 + d1 + d2 + d3;
  for (; k < D; k += 1) d += V[a + k] * V[b + k];
  return d;
}

/** Int8 rows (*values*, n × dim) as rows of length one; a row of zeros has no place (`has` 0). */
export function unitRows(values, dim, n) {
  const v = new Float32Array(n * dim);
  const has = new Uint8Array(n);
  for (let i = 0; i < n; i += 1) {
    const o = i * dim;
    let s = 0;
    for (let k = 0; k < dim; k += 1) s += values[o + k] * values[o + k];
    if (!s) continue;
    const inv = 1 / Math.sqrt(s);
    has[i] = 1;
    for (let k = 0; k < dim; k += 1) v[o + k] = values[o + k] * inv;
  }
  return { dim, n, v, has };
}

/** The items of *kind* in the bundle: people (`person`) or organisations. */
export function distItems(index, kind) {
  return kind === 'organisation' ? index.orgs : index.people;
}

/** An item's shares at theme *level* (1: the top), `{node: share}`. */
export function itemShares(index, kind, i, level = 1) {
  if (kind === 'organisation') return index.orgSharesAt(index.orgs[i].id, level);
  const p = index.people[i];
  return (p.shares && p.shares[level - 1]) || {};
}

/** The top-level shares of every item of *kind*, rows of `index.tops`: `{T, m}`. */
export function topRows(index, kind) {
  const items = distItems(index, kind);
  const T = index.tops.length;
  const col = new Map(index.tops.map((id, j) => [id, j]));
  const m = new Float32Array(items.length * T);
  items.forEach((it, i) => {
    for (const [node, share] of Object.entries(itemShares(index, kind, i, 1))) {
      const j = col.get(node);
      if (j !== undefined) m[i * T + j] = share;
    }
  });
  return { T, m };
}

/** An item's main theme at *level*, or null. */
export function mainTheme(index, kind, i, level = 1) {
  let best = null;
  let value = 0;
  for (const [node, share] of Object.entries(itemShares(index, kind, i, level))) {
    if (share > value) {
      best = node;
      value = share;
    }
  }
  return best;
}

/**
 * What the measures read, for one kind: `vec` (unit rows, or null without vectors) and `top`
 * (top-level shares). *data* is `{index, vec: {person, organisation}, top: {…}, links: {…}}`.
 */
function sideOf(data, kind) {
  return { vec: data.vec ? data.vec[kind] : null, top: data.top[kind] };
}

/** The query of item *i* of *kind*: its vector (or null) and its top-level shares. */
export function distQuery(data, kind, i) {
  const { vec, top } = sideOf(data, kind);
  const v = vec && vec.has[i] ? vec.v.subarray(i * vec.dim, (i + 1) * vec.dim) : null;
  return { v, s: top.m.subarray(i * top.T, (i + 1) * top.T) };
}

/** The similarity of query *q* to item *j* of *side*, by *measure* (NaN: no place). */
function scoreOne(measure, q, side, j) {
  if (measure === 'space') {
    const vec = side.vec;
    if (!q.v || !vec || !vec.has[j]) return NaN;
    const o = j * vec.dim;
    let d = 0;
    for (let k = 0; k < vec.dim; k += 1) d += q.v[k] * vec.v[o + k];
    return d;
  }
  const { T, m } = side.top;
  const o = j * T;
  let s = 0;
  for (let k = 0; k < T; k += 1) s += Math.min(q.s[k], m[o + k]);
  return s;
}

/** The cost of one score (multiply-adds), for the pauses. */
function costOf(measure, data) {
  const dims = data.vec && data.vec.person ? data.vec.person.dim : 1;
  return measure === 'space' ? dims : data.index.tops.length || 1;
}

/** The similarity of *q* to each of *targets* (indexes of *kind*): a Float32Array, NaN where
 * one has no place; pauses between blocks. */
export const scoreAll = async (data, measure, q, kind, targets, signal) => {
  const side = sideOf(data, kind);
  const out = new Float32Array(targets.length);
  const step = Math.max(256, Math.floor(DIST_STEP / costOf(measure, data)));
  for (let s = 0; s < targets.length; s += step) {
    const e = Math.min(targets.length, s + step);
    for (let t = s; t < e; t += 1) out[t] = scoreOne(measure, q, side, targets[t]);
    if (e < targets.length) await distPause(signal);
  }
  return out;
};

/** The order of *scores* from the largest down (NaN last), as positions. */
export function rankOrder(scores) {
  const order = new Int32Array(scores.length);
  for (let i = 0; i < order.length; i += 1) order[i] = i;
  const key = (i) => (Number.isNaN(scores[i]) ? -Infinity : scores[i]);
  return order.sort((a, b) => key(b) - key(a) || a - b);
}

/** The partners of item *i* in a CSR (`ptr`, `nbr`, `cnt`) of *n* items: Map j → texts. */
export function partnersOf(csr, i, n) {
  const out = new Map();
  if (!csr || i + 1 >= csr.ptr.length) return out;
  for (let k = csr.ptr[i]; k < csr.ptr[i + 1]; k += 1) if (csr.nbr[k] < n) out.set(csr.nbr[k], csr.cnt[k]);
  return out;
}

/**
 * How *focus* (`{kind, i}`) works with the items of *kind*: Map j → n. Two people: the texts
 * they wrote together; two organisations of one level: their texts together; a person and
 * organisations: how many of their co-authors are members of each; an organisation and
 * people: how many of its members write with each. Null when the host gave no links.
 */
export function togetherWith(data, focus, kind) {
  const { index, links } = data;
  if (!links) return null;
  const P = index.people.length;
  if (focus.kind === kind) {
    const csr = links[kind];
    if (!csr) return null;
    if (kind === 'organisation') {
      // organisations are paired within a level
      return partnersOf(csr, focus.i, index.orgs.length);
    }
    return partnersOf(csr, focus.i, P);
  }
  if (!links.person) return null;
  const out = new Map();
  if (focus.kind === 'person') {
    for (const j of partnersOf(links.person, focus.i, P).keys()) {
      for (const o of index.orgsOfPerson.get(index.people[j].person_id) || []) {
        const at = index.byOrg.get(o);
        if (at !== undefined) out.set(at, (out.get(at) || 0) + 1);
      }
    }
    return out;
  }
  for (const m of index.members.get(index.orgs[focus.i].id) || []) {
    for (const j of partnersOf(links.person, m, P).keys()) out.set(j, (out.get(j) || 0) + 1);
  }
  return out;
}

/** The top-level themes *a* and *b* (`{kind, i}`) share most: `[[node, min share]]`. */
export function sharedThemes(data, a, b, most = 3) {
  const { T, m: ma } = data.top[a.kind];
  const mb = data.top[b.kind].m;
  const out = [];
  for (let k = 0; k < T; k += 1) {
    const v = Math.min(ma[a.i * T + k], mb[b.i * T + k]);
    if (v >= 0.05) out.push([data.index.tops[k], v]);
  }
  return out.sort((x, y) => y[1] - x[1]).slice(0, most);
}

/**
 * The pairs among *targets* (indexes of *kind*) most alike that never wrote together: at most
 * *limit*, the most alike first, `[{a, b, score}]`. Every pair is measured: the caller caps
 * the targets (`PAIRS_CAP`).
 */
export const pairsApart = async (data, measure, kind, targets, limit, signal) => {
  const side = sideOf(data, kind);
  const n = targets.length;
  const links = data.links ? data.links[kind] : null;
  const N = distItems(data.index, kind).length;
  const heap = [];
  const push = (score, a, b) => {
    if (heap.length < limit) {
      heap.push([score, a, b]);
      let c = heap.length - 1;
      while (c > 0) {
        const p = (c - 1) >> 1;
        if (heap[p][0] <= heap[c][0]) break;
        [heap[p], heap[c]] = [heap[c], heap[p]];
        c = p;
      }
    } else if (score > heap[0][0]) {
      heap[0] = [score, a, b];
      let p = 0;
      for (;;) {
        const l = 2 * p + 1;
        const r = l + 1;
        let m = p;
        if (l < heap.length && heap[l][0] < heap[m][0]) m = l;
        if (r < heap.length && heap[r][0] < heap[m][0]) m = r;
        if (m === p) break;
        [heap[p], heap[m]] = [heap[m], heap[p]];
        p = m;
      }
    }
  };
  const near = new Uint8Array(N);
  const tick = distTicker(signal);
  // the space's rows of the targets, side by side: the hot loop reads them in order
  let rowsOf = null;
  if (measure === 'space' && side.vec) {
    const { v, dim } = side.vec;
    rowsOf = new Float32Array(n * dim);
    for (let x = 0; x < n; x += 1) rowsOf.set(v.subarray(targets[x] * dim, (targets[x] + 1) * dim), x * dim);
  }
  for (let x = 0; x < n; x += 1) {
    const a = targets[x];
    const q = distQuery(data, kind, a);
    if (measure === 'space' && !q.v) continue;
    if (links) for (let k = links.ptr[a]; k < links.ptr[a + 1]; k += 1) if (links.nbr[k] < N) near[links.nbr[k]] = 1;
    if (rowsOf) {
      const D = side.vec.dim;
      const has = side.vec.has;
      for (let y = x + 1; y < n; y += 1) {
        const b = targets[y];
        if (near[b] || !has[b]) continue;
        const d = dotRows(rowsOf, x * D, y * D, D);
        if (heap.length < limit || d > heap[0][0]) push(d, a, b);
      }
    } else {
      for (let y = x + 1; y < n; y += 1) {
        const b = targets[y];
        if (near[b]) continue;
        const s = scoreOne(measure, q, side, b);
        if (!Number.isNaN(s)) push(s, a, b);
      }
    }
    if (links) for (let k = links.ptr[a]; k < links.ptr[a + 1]; k += 1) if (links.nbr[k] < N) near[links.nbr[k]] = 0;
    await tick();
  }
  return heap.sort((p, q) => q[0] - p[0]).map(([score, a, b]) => ({ a, b, score }));
};

/**
 * The pairs among *targets* who wrote together, the least alike first: at most *limit*,
 * `[{a, b, score, texts}]`. Reads the links, so it holds for a whole field.
 */
export const pairsTogether = async (data, measure, kind, targets, limit, signal) => {
  const links = data.links ? data.links[kind] : null;
  if (!links) return [];
  const side = sideOf(data, kind);
  const N = distItems(data.index, kind).length;
  const inScope = new Uint8Array(N);
  for (const t of targets) inScope[t] = 1;
  const found = [];
  let done = 0;
  const cost = costOf(measure, data);
  for (const a of targets) {
    const q = distQuery(data, kind, a);
    for (let k = links.ptr[a]; k < links.ptr[a + 1]; k += 1) {
      const b = links.nbr[k];
      if (b <= a || b >= N || !inScope[b]) continue;
      const s = scoreOne(measure, q, side, b);
      if (!Number.isNaN(s)) found.push({ a, b, score: s, texts: links.cnt[k] });
    }
    done += (links.ptr[a + 1] - links.ptr[a]) * cost;
    if (done > DIST_STEP) {
      done = 0;
      await distPause(signal);
    }
  }
  return found.sort((p, q) => p.score - q.score || q.texts - p.texts).slice(0, limit);
};

/** The similarity of each of *rows* to each of *cols* (indexes of their kinds): a Float32Array
 * of rows × cols, NaN where one has no place. */
export const crossMatrix = async (data, measure, rowKind, rows, colKind, cols, signal) => {
  const out = new Float32Array(rows.length * cols.length);
  const side = sideOf(data, colKind);
  for (let r = 0; r < rows.length; r += 1) {
    const q = distQuery(data, rowKind, rows[r]);
    for (let c = 0; c < cols.length; c += 1) out[r * cols.length + c] = scoreOne(measure, q, side, cols[c]);
    if (r % 64 === 63) await distPause(signal);
  }
  return out;
};

/** The shares of *rows* (indexes of *kind*) in *themes* (node ids of one level): rows × themes. */
export function shareMatrix(index, kind, rows, themes, level) {
  const out = new Float32Array(rows.length * themes.length);
  rows.forEach((i, r) => {
    const shares = itemShares(index, kind, i, level);
    themes.forEach((node, c) => { out[r * themes.length + c] = shares[node] || 0; });
  });
  return out;
}

/**
 * How alike the themes of one level are (themes × themes): by `space`, the cosine of each
 * theme's vector (its people's vectors weighted by their share in it); by `themes`, the cosine
 * of the themes' shares over the people (two themes are close when the same people work in
 * both).
 */
export function themeMatrix(data, measure, themes, level) {
  const { index } = data;
  const vec = data.vec ? data.vec.person : null;
  const space = measure === 'space' && vec;
  const width = space ? vec.dim : index.people.length;
  const col = new Map(themes.map((id, j) => [id, j]));
  const rows = new Float64Array(themes.length * width);
  index.people.forEach((p, i) => {
    if (p.x === null || p.x === undefined) return; // the people on the map, as the app's
    for (const [node, share] of Object.entries((p.shares && p.shares[level - 1]) || {})) {
      const j = col.get(node);
      if (j === undefined) continue;
      if (!space) rows[j * width + i] = share;
      else if (vec.has[i]) for (let k = 0; k < width; k += 1) rows[j * width + k] += share * vec.v[i * width + k];
    }
  });
  const n = themes.length;
  const norm = new Float64Array(n);
  for (let j = 0; j < n; j += 1) {
    let s = 0;
    for (let k = 0; k < width; k += 1) s += rows[j * width + k] ** 2;
    norm[j] = Math.sqrt(s);
  }
  const out = new Float32Array(n * n);
  for (let a = 0; a < n; a += 1) {
    for (let b = a; b < n; b += 1) {
      let d = 0;
      for (let k = 0; k < width; k += 1) d += rows[a * width + k] * rows[b * width + k];
      const v = norm[a] && norm[b] ? d / (norm[a] * norm[b]) : NaN;
      out[a * n + b] = v;
      out[b * n + a] = v;
    }
  }
  return out;
}

/** *items* (indexes of *kind*) ordered by their main top-level theme (the tree's order), then
 * by their share in it, so that the blocks of a matrix show. */
export function orderByTheme(index, kind, items) {
  const rank = new Map(index.tops.map((id, k) => [id, k]));
  const key = items.map((i) => {
    const shares = itemShares(index, kind, i, 1);
    const top = mainTheme(index, kind, i, 1);
    return [i, top === null ? index.tops.length : rank.get(top), top === null ? 0 : shares[top]];
  });
  return key.sort((x, y) => x[1] - y[1] || y[2] - x[2] || x[0] - y[0]).map((k) => k[0]);
}

/** The text of a CSV file (RFC 4180, a byte-order mark so that spreadsheets read UTF-8); a
 * cell that starts like a formula is quoted with an apostrophe. */
export function csvText(rows) {
  const cell = (v) => {
    if (v === null || v === undefined || (typeof v === 'number' && Number.isNaN(v))) return '';
    let s = String(v);
    if (typeof v !== 'number' && /^[=+\-@\t\r]/.test(s)) s = `'${s}`;
    return /[",\n\r;]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return `﻿${rows.map((r) => r.map(cell).join(',')).join('\r\n')}\r\n`;
}
