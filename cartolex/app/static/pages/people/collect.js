// SPDX-License-Identifier: MIT
/**
 * Collecting: choose what to collect, read what leaves the computer, then
 * start. The plan comes before anything is sent, and says how much of the
 * notice to show (`notice.js`): none (nothing personal is sent, within the
 * free daily budget: the collection starts at once, said in its toast), brief
 * (a kind already acknowledged: one sentence, « Details ») or full (each host
 * with what it is for, what it receives, about how many requests; what never
 * leaves; what is kept, and where; the consent and « Don't show this again »).
 * With an OpenAlex snapshot saved on this computer, the plan first offers how
 * OpenAlex is read: its API or the snapshot, each with its time, the faster
 * chosen (changing it plans again). An institution searched by its name is
 * planned at once, without the form. The job then runs in the background,
 * with its progress and a Stop button in the Activity drawer.
 */
import { html, useEffect, useRef, useState } from '../../core/preact.js';
import { formatBytes, formatDate, formatDuration, formatNumber, t } from '../../core/i18n.js';
import { useUid } from '../../core/dom.js';
import {
  Button, Checkbox, Dialog, ErrorCard, FormField, Input,
} from '../../components/index.js';
import { BriefNotice, FullReason, Notice, quickLine } from './notice.js';

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

/** The collect dialog, for *action* with its first *options*. */
export function CollectDialog({ ctx, action, options: initial, onClose, onStarted }) {
  const [options, setOptions] = useState({ rounds: 1, min_works: 2, ...initial });
  // How OpenAlex is read: null until chosen (the plan then offers the faster way).
  const [openalex, setOpenalex] = useState(initial && initial.openalex ? initial.openalex : null);
  const [plan, setPlan] = useState(null);
  const [consent, setConsent] = useState(false);
  const [remember, setRemember] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const startButton = useRef(null);
  // An institution searched by its name is planned at once (no form to fill): the dialog
  // shows only if the plan asks a notice or fails.
  const direct = action === 'institutions' && Boolean(initial && initial.search)
    && !(initial.institutions && initial.institutions.length);
  const [quiet, setQuiet] = useState(direct);

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

  async function start(thePlan = plan, agreed = consent) {
    setBusy(true);
    setError(null);
    const route = thePlan && thePlan.openalex ? thePlan.openalex.chosen : openalex;
    const level = thePlan && thePlan.notice ? thePlan.notice.level : 'full';
    const result = await ctx.api.post('/api/collection/start', {
      ...body(route), consent: level !== 'none' && agreed, remember: level === 'full' && remember });
    setBusy(false);
    if (!result.ok) {
      setQuiet(false);
      setError(result.error);
      return;
    }
    onStarted(result.data.job, level === 'none' ? quickLine(thePlan) : null);
    onClose();
  }

  async function review(route = openalex) {
    setBusy(true);
    setError(null);
    const result = await ctx.api.post('/api/collection/plan', body(route));
    setBusy(false);
    if (!result.ok) {
      setQuiet(false);
      setError(result.error);
      return;
    }
    const level = result.data.notice ? result.data.notice.level : 'full';
    // Nothing personal leaves the computer, within the free budget: no notice to read.
    if (level === 'none' && !result.data.openalex) {
      start(result.data, false);
      return;
    }
    setQuiet(false);
    setPlan(result.data);
    setRemember(false);
    setConsent(level === 'brief' || !result.data.consent_needed);
  }

  useEffect(() => {
    if (direct) review();
  }, []);

  // Another way of reading OpenAlex: planned again (what leaves the computer changes).
  const choose = (route) => {
    if (busy || !plan || !plan.openalex || plan.openalex.chosen === route) return;
    setOpenalex(route);
    review(route);
  };

  if (quiet) return null;
  const level = plan && plan.notice ? plan.notice.level : 'full';
  const footer = plan
    ? html`<${Button} onClick=${() => { setPlan(null); setOpenalex(null); }}>${t('corpus.collect.back')}<//>
      <${Button} variant="primary" buttonRef=${startButton} loading=${busy}
        disabled=${plan.consent_needed && !consent} onClick=${() => start()}>${t('corpus.collect.start')}<//>`
    : html`<${Button} onClick=${onClose}>${t('common.cancel')}<//>
      <${Button} variant="primary" loading=${busy} onClick=${() => review()}>
        ${t(action === 'institutions' ? 'corpus.collect.continue' : 'corpus.collect.review')}<//>`;
  return html`<${Dialog} open=${true} onClose=${onClose} size=${plan && level === 'brief' ? 'm' : 'l'}
    title=${t(`corpus.collect.action.${action}`)}
    description=${plan ? t(level === 'brief' ? 'corpus.notice.lead_brief' : 'corpus.notice.lead') : null} footer=${footer}>
    ${error ? html`<${ErrorCard} error=${error} compact onDismiss=${() => setError(null)} />` : null}
    ${plan ? html`${plan.openalex ? html`<${RouteChoice} route=${plan.openalex}
      value=${openalex || plan.openalex.chosen} onChoose=${choose} />` : null}
      ${level === 'brief' ? html`<${BriefNotice} plan=${plan} />` : html`<${FullReason} notice=${plan.notice} />
        <${Notice} plan=${plan} />`}
      ${plan.consent_needed && level === 'full' ? html`<div class="cx-corpus-consent">
        <${Checkbox} checked=${consent} label=${t('corpus.notice.consent')}
          onChange=${(e) => setConsent(e.currentTarget.checked)} />
        ${plan.notice && plan.notice.personal ? html`<${Checkbox} checked=${remember}
          label=${t('corpus.notice.remember', { action })}
          onChange=${(e) => setRemember(e.currentTarget.checked)} />
          <p class="cx-corpus-muted">${t('corpus.notice.remember_help')}</p>` : null}
      </div>` : null}`
      : html`<form class="cx-corpus-form" onSubmit=${(e) => { e.preventDefault(); review(); }}>
        <${Options} action=${action} options=${options} set=${setOptions} />
      </form>`}
  <//>`;
}
