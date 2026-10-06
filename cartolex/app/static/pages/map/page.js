// SPDX-License-Identifier: MIT
/**
 * The atlas page (`/map`): the shared atlas (`static/atlas/`, `docs/dev/atlas.md`) mounted
 * with the app's source and host (`source.js`), and around it what only the app has: the
 * map's « Tune » panel and its layout preview drawn on the atlas's map (`preview.js`), the
 * map versions and base maps (`versions.js`), the distances' exports
 * (`pages/share/distances.js`). Opening it reads `GET /api/atlas` only; the atlas reads the
 * rest when it shows it.
 */
import { html, useEffect, useMemo, useRef, useState } from '../../core/preact.js';
import { locale, t } from '../../core/i18n.js';
import { usePage, usePageTitle } from '../../core/page.js';
import { runtime } from '../../core/runtime.js';
import { Button, EmptyState, ErrorCard } from '../../components/index.js';
import { mountAtlas } from '../../atlas/atlas.js';
import { matchingPeople } from '../../atlas/data.js';
import { useJobEnd } from '../people/common.js';
import { VersionsDialog } from './versions.js';
import { TunePanel } from '../tune/panel.js';
import { PreviewBar, createLayoutPreview, previewScene } from './preview.js';
import { DistancesDialog } from '../share/distances.js';
import { apiSource, appHost } from './source.js';

/** The base map in the address ('' for none). */
const baseOf = () => new URLSearchParams(window.location.search).get('base') || '';

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
function AtlasMount({ ctx, bundle, onAtlas }) {
  const ref = useRef(null);
  const lang = locale.value;
  useEffect(() => {
    const project = ctx.app.manifest.project;
    const host = appHost(ctx, { title: project && project.open ? project.name || '' : '',
      stem: () => bundle.map_version || 'view' });
    const atlas = mountAtlas(ref.current, { source: apiSource(ctx, { first: bundle, base: baseOf }), host });
    onAtlas(atlas);
    return () => {
      onAtlas(null);
      atlas.destroy();
    };
  }, [bundle, lang]);
  return html`<div ref=${ref} class="cx-atlas-host"></div>`;
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
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [distances, setDistances] = useState(false);
  const [watched, setWatched] = useState(null);
  const [keeping, setKeeping] = useState(false);
  const preview = useMemo(() => createLayoutPreview({ api: ctx.api, jobs: app.stores.jobs }), []);
  useEffect(() => () => preview.dispose(), []);
  // a layout draft outlives the panel: leaving asks first
  useEffect(() => ctx.guard({ dirty: () => preview.draft.value !== null }), []);

  useEffect(() => {
    setError(null);
    ctx.api.get('/api/atlas', { query: base ? { base } : {} }).then((r) => {
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

  const changeBase = (id) => {
    const url = new URL(window.location.href);
    if (id) url.searchParams.set('base', id);
    else url.searchParams.delete('base');
    url.searchParams.delete('sel');
    url.searchParams.delete('with');
    window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
    setBase(id);
  };

  const available = bundle && bundle.available;
  const head = html`<header class="cx-atlas__head">
    <div>
      <h1 class="cx-page__title" tabindex="-1">${t('nav.map')}</h1>
      ${available ? html`<p class="cx-atlas__summary">${t('map.summary', {
        people: (bundle.people || []).filter((p) => p.x !== null).length, keywords: (bundle.keywords || []).length,
        version: bundle.map_version || '' })}</p>` : null}
    </div>
    ${available ? html`<div class="cx-atlas__actions">
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
  return html`<div class="cx-page cx-atlas-page">
    ${head}
    <${TunePanel} ctx=${ctx} id="map" preview=${preview} />
    ${baseInfo ? html`<div class="cx-atlas__banner" role="status">
      <p>${t('map.base.banner', { name: baseInfo.name, version: baseInfo.map_version, shared: baseInfo.shared_keywords })}</p>
      <${Button} size="s" onClick=${() => changeBase('')}>${t('map.base.back')}<//>
    </div>` : null}
    ${preview.phase.value !== 'idle' ? html`<${PreviewBar} store=${preview} onKeep=${keep} busy=${keeping} />` : null}
    <${AtlasMount} ctx=${ctx} bundle=${bundle} onAtlas=${setAtlas} />
    <${DistancesDialog} ctx=${ctx} open=${distances} onClose=${() => setDistances(false)}
      shown=${distances ? shownPeople(atlas) : null}
      onStarted=${() => {
        app.stores.jobs.refresh();
        app.toaster.show({ kind: 'info', title: t('map.distances.started'), message: t('map.distances.started_text'),
          action: { label: t('nav.share'), onClick: () => ctx.navigate('/share') } });
      }} />
    <${VersionsDialog} ctx=${ctx} open=${versionsOpen} onClose=${() => setVersionsOpen(false)}
      drawn=${bundle.map_version} base=${base} onBase=${changeBase}
      onBuild=${(job) => {
        setWatched(job.id);
        app.stores.jobs.refresh();
        app.toaster.show({ kind: 'info', title: t('map.versions.building'), message: t('map.versions.building_text'),
          action: { label: t('activity.title'), onClick: () => runtime.openActivity() } });
      }} />
  </div>`;
}
