/**
 * Privacy: what leaves this computer and what never does, in plain words; the
 * collection notices acknowledged (« Don't show this again »), and the reset
 * that shows each of them in full again. It reads `GET /api/me/notices`.
 */

import { html, useState } from '../../core/preact.js';
import { formatDate, t } from '../../core/i18n.js';
import { Button, Card } from '../../components/index.js';
import { Block, refusal, useResource } from './common.js';

const ITEMS = ['ai_api', 'ai_copilot', 'collection', 'keys', 'project', 'diagnostic'];

/** The notices acknowledged on this computer (per person when hosted), and their reset. */
function Notices({ ctx, app }) {
  const notices = useResource(ctx.api, '/api/me/notices');
  const [busy, setBusy] = useState(false);
  const kinds = (notices.data && notices.data.acknowledged) || [];
  const reset = async () => {
    setBusy(true);
    const result = await ctx.api.delete('/api/me/notices');
    setBusy(false);
    if (!result.ok) {
      app.toaster.show({ kind: 'error', title: t('settings.notices.reset_failed'),
        message: refusal(result.error) });
      return;
    }
    notices.set(result.data);
    app.toaster.show({ kind: 'success', title: t('settings.notices.reset_done') });
  };
  return html`<${Block} title=${t('settings.notices.title')} resource=${notices} class="cx-settings__wide">
    <p class="cx-settings__note">${t('settings.notices.lead')}</p>
    <ul class="cx-settings__list">
      ${['none', 'brief', 'full'].map((level) => html`<li key=${level}>${t(`settings.notices.level.${level}`)}</li>`)}
    </ul>
    ${kinds.length ? html`<p class="cx-settings__note">${t('settings.notices.acknowledged')}</p>
      <ul class="cx-settings__rows">${kinds.map((k) => html`<li key=${k.kind}>
        <span class="cx-settings__row-name">${t(`corpus.collect.action.${k.kind}`)}</span>
        ${k.at ? html`<span class="cx-settings__muted">${t('settings.notices.since', { date: formatDate(k.at, 'date', 'medium') })}</span>` : null}
      </li>`)}</ul>
      <${Button} variant="secondary" loading=${busy} onClick=${reset}>${t('settings.notices.reset')}<//>`
      : html`<p class="cx-settings__muted">${t('settings.notices.none')}</p>`}
  <//>`;
}

export function PrivacySection({ ctx, app }) {
  return html`<div class="cx-settings__grid">
    <${Card} title=${t('settings.privacy.title')} level=${3} class="cx-settings__wide">
      <dl class="cx-settings__facts">
        ${ITEMS.map((id) => html`<div key=${id}><dt>${t(`settings.privacy.${id}`)}</dt>
          <dd>${t(`settings.privacy.${id}.text`)}</dd></div>`)}
      </dl>
    <//>
    <${Notices} ctx=${ctx} app=${app} />
  </div>`;
}
