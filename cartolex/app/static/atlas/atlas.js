// SPDX-License-Identifier: MIT
/**
 * The atlas: the treemap of the themes, the map and the card of links, one piece of code
 * that the app and the offline site both mount (`docs/dev/atlas.md`). A host gives a data
 * source and its capabilities; `mountAtlas(root, {source, host})` does the rest.
 *
 * The focus drives everything: a theme, a keyword, an organisation, a person (or a
 * collaborator who is not mapped), a text, or two people or organisations compared. The map
 * fades what does not belong to it, the treemap shows its own weights, the card lists what
 * is connected to it. Back retraces the focus; Escape goes up one level; ⌂ Home goes back to
 * the whole field.
 */
import { fill, h, listen, symbol } from './dom.js';
import { indexBundle, indexWindows, nameIn, orgLevelOf, pathTo, placeOf } from './data.js';
import {
  DEFAULT_SCHEME, SCHEMES, neutralColour, orderByPlace, schemeOf, themeColours,
} from './schemes.js';
import { SHAPE_OF, createStore, readAtlasState, sameSel, selKey } from './state.js';
import { createRingReader } from './rings.js';
import { mapScene, worldScene } from './scene.js';
import {
  LAYOUT_DEFAULT, createFullscreen, divider, paneLimits, paneSize, readLayout, writeLayout,
} from './panes.js';
import { createTreemap, treemapTitle } from './treemap.js';
import { createFind, findEntries } from './find.js';
import { createLayers, kindsAvailable } from './layers.js';
import { createFilters, filterCount, filtersOffered } from './filters.js';
import { createCard } from './card.js';
import { createMapView } from './mapview.js';

/** The preference that keeps the colour scheme. */
export const SCHEME_PREF = 'colour_scheme';
const USERS_LIMIT = 30;

/** Whether the page shows its dark look (the document's `data-theme`, else the system's). */
export function pageIsDark() {
  const set = document.documentElement.dataset.theme;
  if (set === 'dark') return true;
  if (set === 'light') return false;
  return Boolean(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
}

/** The colours of a bundle's themes under a scheme: `{themes, neutral, categories, dark}`. */
export function coloursOf(index, scheme, dark) {
  const xs = index.tops.map((id) => {
    const c = index.centres.get(id);
    const n = index.nodes.get(id);
    return c ? c.x : (n.x === undefined ? null : n.x);
  });
  const order = schemeOf(scheme).kind === 'scale' ? orderByPlace(xs) : null;
  const themes = themeColours(scheme, index.tops.length, { dark, order });
  const cat = themeColours(schemeOf(scheme).kind === 'scale' ? 'vivid' : scheme, 10, { dark });
  return { themes, neutral: neutralColour(dark), categories: [cat[0], cat[2], cat[4], cat[6], cat[8]], dark, order };
}

/**
 * Mount the atlas in *root*: *source* gives the data, *host* its capabilities (see
 * `docs/dev/atlas.md`). Answers `{select(sel, {centre}), setScene(scene, {label}), slot(name),
 * refresh(), destroy()}`.
 */
export function mountAtlas(root, { source, host }) {
  const t = host.t;
  const locale = host.locale || 'en';
  const fmt = {
    number: (v) => new Intl.NumberFormat(locale).format(Number(v) || 0),
    percent: (v) => new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 0 }).format(Number(v) || 0),
    decimal: (v) => (v === null || v === undefined ? '—'
      : new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v)),
    year: (v) => String(v),
  };
  const prefs = host.prefs || null;
  const store = createStore(readAtlasState(host.address ? host.address.read() : null), host.address);
  let layout = readLayout(prefs);
  let scheme = (prefs && prefs.get && prefs.get(SCHEME_PREF)) || DEFAULT_SCHEME;
  let dark = host.look && host.look.dark ? host.look.dark() : pageIsDark();
  let index = null;
  let colours = null;
  let built = null;
  let override = null;
  let texts = null;
  let land = null;
  let entries = null;
  let filtersOpen = false;
  let alive = true;
  let centred = false;
  const sets = new Map();
  const asked = new Set();
  const answers = { users: new Map(), compare: new Map() };
  const windowsAsked = { all: false, people: new Set() };
  const offs = [];

  // ── the skeleton ──────────────────────────────────────────────────────────
  const crumbs = h('nav', { class: 'cx-atlas__crumbs', 'aria-label': t('atlas.crumbs') });
  const find = createFind({ t, entries: () => entriesNow(), label: t('atlas.find'), placeholder: t('atlas.find.placeholder'),
    onPick: (sel) => select(sel, { centre: true }) });
  const homeBtn = h('button', { type: 'button', class: 'cx-atlas-btn', title: t('atlas.home_help'), text: `⌂ ${t('atlas.home')}`,
    onClick: () => home() });
  const backBtn = h('button', { type: 'button', class: 'cx-atlas-btn', text: `← ${t('atlas.back')}`, onClick: () => store.back() });
  const schemeSelect = h('select', { class: 'cx-atlas-scheme', 'aria-label': t('atlas.scheme'), title: t('atlas.scheme'),
    onChange: (e) => setScheme(e.currentTarget.value) },
  ['each', 'scale'].map((kind) => h('optgroup', { label: t(`atlas.scheme.group.${kind}`) },
    SCHEMES.filter((s) => s.kind === kind).map((s) => h('option', { value: s.id, selected: s.id === scheme,
      text: t(`atlas.scheme.${s.id}`) })))));
  const lookGroup = host.look && host.look.set ? h('span', { class: 'cx-atlas-group', role: 'group', 'aria-label': t('atlas.look') },
    ['dark', 'bright'].map((m) => h('button', { type: 'button', class: 'cx-atlas-btn', dataset: { mode: m }, text: t(`atlas.look.${m}`),
      onClick: () => host.look.set(m === 'dark') }))) : null;
  const filtersBtn = h('button', { type: 'button', class: 'cx-atlas-btn', 'aria-expanded': 'false', onClick: () => {
    filtersOpen = !filtersOpen;
    render();
  } });
  const worldBtn = h('button', { type: 'button', class: 'cx-atlas-btn', 'aria-pressed': 'false', text: t('atlas.world'),
    title: t('atlas.world_help'), onClick: () => store.set({ view: store.get().view === 'world' ? 'map' : 'world' }) });
  const barSlot = h('span', { class: 'cx-atlas__slot' });
  const fullBtn = h('button', { type: 'button', class: 'cx-atlas-btn', text: `⤢ ${t('atlas.full')}` });
  const bar = h('div', { class: 'cx-atlas__bar' }, crumbs,
    h('div', { class: 'cx-atlas__tools' }, find.el, homeBtn, backBtn, filtersBtn, schemeSelect, lookGroup, worldBtn, barSlot, fullBtn));
  const filtersEl = h('div', { class: 'cx-atlas__filters', role: 'group', 'aria-label': t('atlas.filters'), hidden: true });
  const notesEl = h('div', { class: 'cx-atlas__notes' });

  const treeTitle = h('h2', { class: 'cx-atlas-pane__title' });
  const upBtn = h('button', { type: 'button', class: 'cx-atlas-mini', text: `↑ ${t('atlas.tree.up')}`, onClick: () => up() });
  const treeFull = h('button', { type: 'button', class: 'cx-atlas-mini', title: t('atlas.tree.alone'), 'aria-label': t('atlas.tree.alone'), text: '⤢' });
  const treeBox = h('div', { class: 'cx-atlas-tree' });
  const treePane = h('section', { class: 'cx-atlas-pane cx-atlas-pane--tree', 'aria-label': t('atlas.tree.label') },
    h('div', { class: 'cx-atlas-pane__head' }, treeTitle, upBtn, treeFull,
      h('button', { type: 'button', class: 'cx-atlas-mini', text: t('atlas.hide'), onClick: () => setLayout({ treeOn: false }) })),
    treeBox);
  const treeRail = h('button', { type: 'button', class: 'cx-atlas-rail', text: `${t('atlas.tree.rail')} ›`,
    onClick: () => setLayout({ treeOn: true }) });
  const treeSplit = h('div', { class: 'cx-atlas-split cx-atlas-split--v', role: 'separator', tabindex: '0',
    'aria-orientation': 'vertical', 'aria-label': t('atlas.resize.tree') });

  const mapEl = h('div', { class: 'cx-atlas-map cx-atlas__frame' });
  const mapSlot = h('div', { class: 'cx-atlas-map__slot' });
  const layersEl = h('div', { class: 'cx-atlas-layers', role: 'group', 'aria-label': t('atlas.layers') });
  const hint = h('p', { class: 'cx-atlas-map__hint', 'aria-hidden': 'true', text: t('atlas.map.hint') });
  const belowSplit = h('div', { class: 'cx-atlas-split cx-atlas-split--h', role: 'separator', tabindex: '0',
    'aria-orientation': 'horizontal', 'aria-label': t('atlas.resize.card') });
  const belowSlot = h('div', { class: 'cx-atlas__below' });
  const centre = h('div', { class: 'cx-atlas__centre' }, mapEl, belowSplit, belowSlot);

  const cardBody = h('div', { class: 'cx-atlas-card', 'aria-live': 'polite' });
  const cardPosBtn = h('button', { type: 'button', class: 'cx-atlas-mini', onClick: () => setLayout({ at: layout.at === 'below' ? 'right' : 'below' }) });
  const cardPane = h('aside', { class: 'cx-atlas-pane cx-atlas-pane--card', 'aria-label': t('atlas.card') },
    h('div', { class: 'cx-atlas-pane__head' }, h('h2', { class: 'cx-atlas-pane__title', text: t('atlas.card') }), cardPosBtn,
      h('button', { type: 'button', class: 'cx-atlas-mini', text: t('atlas.hide'), onClick: () => setLayout({ cardOn: false }) })),
    cardBody);
  const cardSplit = h('div', { class: 'cx-atlas-split cx-atlas-split--v', role: 'separator', tabindex: '0',
    'aria-orientation': 'vertical', 'aria-label': t('atlas.resize.card') });
  const cardRail = h('button', { type: 'button', class: 'cx-atlas-rail cx-atlas-rail--right', text: `‹ ${t('atlas.card')}`,
    onClick: () => setLayout({ cardOn: true }) });
  const stage = h('div', { class: 'cx-atlas__stage' }, treeRail, treePane, treeSplit, centre, cardSplit, cardPane, cardRail);
  const loading = h('p', { class: 'cx-atlas__loading', 'aria-busy': 'true', text: t('atlas.loading') });
  const shell = h('div', { class: 'cx-atlas' }, bar, filtersEl, notesEl, stage, loading);
  fill(root, shell);
  stage.hidden = true;

  // ── what is read beside the bundle ───────────────────────────────────────
  const rings = createRingReader(source, () => render());
  const nameOf = (kind, item) => {
    if (!item) return '';
    if (item.name) return item.name;
    const id = item.person_id || item.id;
    const own = host.label ? host.label(kind, id) : null;
    return own || t(kind === 'projected' ? 'atlas.projected.unnamed' : 'atlas.person.unnamed', { id });
  };
  const levelName = (id) => {
    const lv = (index ? index.levels : []).find((l) => l.id === id);
    return lv ? nameIn(lv.names, locale) || lv.id : id || '';
  };
  const nameOfSel = (sel) => {
    if (!sel || !index) return '';
    if (sel.kind === 'person' && index.byPerson.has(sel.id)) return nameOf('person', index.people[index.byPerson.get(sel.id)]);
    if (sel.kind === 'projected' && index.byProjected.has(sel.id)) return nameOf('projected', index.projected[index.byProjected.get(sel.id)]);
    if (sel.kind === 'organisation' && index.byOrg.has(sel.id)) {
      const o = index.orgs[index.byOrg.get(sel.id)];
      return o.acronym || o.name;
    }
    if (sel.kind === 'theme' && index.nodes.has(sel.id)) return nameIn(index.nodes.get(sel.id).names, locale);
    if (sel.kind === 'text' && texts) {
      const i = texts.id.indexOf(sel.id);
      if (i >= 0) return texts.title[i] || sel.id;
    }
    return sel.kind === 'person' ? nameOf('person', { id: sel.id }) : sel.id;
  };
  function entriesNow() {
    if (!index) return [];
    if (!entries) entries = findEntries(index, texts, { locale, nameOf, levelName });
    return entries;
  }
  const settle = (promise) => Promise.resolve().then(() => promise)
    .then((data) => (data && data.error ? { error: data.error } : { data }))
    .catch((error) => ({ error: { message: String(error && error.message ? error.message : error) } }));

  function readBeside(state) {
    const sel = state.sel;
    // the texts: when shown or one is in focus
    if (source.texts && !texts && !asked.has('texts') && (state.show.includes('texts') || (sel && sel.kind === 'text'))) {
      asked.add('texts');
      settle(source.texts()).then((r) => {
        if (!alive || r.error || !r.data || r.data.available === false) return;
        texts = r.data;
        entries = null;
        render();
      });
    }
    // the time windows: every person's when shown, else the person in focus
    if (source.windows && (index.bundle.windows || 0) > 0) {
      const all = state.show.includes('windows');
      const who = sel && sel.kind === 'person' ? sel.id : null;
      if ((all && !windowsAsked.all) || (!all && who && !windowsAsked.all && !windowsAsked.people.has(who))) {
        if (all) windowsAsked.all = true;
        else windowsAsked.people.add(who);
        settle(source.windows(all ? {} : { person: who })).then((r) => {
          if (!alive || r.error || !r.data || r.data.available === false) return;
          index.windows = indexWindows(index, r.data, all ? new Map() : index.windows);
          render();
        });
      }
    }
    // the keywords of the people and organisations in focus, or drawn as regions
    if (source.keywordsOf && state.view === 'map') {
      const want = { person: [], organisation: [] };
      for (const s of [sel, state.with]) if (s && (s.kind === 'person' || s.kind === 'organisation')) want[s.kind].push(s.id);
      if (state.as === 'regions') {
        const level = orgLevelOf(index, state);
        if (state.show.includes('organisations')) for (const o of index.orgs) if (o.level === level && o.x !== null) want.organisation.push(o.id);
      }
      for (const kind of ['person', 'organisation']) {
        const ids = want[kind].filter((id) => !asked.has(`${kind}:${id}`)).slice(0, 500);
        if (!ids.length) continue;
        for (const id of ids) asked.add(`${kind}:${id}`);
        settle(source.keywordsOf(kind, ids)).then((r) => {
          if (!alive || r.error) return;
          for (const [id, terms] of Object.entries((r.data && (r.data.keywords || r.data)) || {})) sets.set(`${kind}:${id}`, terms);
          render();
        });
      }
    }
    // the people who use the keyword in focus
    if (source.keywordUsers && sel && sel.kind === 'keyword' && !answers.users.has(sel.id)) {
      answers.users.set(sel.id, null);
      settle(source.keywordUsers(sel.id, { limit: USERS_LIMIT })).then((r) => {
        if (!alive) return;
        answers.users.set(sel.id, r);
        render();
      });
    }
    // the comparison
    if (source.compare && sel && state.with) {
      const key = `${selKey(sel)}|${selKey(state.with)}`;
      if (!answers.compare.has(key)) {
        answers.compare.set(key, null);
        settle(source.compare(sel, state.with)).then((r) => {
          if (!alive) return;
          answers.compare.set(key, r);
          render();
        });
      }
    }
    // the outline of the land under the world view
    if (state.view === 'world' && source.land && !land && !asked.has('land')) {
      asked.add('land');
      settle(source.land()).then((r) => {
        if (!alive || r.error || !r.data) return;
        land = r.data;
        render();
      });
    }
  }

  // ── focus, levels and layout ─────────────────────────────────────────────
  function select(sel, { centre: centreIt = false } = {}) {
    const state = store.get();
    const patch = { sel, with: null };
    if (sel && (sel.kind === 'person' || sel.kind === 'organisation' || sel.kind === 'projected')) patch.open = '';
    if (!sameSel(sel, state.sel) || state.with) store.set(patch, { push: true });
    if (centreIt && sel && index) {
      const at = placeOf(index, sel, texts);
      if (at && store.get().view === 'map') mapView.controller.centreOn(at.x, at.y, 3);
    }
  }
  function home() {
    store.set({ sel: null, with: null, open: '' }, { push: true });
    if (mapView) mapView.controller.fit();
  }
  function up() {
    const open = store.get().open;
    if (!open || !index) return;
    const node = index.nodes.get(open);
    store.set({ open: node && node.parent ? node.parent : '' }, { push: true });
  }
  /** The crumbs from the field to the focus: `[[text, sel or null]]`. */
  function trail(state) {
    const parts = [[t('atlas.crumbs.field'), null]];
    const sel = state.sel;
    if (!sel || !index) return parts;
    if (state.with) {
      parts.push([t('atlas.crumbs.compare', { a: nameOfSel(sel), b: nameOfSel(state.with) }), sel]);
      return parts;
    }
    if (sel.kind === 'theme' && index.nodes.has(sel.id)) {
      for (const id of pathTo(index, sel.id)) parts.push([nameOfSel({ kind: 'theme', id }), { kind: 'theme', id }]);
    } else if (sel.kind === 'keyword' && index.byTerm.has(sel.id)) {
      const node = index.keywords[index.byTerm.get(sel.id)].node;
      for (const id of node ? pathTo(index, node) : []) parts.push([nameOfSel({ kind: 'theme', id }), { kind: 'theme', id }]);
      parts.push([sel.id, sel]);
    } else if (sel.kind === 'person') {
      const level = orgLevelOf(index, state);
      const org = (index.orgsOfPerson.get(sel.id) || []).map((o) => index.orgs[index.byOrg.get(o)]).find((o) => o && o.level === level);
      if (org) parts.push([org.acronym || org.name, { kind: 'organisation', id: org.id }]);
      parts.push([nameOfSel(sel), sel]);
    } else parts.push([nameOfSel(sel), sel]);
    return parts;
  }
  /** Escape: the focus's parent in the crumbs, else the treemap's level above. */
  function escape() {
    const state = store.get();
    if (state.sel) {
      const parts = trail(state);
      const target = parts.length > 1 ? parts[parts.length - 2][1] : null;
      if (state.with) store.set({ with: null }, { push: true });
      else if (target) select(target);
      else store.set({ sel: null, with: null, open: '' }, { push: true });
    } else if (state.open) up();
  }

  function setLayout(patch) {
    const before = layout;
    layout = { ...layout, ...patch };
    writeLayout(prefs, layout, before);
    applyLayout();
  }
  function applyLayout() {
    const below = layout.at === 'below';
    if (below && cardPane.parentNode !== belowSlot) belowSlot.appendChild(cardPane);
    if (!below && cardPane.parentNode !== stage) stage.insertBefore(cardPane, cardRail);
    shell.classList.toggle('is-card-below', below);
    treePane.style.setProperty('--cx-pane', `${layout.tree}px`);
    cardPane.style.setProperty('--cx-pane', below ? `${layout.below}px` : `${layout.card}px`);
    treePane.hidden = !layout.treeOn;
    treeSplit.hidden = !layout.treeOn;
    treeRail.hidden = layout.treeOn;
    cardPane.hidden = !layout.cardOn;
    cardSplit.hidden = !layout.cardOn || below;
    belowSplit.hidden = !layout.cardOn || !below;
    belowSlot.hidden = !layout.cardOn || !below;
    cardRail.hidden = layout.cardOn;
    cardPosBtn.textContent = t(below ? 'atlas.card.to_right' : 'atlas.card.to_below');
    for (const [el, field] of [[treeSplit, 'tree'], [cardSplit, 'card'], [belowSplit, 'below']]) {
      const [lo, hi] = paneLimits(field);
      el.setAttribute('aria-valuemin', String(lo));
      el.setAttribute('aria-valuemax', String(hi));
      el.setAttribute('aria-valuenow', String(layout[field]));
    }
  }
  offs.push(divider(treeSplit, { get: () => layout, onMove: (dx, dy, s) => { layout = { ...layout, tree: paneSize('tree', s.tree + dx) }; applyLayout(); },
    onEnd: () => writeLayout(prefs, layout) }));
  offs.push(divider(cardSplit, { get: () => layout, onMove: (dx, dy, s) => { layout = { ...layout, card: paneSize('card', s.card - dx) }; applyLayout(); },
    onEnd: () => writeLayout(prefs, layout) }));
  offs.push(divider(belowSplit, { get: () => layout, onMove: (dx, dy, s) => { layout = { ...layout, below: paneSize('below', s.below - dy) }; applyLayout(); },
    onEnd: () => writeLayout(prefs, layout) }));

  function setScheme(id) {
    scheme = schemeOf(id).id;
    if (prefs && prefs.set) prefs.set(SCHEME_PREF, scheme);
    if (host.onScheme) host.onScheme(scheme);
    render();
  }

  // full screen: the whole atlas, the map alone, the treemap alone
  const fsAtlas = createFullscreen(shell, { exitLabel: t('atlas.full_leave'), onChange: () => render() });
  const fsMap = createFullscreen(mapEl, { exitLabel: t('atlas.full_leave'), onChange: () => render() });
  const fsTree = createFullscreen(treePane, { exitLabel: t('atlas.full_leave'), onChange: () => render() });
  offs.push(listen(fullBtn, 'click', () => fsAtlas.toggle()), listen(treeFull, 'click', () => fsTree.toggle()));

  // ── the map ──────────────────────────────────────────────────────────────
  const emptyScene = { layers: [], regions: [], lines: [], labels: [], bounds: null };
  const currentScene = () => (override ? override.scene : built ? built.scene : emptyScene);
  const pickOf = (hit) => {
    if (!hit || override || !built) return null;
    const layer = built.scene.layers.find((l) => l.id === hit.layer);
    if (!layer) return null;
    const i = layer.ref[hit.index];
    if (layer.id === 'people') return { kind: 'person', id: index.people[i].person_id };
    if (layer.id === 'keywords') return { kind: 'keyword', id: index.keywords[i].term };
    if (layer.id === 'organisations') return { kind: 'organisation', id: index.orgs[i].id };
    if (layer.id === 'texts' && texts) return { kind: 'text', id: texts.id[i] };
    if (layer.id === 'projected') return { kind: 'projected', id: index.projected[i].person_id };
    if (layer.id === 'windows') return { kind: 'person', id: layer.items[i].person_id };
    return null;
  };
  const hoverCard = (hit) => {
    const sel = pickOf(hit);
    if (!sel) return null;
    let detail = '';
    if (sel.kind === 'person') detail = index.people[index.byPerson.get(sel.id)].unit || '';
    else if (sel.kind === 'keyword') {
      const node = index.keywords[index.byTerm.get(sel.id)].node;
      detail = node && index.nodes.has(node) ? nameIn(index.nodes.get(node).names, locale) : '';
    } else if (sel.kind === 'organisation') detail = t('atlas.card.people', { count: (index.members.get(sel.id) || []).length });
    else if (sel.kind === 'projected') detail = t('atlas.kind.projected');
    else if (sel.kind === 'text' && texts) {
      const i = texts.id.indexOf(sel.id);
      detail = texts.year[i] ? String(texts.year[i]) : '';
    }
    return h('div', {}, h('strong', { class: 'cx-atlas-map__card-title', text: nameOfSel(sel) }),
      detail ? h('span', { class: 'cx-atlas-map__card-detail', text: detail }) : null);
  };
  const legendOf = () => {
    if (!built || !index) return null;
    const kinds = Object.keys(built.counts).map((k) => ({ shape: SHAPE_OF[k], text: k === 'organisations'
      ? levelName(orgLevelOf(index, store.get())) : t(`atlas.kinds.${k}`) }));
    const entriesList = index.tops.map((id, i) => ({ color: colours.themes[i], text: nameIn(index.nodes.get(id).names, locale) }));
    return { kinds, entries: entriesList };
  };
  const zoomSelection = () => {
    if (!built) return;
    let box = null;
    const grow = (x, y) => {
      if (!box) box = { xmin: x, xmax: x, ymin: y, ymax: y };
      else {
        box.xmin = Math.min(box.xmin, x);
        box.xmax = Math.max(box.xmax, x);
        box.ymin = Math.min(box.ymin, y);
        box.ymax = Math.max(box.ymax, y);
      }
    };
    for (const layer of built.scene.layers) {
      if (!layer.highlightCount) continue;
      for (let i = 0; i < layer.x.length; i += 1) if (layer.highlight[i]) grow(layer.x[i], layer.y[i]);
    }
    const at = placeOf(index, store.get().sel, texts);
    if (at) grow(at.x, at.y);
    if (box) mapView.zoomTo(box);
  };
  const mapView = createMapView(mapEl, { t, label: t('atlas.map.label'), scene: currentScene,
    onPick: (hit) => {
      const sel = pickOf(hit);
      if (sel) select(sel);
    },
    hoverCard,
    actions: { home, fullscreen: () => fsMap.toggle(), legend: legendOf, zoomSelection,
      stem: () => (host.fileStem ? host.fileStem() : (index && index.bundle.map_version) || 'view') } });
  mapEl.append(layersEl, hint, mapSlot);

  const treemap = createTreemap(treeBox, { label: t('atlas.tree.label'),
    onFocus: (sel) => select(sel),
    onOpen: (id) => {
      store.set({ open: id, ...(index.nodes.has(id) ? { sel: { kind: 'theme', id }, with: null } : {}) }, { push: true });
    },
    onUp: () => up() });
  const layers = createLayers(layersEl, { onChange: (patch) => store.set(patch) });
  const filters = createFilters(filtersEl, { onChange: (patch) => store.set(patch) });
  const card = createCard(cardBody, {
    go: (sel) => select(sel, { centre: false }),
    follow: (event, href) => {
      if (!host.navigate) return;
      event.preventDefault();
      host.navigate(href);
    },
    setWith: (other) => store.set({ with: other }, { push: true }),
  });

  // ── drawing ──────────────────────────────────────────────────────────────
  let frame = 0;
  function render() {
    if (!alive || frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      draw();
    });
  }
  function draw() {
    if (!alive || !index) return;
    const state = store.get();
    const nextDark = host.look && host.look.dark ? host.look.dark() : pageIsDark();
    if (!colours || nextDark !== dark || colours.scheme !== scheme) {
      dark = nextDark;
      colours = { ...coloursOf(index, scheme, dark), scheme };
      // the app's other screens order a scale's themes as the map places them
      if (colours.order && prefs && prefs.set) prefs.set('colour_order', colours.order.join(','));
    }
    readBeside(state);
    const sel = state.sel;
    const ringAnswer = sel && state.net > 0 && state.view === 'map' ? rings.get(sel, state.net) : null;
    const users = sel && sel.kind === 'keyword' ? answers.users.get(sel.id) || null : null;
    const compare = sel && state.with ? answers.compare.get(`${selKey(sel)}|${selKey(state.with)}`) || null : null;
    built = state.view === 'world' ? worldScene(index, state, { land, colours })
      : mapScene(index, state, { texts, sets, users, rings: ringAnswer, colours, locale, nameOf });
    mapView.redraw();
    // a focus given in the address is centred once, when the map is first drawn
    if (!centred) {
      centred = true;
      const at = sel ? placeOf(index, sel, texts) : null;
      if (at && state.view === 'map') mapView.controller.centreOn(at.x, at.y, 3);
    }
    const lit = built.scene.layers.reduce((n, l) => n + (l.highlightCount || 0), 0);
    mapView.setStatus(sel ? t('atlas.status', { count: lit, name: nameOfSel(sel) }) : '');
    mapView.setLabel(override ? override.label : state.view === 'world' ? t('atlas.map.world_label') : t('atlas.map.label'));
    mapView.setFullLabel(fsMap.on() ? t('atlas.full_leave') : t('atlas.map.alone'));

    // the bar
    const parts = trail(state);
    fill(crumbs, h('ol', {}, parts.map(([text, target], i) => h('li', {}, i === parts.length - 1
      ? h('span', { class: 'cx-atlas__here', 'aria-current': 'location', text })
      : h('button', { type: 'button', class: 'cx-atlas-link', text, onClick: () => (target ? select(target)
        : store.set({ sel: null, with: null, open: '' }, { push: true })) })))));
    backBtn.disabled = !store.canGoBack();
    const n = filterCount(state);
    filtersBtn.hidden = !filtersOffered(index);
    filtersBtn.textContent = n ? t('atlas.filters.on', { count: n }) : t('atlas.filters');
    filtersBtn.setAttribute('aria-expanded', String(filtersOpen));
    filtersEl.hidden = !filtersOpen;
    if (filtersOpen) filters.update({ index, state, t, fmt });
    schemeSelect.value = scheme;
    worldBtn.hidden = !index.orgs.some((o) => o.location);
    worldBtn.setAttribute('aria-pressed', String(state.view === 'world'));
    layersEl.hidden = false;
    if (lookGroup) lookGroup.querySelectorAll('[data-mode]').forEach((b) => b.setAttribute('aria-pressed', String((b.dataset.mode === 'dark') === dark)));
    const fullLabel = fsAtlas.on() ? t('atlas.full_leave') : t('atlas.full');
    fullBtn.textContent = ['⤢', fullLabel].join(' ');
    fill(notesEl, built.notes.map((note) => h('p', { class: 'cx-atlas__note', role: 'note',
      text: t(note.key, { kind: note.kind ? t(`atlas.kinds.${note.kind}`) : '', count: note.count, max: note.max || 0, total: note.total || 0 }) })));

    // the treemap, the layers, the card
    treeTitle.textContent = treemapTitle({ index, state, t, locale, nameOfSel });
    upBtn.hidden = !state.open;
    treemap.update({ index, state, colours, t, locale });
    layers.update({ index, state, t, has: kindsAvailable(index, source), counts: built.counts, levelName,
      network: rings.available });
    card.update({ index, state, t, fmt, colours, locale, levelName, nameOf, nameOfSel, title: host.title || '',
      links: host.links || null, rings: ringAnswer, ringsOffered: rings.available, users, usersOffered: Boolean(source.keywordUsers),
      compare, compareOffered: Boolean(source.compare), sets, texts, findEntries: entriesNow });
  }

  offs.push(store.subscribe((state, old) => {
    if (old && (old.view !== state.view || old.base !== state.base)) mapView.redraw(true);
    render();
  }));
  if (host.look && host.look.subscribe) offs.push(host.look.subscribe(() => render()));
  else {
    const observer = new MutationObserver(() => render());
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    offs.push(() => observer.disconnect());
    if (window.matchMedia) offs.push(listen(window.matchMedia('(prefers-color-scheme: dark)'), 'change', () => render()));
  }
  offs.push(listen(shell, 'keydown', (e) => {
    if (e.key !== 'Escape' || e.defaultPrevented) return;
    if (e.target.closest && e.target.closest('input, select, textarea, dialog, [role=menu]')) return;
    if (shell.querySelector('.is-blown')) return;
    e.preventDefault();
    escape();
  }));

  function load() {
    loading.hidden = false;
    loading.textContent = t('atlas.loading');
    settle(source.bundle()).then((r) => {
      if (!alive) return;
      if (r.error || !r.data) {
        fill(loading, h('span', { text: t('atlas.unread') }), ' ',
          h('button', { type: 'button', class: 'cx-atlas-btn', text: t('atlas.retry'), onClick: () => load() }));
        loading.removeAttribute('aria-busy');
        return;
      }
      index = indexBundle(r.data);
      entries = null;
      colours = null;
      texts = null;
      sets.clear();
      asked.clear();
      rings.clear();
      answers.users.clear();
      answers.compare.clear();
      windowsAsked.all = false;
      windowsAsked.people.clear();
      loading.hidden = true;
      stage.hidden = false;
      applyLayout();
      draw();
      mapView.redraw(true);
      if (host.onReady) host.onReady({ index });
    });
  }
  applyLayout();
  load();

  return {
    select,
    /** A host's own scene in place of the map's (a preview), with its label; null: the map's. */
    setScene(scene, { label = '' } = {}) {
      override = scene ? { scene, label } : null;
      mapView.hideCard();
      mapView.redraw(false);
      render();
    },
    slot(name) {
      return name === 'bar' ? barSlot : name === 'map' ? mapSlot : null;
    },
    state: () => store.get(),
    set: (patch) => store.set(patch),
    index: () => index,
    /** The colours of the themes now (`{themes, neutral, categories, dark}`). */
    colours: () => colours || (index ? coloursOf(index, scheme, dark) : null),
    map: () => mapView.controller,
    refresh: () => load(),
    destroy() {
      alive = false;
      cancelAnimationFrame(frame);
      rings.dispose();
      offs.forEach((off) => off());
      fsAtlas.destroy();
      fsMap.destroy();
      fsTree.destroy();
      find.destroy();
      treemap.destroy();
      mapView.destroy();
      root.textContent = '';
    },
  };
}

/** The default layout (for a host that shows it before mounting). */
export const ATLAS_LAYOUT = LAYOUT_DEFAULT;
/** The symbol of a kind, for a host's own legends. */
export const atlasSymbol = symbol;
