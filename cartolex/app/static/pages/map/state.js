// SPDX-License-Identifier: MIT
/**
 * The atlas page's state, kept in the address so that a view can be shared
 * and survives a reload: what the map shows (`show`), as points or regions
 * (`as`), the organisations' level (`org`), the period (`from`, `to`), the
 * people's filters (`f`, repeated `column:value`), the selection (`sel`,
 * `kind:id`), the treemap's zoom (`theme`), the view (`view`: `map` or
 * `world`), the base map (`base`), the keywords' categories shown (`kc`,
 * comma-separated; none: every one) and the keywords' colour (`kcol`: by
 * `theme` or by `category`). Values that are the defaults are left
 * out of the address.
 */

/** What the map can show, in the order of the controls. */
export const KINDS = ['people', 'keywords', 'organisations', 'texts', 'projected', 'windows'];

/** The symbol of each kind: one shape per kind, on the map and in the legend. */
export const SHAPE_OF = {
  people: 'circle',
  keywords: 'diamond',
  organisations: 'square',
  texts: 'triangle',
  projected: 'ring',
  windows: 'plus',
};

const DEFAULT_SHOW = ['people', 'keywords'];

/** The state of an address's query. */
export function readState(query) {
  const q = query || new URLSearchParams();
  const show = (q.get('show') || '').split(',').filter((k) => KINDS.includes(k));
  const year = (key) => {
    const v = Number.parseInt(q.get(key) || '', 10);
    return Number.isFinite(v) ? v : null;
  };
  const sel = q.get('sel');
  const cut = sel ? sel.indexOf(':') : -1;
  return {
    show: q.has('show') ? show : DEFAULT_SHOW.slice(),
    as: q.get('as') === 'regions' ? 'regions' : 'points',
    org: q.get('org') || '',
    from: year('from'),
    to: year('to'),
    filters: q.getAll('f').filter((f) => f.includes(':')),
    sel: cut > 0 ? { kind: sel.slice(0, cut), id: sel.slice(cut + 1) } : null,
    theme: q.get('theme') || '',
    view: q.get('view') === 'world' ? 'world' : 'map',
    base: q.get('base') || '',
    kc: (q.get('kc') || '').split(',').filter(Boolean),
    kcol: q.get('kcol') === 'category' ? 'category' : 'theme',
  };
}

/** The query of a state (the defaults left out). */
export function queryOf(state) {
  const q = new URLSearchParams();
  const show = state.show.join(',');
  if (show !== DEFAULT_SHOW.join(',')) q.set('show', show);
  if (state.as !== 'points') q.set('as', state.as);
  if (state.org) q.set('org', state.org);
  if (state.from !== null) q.set('from', String(state.from));
  if (state.to !== null) q.set('to', String(state.to));
  for (const f of state.filters) q.append('f', f);
  if (state.sel) q.set('sel', `${state.sel.kind}:${state.sel.id}`);
  if (state.theme) q.set('theme', state.theme);
  if (state.view !== 'map') q.set('view', state.view);
  if (state.base) q.set('base', state.base);
  if (state.kc && state.kc.length) q.set('kc', state.kc.join(','));
  if (state.kcol === 'category') q.set('kcol', 'category');
  return q;
}

/** Put *state* in the address, replacing the current entry (no new history step). */
export function writeState(state) {
  const url = new URL(window.location.href);
  const q = queryOf(state).toString();
  const next = url.pathname + (q ? `?${q}` : '') + url.hash;
  if (next !== url.pathname + url.search + url.hash) {
    window.history.replaceState(window.history.state, '', next);
  }
}

/** Every filter cleared: the people's columns, the keywords' categories and the period. */
export function withoutFilters(state) {
  return { ...state, filters: [], kc: [], from: null, to: null };
}

/** Whether any filter is on (the people's columns, the keywords' categories or the period). */
export function filtered(state) {
  return state.filters.length > 0 || (state.kc || []).length > 0 || state.from !== null
    || state.to !== null;
}

/** The filters grouped by column: a person matches one value of each column. */
export function filterGroups(filters) {
  const groups = new Map();
  for (const f of filters) {
    const cut = f.indexOf(':');
    const column = f.slice(0, cut);
    if (!groups.has(column)) groups.set(column, new Set());
    groups.get(column).add(f.slice(cut + 1));
  }
  return groups;
}
