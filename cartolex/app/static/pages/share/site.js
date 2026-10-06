// SPDX-License-Identifier: MIT
/**
 * Building the offline site: its options, what it will hold (the privacy
 * summary), the checks before publishing, then the build as a job.
 *
 * A people atlas asks at each build whether the site shows names or
 * pseudonyms: neither is chosen until the person chooses, and the build
 * waits for the answer. Texts are left out unless asked (titles, or titles
 * and abstracts; never a full text). The summary and the checks are read
 * again when an option changes.
 */
import { html, useEffect, useRef, useState } from '../../core/preact.js';
import { formatBytes, formatList, formatNumber, locale, t } from '../../core/i18n.js';
import { runtime } from '../../core/runtime.js';
import { Button, Card, ErrorCard, FormField, Icon, Input, ProgressBar, Select } from '../../components/index.js';

const LEVEL_ICON = { blocker: 'error', question: 'help', warning: 'warning', info: 'info' };
const LANGUAGES = ['en', 'fr', 'pt-BR'];

/** A group of radio buttons (native inputs: arrows move, Space chooses). */
function Choice({ name, label, value, options, onChange, help }) {
  return html`<fieldset class="cx-share-choice" id=${`cx-share-${name}`}>
    <legend class="cx-share-choice__legend">${label}</legend>
    ${help ? html`<p class="cx-share__note">${help}</p>` : null}
    ${options.map((o) => html`<label key=${o.value} class="cx-share-choice__option">
      <input type="radio" name=${`cx-share-${name}`} value=${o.value} checked=${value === o.value}
        onChange=${() => onChange(o.value)} />
      <span><span class="cx-share-choice__label">${o.label}</span>
        ${o.help ? html`<span class="cx-share-choice__help">${o.help}</span>` : null}</span>
    </label>`)}
  </fieldset>`;
}

/** The words of a check. */
/** A texts option, with what it adds to the site when the plan says (« about 1.3 GB »). */
function textsLabel(value, plan) {
  const bytes = plan && plan.summary.text_bytes ? plan.summary.text_bytes[value] : 0;
  const label = t(`share.texts.${value}`);
  return bytes ? `${label} (${t('share.texts.about', { size: formatBytes(bytes) })})` : label;
}

function checkText(check) {
  const p = check.params || {};
  const params = { ...p, examples: formatList((p.examples || []).map(String)), stages: formatList(p.stages || []) };
  if (p.size !== undefined) params.size = formatBytes(p.size);
  if (p.titles !== undefined) params.titles = formatBytes(p.titles);
  return t(`share.check.${check.code}`, params);
}

/** What the site will hold and never hold. */
function Summary({ summary, options }) {
  const s = summary;
  const people = s.people;
  return html`<ul class="cx-share-summary" aria-label=${t('share.summary')}>
    ${people ? html`<li><${Icon} name=${options.names ? 'warning' : options.names === false ? 'check' : 'help'} />
      <span>${t(options.names === null ? 'share.summary.people_unanswered'
        : options.names ? 'share.summary.people_names' : 'share.summary.people_pseudonyms', { n: people })}</span></li>` : null}
    ${s.projected ? html`<li><${Icon} name=${options.namesProjected ? 'warning' : 'check'} />
      <span>${t(options.namesProjected ? 'share.summary.projected_names' : 'share.summary.projected_pseudonyms',
        { n: s.projected })}</span></li>` : null}
    <li><${Icon} name="check" /><span>${t('share.summary.orgs', { n: s.organisations })}</span></li>
    <li><${Icon} name="check" /><span>${t('share.summary.keywords', { keywords: s.keywords, themes: s.themes })}</span></li>
    <li><${Icon} name=${options.texts === 'abstracts' ? 'warning' : 'check'} /><span>${t(`share.summary.texts.${options.texts}`)}</span></li>
    <li><${Icon} name="cross" /><span>${t('share.summary.never', { n: s.full_texts })}</span></li>
  </ul>`;
}

function Checks({ checks, onFix }) {
  if (!checks.length) return html`<p class="cx-share__note">${t('share.checks.none')}</p>`;
  return html`<ul class="cx-share-checks" aria-label=${t('share.checks')}>
    ${checks.map((c) => html`<li key=${c.code} class=${`cx-share-check cx-share-check--${c.level}`}>
      <${Icon} name=${LEVEL_ICON[c.level] || 'info'} />
      <span class="cx-share-check__body"><span class="cx-share-check__level">${t(`share.level.${c.level}`)}</span>
        ${' '}${checkText(c)}</span>
      ${c.fix ? html`<${Button} size="s" variant="ghost" onClick=${() => onFix(c.fix)}>
        ${t(`share.fix.${c.fix.action.startsWith('open:') ? 'open' : c.fix.action}`)}<//>` : null}
    </li>`)}
  </ul>`;
}

export function SiteCard({ ctx, available, job, running, onStarted }) {
  const [options, setOptions] = useState({ names: null, namesProjected: false, texts: 'none', title: '',
    language: LANGUAGES.includes(locale.value) ? locale.value : 'en' });
  const [plan, setPlan] = useState(null);
  const [planError, setPlanError] = useState(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState(null);
  const timer = useRef(0);

  const readPlan = async (o) => {
    const query = { texts: o.texts, language: o.language };
    if (o.names !== null) query.names = o.names ? 'names' : 'pseudonyms';
    query.names_projected = o.namesProjected ? 'names' : 'pseudonyms';
    if (o.title) query.title = o.title;
    const r = await ctx.api.get('/api/share/plan', { query });
    if (r.ok) {
      setPlan(r.data);
      setPlanError(null);
    } else setPlanError(r.error);
  };
  useEffect(() => {
    // The first read at once; later ones when the options have stopped changing.
    if (!plan && !planError) readPlan(options);
    else {
      clearTimeout(timer.current);
      timer.current = setTimeout(() => readPlan(options), 300);
    }
    return () => clearTimeout(timer.current);
  }, [options]);

  const set = (patch) => setOptions((o) => ({ ...o, ...patch }));
  const fix = (f) => {
    if (f.action === 'build') runtime.navigate(`/build?scope=${(f.scope || ['map']).join(',')}`);
    else if (f.action.startsWith('open:')) runtime.navigate(f.action.slice(5));
    else if (f.field) {
      const el = document.querySelector(`#cx-share-${f.field} input, #cx-share-${f.field}`);
      if (el) el.focus();
    }
  };
  const build = async () => {
    setStarting(true);
    setStartError(null);
    const r = onStarted(await ctx.api.post('/api/share/builds', {
      names: options.names === null ? null : options.names ? 'names' : 'pseudonyms',
      names_projected: options.namesProjected ? 'names' : 'pseudonyms',
      texts: options.texts, title: options.title, language: options.language }));
    setStarting(false);
    if (!r.ok) setStartError(r.error);
  };

  const people = plan && plan.summary.people > 0;
  const projected = plan && plan.summary.projected > 0;
  const progress = job && job.progress ? job.progress.fraction : null;
  return html`<${Card} level=${2} title=${t('share.site.title')} class="cx-share__site">
    <p class="cx-share__note">${t('share.site.text')}</p>
    <div class="cx-share-form">
      <${FormField} label=${t('share.site.field_title')} help=${t('share.site.field_title.help')}>
        ${(f) => html`<${Input} ...${f} id="cx-share-title" value=${options.title} maxlength="120"
          placeholder=${plan ? plan.summary.title : ''} onInput=${(e) => set({ title: e.currentTarget.value })} />`}
      <//>
      <${FormField} label=${t('share.site.language')}>
        ${(f) => html`<${Select} ...${f} value=${options.language}
          options=${LANGUAGES.map((code) => ({ value: code, label: t(`share.language.${code}`) }))}
          onChange=${(e) => set({ language: e.currentTarget.value })} />`}
      <//>
    </div>
    ${people || !plan ? html`<${Choice} name="names" label=${t('share.names')} help=${t('share.names.help')}
      value=${options.names === null ? '' : options.names ? 'names' : 'pseudonyms'}
      options=${[{ value: 'pseudonyms', label: t('share.names.pseudonyms'), help: t('share.names.pseudonyms.help') },
        { value: 'names', label: t('share.names.names'), help: t('share.names.names.help') }]}
      onChange=${(v) => set({ names: v === 'names' })} />` : null}
    ${projected ? html`<${Choice} name="names_projected" label=${t('share.names_projected')}
      help=${t('share.names_projected.help')} value=${options.namesProjected ? 'names' : 'pseudonyms'}
      options=${[{ value: 'pseudonyms', label: t('share.names.pseudonyms') },
        { value: 'names', label: t('share.names.names') }]}
      onChange=${(v) => set({ namesProjected: v === 'names' })} />` : null}
    <${Choice} name="texts" label=${t('share.texts')} value=${options.texts}
      options=${['none', 'titles', 'abstracts'].map((v) => ({ value: v, label: textsLabel(v, plan) }))}
      onChange=${(v) => set({ texts: v })} />
    <h3 class="cx-share__head">${t('share.summary')}</h3>
    ${planError ? html`<${ErrorCard} error=${planError} compact onRetry=${() => readPlan(options)} />`
      : plan ? html`<${Summary} summary=${plan.summary} options=${options} />
        <h3 class="cx-share__head">${t('share.checks')}</h3>
        <${Checks} checks=${plan.checks} onFix=${fix} />`
      : html`<p class="cx-share__note" aria-busy="true">${t('common.loading')}</p>`}
    ${startError ? html`<${ErrorCard} error=${startError} compact onAction=${() => fix({ action: 'fix-input', field: 'names' })} />` : null}
    ${job && running ? html`<${ProgressBar} value=${progress} label=${t('share.site.progress')} showValue />` : null}
    ${job && job.state === 'succeeded' && job.result ? html`<p class="cx-share__done" role="status">
      <${Icon} name="check" /> ${t('share.site.built', { id: job.result.id || '', size: formatNumber((job.result.size || 0) / 1e6, { maximumFractionDigits: 1 }) })}</p>` : null}
    <div class="cx-share__actions">
      <${Button} variant="primary" loading=${starting || (running && Boolean(job))}
        disabled=${!available || running || !plan || !plan.ready} onClick=${build}>${t('share.site.build')}<//>
      ${plan && !plan.ready ? html`<span class="cx-share__note">${t('share.site.waiting')}</span>` : null}
    </div>
  <//>`;
}
