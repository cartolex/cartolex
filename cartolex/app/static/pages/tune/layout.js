// SPDX-License-Identifier: MIT
/**
 * The map's diagnostic (its « Tune » panel): the pinned map version (method, seed, layout
 * parameters), how many of each person's nearest people of the space the map
 * keeps, and the scores of the previews computed on the current space. The
 * previews themselves are drawn on the map (`pages/map/preview.js`), from the
 * layout's fields above (`map-settings.js`).
 */

import { html } from '../../core/preact.js';
import { formatNumber, formatPercent, t } from '../../core/i18n.js';
import { Facts, Lead } from './common.js';
import { shown } from './params.js';

const pct = (v) => (v === null || v === undefined ? '—' : formatPercent(v, { maximumFractionDigits: 0 }));

function paramsText(params) {
  const entries = Object.entries(params || {});
  return entries.length ? entries.map(([k, v]) => `${k} ${shown(v)}`).join(', ') : t('method.layout.defaults');
}

export function LayoutDiagnostic({ view }) {
  const pinned = view.pinned;
  const previews = view.previews || [];
  return html`<${Lead}>${t('method.layout.lead')}<//>
    <${Facts} items=${[
      [t('method.layout.version'), pinned ? pinned.id : '—'],
      [t('settings.layout.method'), pinned ? t(`settings.layout.method.${pinned.method}`) : '—'],
      [t('method.layout.params'), pinned ? paramsText(pinned.params) : '—'],
      [t('method.layout.seed'), pinned ? formatNumber(pinned.seed) : '—'],
      [t('method.layout.kept'), pct(view.overlap)],
      view.measures && view.measures.trustworthiness !== undefined
        ? [t('method.layout.trust'), pct(view.measures.trustworthiness)] : null,
    ]} />
    ${view.empty ? html`<p class="cx-settings__muted">${t('method.empty.empty_no_map')}</p>` : null}
    ${previews.length ? html`<table class="cx-settings__table" aria-label=${t('method.layout.scores')}>
      <caption class="cx-method-caption">${t('method.layout.scores')}</caption>
      <thead><tr><th scope="col">${t('settings.layout.method')}</th><th scope="col">${t('method.layout.params')}</th>
        <th scope="col" class="cx-num">${t('method.layout.seed')}</th><th scope="col" class="cx-num">${t('method.layout.kept')}</th></tr></thead>
      <tbody>
        ${view.overlap !== undefined && view.overlap !== null ? html`<tr><th scope="row">${t('method.layout.the_map')}</th>
          <td>${pinned ? paramsText(pinned.params) : '—'}</td><td class="cx-num">${pinned ? formatNumber(pinned.seed) : '—'}</td>
          <td class="cx-num">${pct(view.overlap)}</td></tr>` : null}
        ${previews.map((p, i) => html`<tr key=${i}><th scope="row">${t(`settings.layout.method.${p.method}`)}</th>
          <td>${paramsText(p.params)}</td><td class="cx-num">${formatNumber(p.seed)}</td>
          <td class="cx-num">${pct(p.overlap)}</td></tr>`)}
      </tbody></table>
      <p class="cx-settings__muted">${t('method.layout.scores_note')}</p>` : null}`;
}
