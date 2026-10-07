// SPDX-License-Identifier: MIT
/**
 * Distances: how alike people and organisations are, one piece of code that the app (a tab
 * of the Map screen) and the offline site (its « Distances » page) both mount, as they mount
 * the atlas (`docs/dev/atlas.md`, « Distances »). Three views:
 *
 * - **Ranked list** (`rank.js`): a person or an organisation, and every other from the most
 *   to the least alike, with the themes they share and whether they write together;
 * - **Pairs** (`pairs.js`): those who talk the same but never wrote together, or who write
 *   together but talk differently;
 * - **Matrices** (`matrix.js`): organisations, themes, or a selection of people, as a heatmap
 *   ordered by theme.
 *
 * `mountDistances(root, {source, host})`: the source is the atlas's (`bundle()`, and
 * `vectors(kind)`, `links(kind)`, `measure()` when it has them), the host the atlas's too
 * (`t`, `locale`, `address`, `prefs`, `look`, `label`, `links`) with `atlas(sel, other)`, the
 * address of the atlas showing *sel* (compared with *other*), and `fileStem()`. The state
 * lives in the address (`d`, `of`, `m`, `among`, `th`, `pg`, `mode`, `mx`, `in`, `tl`), so a
 * view can be shared and survives a reload.
 */
import { fill, h, listen } from '../atlas/dom.js';
import { indexBundle, nameIn } from '../atlas/data.js';
import { coloursOf, pageIsDark } from '../atlas/atlas.js';
import { DEFAULT_SCHEME } from '../atlas/schemes.js';
import { DIST_MEASURES, csvText, topRows, unitRows } from './engine.js';
import { createRankView } from './rank.js';
import { createPairsView } from './pairs.js';
import { SHARE_MATRICES, createMatrixView } from './matrix.js';
import { ALL_MEASURES } from './remote.js';
import { findEntries } from '../atlas/find.js';

/** The views, in the order of the tabs. */
export const DIST_VIEWS = ['rank', 'pairs', 'matrix'];
const DIST_KEYS = ['d', 'of', 'm', 'among', 'th', 'pg', 'mode', 'mx', 'in', 'tl'];

/** The state of an address's query: `{d, of, m, among, th, pg, mode, mx, in, tl}` (strings). */
export function readDistState(query) {
  const q = query || new URLSearchParams();
  const out = {};
  for (const k of DIST_KEYS) out[k] = q.get(k) || '';
  if (!DIST_VIEWS.includes(out.d)) out.d = 'rank';
  return out;
}

/** Mount Distances in *root*; answers `{destroy(), state(), set(patch)}`. */
export function mountDistances(root, { source, host }) {
  const t = host.t;
  const locale = host.locale || 'en';
  const fmt = {
    number: (v) => new Intl.NumberFormat(locale).format(Number(v) || 0),
    percent: (v) => new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 0 }).format(Number(v) || 0),
    decimal: (v) => (v === null || v === undefined || Number.isNaN(v) ? '—'
      : new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v)),
  };
  let state = readDistState(host.address ? host.address.read() : null);
  let alive = true;
  const offs = [];
  const loaded = new Map();
  const ctx = {
    t, fmt, locale, host, source, index: null, data: null, colours: null, measure: 'space', measures: [], remote: false,
    /** Whether the browser computes *measure* (else the host's server does). */
    local: (measure) => DIST_MEASURES.includes(measure),
    state: () => state,
    set,
    need,
    nameOf,
    themeName: (id) => (ctx.index.nodes.has(id) ? nameIn(ctx.index.nodes.get(id).names, locale) : id),
    levelName: (id) => {
      const lv = ctx.index.levels.find((l) => l.id === id);
      return lv ? nameIn(lv.names, locale) || lv.id : id || '';
    },
    colour: (node) => {
      const i = ctx.index.colourOf(node);
      return i < ctx.colours.themes.length ? ctx.colours.themes[i] : ctx.colours.neutral;
    },
    scheme: () => (host.prefs && host.prefs.get && host.prefs.get('colour_scheme')) || DEFAULT_SCHEME,
    dark: () => (host.look && host.look.dark ? host.look.dark() : pageIsDark()),
    pageOf: (kind, i) => pageLink(kind, i),
    compareHref: (a, b) => (host.atlas ? host.atlas(refOf(a), refOf(b)) : null),
    atlasHref: (sel) => (host.atlas ? host.atlas(sel, null) : null),
    download,
    entries: () => {
      if (!entries) entries = findEntries(ctx.index, null, { locale, nameOf: findName, levelName: ctx.levelName });
      return entries;
    },
  };
  let entries = null;

  // ── the skeleton ────────────────────────────────────────────────────────
  const tabs = h('div', { class: 'cx-dist__tabs', role: 'tablist', 'aria-label': t('atlas.dist.views') });
  const measureBox = h('div', { class: 'cx-dist__measure' });
  const bar = h('div', { class: 'cx-dist__bar' }, tabs, measureBox);
  const panel = h('div', { class: 'cx-dist__panel', role: 'tabpanel', tabindex: '-1' });
  const loading = h('p', { class: 'cx-atlas-note', 'aria-busy': 'true', text: t('atlas.loading') });
  const shell = h('div', { class: 'cx-dist cx-atlas' }, bar, panel, loading);
  fill(root, shell);
  bar.hidden = true;
  let view = null;

  function refOf(x) {
    const items = x.kind === 'organisation' ? ctx.index.orgs : ctx.index.people;
    const it = items[x.i];
    return { kind: x.kind, id: x.kind === 'organisation' ? it.id : it.person_id };
  }

  /** A name for Find: the bundle's, else the host's label (a pseudonym). */
  function findName(kind, item) {
    const own = item.name || (host.label ? host.label(kind, item.person_id) : null);
    return own || t('atlas.person.unnamed', { id: item.person_id });
  }

  function nameOf(kind, i) {
    const index = ctx.index;
    if (kind === 'organisation') {
      const o = index.orgs[i];
      return o.acronym ? `${o.acronym} · ${o.name}` : o.name;
    }
    const p = index.people[i];
    if (p.name) return p.name;
    const own = host.label ? host.label('person', p.person_id) : null;
    return own || t('atlas.person.unnamed', { id: p.person_id });
  }

  /** A link to the host's page of an item, or its name alone. */
  function pageLink(kind, i) {
    const ref = refOf({ kind, i });
    const make = host.links && host.links[kind];
    const target = make ? make(ref.id) : null;
    const href = target && typeof target === 'object' ? target.href : target;
    const name = nameOf(kind, i);
    return href ? h('a', { href, class: 'cx-dist-name', text: name }) : h('span', { class: 'cx-dist-name', text: name });
  }

  /** A CSV file saved by the browser: *what* names it after the host's stem. */
  function download(what, rows) {
    const stem = String((host.fileStem && host.fileStem()) || 'cartolex').replace(/[^\p{L}\p{N}._-]+/gu, '-').slice(0, 60);
    const url = URL.createObjectURL(new Blob([csvText(rows)], { type: 'text/csv;charset=utf-8' }));
    const a = h('a', { href: url, download: `${stem}-${what}.csv`, hidden: true });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  /** What a view needs, read once: `vectors:person`, `vectors:organisation`, `links:person`,
   * `links:organisation`. Resolves when there (a part the source cannot give stays null). */
  function need(parts) {
    return Promise.all(parts.map((part) => {
      if (!loaded.has(part)) {
        const [what, kind] = part.split(':');
        const read = what === 'vectors' ? source.vectors : source.links;
        loaded.set(part, read ? Promise.resolve().then(() => read.call(source, kind)).then((answer) => {
          if (!answer || answer.error) return null;
          if (what === 'links') return answer;
          const n = kind === 'organisation' ? ctx.index.orgs.length : ctx.index.people.length;
          return answer.dim ? unitRows(answer.values, answer.dim, n) : null;
        }).catch(() => null).then((value) => {
          const slot = what === 'vectors' ? 'vec' : 'links';
          if (value) {
            ctx.data[slot] = ctx.data[slot] || {};
            ctx.data[slot][kind] = value;
          }
          return value;
        }) : Promise.resolve(null));
      }
      return loaded.get(part);
    }));
  }

  function set(patch) {
    const next = { ...state, ...patch };
    // a new focus or scope starts at the first page
    if (!('pg' in patch) && ['of', 'among', 'th', 'm', 'd', 'mode'].some((k) => k in patch)) next.pg = '';
    state = next;
    if (host.address && host.address.write) {
      const q = host.address.read();
      for (const k of DIST_KEYS) {
        if (state[k] && !(k === 'd' && state[k] === 'rank')) q.set(k, state[k]);
        else q.delete(k);
      }
      host.address.write(q);
    }
    render();
  }

  function renderTabs() {
    const buttons = DIST_VIEWS.map((id) => h('button', { type: 'button', role: 'tab', class: 'cx-atlas-btn',
      id: `cx-dist-tab-${id}`, 'aria-selected': String(state.d === id),
      tabindex: state.d === id ? '0' : '-1', dataset: { view: id }, text: t(`atlas.dist.view.${id}`),
      onClick: () => set({ d: id }) }));
    fill(tabs, h('span', { class: 'cx-atlas-group' }, buttons));
    panel.setAttribute('aria-labelledby', `cx-dist-tab-${state.d}`);
  }

  function renderMeasure() {
    const measure = ctx.measures.includes(state.m) ? state.m : ctx.measure;
    // a matrix of theme shares measures nothing: the choice is not shown
    measureBox.hidden = state.d === 'matrix' && SHARE_MATRICES.includes(state.mx);
    const select = h('select', { id: 'cx-dist-measure', onChange: (e) => set({ m: e.currentTarget.value }) },
      ctx.measures.map((m) => h('option', { value: m, selected: m === measure, text: t(`atlas.similarity.${m}`) })));
    fill(measureBox, h('label', { for: 'cx-dist-measure', class: 'cx-dist__label', text: t('atlas.dist.measure') }), select,
      h('p', { class: 'cx-atlas-note cx-dist__help', text: t(`atlas.similarity.${measure}.help`) }),
      ctx.remote ? null : h('p', { class: 'cx-atlas-note', text: ctx.project && !ctx.measures.includes(ctx.project)
        ? t('atlas.dist.measure.elsewhere', { name: t(`atlas.similarity.${ctx.project}`) }) : t('atlas.dist.measure.browser') }));
    return measure;
  }

  function render() {
    if (!alive || !ctx.index) return;
    renderTabs();
    ctx.measureNow = renderMeasure();
    const make = { rank: createRankView, pairs: createPairsView, matrix: createMatrixView }[state.d];
    if (!view || view.id !== state.d) {
      if (view) view.destroy();
      view = make(ctx);
      view.id = state.d;
      fill(panel, view.el);
    }
    view.update(state);
  }

  offs.push(listen(tabs, 'keydown', (e) => {
    const at = DIST_VIEWS.indexOf(state.d);
    const to = { ArrowRight: at + 1, ArrowLeft: at - 1, Home: 0, End: DIST_VIEWS.length - 1 }[e.key];
    if (to === undefined) return;
    e.preventDefault();
    set({ d: DIST_VIEWS[(to + DIST_VIEWS.length) % DIST_VIEWS.length] });
    const btn = tabs.querySelector(`[data-view="${state.d}"]`);
    if (btn) btn.focus();
  }));
  if (host.look && host.look.subscribe) {
    offs.push(host.look.subscribe(() => {
      if (!ctx.index) return;
      ctx.colours = coloursOf(ctx.index, ctx.scheme(), ctx.dark());
      if (view && view.repaint) view.repaint();
    }));
  }

  Promise.all([Promise.resolve().then(() => source.bundle()),
    source.measure ? Promise.resolve().then(() => source.measure()).catch(() => null) : null]).then(([bundle, project]) => {
    if (!alive) return;
    if (!bundle || bundle.error) {
      fill(loading, t('atlas.dist.unavailable'));
      loading.removeAttribute('aria-busy');
      return;
    }
    const index = indexBundle(bundle);
    ctx.index = index;
    ctx.colours = coloursOf(index, ctx.scheme(), ctx.dark());
    ctx.data = { index, vec: null, links: null, top: { person: topRows(index, 'person'), organisation: topRows(index, 'organisation') } };
    // the browser's measures, and the server's when the host has one (the app)
    ctx.remote = Boolean(source.similarity && source.similarPairs);
    ctx.measures = ALL_MEASURES.filter((m) => (DIST_MEASURES.includes(m) ? m !== 'space' || source.vectors : ctx.remote));
    ctx.project = typeof project === 'string' ? project : null;
    ctx.measure = ctx.measures.includes(ctx.project) ? ctx.project : ctx.measures[0];
    loading.remove();
    bar.hidden = false;
    render();
  }, () => {
    if (alive) fill(loading, t('atlas.dist.unavailable'));
  });

  return {
    state: () => ({ ...state }),
    set,
    destroy() {
      alive = false;
      if (view) view.destroy();
      offs.forEach((off) => off());
      fill(root);
    },
  };
}
