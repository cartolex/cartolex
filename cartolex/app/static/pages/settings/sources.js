/**
 * Data sources: the bibliographic services texts are collected from (what
 * each is sent), the order in which text providers are preferred, and the
 * OpenAlex key of this computer (a free key raises OpenAlex's daily budget).
 */

import { html } from '../../core/preact.js';
import { formatList, formatNumber, t } from '../../core/i18n.js';
import { Block, State, useResource } from './common.js';
import { KeyField } from './ai.js';

function usd(value) {
  return formatNumber(value, { style: 'currency', currency: 'USD', minimumFractionDigits: 2 });
}

export function SourcesSection({ ctx, app, open }) {
  const machine = useResource(ctx.api, '/api/machine');
  const settings = useResource(ctx.api, open ? '/api/settings' : null);
  const params = useResource(ctx.api, open ? '/api/params' : null);
  const budget = machine.data && machine.data.openalex;
  const assemble = params.data && (params.data.stages || []).find((s) => s.id === 'corpus.assemble');
  const priority = assemble && assemble.params.find((p) => p.name === 'provider_priority');
  return html`<div class="cx-settings__grid">
    ${open ? html`<${Block} title=${t('settings.sources.services')} resource=${settings}>
      ${settings.data ? html`<ul class="cx-settings__rows">
        ${settings.data.data_sources.map((s) => html`<li key=${s.id}>
          <span class="cx-settings__row-name">${s.name}</span>
          ${s.enabled ? html`<${State} kind="ok">${t('settings.sources.enabled')}<//>`
            : html`<${State} kind="none">${t('settings.sources.disabled')}<//>`}
          ${s.sends && s.sends.length ? html`<span class="cx-settings__muted">
            ${t('settings.sources.sends', { items: formatList(s.sends) })}</span>` : null}
        </li>`)}
      </ul>` : null}
    <//>` : null}
    ${open ? html`<${Block} title=${t('settings.sources.providers')} resource=${params}>
      ${priority ? html`<p class="cx-settings__note">${t('settings.sources.providers_lead')}</p>
        <ol class="cx-settings__order">${(priority.value || []).map((p) => html`<li key=${p}><code>${p}</code></li>`)}</ol>
        <p class="cx-settings__muted">${t(`settings.origin.${priority.from}`)} · ${t('settings.sources.providers_edit')}</p>`
        : html`<p class="cx-settings__note">${t('settings.sources.providers_none')}</p>`}
    <//>` : null}
    <${Block} title=${t('settings.sources.openalex')} resource=${machine}>
      ${budget ? html`
        <p class="cx-settings__note">${t('settings.sources.openalex_budget', {
          without: usd(budget.without_key_usd), with: usd(budget.with_key_usd),
          lists: usd(budget.list_per_1000_usd), searches: usd(budget.search_per_1000_usd) })}</p>
        <p class="cx-settings__note">${t('settings.sources.openalex_get')}
          <a href=${budget.get_key} rel="noreferrer" target="_blank"> <code>${budget.get_key}</code></a></p>
        <${KeyField} ctx=${ctx} app=${app} machine=${machine} service="openalex"
          label=${t('settings.sources.openalex_key')} help=${t('settings.sources.openalex_key_help')} />` : null}
    <//>
  </div>`;
}
