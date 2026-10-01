// SPDX-License-Identifier: MIT
/**
 * The playground's controls, along the top: the levels (1 to 4), the keywords per topic and
 * the top themes — or the size of each level by hand —, the comb (on or off) and its θ
 * (« calibrated » when it is the rule's), the space (people or texts). Each is the shared
 * ParamControl of its parameter (`GET /api/params`); the values start from the project's.
 */

import { html } from '../../../core/preact.js';
import { formatNumber, t } from '../../../core/i18n.js';
import { useUid } from '../../../core/dom.js';
import { ParamControl, ProgressBar } from '../../../components/index.js';
import { paramLabel } from '../../tune/params.js';

/** The settings of the playground, per stage. */
export const SETTINGS = {
  'themes.space': ['space_unit'],
  'themes.group': ['depth', 'top_groups', 'keywords_per_group', 'level_sizes', 'comb', 'comb_theta'],
};

/** A parameter of `GET /api/params`: `{stage, p}`, or null. */
export function paramOf(data, stage, name) {
  const s = data && (data.stages || []).find((x) => x.id === stage);
  const p = s && s.params.find((x) => x.name === name);
  return p || null;
}

/** The controls' values from the parameters read: `{<stage>.<name>: value}`. */
export function valuesOf(data) {
  const out = {};
  for (const [stage, names] of Object.entries(SETTINGS)) {
    for (const name of names) {
      const p = paramOf(data, stage, name);
      if (p) out[`${stage}.${name}`] = p.value;
    }
  }
  return out;
}

/** The body's settings of the controls' values (`null`: back to the default or the rule). */
export function settingsOf(values) {
  const out = {};
  for (const [stage, names] of Object.entries(SETTINGS)) {
    out[stage] = {};
    for (const name of names) {
      const key = `${stage}.${name}`;
      if (key in values) out[stage][name] = values[key] === undefined ? null : values[key];
    }
  }
  return out;
}

function Control({ label, children, labelId }) {
  return html`<div class="cx-pg-control" role="group" aria-labelledby=${labelId}>
    <span class="cx-pg-control__label" id=${labelId}>${label}</span>${children}</div>`;
}

/**
 * The controls. *values* and *onChange(key, value, invalid)*; *theta*: the θ the last preview's
 * comb kept; *sizes*: its groups per level (the start of « by hand »).
 */
export function Controls({ data, values, onChange, theta, sizes, state }) {
  const uid = useUid('cx-pg');
  const one = (stage, name, extra = {}, { disabled = false, label = null } = {}) => {
    const p = paramOf(data, stage, name);
    if (!p) return null;
    const key = `${stage}.${name}`;
    const id = `${uid}-${name}`;
    const words = label || paramLabel(stage, name);
    return html`<${Control} label=${words} labelId=${`${id}-label`}>
      <${ParamControl} p=${{ ...p, ...extra }} id=${id} labelId=${`${id}-label`} label=${words}
        value=${values[key]} disabled=${disabled} onChange=${(v, bad) => onChange(key, v, bad)} /><//>`;
  };
  const byHand = Array.isArray(values['themes.group.level_sizes']);
  const comb = Boolean(values['themes.group.comb']);
  const calibrated = values['themes.group.comb_theta'] === null || values['themes.group.comb_theta'] === undefined;
  const thetaId = `${uid}-theta`;
  return html`<div class="cx-pg-controls" role="group" aria-label=${t('playground.controls')}>
    ${one('themes.group', 'depth', { widget: 'choice', choices: [1, 2, 3, 4] },
      { disabled: byHand, label: t('playground.levels') })}
    ${one('themes.group', 'keywords_per_group', {}, { disabled: byHand, label: t('playground.per_topic') })}
    ${one('themes.group', 'top_groups', {}, { disabled: byHand, label: t('playground.top') })}
    ${one('themes.group', 'level_sizes', { last_run: sizes && sizes.length ? sizes : null })}
    ${one('themes.group', 'comb', {}, { label: t('playground.comb') })}
    <${Control} label=${t('playground.theta')} labelId=${`${thetaId}-label`}>
      <div class="cx-param__control-row">
        <label class="cx-param__unset"><input type="checkbox" checked=${calibrated} disabled=${!comb}
          onChange=${(e) => onChange('themes.group.comb_theta', e.currentTarget.checked ? null
            : Number((theta ?? 0.1).toFixed(3)), false)} />
          <span>${t('playground.calibrated')}</span></label>
        ${calibrated ? html`<span class="cx-pg-control__value cx-num">${!comb ? t('playground.comb_off')
          : theta === null || theta === undefined ? '…' : t('playground.theta_value', { theta: formatNumber(theta, { maximumFractionDigits: 3 }) })}</span>`
          : html`<${ParamControl} p=${{ ...paramOf(data, 'themes.group', 'comb_theta'), nullable: false }} id=${thetaId}
            labelId=${`${thetaId}-label`} label=${t('playground.theta')} disabled=${!comb}
            value=${values['themes.group.comb_theta']} onChange=${(v, bad) => onChange('themes.group.comb_theta', v, bad)} />`}
      </div><//>
    ${one('themes.space', 'space_unit', { widget: 'choice' }, { label: t('playground.space') })}
    <div class="cx-pg-controls__state" role="status" aria-live="polite">${state}</div>
  </div>`;
}

/** The line under the controls: computing (with the job's progress), or how long it took. */
export function StateLine({ store, refit }) {
  const phase = store.phase.value;
  const p = store.preview.value;
  const progress = store.progress.value;
  if (phase === 'waiting' || phase === 'computing') {
    const fraction = progress && typeof progress.fraction === 'number' ? progress.fraction : undefined;
    return html`<${ProgressBar} value=${fraction} label=${t('playground.computing')} />
      <span>${refit ? t('playground.computing_space') : t('playground.computing')}</span>`;
  }
  if (phase === 'ready' && p) {
    const s = p.seconds || {};
    return html`<span class="cx-num">${p.space_refit
      ? t('playground.done_space', { seconds: s.total || 0, space: s.space || 0 })
      : t('playground.done', { seconds: s.total || 0 })}</span>`;
  }
  return null;
}
