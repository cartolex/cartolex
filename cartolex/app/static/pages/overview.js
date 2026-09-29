// SPDX-License-Identifier: MIT
/**
 * Overview (a first version): the project's name, every stage of the build
 * with its state, and the `overview.cards` slot extensions add cards to.
 * The status renders at once from the cached state, then refreshes.
 */
import { html, useEffect } from '../core/preact.js';
import { formatDate, t } from '../core/i18n.js';
import { definePage, usePageTitle } from '../core/page.js';
import { Card, EmptyState, ErrorCard, Slot, StageTracker } from '../components/index.js';

function Overview({ ctx }) {
  const { app } = ctx;
  const { project } = app.stores;
  usePageTitle(t('nav.overview'));
  useEffect(() => {
    // Refresh on arrival; a cached state is already shown, so the page is ready now.
    ctx.keep(project.refresh());
  }, []);
  const info = app.manifest.project || { open: false };
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
  return html`<div class="cx-page">
    <h1 class="cx-page__title">${info.name || t('nav.overview')}</h1>
    <p class="cx-page__lead">
      ${data && data.updated_at ? t('overview.updated', { time: formatDate(data.updated_at, 'datetime') })
        : t('common.loading')}
    </p>
    ${project.error.value && !data ? html`<${ErrorCard} error=${project.error.value}
      onRetry=${() => project.refresh()} />` : null}
    <div class="cx-grid">
      <${Card} title=${t('overview.build')} level=${2} loading=${!data} class="cx-grid__wide">
        ${data ? html`<${StageTracker} stages=${data.stages || []} />` : null}
      <//>
      <${Slot} slots=${app.registries.slots} name="overview.cards" class="cx-grid__slot" />
    </div>
  </div>`;
}

export const page = definePage(Overview);
