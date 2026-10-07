// SPDX-License-Identifier: MIT
/**
 * The atlas page (`/map`): the shared atlas (`static/atlas/`, `docs/dev/atlas.md`) mounted
 * with the app's source and host (`source.js`), and around it what only the app has: the
 * map's « Tune » panel, opened beside the atlas in the card's column (the card on its rail
 * meanwhile, `atlas.hold`; its width the person's), and its layout preview drawn on the
 * atlas's map (`preview.js`), the
 * map versions and base maps (`versions.js`), the distances' exports
 * (`pages/share/distances.js`); and, in place of the atlas, the « Distances » pane
 * (`distances.js`, `?pane=distances`: ranked lists, pairs and matrices, the offline site's own
 * code). Opening it reads `GET /api/atlas` only; the atlas reads the rest when it shows it.
 */
import { html, useEffect, useMemo, useRef, useState } from '../../core/preact.js';
import { locale, t } from '../../core/i18n.js';
import { usePage, usePageTitle } from '../../core/page.js';
import { runtime } from '../../core/runtime.js';
import { Button, EmptyState, ErrorCard } from '../../components/index.js';
import { mountAtlas } from '../../atlas/atlas.js';
import { divider } from '../../atlas/panes.js';
import { matchingPeople } from '../../atlas/data.js';
import { useJobEnd } from '../people/common.js';
import { VersionsDialog } from './versions.js';
import { TunePanel, TuneSide } from '../tune/panel.js';
import { PANELS, changedCount } from '../tune/common.js';
import { PreviewBar, createLayoutPreview, previewScene } from './preview.js';
import { DistancesDialog } from '../share/distances.js';
import { apiSource, appHost } from './source.js';
import { DistancesMount } from './distances.js';

/** The panes the screen shows, one at a time. */
const PANES = ['atlas', 'distances'];

/** The width of the side « Tune » panel: the person's preference, its limits and default. */
const TUNE_WIDTH = 'map.tune_width';
const TUNE_LIMITS = [300, 760];
const TUNE_DEFAULT = 440;
const clampWidth = (v) => Math.max(TUNE_LIMITS[0], Math.min(TUNE_LIMITS[1], Math.round(v)));

/** The base map in the address ('' for none). */
const baseOf = () => new URLSearchParams(window.location.search).get('base') || '';
/** The map version the address shows ('' for the pinned one). */
const versionOf = () => new URLSearchParams(window.location.search).get('map') || '';

/** The people on the map the atlas's filters keep (null: no filter). */
function shownPeople(atlas) {
  const index = atlas && atlas.index();
  const state = atlas && atlas.state();
  if (!index || !state || !state.filters.length) return null;
  const mask = matchingPeople(index, state);
  return index.people.filter((p, i) => mask[i] && p.person_id && p.x !== null).map((p) => p.person_id);
}

/** The mounted atlas, in an element of its own; mounted again when the bundle or the
 * interface's language changes. */
function AtlasMount({ ctx, bundle, onAtlas, onShown }) {
  const ref = useRef(null);
  const lang = locale.value;
  useEffect(() => {
    const project = ctx.app.manifest.project;
    let shown = bundle;
    const host = appHost(ctx, { title: project && project.open ? project.name || '' : '',
      stem: () => shown.map_version || 'view',
      onReady: ({ index }) => {
        shown = index.bundle;
        onShown(index.bundle);
      } });
    const atlas = mountAtlas(ref.current, { source: apiSource(ctx, { first: bundle, base: baseOf }), host });
    onAtlas(atlas);
    return () => {
      onAtlas(null);
      atlas.destroy();
    };
  }, [bundle, lang]);
  return html`<div ref=${ref} class="cx-atlas-host"></div>`;
}

/** Focus the element of *id* once it is shown (the panel reads its parameters first) and no
 * dialog is open (a dialog gives the focus back to its opener when it closes). */
function focusOnceShown(id, tries = 40) {
  const at = document.getElementById(id);
  if (at && !document.querySelector('dialog[open]')) {
    at.scrollIntoView({ block: 'start' });
    at.focus();
  } else if (tries > 0) setTimeout(() => focusOnceShown(id, tries - 1), 100);
}

/** The divider between the atlas and the side « Tune » panel: dragged, or moved with the
 * arrows (Home and End for the smallest and largest); the width is kept per person. */
function TuneDivider({ width, onWidth, onEnd }) {
  const ref = useRef(null);
  const now = useRef(width);
  now.current = width;
  useEffect(() => divider(ref.current, {
    get: () => now.current,
    onMove: (dx, dy, start) => {
      now.current = clampWidth(start - dx);
      onWidth(now.current);
    },
    onEnd: () => onEnd(now.current),
  }), []);
  return html`<div ref=${ref} class="cx-atlas-body__split" role="separator" tabindex="0"
    aria-orientation="vertical" aria-label=${t('map.tune.resize')}
    aria-valuemin=${TUNE_LIMITS[0]} aria-valuemax=${TUNE_LIMITS[1]} aria-valuenow=${width}></div>`;
}

/** The screen. */
export function AtlasScreen() {
  const ctx = usePage();
  const { app } = ctx;
  usePageTitle(t('nav.map'));
  const [bundle, setBundle] = useState(null);
  const [error, setError] = useState(null);
  const [tick, setTick] = useState(0);
  const [base, setBase] = useState(baseOf);
  const [atlas, setAtlas] = useState(null);
  // the bundle the atlas shows now (another map version, chosen in its « Layout »)
  const [shownBundle, setShownBundle] = useState(null);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [distances, setDistances] = useState(false);
  const [watched, setWatched] = useState(null);
  const [keeping, setKeeping] = useState(false);
  const prefs = app.stores.prefs;
  const [tuneOpen, setTuneOpen] = useState(() => Boolean(ctx.query && ctx.query.get('tune') === '1'));
  const [pane, setPane] = useState(() => (ctx.query && ctx.query.get('pane') === 'distances' ? 'distances' : 'atlas'));
  const showPane = (id) => {
    const url = new URL(window.location.href);
    if (id === 'distances') url.searchParams.set('pane', id);
    else url.searchParams.delete('pane');
    window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
    if (id === 'distances') setTuneOpen(false);
    setPane(id);
  };
  const [tuneWidth, setTuneWidth] = useState(() => {
    const kept = Number(prefs.get(TUNE_WIDTH));
    return Number.isFinite(kept) && kept > 0 ? clampWidth(kept) : TUNE_DEFAULT;
  });
  const tuneButton = useRef(null);
  const sideRef = useRef(null);
  const openTune = (open) => {
    setTuneOpen(open);
    // the focus follows: into the panel's title when it opens, back to its button when it closes
    setTimeout(() => {
      const target = open ? sideRef.current && sideRef.current.querySelector('.cx-tune__title') : tuneButton.current;
      if (target) target.focus();
    }, 0);
  };
  const preview = useMemo(() => createLayoutPreview({ api: ctx.api, jobs: app.stores.jobs }), []);
  useEffect(() => () => preview.dispose(), []);
  // a layout draft outlives the panel: leaving asks first
  useEffect(() => ctx.guard({ dirty: () => preview.draft.value !== null }), []);

  useEffect(() => {
    setError(null);
    // a base map is placed on the pinned (flat) version only
    const version = base ? '' : versionOf();
    ctx.api.get('/api/atlas', { query: { ...(base ? { base } : {}), ...(version ? { version } : {}) } }).then((r) => {
      if (!r.ok) setError(r.error);
      else setBundle(r.data);
    });
  }, [base, tick]);

  useJobEnd(app, watched, (job) => {
    setWatched(null);
    if (job.state === 'succeeded') setTick((n) => n + 1);
  });

  // a preview of the layout replaces the atlas's map (the sample of people it drew)
  const shown = preview.phase.value !== 'idle' ? preview.preview.value : null;
  const before = Boolean(shown && shown.current && preview.side.value === 'before');
  useEffect(() => {
    if (!atlas) return;
    const index = atlas.index();
    const colours = atlas.colours();
    if (!shown || !index || !colours) {
      atlas.setScene(null);
      return;
    }
    atlas.setScene(previewScene(before ? shown.current.points : shown.points, shown.themes, index, colours),
      { label: t(before ? 'map.preview.before_label' : 'map.preview.label', { n: shown.sample }) });
  }, [atlas, shown, before]);

  // the side « Tune » panel takes the card's column: the card waits on its rail meanwhile
  useEffect(() => {
    if (atlas) atlas.hold(tuneOpen ? { cardOn: false } : null);
  }, [atlas, tuneOpen]);

  const changeBase = (id) => {
    const url = new URL(window.location.href);
    if (id) {
      url.searchParams.set('base', id);
      // a base map is shown on the pinned version: the address leaves any other one
      url.searchParams.delete('map');
    } else url.searchParams.delete('base');
    url.searchParams.delete('sel');
    url.searchParams.delete('with');
    window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
    setBase(id);
  };

  /** Show a built map version in the atlas (`map=<id>`; the pinned one: none). A base map
   * is shown on the pinned version only, so it is left first. */
  const showVersion = (v) => {
    setVersionsOpen(false);
    const id = v.pinned ? '' : v.id;
    if (base) {
      const url = new URL(window.location.href);
      if (id) url.searchParams.set('map', id);
      else url.searchParams.delete('map');
      window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
      changeBase('');
    } else if (atlas) atlas.set({ map: id });
  };

  const available = bundle && bundle.available;
  const tuneCount = changedCount(PANELS.map, app.stores.project.state.value);
  const head = html`<header class="cx-atlas__head">
    <div>
      <h1 class="cx-page__title" tabindex="-1">${t('nav.map')}</h1>
      ${available ? html`<p class="cx-atlas__summary">${t('map.summary', {
        people: (bundle.people || []).filter((p) => p.x !== null).length, keywords: (bundle.keywords || []).length,
        version: (shownBundle || bundle).map_version || '' })}</p>` : null}
    </div>
    ${available ? html`<div class="cx-atlas__actions">
      <div class="cx-atlas__panes" role="group" aria-label=${t('map.pane')}>
        ${PANES.map((id) => html`<${Button} key=${id} variant=${pane === id ? 'primary' : 'secondary'}
          aria-pressed=${pane === id ? 'true' : 'false'} data-pane=${id} onClick=${() => showPane(id)}>${t(`map.pane.${id}`)}<//>`)}
      </div>
      ${pane === 'atlas' ? html`<${Button} icon="panel-right" buttonRef=${tuneButton} aria-expanded=${tuneOpen ? 'true' : 'false'}
        aria-controls="cx-map-tune" data-tune-toggle onClick=${() => openTune(!tuneOpen)}>${t('tune.title.map')}${
        tuneCount ? html` <span class="cx-tune__count is-changed">${t('tune.changed', { n: tuneCount })}</span>` : null}<//>` : null}
      <${Button} icon="download" onClick=${() => setDistances(true)} aria-haspopup="dialog">${t('map.distances.button')}<//>
      <${Button} icon="settings" onClick=${() => setVersionsOpen(true)}>${t('map.versions.button')}<//>
    </div>` : null}
  </header>`;

  if (error) {
    return html`<div class="cx-page cx-atlas-page">${head}
      <${ErrorCard} error=${error} onRetry=${() => setTick((n) => n + 1)} /></div>`;
  }
  if (!bundle) return html`<div class="cx-page cx-atlas-page">${head}<p aria-busy="true">${t('common.loading')}</p></div>`;
  if (!available) {
    return html`<div class="cx-page cx-atlas-page">${head}
      <${TunePanel} ctx=${ctx} id="map" />
      <${EmptyState} icon="file" level=${2} title=${t('map.none')}
        action=${{ label: t('map.none.build'), href: '/build?scope=map' }}>${t('map.none.text')}<//>
    </div>`;
  }
  const keep = async () => {
    setKeeping(true);
    await preview.keep(ctx.navigate, app.toaster);
    setKeeping(false);
  };
  const baseInfo = bundle.base || null;
  const project = app.manifest.project;
  const previewing = preview.phase.value !== 'idle';
  const previewBar = html`<${PreviewBar} store=${preview} onKeep=${keep} busy=${keeping} />`;
  return html`<div class="cx-page cx-atlas-page">
    ${head}
    ${baseInfo ? html`<div class="cx-atlas__banner" role="status">
      <p>${t('map.base.banner', { name: baseInfo.name, version: baseInfo.map_version, shared: baseInfo.shared_keywords })}</p>
      <${Button} size="s" onClick=${() => changeBase('')}>${t('map.base.back')}<//>
    </div>` : null}
    ${pane === 'distances' ? html`<div class="cx-distances-pane">
      <${DistancesMount} ctx=${ctx} bundle=${baseInfo ? null : bundle} title=${project && project.open ? project.name || '' : ''} />
    </div>` : html`<div class=${`cx-atlas-body ${tuneOpen ? 'has-side' : ''}`} style=${`--cx-tune-width: ${tuneWidth}px`}>
      <div class="cx-atlas-body__main">
        ${!tuneOpen && previewing ? previewBar : null}
        <${AtlasMount} ctx=${ctx} bundle=${bundle} onAtlas=${setAtlas} onShown=${setShownBundle} />
      </div>
      ${tuneOpen ? html`<${TuneDivider} width=${tuneWidth} onWidth=${setTuneWidth}
          onEnd=${(w) => prefs.set(TUNE_WIDTH, w)} />
        <div class="cx-atlas-body__side" id="cx-map-tune" ref=${sideRef}>
          <${TuneSide} ctx=${ctx} id="map" preview=${preview} top=${previewing ? previewBar : null}
            onClose=${() => openTune(false)} />
        </div>` : null}
    </div>`}
    <${DistancesDialog} ctx=${ctx} open=${distances} onClose=${() => setDistances(false)}
      shown=${distances ? shownPeople(atlas) : null}
      onTune=${() => {
        setDistances(false);
        setTuneOpen(true);
        setTimeout(() => focusOnceShown('cx-map-similarity-title'), 50);
      }}
      onStarted=${() => {
        app.stores.jobs.refresh();
        app.toaster.show({ kind: 'info', title: t('map.distances.started'), message: t('map.distances.started_text'),
          action: { label: t('nav.share'), onClick: () => ctx.navigate('/share') } });
      }} />
    <${VersionsDialog} ctx=${ctx} open=${versionsOpen} onClose=${() => setVersionsOpen(false)}
      drawn=${(shownBundle || bundle).map_version} base=${base} onBase=${changeBase}
      pinned3d=${(bundle.versions || []).some((v) => v.pinned && v.dimensions === 3)}
      onShow=${showVersion} refresh=${tick}
      onBuild=${(job) => {
        setWatched(job.id);
        app.stores.jobs.refresh();
        app.toaster.show({ kind: 'info', title: t('map.versions.building'), message: t('map.versions.building_text'),
          action: { label: t('activity.title'), onClick: () => runtime.openActivity() } });
      }} />
  </div>`;
}
