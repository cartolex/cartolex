// SPDX-License-Identifier: MIT
/**
 * Collecting: choose what to collect, read what leaves the computer, then
 * start. The notice always comes before anything is sent: each host with what
 * it is for, what it receives, about how many requests and their cost; what
 * never leaves; what is kept, and where. With an OpenAlex snapshot saved on
 * this computer, the notice first offers how OpenAlex is read: its API or the
 * snapshot, each with its time, the faster chosen (changing it plans again).
 * The job then runs in the background, with its progress and a Stop button in
 * the Activity drawer.
 */
import { html, useRef, useState } from '../../core/preact.js';
import { formatBytes, formatDate, formatDuration, formatNumber, t } from '../../core/i18n.js';
import { useUid } from '../../core/dom.js';
import {
  Button, Checkbox, Dialog, ErrorCard, FormField, Input,
} from '../../components/index.js';
import { coded } from './common.js';

function numberOr(value) {
  const n = Number(value);
  return value === '' || value === null || value === undefined || Number.isNaN(n) ? null : n;
}

/** The options of one action. */
function Options({ action, options, set }) {
  const field = (key, label, help, attrs = {}) => html`<${FormField} label=${label} help=${help}>
    ${(f) => html`<${Input} ...${f} ...${attrs} value=${options[key] ?? ''}
      onInput=${(e) => set({ ...options, [key]: e.currentTarget.value })} />`}<//>`;
  const check = (key, label, fallback) => html`<${Checkbox} label=${label}
    checked=${options[key] ?? fallback}
    onChange=${(e) => set({ ...options, [key]: e.currentTarget.checked })} />`;
  const years = html`<div class="cx-corpus-two">
    ${field('first', t('corpus.collect.first_year'), t('corpus.collect.years_help'), { inputmode: 'numeric' })}
    ${field('last', t('corpus.collect.last_year'), null, { inputmode: 'numeric' })}</div>`;
  if (action === 'identify') {
    return html`<p>${t('corpus.collect.identify_lead')}</p>
      ${check('hal', t('corpus.collect.with_hal'), true)}
      ${field('scielo', t('corpus.collect.scielo'), t('corpus.collect.scielo_help'))}`;
  }
  if (action === 'harvest') {
    return html`<p>${t('corpus.collect.harvest_lead')}</p>${years}
      ${check('hal', t('corpus.collect.with_hal'), true)}
      ${check('abstracts', t('corpus.collect.abstracts'), false)}`;
  }
  if (action === 'institutions') {
    return options.institutions && options.institutions.length
      ? html`<p>${t('corpus.collect.institutions_lead', { n: options.institutions.length })}</p>${years}
        ${field('min_works', t('corpus.collect.min_works'), null, { inputmode: 'numeric' })}`
      : html`<p>${t('corpus.collect.search_lead')}</p>
        ${field('search', t('corpus.institutions.search'), null)}`;
  }
  if (action === 'collaborators') {
    return html`<p>${t('corpus.collect.collab_lead')}</p>
      <div class="cx-corpus-two">
        ${field('rounds', t('corpus.collect.rounds'), null, { inputmode: 'numeric' })}
        ${field('cap', t('corpus.collect.cap'), t('corpus.collect.cap_help'), { inputmode: 'numeric' })}
      </div>
      ${field('max_authors', t('corpus.collect.max_authors'), t('corpus.collect.max_authors_help'), { inputmode: 'numeric' })}`;
  }
  return html`<p>${t('corpus.collect.retry_lead')}</p>`;
}

/**
 * How OpenAlex is read: its API or the snapshot of this computer, each with its time;
 * *value* is the way chosen (at once, before the plan made with it comes back).
 */
function RouteChoice({ route, value, onChoose }) {
  const uid = useUid('cx-corpus-route');
  const { api, snapshot } = route;
  const ready = snapshot.state === 'ready';
  const apiTime = t(api.at_least ? 'corpus.route.api_at_least' : 'corpus.route.api_time', {
    n: formatNumber(api.requests), time: formatDuration(Math.max(1, api.seconds || 0)) });
  const options = [
    { id: 'api', label: t('corpus.route.api'), disabled: false,
      help: api.days ? `${apiTime} ${t('corpus.route.api_days', { days: api.days })}` : apiTime },
    { id: 'snapshot', label: t('corpus.route.snapshot', { release: formatDate(snapshot.release) }),
      disabled: !ready,
      help: ready ? t(snapshot.indexed ? 'corpus.route.snapshot_indexed'
        : snapshot.rate_measured ? 'corpus.route.snapshot_time' : 'corpus.route.snapshot_guess', {
        size: formatBytes(snapshot.bytes), time: formatDuration(Math.max(1, snapshot.seconds || 0)) })
        : t(`corpus.route.snapshot_${snapshot.state}`) },
  ];
  // Never disabled while chosen: the focus stays on the radio button.
  return html`<fieldset class="cx-corpus-route">
    <legend class="cx-corpus-route__legend">${t('corpus.route.legend')}</legend>
    ${options.map((o) => html`<label key=${o.id}
      class=${`cx-corpus-route__option ${o.disabled ? 'is-disabled' : ''}`}>
      <input type="radio" name=${uid} value=${o.id} checked=${value === o.id}
        disabled=${o.disabled && value !== o.id} onChange=${() => onChoose(o.id)} />
      <span><span class="cx-corpus-route__label">${o.label}</span>
        ${route.preselected === o.id ? html` <span class="cx-corpus-muted">${t('corpus.route.faster')}</span>` : null}
        <span class="cx-corpus-route__help">${o.help}</span></span>
    </label>`)}
  </fieldset>`;
}

/** What leaves the computer, and what does not. */
export function Notice({ plan }) {
  const cost = plan.estimate && plan.estimate.cost_usd;
  return html`<div class="cx-corpus-notice">
    <h3 class="cx-corpus-h3">${t('corpus.notice.title')}</h3>
    <p>${t('corpus.notice.people', { n: plan.people })}</p>
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
          ${h.cost_usd ? html`<br /><span class="cx-corpus-muted">${t('corpus.notice.cost', { usd: h.cost_usd.toFixed(3) })}</span>` : null}</td>
      </tr>`)}</tbody>
    </table>` : html`<p>${t('corpus.notice.nothing')}</p>`}
    ${plan.notes.map((n, i) => html`<p key=${i} class="cx-corpus-note" role="note">${coded('corpus.note', n)}</p>`)}
    <h3 class="cx-corpus-h3">${t('corpus.notice.never')}</h3>
    <ul class="cx-corpus-list">${plan.never_leaves.map((n) => html`<li key=${n.code}>${coded('corpus.never', n)}</li>`)}</ul>
    ${plan.stored && plan.stored.length ? html`<details class="cx-corpus-part">
      <summary>${t('corpus.notice.stored')}</summary>
      <ul class="cx-corpus-list">${plan.stored.map((n) => html`<li key=${n.code}>${coded('corpus.stored', n)}</li>`)}</ul>
    </details>` : null}
    ${plan.estimate ? html`<p class="cx-corpus-muted">${t('corpus.notice.estimate', {
      time: formatDuration(Math.max(1, plan.estimate.seconds || 0)) })}${cost ? ` ${t('corpus.notice.total_cost', { usd: cost.toFixed(2) })}` : ''}</p>` : null}
  </div>`;
}

/** The collect dialog, for *action* with its first *options*. */
export function CollectDialog({ ctx, action, options: initial, onClose, onStarted }) {
  const [options, setOptions] = useState({ rounds: 1, min_works: 2, ...initial });
  // How OpenAlex is read: null until chosen (the plan then offers the faster way).
  const [openalex, setOpenalex] = useState(initial && initial.openalex ? initial.openalex : null);
  const [plan, setPlan] = useState(null);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const startButton = useRef(null);

  const body = (route = openalex) => {
    const out = { action };
    if (route) out.openalex = route;
    if (options.people) out.people = options.people;
    const first = numberOr(options.first);
    const last = numberOr(options.last);
    if (first !== null || last !== null) out.years = [first, last];
    if (options.hal !== undefined) out.hal = options.hal;
    if (options.abstracts) out.abstracts = true;
    if (options.scielo) out.scielo = String(options.scielo).trim().toLowerCase();
    if (action === 'institutions') {
      if (options.institutions && options.institutions.length) {
        out.institutions = options.institutions;
        out.min_works = numberOr(options.min_works) || 2;
      } else out.search = String(options.search || '').trim();
    }
    if (action === 'collaborators') {
      out.rounds = numberOr(options.rounds) || 1;
      if (numberOr(options.cap)) out.cap = numberOr(options.cap);
      if (numberOr(options.max_authors)) out.max_authors = numberOr(options.max_authors);
    }
    return out;
  };

  async function review(route = openalex) {
    setBusy(true);
    setError(null);
    const result = await ctx.api.post('/api/collection/plan', body(route));
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setPlan(result.data);
    setConsent(!result.data.consent_needed);
  }

  // Another way of reading OpenAlex: planned again (what leaves the computer changes).
  const choose = (route) => {
    if (busy || !plan || !plan.openalex || plan.openalex.chosen === route) return;
    setOpenalex(route);
    review(route);
  };

  async function start() {
    setBusy(true);
    setError(null);
    const route = plan && plan.openalex ? plan.openalex.chosen : openalex;
    const result = await ctx.api.post('/api/collection/start', { ...body(route), consent });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    onStarted(result.data.job);
    onClose();
  }

  const footer = plan
    ? html`<${Button} onClick=${() => { setPlan(null); setOpenalex(null); }}>${t('corpus.collect.back')}<//>
      <${Button} variant="primary" buttonRef=${startButton} loading=${busy}
        disabled=${plan.consent_needed && !consent} onClick=${start}>${t('corpus.collect.start')}<//>`
    : html`<${Button} onClick=${onClose}>${t('common.cancel')}<//>
      <${Button} variant="primary" loading=${busy} onClick=${() => review()}>${t('corpus.collect.review')}<//>`;
  return html`<${Dialog} open=${true} onClose=${onClose} size="l"
    title=${t(`corpus.collect.action.${action}`)}
    description=${plan ? t('corpus.notice.lead') : null} footer=${footer}>
    ${error ? html`<${ErrorCard} error=${error} compact onDismiss=${() => setError(null)} />` : null}
    ${plan ? html`${plan.openalex ? html`<${RouteChoice} route=${plan.openalex}
      value=${openalex || plan.openalex.chosen} onChoose=${choose} />` : null}
      <${Notice} plan=${plan} />
      ${plan.consent_needed ? html`<${Checkbox} class="cx-corpus-consent" checked=${consent}
        label=${t('corpus.notice.consent')} onChange=${(e) => setConsent(e.currentTarget.checked)} />` : null}`
      : html`<form class="cx-corpus-form" onSubmit=${(e) => { e.preventDefault(); review(); }}>
        <${Options} action=${action} options=${options} set=${setOptions} />
      </form>`}
  <//>`;
}

