// SPDX-License-Identifier: MIT
/**
 * ProgressBar: how far a task is. It never goes back: a value lower than one
 * already shown is ignored until `resetKey` changes (a new task). Without a
 * value it is indeterminate (a moving band, still under reduced motion only as
 * a static pattern).
 */
import { html, useRef } from '../core/preact.js';
import { formatPercent, t } from '../core/i18n.js';

/**
 * @param {object} props
 * @param {number|null} [props.value] a fraction from 0 to 1; null or undefined: indeterminate
 * @param {string} props.label the accessible name (« Progress of the keywords »)
 * @param {any} [props.resetKey] a new key starts again from 0
 * @param {boolean} [props.showValue] show the percentage beside the bar
 * @param {string} [props.valueText] a spoken value (« phase 3 of 7, 45 % »)
 */
export function ProgressBar({ value, label, resetKey, showValue = false, valueText, class: cls = '' }) {
  const state = useRef({ key: resetKey, max: 0 });
  if (state.current.key !== resetKey) state.current = { key: resetKey, max: 0 };
  const known = typeof value === 'number' && Number.isFinite(value);
  if (known) state.current.max = Math.max(state.current.max, Math.min(1, Math.max(0, value)));
  const shown = state.current.max;
  const pct = Math.round(shown * 100);
  return html`<div class=${`cx-progress ${known ? '' : 'is-indeterminate'} ${cls}`}>
    <div class="cx-progress__track" role="progressbar" aria-label=${label}
      aria-valuemin="0" aria-valuemax="100"
      aria-valuenow=${known ? pct : undefined}
      aria-valuetext=${known ? valueText || formatPercent(shown) : t('progress.working')}
      style=${{ '--cx-progress': String(shown) }}>
      <span class="cx-progress__bar"></span>
    </div>
    ${showValue ? html`<span class="cx-progress__value" aria-hidden="true">
      ${known ? formatPercent(shown) : t('progress.working')}</span>` : null}
  </div>`;
}
