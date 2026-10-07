// SPDX-License-Identifier: MIT
/**
 * What Distances compares: the people on the map or the organisations of one level, all of
 * them or those of one theme (their main theme at its level); and, for a matrix of people,
 * a selection: an organisation's members, a theme's people or a person and their co-authors.
 * The caps keep every computation in the browser's reach: a matrix shows at most
 * `MATRIX_CAP` rows, every pair is measured among at most `PAIRS_CAP` items.
 */
import { distItems, itemShares, mainTheme, orderByTheme, partnersOf } from './engine.js';

/** The most rows (and columns) of a matrix of people or organisations. */
export const MATRIX_CAP = 300;
/** The most items whose every pair is measured (« talk the same, don't work together »). */
export const PAIRS_CAP = 5000;
/** Rows of the ranked list per page, and pairs listed. */
export const RANK_PAGE = 50;
export const PAIRS_LIMIT = 200;

/** `kind:id` as `{kind, id}` (null for anything else). */
export function distRef(text) {
  const cut = String(text || '').indexOf(':');
  if (cut <= 0) return null;
  return { kind: text.slice(0, cut), id: text.slice(cut + 1) };
}

/** The level of theme *id* (1: the top). */
export function themeLevel(index, id) {
  const n = index.nodes.get(id);
  return n ? n.level || 1 : 1;
}

/** The themes of *level*, in the tree's order. */
export function themesAt(index, level) {
  const out = [];
  const visit = (id) => {
    const n = index.nodes.get(id);
    if ((n.level || 1) === level) out.push(id);
    else for (const c of index.children.get(id) || []) visit(c);
  };
  index.tops.forEach(visit);
  return out;
}

/** Whether item *i* of *kind* counts: a person on the map, an organisation with members on it
 * (of *level*). */
function counted(index, kind, i, level) {
  if (kind === 'organisation') {
    const o = index.orgs[i];
    return (!level || o.level === level) && (index.members.get(o.id) || []).length > 0;
  }
  const p = index.people[i];
  return Boolean(p.person_id) && p.x !== null && p.x !== undefined;
}

/** The items of *kind* (organisations of *level*) whose main theme is *theme* (any theme when
 * null), as indexes. */
export function scopeOf(index, kind, { level = null, theme = null } = {}) {
  const at = theme ? themeLevel(index, theme) : 1;
  const out = [];
  distItems(index, kind).forEach((it, i) => {
    if (!counted(index, kind, i, level)) return;
    if (theme && mainTheme(index, kind, i, at) !== theme) return;
    out.push(i);
  });
  return Int32Array.from(out);
}

/** At most *cap* of *items*: the organisations with the most members, the people with the
 * largest share in *theme* (else in their main theme); then ordered by theme. Answers
 * `{items, total}`. */
export function capped(index, kind, items, cap, theme = null) {
  let list = Array.from(items);
  const total = list.length;
  if (list.length > cap) {
    const level = theme ? themeLevel(index, theme) : 1;
    const weight = (i) => {
      if (kind === 'organisation') return (index.members.get(index.orgs[i].id) || []).length;
      const shares = itemShares(index, kind, i, level);
      return theme ? shares[theme] || 0 : Math.max(0, ...Object.values(shares));
    };
    list = list.map((i) => [i, weight(i)]).sort((a, b) => b[1] - a[1] || a[0] - b[0]).slice(0, cap).map((x) => x[0]);
  }
  return { items: orderByTheme(index, kind, list), total };
}

/**
 * The people of a selection (`{kind, id}`): an organisation's members, a theme's people (their
 * main theme at its level), or a person and their co-authors (from *links*, the people's CSR),
 * capped at *cap* and ordered by theme: `{items, total}`; null for an unknown selection.
 */
export function selectionOf(index, links, sel, cap = MATRIX_CAP) {
  if (!sel) return null;
  if (sel.kind === 'organisation' && index.byOrg.has(sel.id)) {
    const items = (index.members.get(sel.id) || []).filter((i) => counted(index, 'person', i));
    return capped(index, 'person', items, cap);
  }
  if (sel.kind === 'theme' && index.nodes.has(sel.id)) {
    return capped(index, 'person', scopeOf(index, 'person', { theme: sel.id }), cap, sel.id);
  }
  if (sel.kind === 'person' && index.byPerson.has(sel.id)) {
    const i = index.byPerson.get(sel.id);
    const near = links ? [...partnersOf(links, i, index.people.length).keys()] : [];
    const items = [i, ...near.filter((j) => counted(index, 'person', j))];
    // the person first, then their co-authors with the most texts together
    const kept = items.slice(0, cap);
    return { items: [i, ...orderByTheme(index, 'person', kept.slice(1))], total: items.length };
  }
  return null;
}
