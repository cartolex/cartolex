// SPDX-License-Identifier: MIT
/**
 * The pre-flight sheet: what the build will run and why, what it keeps and
 * skips, its cost (time, memory, AI calls), the consent a stage asks for, and
 * a refusal when a stage needs more memory than the machine has. Nothing runs
 * before the person presses « Build ».
 */
import { html, useState } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import {
  Button, Card, Checkbox, EmptyState, ErrorCard, Icon, StageTracker, reasonText,
} from '../../components/index.js';
import { formatMb, formatSeconds, nameOf } from './words.js';

/** Why a stage runs, in the interface language (the project state's reasons have codes). */
function why(item, known) {
  const out = [];
  if (item.state === 'never_built') out.push(t('build.why.never_built'));
  if (item.state === 'failed') out.push(t('build.why.failed'));
  for (const r of (known && known.reasons) || []) out.push(reasonText(r));
  if (!out.length) out.push(t('build.why.upstream'));
  return out;
}

/** The plan's items as tracker rows: the action is the state's word, the cost its note. */
export function planRows(plan, stages) {
  return plan.items.map((item) => {
    const known = stages.get(item.stage);
    const base = { id: item.stage, name: item.name };
    if (item.action === 'keep') return { ...base, state: 'up_to_date', stateText: t('build.action.keep') };
    if (item.action === 'skip') {
      return { ...base, state: 'skipped', stateText: t('build.action.skip'),
        note: known && known.skip ? known.skip.message : (item.reasons || [])[0] || '' };
    }
    const e = item.estimate || {};
    const cost = t('build.cost', { time: formatSeconds(e.seconds), memory: formatMb(e.peak_memory_mb) });
    if (item.blocked) {
      const note = item.over_budget && !item.refusal
        ? t('build.too_large', { need: formatMb(e.peak_memory_mb), have: formatMb(plan.budget_mb) })
        : t('build.cannot_run', { detail: item.blocked });
      return { ...base, state: 'failed', stateText: t('build.action.blocked'), note };
    }
    return { ...base, state: item.state === 'failed' ? 'failed' : 'needs_update',
      stateText: t('build.action.run'), note: [...why(item, known), cost].join(' · ') };
  });
}

function Consent({ request, checked, onChange }) {
  const e = request.estimate || {};
  return html`<div class="cx-build-consent" data-consent=${request.stage}>
    <${Checkbox} checked=${checked} onChange=${(ev) => onChange(ev.currentTarget.checked)}
      label=${t('build.consent.allow', { stage: nameOf(request.stage) })} />
    <ul class="cx-build-consent__facts">
      ${request.paid ? html`<li>${t('build.consent.ai')}</li>` : null}
      ${request.network && !request.paid ? html`<li>${t('build.consent.network')}</li>` : null}
      ${request.paid ? html`<li>${request.ai_calls_max
        ? t('build.consent.calls', { n: request.ai_calls_max }) : t('build.consent.calls_unknown')}</li>` : null}
      <li>${t('build.consent.time', { time: formatSeconds(e.seconds) })}</li>
      <li>${t('build.consent.without')}</li>
    </ul>
  </div>`;
}

/**
 * @param {{plan: object, stages: Map, starting: boolean, error: object|null,
 *   onStart: Function, onClose: Function}} props
 */
export function Preflight({ plan, stages, starting, error, onStart, onClose }) {
  const [consent, setConsent] = useState(() => new Set());
  const [override, setOverride] = useState(false);
  const toRun = plan.to_run || [];
  const large = plan.items.filter((i) => i.action === 'run' && i.over_budget && !i.refusal);
  const refused = plan.items.filter((i) => i.action === 'run' && i.refusal);
  const est = plan.estimate || {};
  if (!toRun.length && !large.length) {
    return html`<${Card} level=${2} title=${t('build.pre.title')}>
      <${EmptyState} icon="check" title=${t('build.pre.nothing')}
        action=${{ label: t('build.back'), onClick: onClose }}>${t('build.pre.nothing.text')}<//>
    <//>`;
  }
  const runs = toRun.length + (override ? large.length : 0);
  const toggle = (id, on) => {
    const next = new Set(consent);
    if (on) next.add(id); else next.delete(id);
    setConsent(next);
  };
  return html`<div class="cx-build-pre">
    <${Card} level=${2} title=${t('build.pre.title')} class="cx-build-pre__plan">
      <p class="cx-build-pre__lead">${t('build.pre.lead', {
        run: toRun.length, keep: (plan.to_keep || []).length, skip: (plan.to_skip || []).length })}</p>
      <dl class="cx-build-cost">
        <div><dt>${t('build.pre.time')}</dt><dd>${formatSeconds(est.seconds)}</dd></div>
        <div><dt>${t('build.pre.memory')}</dt><dd>${formatMb(est.peak_memory_mb)}</dd></div>
        <div><dt>${t('build.pre.machine')}</dt><dd>${formatMb(plan.budget_mb)}</dd></div>
        <div><dt>${t('build.pre.ai')}</dt><dd>${plan.consent.some((c) => c.paid)
          ? t('build.pre.ai_asks') : t('build.pre.ai_none')}</dd></div>
      </dl>
      <${StageTracker} stages=${planRows(plan, stages)} label=${t('build.pre.stages')} />
    <//>
    ${large.length ? html`<div class="cx-build-refusal" role="note">
      <${Icon} name="warning" /><div>
        <p><strong>${t('build.refusal.title')}</strong>${' '}${t('build.refusal.text', {
          n: large.length, stages: large.map((i) => nameOf(i.stage)).join(', '), have: formatMb(plan.budget_mb) })}</p>
        <${Checkbox} checked=${override} onChange=${(ev) => setOverride(ev.currentTarget.checked)}
          label=${t('build.refusal.override')} />
      </div>
    </div>` : null}
    ${refused.length ? html`<p class="cx-build-note">${t('build.refused', {
      n: refused.length, stages: refused.map((i) => nameOf(i.stage)).join(', ') })}</p>` : null}
    ${plan.consent.length ? html`<${Card} level=${2} title=${t('build.consent.title')}>
      ${plan.consent.map((c) => html`<${Consent} key=${c.stage} request=${c} checked=${consent.has(c.stage)}
        onChange=${(on) => toggle(c.stage, on)} />`)}
    <//>` : null}
    ${error ? html`<${ErrorCard} error=${error} live compact />` : null}
    <div class="cx-build-actions">
      <${Button} variant="primary" loading=${starting} disabled=${!runs}
        onClick=${() => onStart({ consent: [...consent], allowOverBudget: override })}>
        ${t('build.start', { n: runs })}<//>
      <${Button} variant="ghost" onClick=${onClose}>${t('common.cancel')}<//>
    </div>
  </div>`;
}
