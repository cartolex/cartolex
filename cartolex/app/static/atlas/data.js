// SPDX-License-Identifier: MIT
/**
 * The atlas bundle (the source's `bundle()`, the shape of `GET /api/atlas`) indexed for the
 * atlas: the theme tree (children, levels, top-level ancestors, where each node sits on the
 * map, its own keywords), people with their largest share and their filters, organisations
 * with their current members and the mean of their members' shares at every level,
 * projected people, names; and the time windows per person, which come apart
 * (`indexWindows`). A bundle of a map version in three dimensions (`dimensions: 3`) gives
 * every place a `z`: the index keeps it beside x and y.
 */

/** The categories of keywords an AI or a person gave. */
export const KEYWORD_CATEGORIES = ['concept', 'method', 'object', 'place', 'field', 'none'];

/** The id and value of the largest entry of `{id: share}`. */
export function largestShare(shares) {
  let best = null;
  let value = 0;
  for (const [id, share] of Object.entries(shares || {})) {
    if (share > value) {
      best = id;
      value = share;
    }
  }
  return [best, value];
}

/** A two-letter language of a locale tag (`pt-BR` → `pt`). */
export function langOf(locale) {
  return String(locale || 'en').slice(0, 2).toLowerCase();
}

/** A name from `{lang: name}` in the language of *locale*, else English, else any. */
export function nameIn(names, locale) {
  const n = names || {};
  return n[langOf(locale)] || n.en || Object.values(n).find(Boolean) || '';
}

/** Everything the atlas reads from the bundle, by id. */
export function indexBundle(bundle) {
  const nodes = new Map();
  const children = new Map([[null, []]]);
  for (const n of bundle.nodes || []) {
    nodes.set(n.id, n);
    children.set(n.id, []);
  }
  for (const n of bundle.nodes || []) {
    const parent = n.parent && nodes.has(n.parent) ? n.parent : null;
    children.get(parent).push(n.id);
  }
  for (const list of children.values()) {
    list.sort((a, b) => ((nodes.get(a).order || 0) - (nodes.get(b).order || 0)) || (a < b ? -1 : 1));
  }
  const tops = children.get(null);
  const topOf = new Map();
  const topIndex = new Map(tops.map((id, i) => [id, i]));
  let maxLevel = 1;
  const visit = (id, top) => {
    topOf.set(id, top);
    maxLevel = Math.max(maxLevel, nodes.get(id).level || 1);
    for (const c of children.get(id) || []) visit(c, top);
  };
  for (const top of tops) visit(top, top);
  /** The palette index of a node's top-level theme (tops.length: none). */
  const colourOf = (node) => (node && topOf.has(node) ? topIndex.get(topOf.get(node)) : tops.length);

  const dimensions = bundle.dimensions === 3 ? 3 : 2;
  const keywords = bundle.keywords || [];
  const byTerm = new Map(keywords.map((k, i) => [k.term, i]));
  const nodeKeywords = new Map();
  const centres = new Map();
  const categoryCounts = {};
  keywords.forEach((k, i) => {
    const cat = k.category || 'none';
    categoryCounts[cat] = (categoryCounts[cat] || 0) + 1;
    if (!k.node || !nodes.has(k.node)) return;
    if (!nodeKeywords.has(k.node)) nodeKeywords.set(k.node, []);
    nodeKeywords.get(k.node).push(i);
    if (k.x === null || k.x === undefined) return;
    for (let at = k.node; at && nodes.has(at); at = nodes.get(at).parent) {
      const c = centres.get(at) || [0, 0, 0, 0, []];
      c[0] += k.x;
      c[1] += k.y;
      c[2] += 1;
      c[3] += k.z || 0;
      if (dimensions === 3) c[4].push(i);
      centres.set(at, c);
    }
  });
  for (const list of nodeKeywords.values()) list.sort((a, b) => (keywords[b].weight || 0) - (keywords[a].weight || 0));
  for (const [id, c] of centres) {
    const at = { x: c[0] / c[2], y: c[1] / c[2] };
    if (dimensions === 3) {
      // in space a theme's mean may fall in the void between its parts: its keyword nearest
      // that mean stands for it (where its name is written, where « centre on » goes)
      at.z = c[3] / c[2];
      let best = Infinity;
      let near = null;
      for (const i of c[4]) {
        const k = keywords[i];
        const d = (k.x - at.x) ** 2 + (k.y - at.y) ** 2 + ((k.z || 0) - at.z) ** 2;
        if (d < best) {
          best = d;
          near = k;
        }
      }
      if (near) Object.assign(at, { x: near.x, y: near.y, z: near.z || 0 });
    }
    centres.set(id, at);
  }

  const people = bundle.people || [];
  const extra = bundle.people_extra || {};
  const byPerson = new Map();
  const personTop = people.map((p, i) => {
    if (p.person_id) byPerson.set(p.person_id, i);
    return largestShare(p.shares && p.shares[0])[0];
  });

  // Organisations: their current members (on the map), directly or through one below them.
  const orgs = bundle.organisations || [];
  const byOrg = new Map(orgs.map((o, i) => [o.id, i]));
  const members = new Map(orgs.map((o) => [o.id, []]));
  const parents = new Map(orgs.map((o) => [o.id, o.parents || []]));
  const orgsOfPerson = new Map();
  people.forEach((p, i) => {
    const info = extra[p.person_id];
    if (!info || p.x === null || p.x === undefined) return;
    const seen = new Set();
    const todo = [...(info.orgs || [])];
    while (todo.length) {
      const o = todo.pop();
      if (seen.has(o) || !members.has(o)) continue;
      seen.add(o);
      members.get(o).push(i);
      todo.push(...(parents.get(o) || []));
    }
    orgsOfPerson.set(p.person_id, [...seen]);
  });
  const sharesCache = new Map();
  /** The mean of an organisation's members' shares at *level* (1: the top). */
  const orgSharesAt = (id, level = 1) => {
    const key = `${id}|${level}`;
    if (!sharesCache.has(key)) {
      const sum = {};
      const list = members.get(id) || [];
      for (const i of list) {
        for (const [n, v] of Object.entries((people[i].shares && people[i].shares[level - 1]) || {})) {
          sum[n] = (sum[n] || 0) + v / list.length;
        }
      }
      sharesCache.set(key, sum);
    }
    return sharesCache.get(key);
  };
  const orgTop = orgs.map((o) => largestShare(orgSharesAt(o.id, 1))[0]);

  const projected = (bundle.overlays || []).filter((o) => o.x !== null && o.x !== undefined && o.y !== null);
  const byProjected = new Map(projected.map((o, i) => [o.person_id, i]));

  const years = bundle.years || {};
  const spans = bundle.window_years || {};
  let first = years.min;
  let last = years.max;
  if (spans.min !== undefined && (first === null || first === undefined || spans.min < first)) first = spans.min;
  if (spans.max !== undefined && (last === null || last === undefined || spans.max > last)) last = spans.max;
  return {
    bundle, dimensions, nodes, children, tops, topOf, topIndex, colourOf, maxLevel, centres, nodeKeywords,
    keywords, byTerm, categoryCounts,
    people, extra, byPerson, personTop,
    orgs, byOrg, members, orgSharesAt, orgTop, orgsOfPerson,
    projected, byProjected,
    windows: new Map(),
    levels: (bundle.organisation_levels || []).filter((lv) => lv.count === undefined || lv.count > 0),
    columns: bundle.columns || [],
    years: { min: first === undefined ? null : first, max: last === undefined ? null : last },
  };
}

/** The time windows (columns of the source's `windows()`), by person id, each list in time
 * order: `{person_id, start, end, texts, x, y, z, top}` (z 0 on a flat map), added to *into*. */
export function indexWindows(index, data, into = new Map()) {
  const fresh = new Map();
  const n = data && data.person ? data.person.length : 0;
  for (let k = 0; k < n; k += 1) {
    const p = index.people[data.person[k]];
    if (!p || !p.person_id || data.x[k] === null) continue;
    if (!fresh.has(p.person_id)) fresh.set(p.person_id, []);
    fresh.get(p.person_id).push({ person_id: p.person_id, start: data.start[k], end: data.end[k],
      texts: data.texts[k], x: data.x[k], y: data.y[k], z: data.z ? data.z[k] : 0, top: data.top[k] });
  }
  for (const list of fresh.values()) list.sort((a, b) => a.start - b.start);
  return new Map([...into, ...fresh]);
}

/** The filters grouped by column: a person matches one value of each column. */
export function filterGroups(filters) {
  const groups = new Map();
  for (const f of filters || []) {
    const cut = f.indexOf(':');
    const column = f.slice(0, cut);
    if (!groups.has(column)) groups.set(column, new Set());
    groups.get(column).add(f.slice(cut + 1));
  }
  return groups;
}

/** The people who match the state's filters, as a Uint8Array (1: matches). */
export function matchingPeople(index, state) {
  const groups = filterGroups(state.filters);
  const out = new Uint8Array(index.people.length);
  for (let i = 0; i < out.length; i += 1) {
    let ok = true;
    if (groups.size) {
      const info = index.extra[index.people[i].person_id];
      const columns = (info && info.columns) || {};
      for (const [column, values] of groups) {
        if (!values.has(columns[column])) {
          ok = false;
          break;
        }
      }
    }
    out[i] = ok ? 1 : 0;
  }
  return out;
}

/** The nodes under *node* (itself included). */
export function nodesUnder(index, node) {
  const out = new Set();
  const todo = [node];
  while (todo.length) {
    const id = todo.pop();
    if (out.has(id) || !index.nodes.has(id)) continue;
    out.add(id);
    todo.push(...(index.children.get(id) || []));
  }
  return out;
}

/** The path from the top to *node* (itself included). */
export function pathTo(index, node) {
  const out = [];
  for (let at = node; at && index.nodes.has(at); at = index.nodes.get(at).parent) out.unshift(at);
  return out;
}

/** The period of the state within the map's years: `[from, to]`, or null without years. */
export function periodOf(index, state) {
  const { min, max } = index.years;
  if (min === null || max === null) return null;
  const from = state.from === null ? min : Math.max(min, Math.min(max, state.from));
  const to = state.to === null ? max : Math.max(from, Math.min(max, state.to));
  return [from, to];
}

/** The organisations' level the state chose (the first one by default). */
export function orgLevelOf(index, state) {
  if (state.org && index.levels.some((lv) => lv.id === state.org)) return state.org;
  return index.levels[0] ? index.levels[0].id : '';
}

/** An item's share vector at *level* (1: the top): a person's, an organisation's or a
 * projected person's; `{}` when it has none. */
export function sharesOf(index, sel, level = 1) {
  if (!sel) return {};
  if (sel.kind === 'person' && index.byPerson.has(sel.id)) {
    const p = index.people[index.byPerson.get(sel.id)];
    return (p.shares && p.shares[level - 1]) || {};
  }
  if (sel.kind === 'projected' && index.byProjected.has(sel.id)) {
    const p = index.projected[index.byProjected.get(sel.id)];
    return (p.shares && p.shares[level - 1]) || {};
  }
  if (sel.kind === 'organisation' && index.byOrg.has(sel.id)) return index.orgSharesAt(sel.id, level);
  return {};
}

/** The x, y and z (0 on a flat map) of a selection on the map, or null when it has no place
 * there. */
export function placeOf(index, sel, texts) {
  if (!sel) return null;
  let at = null;
  if (sel.kind === 'person' && index.byPerson.has(sel.id)) at = index.people[index.byPerson.get(sel.id)];
  else if (sel.kind === 'organisation' && index.byOrg.has(sel.id)) at = index.orgs[index.byOrg.get(sel.id)];
  else if (sel.kind === 'keyword' && index.byTerm.has(sel.id)) at = index.keywords[index.byTerm.get(sel.id)];
  else if (sel.kind === 'projected' && index.byProjected.has(sel.id)) at = index.projected[index.byProjected.get(sel.id)];
  else if (sel.kind === 'theme' && index.centres.has(sel.id)) at = index.centres.get(sel.id);
  else if (sel.kind === 'text' && texts) {
    const i = texts.id.indexOf(sel.id);
    if (i >= 0) at = { x: texts.x[i], y: texts.y[i], z: texts.z ? texts.z[i] : 0 };
  }
  return at && at.x !== null && at.x !== undefined ? { x: at.x, y: at.y, z: at.z || 0 } : null;
}
