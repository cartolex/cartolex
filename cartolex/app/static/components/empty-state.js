// SPDX-License-Identifier: MIT
/**
 * EmptyState: what an empty list or screen means, and the next action.
 * An empty state always names what to do next (a button or a link).
 */
import { html } from '../core/preact.js';
import { Button } from './button.js';
import { Icon } from './icons.js';

/**
 * @param {object} props
 * @param {any} props.title what is empty, in plain words
 * @param {any} [props.children] why, and what the action does
 * @param {{label: any, onClick?: Function, href?: string, icon?: string}} [props.action]
 * @param {string} [props.icon]
 * @param {2|3|4} [props.level] the heading level (3 by default)
 */
export function EmptyState({ title, children, action, icon = 'file', level = 3, class: cls = '' }) {
  const Heading = `h${level}`;
  return html`<div class=${`cx-empty ${cls}`}>
    <span class="cx-empty__icon" aria-hidden="true"><${Icon} name=${icon} size=${24} /></span>
    <${Heading} class="cx-empty__title">${title}<//>
    ${children ? html`<div class="cx-empty__text">${children}</div>` : null}
    ${action ? html`<div class="cx-empty__action">
      ${action.href
        ? html`<a class="cx-button cx-button--primary cx-button--m" href=${action.href}>
            <span class="cx-button__label">${action.label}</span></a>`
        : html`<${Button} variant="primary" icon=${action.icon} onClick=${action.onClick}>
            ${action.label}<//>`}
    </div>` : null}
  </div>`;
}
