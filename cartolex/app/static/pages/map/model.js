// SPDX-License-Identifier: MIT
/**
 * The atlas bundle (`GET /api/atlas`), indexed for the page: the theme tree
 * (children, top-level ancestors, hue families), people with their largest
 * share and their filters, organisations with their current members and the
 * mean of their members' shares, time windows per person, and names.
 */
import { lang2, nodeName } from '../themes/model.js';
import { filterGroups } from './state.js';

/** Palette index of what no top-level theme holds (the last colour). */
export const NEUTRAL = 12;
/** The palette of the map: one token per hue family, then a neutral grey. */
export const PALETTE = [...Array.from({ length: 12 }, (_, i) => `--cx-hue-${i + 1}`), '--cx-text-muted'];

/** The id and value of the largest entry of `{id: share}`. */
export function largest(shares) {
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

/** Everything the page reads from the bundle, by id. */
export function indexAtlas(atlas) {
  const nodes = new Map();
  const children = new Map([[null, []]]);
  for (const n of atlas.nodes || []) {
    nodes.set(n.id, n);
    children.set(n.id, []);
  }
  for (const n of atlas.nodes || []) {
    const parent = n.parent && nodes.has(n.parent) ? n.parent : null;
    children.get(parent).push(n.id);
  }
  for (const list of children.values()) {
    list.sort((a, b) => (nodes.get(a).order - nodes.get(b).order) || (a < b ? -1 : 1));
  }
  const tops = children.get(null);
  const topOf = new Map();
  const hue = new Map();
  const visit = (id, top) => {
    topOf.set(id, top);
    hue.set(id, tops.indexOf(top) % 12);
    for (const c of children.get(id) || []) visit(c, top);
  };
  for (const top of tops) visit(top, top);
  const colourOf = (node) => (node && hue.has(node) ? hue.get(node) : NEUTRAL);

  const people = atlas.people || [];
  const extra = atlas.people_extra || {};
  const byPerson = new Map();
  const personTop = people.map((p, i) => {
    if (p.person_id) byPerson.set(p.person_id, i);
    return largest(p.shares && p.shares[0])[0];
  });

  const keywords = atlas.keywords || [];
  const byTerm = new Map(keywords.map((k, i) => [k.term, i]));

  // Organisations: their current members (on the map) and the mean of their members' shares.
  const orgs = atlas.organisations || [];
  const byOrg = new Map(orgs.map((o, i) => [o.id, i]));
  const members = new Map(orgs.map((o) => [o.id, []]));
  const parents = new Map(orgs.map((o) => [o.id, o.parents || []]));
  people.forEach((p, i) => {
    const info = extra[p.person_id];
    if (!info || p.x === null) return;
    const seen = new Set();
    const todo = [...(info.orgs || [])];
    while (todo.length) {
      const o = todo.pop();
      if (seen.has(o) || !members.has(o)) continue;
      seen.add(o);
      members.get(o).push(i);
      todo.push(...(parents.get(o) || []));
    }
  });
  const orgShares = new Map();
  for (const o of orgs) {
    const sum = {};
    const list = members.get(o.id);
    for (const i of list) {
      for (const [id, v] of Object.entries((people[i].shares && people[i].shares[0]) || {})) {
        sum[id] = (sum[id] || 0) + v / list.length;
      }
    }
    orgShares.set(o.id, sum);
  }
  const orgTop = orgs.map((o) => largest(orgShares.get(o.id))[0]);

  const windows = new Map();
  for (const w of atlas.trajectories || []) {
    if (!w.person_id || w.x === null) continue;
    if (!windows.has(w.person_id)) windows.set(w.person_id, []);
    windows.get(w.person_id).push(w);
  }
  for (const list of windows.values()) list.sort((a, b) => a.start - b.start);

  const years = atlas.years || {};
  let first = years.min;
  let last = years.max;
  for (const w of atlas.trajectories || []) {
    if (first === null || first === undefined || w.start < first) first = w.start;
    if (last === null || last === undefined || w.end > last) last = w.end;
  }
  return {
    atlas, nodes, children, tops, topOf, hue, colourOf,
    people, extra, byPerson, personTop,
    keywords, byTerm,
    orgs, byOrg, members, orgShares, orgTop,
    windows,
    projected: (atlas.overlays || []).filter((o) => o.x !== null && o.y !== null),
    levels: atlas.organisation_levels || [],
    columns: atlas.columns || [],
    years: { min: first === undefined ? null : first, max: last === undefined ? null : last },
  };
}

/** Whether person *i* matches the filters (one value of each filtered column). */
export function personMatches(index, i, groups) {
  if (!groups.size) return true;
  const info = index.extra[index.people[i].person_id];
  const columns = (info && info.columns) || {};
  for (const [column, values] of groups) {
    if (!values.has(columns[column])) return false;
  }
  return true;
}

/** The people who match the state's filters, as a Uint8Array (1: matches). */
export function matching(index, state) {
  const groups = filterGroups(state.filters);
  const out = new Uint8Array(index.people.length);
  for (let i = 0; i < out.length; i += 1) out[i] = personMatches(index, i, groups) ? 1 : 0;
  return out;
}

/** The node under *theme* (itself included). */
export function under(index, theme) {
  const out = new Set();
  const todo = [theme];
  while (todo.length) {
    const id = todo.pop();
    if (out.has(id) || !index.nodes.has(id)) continue;
    out.add(id);
    todo.push(...(index.children.get(id) || []));
  }
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

/** A node's name in the interface's language. */
export function themeName(index, id, locale) {
  return nodeName(index.nodes.get(id), lang2(locale));
}

/** An organisation's short name (its acronym, else its name). */
export function orgName(org) {
  return (org && (org.acronym || org.name)) || '';
}

/** A level's name in the interface's language, else its id. */
export function levelLabel(level, locale) {
  const names = (level && level.names) || {};
  return names[lang2(locale)] || names.en || Object.values(names).find(Boolean) || (level && level.id) || '';
}
