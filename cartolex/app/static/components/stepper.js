// SPDX-License-Identifier: MIT
/**
 * Stepper: where a person is in a flow of several steps (import, handoff…).
 * Each step is done (a check), current (a filled number), to do (an empty
 * number) or in error (a cross, with the error hue and the word). Done steps
 * can be buttons that go back to them.
 */
import { html } from '../core/preact.js';
import { formatNumber, t } from '../core/i18n.js';
import { Icon } from './icons.js';

/**
 * @param {object} props
 * @param {Array<{id: string, label: any, state: 'done'|'current'|'todo'|'error'}>} props.steps
 * @param {(id: string) => void} [props.onSelect] makes done steps buttons
 * @param {string} props.label the flow's accessible name
 */
export function Stepper({ steps, onSelect, label, class: cls = '' }) {
  return html`<ol class=${`cx-stepper ${cls}`} aria-label=${label}>
    ${steps.map((step, i) => {
      const mark = step.state === 'done' ? html`<${Icon} name="check" />`
        : step.state === 'error' ? html`<${Icon} name="cross" />`
          : html`<span>${formatNumber(i + 1)}</span>`;
      const status = html`<span class="cx-visually-hidden">${t(`stepper.${step.state}`)}</span>`;
      const inner = html`<span class="cx-stepper__mark" aria-hidden="true">${mark}</span>
        <span class="cx-stepper__label">${step.label}</span>${status}`;
      return html`<li key=${step.id} class=${`cx-stepper__step cx-stepper__step--${step.state}`}
        aria-current=${step.state === 'current' ? 'step' : undefined}>
        ${onSelect && step.state === 'done'
          ? html`<button type="button" class="cx-stepper__button"
              onClick=${() => onSelect(step.id)}>${inner}</button>`
          : html`<span class="cx-stepper__static">${inner}</span>`}
      </li>`;
    })}
  </ol>`;
}
