// SPDX-License-Identifier: MIT
/** The page of an address no page answers. */
import { html } from '../core/preact.js';
import { t } from '../core/i18n.js';
import { definePage, usePageTitle } from '../core/page.js';
import { EmptyState } from '../components/index.js';

function NotFound() {
  usePageTitle(t('notfound.title'));
  return html`<div class="cx-page">
    <h1 class="cx-page__title">${t('notfound.title')}</h1>
    <${EmptyState} icon="search" level=${2} title=${t('notfound.lead')}
      action=${{ label: t('notfound.action'), href: '/' }}>
      ${t('notfound.text')}
    <//>
  </div>`;
}

export const NotFoundPage = definePage(NotFound);
export default NotFoundPage;
