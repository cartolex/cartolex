// SPDX-License-Identifier: MIT
/**
 * The page of a screen that comes in a later version. The core pages'
 * modules (people.js, keywords.js…) re-export it until their screen is built;
 * it shows the entry's label, the area's stages and their state, and the slot
 * `<page id>.cards`.
 */
import { html } from '../core/preact.js';
import { t } from '../core/i18n.js';
import { definePage, usePageTitle } from '../core/page.js';
import { areaOfPage } from '../core/states.js';
import { EmptyState, Slot, StageTracker } from '../components/index.js';

function Placeholder({ ctx }) {
  const { app } = ctx;
  const entry = app.registries.pages.get(ctx.pageId) || {};
  const title = entry.label ? t(entry.label) : ctx.pageId;
  usePageTitle(title);
  const area = app.stores.project.areas.value.get(areaOfPage({ id: ctx.pageId, ...entry }));
  return html`<div class="cx-page">
    <h1 class="cx-page__title">${title}</h1>
    ${area && area.stageList.length ? html`<section class="cx-page__section"
      aria-label=${t('placeholder.stages')}>
      <${StageTracker} stages=${area.stageList} label=${t('placeholder.stages')} />
    </section>` : null}
    <${Slot} slots=${app.registries.slots} name=${`${ctx.pageId}.cards`} class="cx-grid" />
    <${EmptyState} icon="file" level=${2} title=${t('placeholder.title')}
      action=${{ label: t('placeholder.action'), href: '/gallery' }}>
      ${t('placeholder.text')}
    <//>
  </div>`;
}

export const page = definePage(Placeholder);
