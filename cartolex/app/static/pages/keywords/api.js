// SPDX-License-Identifier: MIT
/**
 * AI filtering by API: cartolex sends every keyword but those rejected
 * automatically to the provider set in the project, with the key saved in the settings. Before it
 * runs, the dialog says what leaves the computer, what never does, and an
 * estimate of the calls and tokens (an upper bound: answers already paid for
 * are reused); it runs only after the person consents, as a build job
 * followed in the Activity drawer.
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatDate, formatNumber, t } from '../../core/i18n.js';
import { useUid } from '../../core/dom.js';
import { Button, Checkbox, Dialog, ErrorCard, Icon } from '../../components/index.js';

/**
 * @param {object} props
 * @param {object} props.ctx
 * @param {Function} props.onClose
 * @param {(job: object) => void} props.onStarted
 */
export function ApiDialog({ ctx, onClose, onStarted }) {
  const [info, setInfo] = useState(null);
  const [error, setError] = useState(null);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const uid = useUid('cx-kw-api');
  useEffect(() => {
    ctx.api.get('/api/keywords/ai').then((r) => {
      if (r.ok) setInfo(r.data.api);
      else setError(r.error);
    });
  }, []);
  const start = async () => {
    setBusy(true);
    setError(null);
    const r = await ctx.api.post('/api/keywords/ai/run', { consent: true });
    setBusy(false);
    if (r.ok) onStarted(r.data.job);
    else setError(r.error);
  };
  const ready = info && info.ready;
  const e = info ? info.estimate : null;
  const footer = html`<${Button} variant="ghost" onClick=${onClose}>${t('common.cancel')}<//>
    ${info && !ready ? html`<${Button} variant="primary" icon="settings"
      onClick=${() => ctx.navigate('/settings?section=ai')}>${t('keywords.api.open_settings')}<//>`
      : html`<${Button} variant="primary" loading=${busy} disabled=${!ready || !consent}
        onClick=${start}>${t('keywords.api.start')}<//>`}`;
  return html`<${Dialog} open=${true} onClose=${onClose} size="m" title=${t('keywords.api.title')}
    description=${t('keywords.api.description')} footer=${footer}>
    ${!info && !error ? html`<p aria-busy="true">${t('common.loading')}</p>` : null}
    ${info ? html`<div class="cx-kw-api">
      <p class="cx-kw-api__state">${ready
        ? html`<${Icon} name="check" /> ${t('keywords.api.ready', { provider: info.provider, model: info.model })}`
        : html`<span class="cx-kw-warning"><${Icon} name="warning" /> <strong>${t('keywords.warning.word')}</strong>
          ${' '}${!info.provider ? t('keywords.api.no_provider') : t('keywords.api.no_key')}</span>`}</p>
      <div class="cx-handoff__columns">
        <section class="cx-handoff__box" aria-labelledby=${`${uid}-sends`}>
          <h3 id=${`${uid}-sends`} class="cx-handoff__box-title"><${Icon} name="check" />${t('keywords.api.sends')}</h3>
          <ul class="cx-handoff__list">
            <li>${t('keywords.api.sends.terms', { n: e.terms })}</li>
            ${e.answered ? html`<li>${t('keywords.api.sends.answered', { n: e.answered, new: e.new })}</li>` : null}
            <li>${t('keywords.api.sends.field')}</li>
          </ul>
        </section>
        <section class="cx-handoff__box" aria-labelledby=${`${uid}-never`}>
          <h3 id=${`${uid}-never`} class="cx-handoff__box-title"><${Icon} name="cross" />${t('copilot.never')}</h3>
          <ul class="cx-handoff__list">
            <li>${t('keywords.api.never.texts')}</li>
            <li>${t('copilot.never.people')}</li>
          </ul>
        </section>
      </div>
      <dl class="cx-kw-estimate" aria-label=${t('keywords.api.estimate')}>
        <div><dt>${t('keywords.api.new_terms')}</dt><dd>${formatNumber(e.new)}</dd></div>
        <div><dt>${t('keywords.api.calls')}</dt><dd>${formatNumber(e.calls)}</dd></div>
        <div><dt>${t('keywords.api.tokens_in')}</dt><dd>${formatNumber(e.tokens_in)}</dd></div>
        <div><dt>${t('keywords.api.tokens_out')}</dt><dd>${formatNumber(e.tokens_out)}</dd></div>
      </dl>
      <p class="cx-corpus-muted">${t('keywords.api.upper_bound')}</p>
      ${e.rejected ? html`<p class="cx-corpus-muted">${t('keywords.api.rejected', { n: e.rejected })}</p>` : null}
      ${info.last ? html`<p class="cx-corpus-muted">${t('keywords.api.last', {
        at: formatDate(info.last.at, 'datetime', 'short'), accepted: info.last.accepted || 0,
        rejected: info.last.rejected || 0 })}</p>` : null}
      ${ready ? html`<${Checkbox} checked=${consent} onChange=${(ev) => setConsent(ev.currentTarget.checked)}
        label=${t('keywords.api.consent', { provider: info.provider })} />` : null}
    </div>` : null}
    ${error ? html`<${ErrorCard} error=${error} compact />` : null}
  <//>`;
}
