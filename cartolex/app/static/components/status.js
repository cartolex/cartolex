// SPDX-License-Identifier: MIT
/**
 * StatusDot, StatusPill and StageTracker: the six states of a build stage.
 *
 * State is coded by shape, so it reads in black-and-white print and for
 * colour-blind readers:
 *
 *   up to date    a full dot          needs update  a half dot
 *   never built   an empty dot        running       a ring with a gap
 *   failed        a cross (+ the error hue and the word)
 *   skipped       a dash
 *
 * The states are the build's (`cartolex.build.validity.StageState`); the API
 * sends them as words (« up to date ») and they are normalised to keys
 * (`up_to_date`).
 */
import { html } from '../core/preact.js';
import { formatDuration, formatNumber, formatPercent, has, t } from '../core/i18n.js';
import { STATES, stateKey, summaryState } from '../core/states.js';
import { ProgressBar } from './progress.js';

export { STATES, stateKey, summaryState };

/** The state's word in the interface language (« Up to date »). */
export function stateLabel(state) {
  return t(`state.${stateKey(state)}`);
}

function Shape({ state }) {
  switch (state) {
    case 'up_to_date':
      return html`<circle cx="8" cy="8" r="5" class="cx-dot__fill" />`;
    case 'needs_update':
      return html`<circle cx="8" cy="8" r="4.5" class="cx-dot__line" />
        <path d="M8 3.5a4.5 4.5 0 0 0 0 9z" class="cx-dot__fill" />`;
    case 'running':
      return html`<path d="M8 3a5 5 0 1 1-4.33 2.5" class="cx-dot__line cx-dot__spin" />`;
    case 'failed':
      return html`<path d="M4.5 4.5l7 7M11.5 4.5l-7 7" class="cx-dot__line cx-dot__thick" />`;
    case 'skipped':
      return html`<path d="M3.5 8h9" class="cx-dot__line cx-dot__thick" />`;
    default:
      return html`<circle cx="8" cy="8" r="4.5" class="cx-dot__line" />`;
  }
}

/**
 * The state's shape. With `label`, the state's word (or the given text) is
 * shown beside it; without, the shape carries the word as its accessible name.
 * @param {{state: string, label?: boolean|string, size?: 's'|'m'}} props
 */
export function StatusDot({ state, label, size = 'm', class: cls = '' }) {
  const key = stateKey(state);
  const word = typeof label === 'string' ? label : stateLabel(key);
  const svg = html`<svg class=${`cx-dot cx-dot--${size}`} viewBox="0 0 16 16" focusable="false"
    ...${label ? { 'aria-hidden': 'true' } : { role: 'img', 'aria-label': word }}>
    <${Shape} state=${key} />
  </svg>`;
  return html`<span class=${`cx-status cx-status--${key} ${cls}`} data-state=${key}>
    ${svg}${label ? html`<span class="cx-status__label">${word}</span>` : null}
  </span>`;
}

/**
 * The state's shape and word in a pill, with an optional detail
 * (« needs update · decisions/keywords.csv changed »).
 */
export function StatusPill({ state, detail, class: cls = '' }) {
  const key = stateKey(state);
  return html`<span class=${`cx-pill cx-pill--${key} ${cls}`} data-state=${key}>
    <${StatusDot} state=${key} size="s" label=${true} />
    ${detail ? html`<span class="cx-pill__detail">${detail}</span>` : null}
  </span>`;
}

/** A stage's name in the interface language, or the name the API gave. */
export function stageName(stage) {
  const key = `stage.${stage.id}`;
  return has(key) ? t(key) : stage.name || stage.id;
}

/** One reason a stage needs an update, in the interface language. */
export function reasonText(reason) {
  if (!reason) return '';
  const key = `reason.${reason.kind}`;
  if (reason.kind === 'upstream') {
    return t(key, { stage: stageName({ id: reason.subject, name: reason.subject }) });
  }
  return has(key) ? t(key, { subject: reason.subject }) : reason.detail || '';
}

/**
 * The build's stages with their state, reasons and progress.
 * @param {{stages: Array<object>, title?: any, compact?: boolean}} props
 *   each stage: {id, name, state, reasons?, skip_reason?, progress?}; `stateText` replaces
 *   the state's word (« Waiting » in a running build), `note` adds a line under it
 */
export function StageTracker({ stages, label, compact = false }) {
  const done = stages.filter((s) => ['up_to_date', 'skipped'].includes(stateKey(s.state))).length;
  return html`<div class=${`cx-tracker ${compact ? 'cx-tracker--compact' : ''}`}>
    <p class="cx-tracker__summary">${t('tracker.summary', { done, total: stages.length })}</p>
    <ol class="cx-tracker__list" aria-label=${label || t('tracker.label')}>
      ${stages.map((stage, i) => {
        const key = stateKey(stage.state);
        const p = stage.progress;
        return html`<li class=${`cx-tracker__stage cx-tracker__stage--${key}`} key=${stage.id}
          data-stage=${stage.id}>
          <span class="cx-tracker__rail" aria-hidden="true"></span>
          <${StatusDot} state=${key} />
          <div class="cx-tracker__body">
            <div class="cx-tracker__line">
              <span class="cx-tracker__index">${formatNumber(i + 1)}</span>
              <span class="cx-tracker__name">${stageName(stage)}</span>
              <span class="cx-tracker__state">${stage.stateText || stateLabel(key)}</span>
            </div>
            ${!compact && stage.note ? html`<p class="cx-tracker__reason">${stage.note}</p>` : null}
            ${!compact && key === 'skipped' && stage.skip_reason
              ? html`<p class="cx-tracker__reason">${stage.skip_reason}</p>` : null}
            ${!compact && (stage.reasons || []).length ? html`<ul class="cx-tracker__reasons">
              ${stage.reasons.map((r) => html`<li>${reasonText(r)}</li>`)}</ul>` : null}
            ${!compact && key === 'failed' && stage.attempt && stage.attempt.error
              ? html`<p class="cx-tracker__reason cx-tracker__reason--error">
                  ${t('tracker.last_attempt', { error: stage.attempt.error })}</p>` : null}
            ${key === 'running' && p ? html`<div class="cx-tracker__progress">
              <${ProgressBar} value=${p.stage_fraction} resetKey=${stage.id}
                label=${t('tracker.progress', { stage: stageName(stage) })} />
              <span class="cx-tracker__meta">
                ${t('tracker.phase', { phase: p.phase, phases: p.phases })}
                ${' · '}${formatPercent(p.stage_fraction)}
                ${p.eta_s ? html`${' · '}${t('tracker.eta', { eta: formatDuration(p.eta_s) })}` : null}
              </span>
            </div>` : null}
          </div>
        </li>`;
      })}
    </ol>
  </div>`;
}
