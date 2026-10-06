// SPDX-License-Identifier: MIT
/**
 * What leaves the computer before a collection, at the level the server asks
 * (`plan.notice.level`, see `cartolex/app/notices.py`):
 *
 * - `none`: nothing personal is sent and the work fits OpenAlex's free daily
 *   budget: no dialog, one line (`quickLine`) in the toast of the job;
 * - `brief`: the notice of this kind was acknowledged with the same content:
 *   one sentence, the whole notice under « Details »;
 * - `full`: the whole notice (`Notice`), the consent and « Don't show this
 *   again ».
 *
 * OpenAlex's cost shows only beyond its free daily budget (`BudgetCallout`):
 * a free key or days without one, days with a key, and the snapshot above
 * about ten euros.
 */
import { html } from '../../core/preact.js';
import { formatDuration, formatList, formatNumber, t } from '../../core/i18n.js';
import { runtime } from '../../core/runtime.js';
import { Button, Icon } from '../../components/index.js';
import { coded } from './common.js';

/** A sum in US dollars, two decimals, else « less than $0.01 ». */
export function usd(value) {
  if (!(value >= 0.005)) return t('corpus.notice.under_a_cent');
  return formatNumber(value, { style: 'currency', currency: 'USD', minimumFractionDigits: 2 });
}

/** Whether OpenAlex's cost is worth showing: beyond the free daily budget only. */
const beyond = (plan) => Boolean(plan.budget && !plan.budget.fits);

/** The notes the budget callout says better (the budget's own) are left out of the list. */
const shownNotes = (plan) => plan.notes.filter((n) => !(plan.budget && n.code.startsWith('note_openalex_')));

/** The services a plan reaches, by name. */
const servicesOf = (plan) => formatList(plan.leaves_the_computer.map((h) => h.label));

/** The one line of a collection that needs no notice (« OpenAlex: about 1 request… »). */
export function quickLine(plan) {
  const n = plan.estimate ? plan.estimate.requests : 0;
  return plan.leaves_the_computer.length
    ? t('corpus.notice.quick', { services: servicesOf(plan), n })
    : t('corpus.notice.quick_nothing');
}

/** OpenAlex's cost beyond its free daily budget: what it takes, and what to do. */
export function BudgetCallout({ plan }) {
  const b = plan.budget;
  if (!b || (b.fits && !b.snapshot_advised)) return null;
  const settings = () => runtime.navigate('/settings?section=sources');
  return html`<div class="cx-corpus-budget" role="note">
    <${Icon} name="warning" />
    <div>
      ${b.state === 'needs_key' ? html`<p>${t('corpus.budget.needs_key', {
        daily: usd(b.daily_usd), days: b.days })}${b.days_with_key > 1
        ? ` ${t('corpus.budget.with_key', { days: b.days_with_key })}` : ''}</p>` : null}
      ${b.state === 'days' ? html`<p>${t('corpus.budget.days', { days: b.days })}</p>` : null}
      ${b.snapshot_advised ? html`<p>${t('corpus.budget.snapshot', { cost: usd(b.cost_usd) })}</p>` : null}
      ${b.state === 'needs_key' || b.snapshot_advised ? html`<${Button} size="s" variant="secondary"
        onClick=${settings}>${t(b.state === 'needs_key' ? 'corpus.budget.set_key' : 'corpus.budget.open_sources')}<//>`
        : null}
    </div>
  </div>`;
}

/** What leaves the computer, and what does not. */
export function Notice({ plan }) {
  const cost = beyond(plan) && plan.estimate ? plan.estimate.cost_usd : null;
  return html`<div class="cx-corpus-notice">
    <h3 class="cx-corpus-h3">${t('corpus.notice.title')}</h3>
    ${plan.people > 0 ? html`<p>${t('corpus.notice.people', { n: plan.people })}</p>` : null}
    ${plan.leaves_the_computer.length ? html`<table class="cx-corpus-simple cx-corpus-notice__hosts">
      <thead><tr>
        <th scope="col">${t('corpus.notice.where')}</th><th scope="col">${t('corpus.notice.why')}</th>
        <th scope="col">${t('corpus.notice.what')}</th><th scope="col">${t('corpus.notice.requests')}</th>
      </tr></thead>
      <tbody>${plan.leaves_the_computer.map((h) => html`<tr key=${h.service}>
        <td><strong>${h.label}</strong><br /><code>${h.host || t('corpus.notice.this_computer')}</code></td>
        <td>${coded('corpus.purpose', h.purpose)}</td>
        <td>${h.sends.map((s) => coded('corpus.sends', s)).join(', ')}</td>
        <td>${h.requests ? t('corpus.notice.about', { n: formatNumber(h.requests) }) : t('corpus.notice.depends')}
          ${h.requests && h.seconds >= 1 ? html`<br /><span class="cx-corpus-muted">${t('corpus.notice.host_time', { time: formatDuration(h.seconds) })}</span>` : null}
          ${h.cost_usd && beyond(plan) ? html`<br /><span class="cx-corpus-muted">${t('corpus.notice.cost', { usd: usd(h.cost_usd) })}</span>` : null}</td>
      </tr>`)}</tbody>
    </table>` : html`<p>${t('corpus.notice.nothing')}</p>`}
    <${BudgetCallout} plan=${plan} />
    ${shownNotes(plan).map((n, i) => html`<p key=${i} class="cx-corpus-note" role="note">${coded('corpus.note', n)}</p>`)}
    <h3 class="cx-corpus-h3">${t('corpus.notice.never')}</h3>
    <ul class="cx-corpus-list">${plan.never_leaves.map((n) => html`<li key=${n.code}>${coded('corpus.never', n)}</li>`)}</ul>
    ${plan.stored && plan.stored.length ? html`<details class="cx-corpus-part">
      <summary>${t('corpus.notice.stored')}</summary>
      <ul class="cx-corpus-list">${plan.stored.map((n) => html`<li key=${n.code}>${coded('corpus.stored', n)}</li>`)}</ul>
    </details>` : null}
    ${plan.estimate ? html`<p class="cx-corpus-muted">${t('corpus.notice.estimate', {
      time: formatDuration(Math.max(1, plan.estimate.seconds || 0)) })}${cost ? ` ${t('corpus.notice.total_cost', { usd: usd(cost) })}` : ''}</p>` : null}
  </div>`;
}

/** The notice of a kind already acknowledged: one sentence, the whole of it under « Details ». */
export function BriefNotice({ plan }) {
  const kinds = [...new Set(plan.leaves_the_computer.flatMap((h) => h.sends
    .filter((s) => s.code !== 'sends_contact' && s.code !== 'sends_api_key')
    .map((s) => coded('corpus.sends', s))))];
  return html`<div class="cx-corpus-brief">
    <p>${t('corpus.notice.brief', { services: servicesOf(plan), kinds: formatList(kinds),
      n: plan.estimate ? plan.estimate.requests : 0,
      time: formatDuration(Math.max(1, (plan.estimate && plan.estimate.seconds) || 0)) })}</p>
    ${shownNotes(plan).map((n, i) => html`<p key=${i} class="cx-corpus-note" role="note">${coded('corpus.note', n)}</p>`)}
    <details class="cx-corpus-part">
      <summary>${t('corpus.notice.details')}</summary>
      <${Notice} plan=${plan} />
    </details>
  </div>`;
}

/** Why the whole notice is shown again (its content changed, or beyond the free budget). */
export function FullReason({ notice }) {
  const reasons = (notice && notice.reasons) || [];
  const shown = reasons.filter((r) => r === 'changed' || r === 'over_budget');
  return shown.length ? html`<p class="cx-corpus-muted">${shown.map((r) => t(`corpus.notice.why_full.${r}`)).join(' ')}</p>` : null;
}
