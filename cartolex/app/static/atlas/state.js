// SPDX-License-Identifier: MIT
/**
 * The atlas's state, kept in the host's address so that a view can be shared and survives
 * a reload: the focus (`sel`, `kind:id`) and the other side of a comparison (`with`), the
 * node the treemap opened (`open`), what the map shows (`show`) and names (`names`), the
 * organisations' level (`org`), the network's rings (`net`), the people's filters (`f`,
 * repeated `column:value`), the period (`from`, `to`), the keywords' categories (`kc`) and
 * colour (`kcol`), the view (`view`: `map` or `world`), a base map (`base`) and points or
 * regions (`as`). Values that are the defaults are left out of the address.
 */

/** What the map can show, in the order of the layers panel. */
export const KINDS = ['people', 'keywords', 'organisations', 'texts', 'projected', 'windows'];
/** The kinds the layers panel shows first; the others are folded under « More ». */
export const MAIN_KINDS = ['people', 'keywords', 'organisations'];
/** The kinds whose names the map can write. */
export const NAMED_KINDS = ['people', 'keywords', 'organisations'];

/** The symbol of each kind: one shape per kind, on the map and in the legend. */
export const SHAPE_OF = {
  people: 'circle',
  keywords: 'diamond',
  organisations: 'tile',
  texts: 'triangle',
  projected: 'ring',
  windows: 'plus',
  themes: 'square',
};

const DEFAULT_SHOW = ['people', 'keywords', 'organisations'];
const FOCUS_KINDS = ['theme', 'keyword', 'person', 'organisation', 'projected', 'text'];

function selOf(text) {
  const cut = text ? text.indexOf(':') : -1;
  if (cut <= 0) return null;
  const kind = text.slice(0, cut);
  return FOCUS_KINDS.includes(kind) ? { kind, id: text.slice(cut + 1) } : null;
}

/** `kind:id` of a selection, '' for none. */
export function selKey(sel) {
  return sel ? `${sel.kind}:${sel.id}` : '';
}

/** Whether two selections name the same thing. */
export function sameSel(a, b) {
  return selKey(a) === selKey(b);
}

/** The state of an address's query (`URLSearchParams`). */
export function readAtlasState(query) {
  const q = query || new URLSearchParams();
  const list = (key) => (q.get(key) || '').split(',').filter(Boolean);
  const year = (key) => {
    const v = Number.parseInt(q.get(key) || '', 10);
    return Number.isFinite(v) ? v : null;
  };
  const net = Number.parseInt(q.get('net') || '', 10);
  return {
    sel: selOf(q.get('sel')),
    with: selOf(q.get('with')),
    open: q.get('open') || q.get('theme') || '',
    show: q.has('show') ? list('show').filter((k) => KINDS.includes(k)) : DEFAULT_SHOW.slice(),
    names: list('names').filter((k) => NAMED_KINDS.includes(k)),
    org: q.get('org') || '',
    net: Number.isFinite(net) ? Math.max(0, Math.min(3, net)) : 1,
    filters: q.getAll('f').filter((f) => f.includes(':')),
    from: year('from'),
    to: year('to'),
    kc: list('kc'),
    kcol: q.get('kcol') === 'category' ? 'category' : 'theme',
    view: q.get('view') === 'world' ? 'world' : 'map',
    base: q.get('base') || '',
    as: q.get('as') === 'regions' ? 'regions' : 'points',
  };
}

/** The query of a state (the defaults left out). */
export function queryOfState(state) {
  const q = new URLSearchParams();
  if (state.sel) q.set('sel', selKey(state.sel));
  if (state.sel && state.with) q.set('with', selKey(state.with));
  if (state.open) q.set('open', state.open);
  const show = KINDS.filter((k) => state.show.includes(k)).join(',');
  if (show !== DEFAULT_SHOW.join(',')) q.set('show', show);
  if (state.names.length) q.set('names', state.names.join(','));
  if (state.org) q.set('org', state.org);
  if (state.net !== 1) q.set('net', String(state.net));
  for (const f of state.filters) q.append('f', f);
  if (state.from !== null) q.set('from', String(state.from));
  if (state.to !== null) q.set('to', String(state.to));
  if (state.kc.length) q.set('kc', state.kc.join(','));
  if (state.kcol === 'category') q.set('kcol', 'category');
  if (state.view !== 'map') q.set('view', state.view);
  if (state.base) q.set('base', state.base);
  if (state.as !== 'points') q.set('as', state.as);
  return q;
}

/** Whether any filter is on (the people's columns, the keywords' categories, the period). */
export function anyFilter(state) {
  return state.filters.length > 0 || state.kc.length > 0 || state.from !== null || state.to !== null;
}

/**
 * The state's store: `get()`, `set(patch, {push})` (a change of focus is a step Back can
 * undo when *push*), `back()`, `canGoBack()`, `subscribe(fn)`. Every change is written to
 * the host's address.
 */
export function createStore(initial, address) {
  let state = initial;
  const steps = [];
  const fns = new Set();
  const write = () => {
    if (address && address.write) address.write(queryOfState(state));
  };
  const emit = (old) => {
    write();
    for (const fn of fns) fn(state, old);
  };
  return {
    get: () => state,
    set(patch, { push = false } = {}) {
      const old = state;
      if (push) {
        steps.push({ sel: old.sel, with: old.with, open: old.open });
        if (steps.length > 100) steps.shift();
      }
      state = { ...state, ...patch };
      emit(old);
    },
    back() {
      const step = steps.pop();
      if (!step) return;
      const old = state;
      state = { ...state, ...step };
      emit(old);
    },
    canGoBack: () => steps.length > 0,
    subscribe(fn) {
      fns.add(fn);
      return () => fns.delete(fn);
    },
  };
}
