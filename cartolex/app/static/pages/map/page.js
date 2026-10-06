// SPDX-License-Identifier: MIT
/**
 * The atlas (`/map`): the treemap of the themes, the map and the panel of
 * what is selected, together. The map shows people, keywords, organisations
 * of one level, texts, projected people and time windows, several at once,
 * each with its own symbol, as points or as regions spanning their keywords;
 * the people's filters and the period hide what they leave out; the world
 * view places organisations at their address. Everything the page shows is in
 * the address (see `state.js`). Reads `GET /api/atlas` when it opens; the
 * texts, the time windows (every person's, or the selected person's), and the
 * keywords of people and organisations, when they are shown.
 * The map's « Tune » panel (`pages/tune/`) reads nothing until it opens; a change of the
 * layout there is previewed on this map (`preview.js`), kept as the pinned version or discarded.
 * The body goes full screen (`fullscreen.js`) and its side columns fold away (remembered in
 * this browser); a selection given in the address is centred once the map is drawn; the
 * nearest of the selection or the people who use a keyword come from the space of the themes
 * (`near.js`), « Compare with… » (`compare.js`), and the distances can be exported
 * (`pages/share/distances.js`). « Save the view » writes the map as it is on screen as a PNG
 * or SVG image, with or without its legend (`save.js`).
 */
import { html, useEffect, useMemo, useRef, useState } from '../../core/preact.js';
import { locale, t } from '../../core/i18n.js';
import { usePage, usePageTitle } from '../../core/page.js';
import { runtime } from '../../core/runtime.js';
import {
  Button, EmptyState, ErrorCard, IconButton, MapFrame, MapSymbol, MenuButton,
} from '../../components/index.js';
import { useJobEnd } from '../people/common.js';
import { CATEGORY_HUE, indexAtlas, indexWindows, matching, themeName } from './model.js';
import { MAX_REGIONS, mapScene, worldScene } from './scene.js';
import { SHAPE_OF, readState, writeState } from './state.js';
import { Controls, Segmented } from './controls.js';
import { Panel } from './panel.js';
import { Find } from './find.js';
import { TreePanel } from './tree.js';
import { VersionsDialog } from './versions.js';
import { TunePanel } from '../tune/panel.js';
import { PreviewBar, createLayoutPreview, previewScene } from './preview.js';
import { FullscreenButton, useFullscreen } from './fullscreen.js';
import { litBySpace, useSpaceOf } from './near.js';
import { CompareDialog } from './compare.js';
import { DistancesDialog } from '../share/distances.js';
import { ColumnButtons, shownPeople, useColumns } from './columns.js';
import { savePng, saveSvg } from './save.js';

const PICKED = { people: 'person', keywords: 'keyword', organisations: 'organisation', texts: 'text',
  projected: 'projected', windows: 'person' };

/** The selection a hit on the scene names. */
function pickOf(scene, hit, index, texts) {
  if (!hit) return null;
  const layer = scene.layers.find((l) => l.id === hit.layer);
  if (!layer) return null;
  const i = layer.ref[hit.index];
  if (layer.id === 'people') return { kind: 'person', id: index.people[i].person_id };
  if (layer.id === 'keywords') return { kind: 'keyword', id: index.keywords[i].term };
  if (layer.id === 'organisations') return { kind: 'organisation', id: index.orgs[i].id };
  if (layer.id === 'texts') return { kind: 'text', id: texts.id[i] };
  if (layer.id === 'projected') return { kind: 'projected', id: index.projected[i].person_id };
  if (layer.id === 'windows') return { kind: 'person', id: layer.items[i].person_id };
  return { kind: PICKED[layer.id], id: '' };
}

/** The words of a hovered point. */
function HoverCard({ sel, index, texts }) {
  const lang = locale.value;
  let title = '';
  let detail = '';
  if (sel.kind === 'person' && index.byPerson.has(sel.id)) {
    const p = index.people[index.byPerson.get(sel.id)];
    title = p.name;
    detail = p.unit;
  } else if (sel.kind === 'keyword') {
    const k = index.keywords[index.byTerm.get(sel.id)];
    title = k.term;
    detail = k.node ? themeName(index, k.node, lang) : '';
  } else if (sel.kind === 'organisation') {
    const o = index.orgs[index.byOrg.get(sel.id)];
    title = o.name;
    detail = t('map.panel.members', { now: index.members.get(o.id).length, ever: o.members_ever });
  } else if (sel.kind === 'text' && texts) {
    const i = texts.id.indexOf(sel.id);
    title = texts.title[i];
    detail = texts.year[i] ? String(texts.year[i]) : '';
  } else if (sel.kind === 'projected') {
    title = sel.id;
    detail = t('map.kind1.projected');
  }
  return html`<strong class="cx-atlas-card__title">${title}</strong>
    ${detail ? html`<span class="cx-atlas-card__detail">${detail}</span>` : null}`;
}

/** The permanent legend: one symbol per kind shown, one colour per top-level theme, and the
 * keywords' colours when they are coloured by category. */
function Legend({ index, state, counts, onSelect }) {
  const kinds = Object.keys(counts);
  return html`<details class="cx-atlas-legend" open>
    <summary class="cx-atlas-legend__summary">${t('map.legend')}</summary>
    <ul class="cx-atlas-legend__kinds" aria-label=${t('map.legend.kinds')}>
      ${kinds.map((k) => html`<li key=${k}><${MapSymbol} shape=${SHAPE_OF[k]} /> ${t(`map.kind.${k}`)}</li>`)}
    </ul>
    <p class="cx-atlas-legend__lead">${t('map.legend.colour')}</p>
    <ul class="cx-atlas-legend__themes" aria-label=${t('map.legend.colour')}>
      ${index.tops.slice(0, 12).map((id) => html`<li key=${id}>
        <button type="button" class=${`cx-atlas-legend__theme ${state.sel && state.sel.id === id ? 'is-selected' : ''}`}
          aria-pressed=${String(Boolean(state.sel && state.sel.kind === 'theme' && state.sel.id === id))}
          onClick=${() => onSelect({ kind: 'theme', id })}>
          <span class="cx-atlas-legend__chip" style=${{ '--cx-chip': `var(--cx-hue-${index.colourOf(id) + 1})` }}
            aria-hidden="true"></span><span class="cx-atlas-legend__name">${themeName(index, id, locale.value)}</span>
        </button></li>`)}
    </ul>
    ${state.kcol === 'category' && kinds.includes('keywords') ? html`
      <p class="cx-atlas-legend__lead">${t('map.categories.legend')}</p>
      <ul class="cx-atlas-legend__themes" aria-label=${t('map.categories.legend')}>
        ${Object.entries(CATEGORY_HUE).map(([c, hue]) => html`<li key=${c} class="cx-atlas-legend__theme">
          <span class="cx-atlas-legend__chip" style=${{ '--cx-chip': `var(--cx-hue-${hue + 1})` }}
            aria-hidden="true"></span><span class="cx-atlas-legend__name">${t(`keywords.category.${c}`)}</span></li>`)}
      </ul>` : null}
  </details>`;
}

/** The legend of a saved view: the kinds shown and the colours of the themes (or categories). */
function legendOf(index, state, counts) {
  const kinds = Object.keys(counts).map((k) => ({ shape: SHAPE_OF[k], text: t(`map.kind.${k}`) }));
  const entries = state.kcol === 'category' && counts.keywords
    ? Object.entries(CATEGORY_HUE).map(([c, hue]) => ({ color: `--cx-hue-${hue + 1}`, text: t(`keywords.category.${c}`) }))
    : index.tops.slice(0, 12).map((id) => ({ color: `--cx-hue-${index.colourOf(id) + 1}`,
      text: themeName(index, id, locale.value) }));
  return { kinds, entries };
}

/** The screen. */
export function AtlasScreen() {
  const ctx = usePage();
  const { app } = ctx;
  usePageTitle(t('nav.map'));
  const [state, setStateRaw] = useState(() => readState(ctx.query));
  const [atlas, setAtlas] = useState(null);
  const [error, setError] = useState(null);
  const [texts, setTexts] = useState(null);
  const [sets, setSets] = useState(() => new Map());
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [watched, setWatched] = useState(null);
  const [tick, setTick] = useState(0);
  const [land, setLand] = useState(null);
  const frame = useRef(null);
  const asked = useRef(new Set());
  const preview = useMemo(() => createLayoutPreview({ api: ctx.api, jobs: app.stores.jobs }), []);
  const [keeping, setKeeping] = useState(false);
  const fs = useFullscreen();
  const [folded, toggleColumn] = useColumns();
  const [comparing, setComparing] = useState(false);
  const [distances, setDistances] = useState(false);
  const centred = useRef(false);
  useEffect(() => () => preview.dispose(), []);
  // a layout draft outlives the panel: leaving asks first
  useEffect(() => ctx.guard({ dirty: () => preview.draft.value !== null }), []);

  const setState = (patch) => {
    setStateRaw((s) => {
      const next = { ...s, ...patch };
      writeState(next);
      return next;
    });
  };

  // The bundle: when the page opens, when the base changes, after a redraw of the map.
  useEffect(() => {
    setError(null);
    ctx.api.get('/api/atlas', { query: state.base ? { base: state.base } : {} }).then((r) => {
      if (!r.ok) setError(r.error);
      else setAtlas(r.data);
    });
    setTexts(null);
  }, [state.base, tick]);

  const bare = useMemo(() => (atlas && atlas.available ? indexAtlas(atlas) : null), [atlas]);
  // The time windows: every person's when they are shown, else the selected person's.
  const [windows, setWindows] = useState(() => new Map());
  const windowsAsked = useRef({ of: null, all: false, people: new Set() });
  const index = useMemo(() => (bare ? { ...bare, windows } : null), [bare, windows]);
  const wantAllWindows = state.show.includes('windows');
  const windowsOf = state.sel && state.sel.kind === 'person' ? state.sel.id : null;
  useEffect(() => {
    if (!bare || !(atlas.windows > 0)) return;
    const asked = windowsAsked.current;
    if (asked.of !== bare) {
      asked.of = bare;
      asked.all = false;
      asked.people = new Set();
      setWindows(new Map());
    }
    if (asked.all || (!wantAllWindows && (!windowsOf || asked.people.has(windowsOf)))) return;
    const query = { ...(state.base ? { base: state.base } : {}), ...(wantAllWindows ? {} : { person: windowsOf }) };
    if (wantAllWindows) asked.all = true;
    else asked.people.add(windowsOf);
    ctx.api.get('/api/atlas/windows', { query }).then((r) => {
      if (windowsAsked.current.of !== bare) return;
      if (!r.ok || !r.data.available) {
        if (wantAllWindows) windowsAsked.current.all = false;
        return;
      }
      setWindows((old) => indexWindows(bare, r.data, wantAllWindows ? new Map() : old));
    });
  }, [bare, wantAllWindows, windowsOf]);
  const wantTexts = state.show.includes('texts') || (state.sel && state.sel.kind === 'text');
  useEffect(() => {
    if (!index || !wantTexts || texts) return;
    ctx.api.get('/api/atlas/texts', { query: state.base ? { base: state.base } : {} }).then((r) => {
      if (r.ok && r.data.available) setTexts(r.data);
    });
  }, [index, wantTexts, texts]);

  // The keywords of the people and organisations the map draws as regions or has selected.
  useEffect(() => {
    if (!index || state.view === 'world') return;
    const want = { person: [], organisation: [] };
    const sel = state.sel;
    if (sel && (sel.kind === 'person' || sel.kind === 'organisation')) want[sel.kind].push(sel.id);
    if (state.as === 'regions') {
      const level = state.org || (index.levels[0] && index.levels[0].id);
      if (state.show.includes('organisations')) {
        for (const o of index.orgs) if (o.level === level && o.x !== null) want.organisation.push(o.id);
      }
      if (state.show.includes('people')) {
        const mask = matching(index, state);
        const ids = index.people.filter((p, i) => mask[i] && p.x !== null).map((p) => p.person_id);
        if (ids.length <= MAX_REGIONS) want.person.push(...ids);
      }
    }
    for (const kind of ['person', 'organisation']) {
      const ids = want[kind].filter((id) => !asked.current.has(`${kind}:${id}`)).slice(0, 500);
      if (!ids.length) continue;
      for (const id of ids) asked.current.add(`${kind}:${id}`);
      ctx.api.get('/api/atlas/regions', { query: { kind, ids: ids.join(',') } }).then((r) => {
        if (!r.ok) return;
        setSets((old) => {
          const next = new Map(old);
          for (const [id, terms] of Object.entries(r.data.keywords)) next.set(`${kind}:${id}`, terms);
          return next;
        });
      });
    }
  }, [index, state.sel, state.as, state.show, state.org, state.filters, state.view]);

  useJobEnd(app, watched, (job) => {
    setWatched(null);
    if (job.state === 'succeeded') setTick((n) => n + 1);
  });

  // The outline of the land under the world view: a static file (Natural Earth, public domain).
  useEffect(() => {
    if (state.view !== 'world' || land) return;
    ctx.keep(fetch('/static/data/world-land-110m.json').then((r) => (r.ok ? r.json() : null)).catch(() => null))
      .then((doc) => { if (doc && Array.isArray(doc.rings)) setLand(doc.rings); });
  }, [state.view]);

  // The space of the themes: the selection's nearest, or the people who use a keyword.
  const spaceAnswer = useSpaceOf(ctx, index, state.sel, state.view !== 'world');
  const space = useMemo(() => (index && spaceAnswer ? litBySpace(index, state.sel, spaceAnswer) : null),
    [index, spaceAnswer]);

  const built = useMemo(() => {
    if (!index) return null;
    return state.view === 'world' ? worldScene(index, state, land)
      : mapScene(index, state, { texts, sets, locale: locale.value, space });
  }, [index, state, texts, sets, locale.value, land, space]);

  // a preview of the layout replaces the map's scene (the sample of people it drew)
  const shown = preview.phase.value !== 'idle' ? preview.preview.value : null;
  const before = Boolean(shown && shown.current && preview.side.value === 'before');
  const previewed = useMemo(() => (shown && index && state.view !== 'world'
    ? previewScene(before ? shown.current.points : shown.points, shown.themes, index) : null), [shown, before, index, state.view]);

  const select = (sel, { centre = false } = {}) => {
    setState({ sel });
    if (!sel || !centre || !frame.current || !index) return;
    let at = null;
    if (sel.kind === 'person') at = index.people[index.byPerson.get(sel.id)];
    else if (sel.kind === 'organisation') {
      const o = index.orgs[index.byOrg.get(sel.id)];
      at = state.view === 'world' && o.location ? { x: o.location.lon, y: o.location.lat } : o;
    } else if (sel.kind === 'keyword') at = index.keywords[index.byTerm.get(sel.id)];
    else if (sel.kind === 'text' && texts) {
      const i = texts.id.indexOf(sel.id);
      if (i >= 0) at = { x: texts.x[i], y: texts.y[i] };
    }
    if (at && at.x !== null && at.x !== undefined) frame.current.centreOn(at.x, at.y, 3);
  };

  // A selection given in the address (a link from another screen): centred once it is drawn
  // (a text's once the texts are read).
  useEffect(() => {
    const sel = state.sel;
    if (centred.current || !index || !sel) return undefined;
    if (sel.kind === 'text' && !texts) return undefined;
    centred.current = true;
    const timer = setTimeout(() => select(sel, { centre: true }), 0);
    return () => clearTimeout(timer);
  }, [index, texts]);

  const head = html`<header class="cx-atlas__head">
    <div>
      <h1 class="cx-page__title" tabindex="-1">${t('nav.map')}</h1>
      ${atlas && atlas.available ? html`<p class="cx-atlas__summary">${t('map.summary', {
        people: (atlas.people || []).filter((p) => p.x !== null).length, keywords: (atlas.keywords || []).length,
        version: atlas.map_version || '' })}</p>` : null}
    </div>
    ${index ? html`<div class="cx-atlas__actions">
      <${Find} index=${index} texts=${texts} onSelect=${select} />
      <${Segmented} label=${t('map.view')} value=${state.view}
        options=${[{ value: 'map', label: t('map.view.map') }, { value: 'world', label: t('map.view.world') }]}
        onChange=${(view) => setState({ view })} />
      <${Button} icon="download" onClick=${() => setDistances(true)} aria-haspopup="dialog">${t('map.distances.button')}<//>
      <${Button} icon="settings" onClick=${() => setVersionsOpen(true)}>${t('map.versions.button')}<//>
    </div>` : null}
  </header>`;

  if (error) {
    return html`<div class="cx-page cx-atlas">${head}
      <${ErrorCard} error=${error} onRetry=${() => setTick((n) => n + 1)} /></div>`;
  }
  if (!atlas) {
    return html`<div class="cx-page cx-atlas">${head}<p aria-busy="true">${t('common.loading')}</p></div>`;
  }
  if (!atlas.available) {
    return html`<div class="cx-page cx-atlas">${head}
      <${TunePanel} ctx=${ctx} id="map" />
      <${EmptyState} icon="file" level=${2} title=${t('map.none')}
        action=${{ label: t('map.none.build'), href: '/build?scope=map' }}>${t('map.none.text')}<//>
    </div>`;
  }
  const { counts, notes } = built;
  const scene = previewed || built.scene;
  const keep = async () => {
    setKeeping(true);
    await preview.keep(ctx.navigate, app.toaster);
    setKeeping(false);
  };
  const sel = state.sel;
  const lit = scene.layers.reduce((n, l) => n + (l.highlightCount || 0), 0);
  const status = sel ? t('map.status', { count: lit }) : '';
  const base = atlas.base || null;
  return html`<div class="cx-page cx-atlas">
    ${head}
    <${TunePanel} ctx=${ctx} id="map" preview=${preview} />
    ${base ? html`<div class="cx-atlas__banner" role="status">
      <p>${t('map.base.banner', { name: base.name, version: base.map_version, shared: base.shared_keywords })}</p>
      <${Button} size="s" onClick=${() => setState({ base: '', sel: null })}>${t('map.base.back')}<//>
    </div>` : null}
    <${Controls} index=${index} state=${state} counts=${counts} texts=${texts} onChange=${setState} />
    ${notes.map((n) => html`<p key=${n.kind} class="cx-atlas__note" role="note">
      ${t(n.key, { kind: t(`map.kind.${n.kind}`), count: n.count, max: MAX_REGIONS, total: n.total || 0 })}</p>`)}
    <div ref=${fs.ref} class=${`cx-atlas__body ${fs.className} ${folded.tree ? 'is-tree-folded' : ''} ${folded.panel ? 'is-panel-folded' : ''}`}>
      ${folded.tree ? null : html`<${TreePanel} index=${index} state=${state} onSelect=${(s) => select(s)}
        onZoom=${(theme) => setState({ theme })} />`}
      <div class="cx-atlas__map">
        ${preview.phase.value !== 'idle' ? html`<${PreviewBar} store=${preview} onKeep=${keep} busy=${keeping} />` : null}
        <div class="cx-atlas__tools">
          <${IconButton} icon="plus" size="s" label=${t('themes.map.zoom_in')}
            onClick=${() => frame.current && frame.current.zoomBy(1.4)} />
          <${IconButton} icon="dash" size="s" label=${t('themes.map.zoom_out')}
            onClick=${() => frame.current && frame.current.zoomBy(1 / 1.4)} />
          <${Button} size="s" variant="ghost" onClick=${() => frame.current && frame.current.fit()}>
            ${t('themes.map.fit')}<//>
          <span class="cx-atlas__keys" aria-hidden="true">${t('themes.map.keys')}</span>
          <${MenuButton} size="s" variant="ghost" icon="download" label=${t('map.save')}
            items=${[{ id: 'png', label: t('map.save.png') }, { id: 'png-legend', label: t('map.save.png_legend') },
              { id: 'svg', label: t('map.save.svg') }, { id: 'svg-legend', label: t('map.save.svg_legend') }]}
            onSelect=${(item) => {
              const box = fs.ref.current && fs.ref.current.querySelector('.cx-atlas__frame .cx-map-frame__box');
              if (!frame.current || !box) return;
              const args = { frame: frame.current, box, scene, version: atlas.map_version,
                legend: item.id.endsWith('-legend') ? legendOf(index, state, counts) : null };
              if (item.id.startsWith('svg')) saveSvg(args);
              else savePng(args);
            }} />
          <${ColumnButtons} hidden=${folded} onToggle=${toggleColumn} />
          <${FullscreenButton} fs=${fs} />
        </div>
        <${MapFrame} class=${`cx-atlas__frame ${previewed ? 'is-preview' : ''}`} scene=${scene} frameRef=${frame}
          label=${previewed ? t(before ? 'map.preview.before_label' : 'map.preview.label', { n: shown.sample })
            : state.view === 'world' ? t('map.world.label') : t('map.label')} status=${status}
          onPick=${(hit) => (previewed ? null : select(pickOf(scene, hit, index, texts)))}
          hoverCard=${(hit) => {
            const s = previewed ? null : pickOf(scene, hit, index, texts);
            return s ? html`<${HoverCard} sel=${s} index=${index} texts=${texts} />` : null;
          }}
          legend=${html`<${Legend} index=${index} state=${state} counts=${previewed ? { people: shown.sample } : counts}
            onSelect=${(s) => select(s)} />`} />
      </div>
      ${folded.panel ? null : html`<${Panel} index=${index} state=${state} counts=${counts} sets=${sets} texts=${texts}
        base=${base} space=${spaceAnswer} onCompare=${() => setComparing(true)}
        onSelect=${(s) => select(s, { centre: true })} onClose=${() => setState({ sel: null })} />`}
    </div>
    <${CompareDialog} ctx=${ctx} index=${index} a=${sel} open=${comparing && Boolean(sel)}
      onClose=${() => setComparing(false)} onSelect=${(s) => select(s, { centre: true })} />
    <${DistancesDialog} ctx=${ctx} open=${distances} onClose=${() => setDistances(false)}
      shown=${distances && state.filters.length ? shownPeople(index, state) : null}
      onStarted=${() => {
        app.stores.jobs.refresh();
        app.toaster.show({ kind: 'info', title: t('map.distances.started'), message: t('map.distances.started_text'),
          action: { label: t('nav.share'), onClick: () => ctx.navigate('/share') } });
      }} />
    <${VersionsDialog} ctx=${ctx} open=${versionsOpen} onClose=${() => setVersionsOpen(false)}
      drawn=${atlas.map_version} base=${state.base}
      onBase=${(id) => setState({ base: id, sel: null })}
      onBuild=${(job) => {
        setWatched(job.id);
        app.stores.jobs.refresh();
        app.toaster.show({ kind: 'info', title: t('map.versions.building'), message: t('map.versions.building_text'),
          action: { label: t('activity.title'), onClick: () => runtime.openActivity() } });
      }} />
  </div>`;
}

