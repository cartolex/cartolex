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
import {
  autonym, formatDate, formatDuration, formatNumber, formatPercent, has, t,
} from '../core/i18n.js';
import { STATES, stateKey, summaryState } from '../core/states.js';
import { ProgressBar } from './progress.js';

export { STATES, stateKey, summaryState };

/** The state's word in the interface language (« Up to date »). */
export function stateLabel(state) {
  return t(`state.${stateKey(state)}`);
}

/** The words of the AI clean-up as the project shows it (`ai_state`), when they differ from
 * its state's: done with the copilot, waiting for it, its earlier decisions only. */
const AI_WORDS = {
  copilot_done: 'build.action.done_copilot',
  copilot_waiting: 'build.action.held',
  copilot_earlier: 'build.action.earlier_copilot',
};

/** The AI clean-up's word for its `ai_state`, or `null` (the state's own word). */
export function aiStateText(aiState) {
  return AI_WORDS[aiState] ? t(AI_WORDS[aiState]) : null;
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

/**
 * A message of the API (`{code, params, message}`: a skip, an attempt, a health item) in
 * the interface language: the catalogue's `message.<code>` filled with its params (stage
 * names, languages and dates in words), else the server's English.
 */
export function messageOf(item) {
  if (!item) return '';
  const key = `message.${item.code}`;
  if (!has(key)) return item.message || '';
  const params = { ...(item.params || {}) };
  if (params.stage) params.stage = stageName({ id: params.stage, name: params.stage });
  if (Array.isArray(params.languages)) params.languages = params.languages.map(autonym);
  if (params.language) params.language = autonym(params.language);
  if (params.date) params.date = formatDate(params.date, 'date', 'medium');
  return t(key, params);
}

/** What changed in a file a stage reads, in plain words (`reason.input.<what>`). */
const INPUTS = [
  [/^decisions\/(people|organisations|affiliations)\.csv$/, 'people'],
  [/^decisions\/keywords\.csv$/, 'keywords'],
  [/^decisions\/(stopwords\.json|excluded\.csv|kept\.csv|merged\.json)$/, 'words'],
  [/^decisions\/prompts\//, 'prompts'],
  [/^decisions\/themes\.json$/, 'themes'],
  [/^decisions\/maps\.json$/, 'maps'],
  [/^sources\/tables\//, 'tables'],
  [/^sources\//, 'sources'],
];

/**
 * One reason a stage needs an update, in the interface language and plain words: a file
 * read by what it holds, a parameter by its label (`param.label.<stage>.<name>`).
 */
export function reasonText(reason, stageId = '') {
  if (!reason) return '';
  const key = `reason.${reason.kind}`;
  if (reason.kind === 'upstream') {
    return t(key, { stage: stageName({ id: reason.subject, name: reason.subject }) });
  }
  if (reason.kind === 'input') {
    const found = INPUTS.find(([re]) => re.test(reason.subject || ''));
    if (found && has(`reason.input.${found[1]}`)) return t(`reason.input.${found[1]}`);
  }
  if (reason.kind === 'parameter' && stageId && has(`param.label.${stageId}.${reason.subject}`)) {
    return t('reason.parameter.named', { label: t(`param.label.${stageId}.${reason.subject}`) });
  }
  return has(key) ? t(key, { subject: reason.subject }) : reason.detail || '';
}

/** A stage's last attempt, quietly: « cancelled on … », « failed on … » (`quiet`), or why. */
function attemptNote(stage, key) {
  const a = stage.attempt;
  if (!a) return null;
  const date = a.finished_at ? formatDate(a.finished_at, 'datetime', 'medium') : '';
  if (a.outcome === 'cancelled' && key !== 'running') {
    return html`<p class="cx-tracker__reason">${t('tracker.cancelled_on', { date })}</p>`;
  }
  if (key !== 'failed') return null;
  if (stage.quiet) return html`<p class="cx-tracker__reason">${t('tracker.failed_on', { date })}</p>`;
  const words = a.code ? messageOf(a) : a.error;
  return words ? html`<p class="cx-tracker__reason cx-tracker__reason--error">
    ${t('tracker.last_attempt', { error: words })}</p>` : null;
}

/**
 * The build's stages with their state, reasons and progress.
 * @param {{stages: Array<object>, title?: any, compact?: boolean}} props
 *   each stage: {id, name, state, reasons?, skip?, skip_reason?, ai_state?, attempt?, progress?};
 *   `stateText` replaces the state's word (« Waiting » in a running build), `note` adds a
 *   line under it, `quiet` says a failed attempt in a word and its date (a later job came)
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
              <span class="cx-tracker__state">${stage.stateText || aiStateText(stage.ai_state)
                || stateLabel(key)}</span>
            </div>
            ${!compact && stage.note ? html`<p class="cx-tracker__reason">${stage.note}</p>` : null}
            ${!compact && !stage.note && stage.skip && (key === 'skipped' || stage.ai)
              ? html`<p class="cx-tracker__reason">${messageOf(stage.skip)}</p>`
              : !compact && !stage.note && key === 'skipped' && stage.skip_reason
                ? html`<p class="cx-tracker__reason">${stage.skip_reason}</p>` : null}
            ${!compact && (stage.reasons || []).length ? html`<ul class="cx-tracker__reasons">
              ${stage.reasons.map((r) => html`<li>${reasonText(r, stage.id)}</li>`)}</ul>` : null}
            ${!compact ? attemptNote(stage, key) : null}
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
