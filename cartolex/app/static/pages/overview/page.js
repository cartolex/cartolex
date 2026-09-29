// SPDX-License-Identifier: MIT
/**
 * The overview: the project's name and state, the one next step, the stage
 * tracker, the health panel, a preview of the map and the recent shared
 * builds, then the `overview.cards` slot extensions add cards to.
 *
 * Two reads: the project state (its cached copy renders at once) and
 * `GET /api/overview`. Both are read again when a job ends.
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatDate, t } from '../../core/i18n.js';
import { usePageTitle } from '../../core/page.js';
import { runtime } from '../../core/runtime.js';
import {
  Button, Card, EmptyState, ErrorCard, Slot, StageTracker, StatusPill,
} from '../../components/index.js';
import { ImportDialog } from '../people/import.js';
import { Health, NextStep, Shares } from './cards.js';
import { MapPreview } from './preview.js';

export function Overview({ ctx }) {
  const { app } = ctx;
  const { project, jobs } = app.stores;
  usePageTitle(t('nav.overview'));
  const [view, setView] = useState(null);
  const [error, setError] = useState(null);
  const info = app.manifest.project || { open: false };
  // A new project that starts from a folder of documents or a corpus (`?start=`)
  // opens that import at once; the address loses `start`, so a reload does not.
  const [importing, setImporting] = useState(() => {
    const start = ctx.query && ctx.query.get('start');
    return info.open && (start === 'folder' || start === 'corpus') ? start : null;
  });
  useEffect(() => {
    const url = new URL(window.location.href);
    if (!url.searchParams.has('start')) return;
    url.searchParams.delete('start');
    window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
  }, []);
  const imported = (job) => {
    jobs.refresh();
    app.toaster.show({ kind: 'info', title: t('corpus.job.started'), message: t('corpus.job.follow'),
      action: { label: t('activity.title'), onClick: () => runtime.openActivity() } });
    return job;
  };
  const load = () => ctx.api.get('/api/overview').then((r) => {
    if (r.ok) {
      setView(r.data);
      setError(null);
    } else setError(r.error);
  });
  useEffect(() => {
    if (!info.open) return;
    // A cached state is already shown, so the page is ready now; both reads refresh it.
    ctx.keep(project.refresh());
    load();
  }, []);
  // A job that ends changes the next step and the health: read them again.
  const active = jobs.active.value.length;
  const [seen, setSeen] = useState(active);
  useEffect(() => {
    if (active < seen && info.open) load();
    setSeen(active);
  }, [active]);

  const data = project.state.value;
  if (!info.open) {
    return html`<div class="cx-page">
      <h1 class="cx-page__title">${t('nav.overview')}</h1>
      <${EmptyState} icon="file" level=${2} title=${t('overview.no_project.title')}
        action=${{ label: t('start.open_or_create'), href: '/start' }}>
        ${t('overview.no_project.text')}
      <//>
    </div>`;
  }
  const state = view ? view.project.state : null;
  const stale = view ? view.health.find((h) => h.code === 'health_map_stale') || null : null;
  return html`<div class="cx-page cx-overview">
    <div class="cx-overview__head">
      <h1 class="cx-page__title">${(view && view.project.name) || info.name || t('nav.overview')}</h1>
      ${state ? html`<${StatusPill} state=${state} />` : null}
    </div>
    <p class="cx-page__lead">
      ${data && data.updated_at ? t('overview.updated', { time: formatDate(data.updated_at, 'datetime') })
        : t('overview.lead')}
    </p>
    ${error ? html`<${ErrorCard} error=${error} onRetry=${load} compact />` : null}
    ${project.error.value && !data ? html`<${ErrorCard} error=${project.error.value}
      onRetry=${() => project.refresh()} />` : null}
    <div class="cx-grid">
      ${view ? html`<${NextStep} item=${view.next} />` : null}
      <${Card} title=${t('overview.build')} level=${2} loading=${!data} class="cx-overview-stages"
        actions=${html`<${Button} size="s" variant="secondary"
          onClick=${() => runtime.navigate('/build')}>${t('overview.build.open')}<//>`}>
        ${data ? html`<${StageTracker} stages=${data.stages || []} />` : null}
      <//>
      <div class="cx-overview__side">
        ${view ? html`<${Health} items=${view.health} />` : null}
        <${MapPreview} preview=${view ? view.preview : null} stale=${stale} loading=${!view && !error} />
        ${view ? html`<${Shares} shares=${view.shares} />` : null}
      </div>
      <${Slot} slots=${app.registries.slots} name="overview.cards" class="cx-grid__slot" />
    </div>
    ${importing ? html`<${ImportDialog} ctx=${ctx} mode=${importing} person=${null}
      onStarted=${imported} onClose=${() => setImporting(null)} />` : null}
  </div>`;
}
