// SPDX-License-Identifier: MIT
/**
 * Button and IconButton.
 *
 * Variants: `primary` (the one main action of a view), `secondary` (default),
 * `ghost` (quiet, in toolbars), `danger` (destroys something; its label says
 * what). A loading button keeps its place and its focus, shows a spinner, is
 * announced as busy and ignores presses. A disabled button uses the native
 * attribute; use `aria-disabled` via `softDisabled` when a disabled button
 * must stay focusable to explain itself through a tooltip.
 */
import { html } from '../core/preact.js';
import { t } from '../core/i18n.js';
import { Icon } from './icons.js';
import { Tooltip } from './tooltip.js';

/**
 * @param {object} props
 * @param {'primary'|'secondary'|'ghost'|'danger'} [props.variant]
 * @param {'m'|'s'} [props.size]
 * @param {string} [props.icon] an icon name shown before the label
 * @param {string} [props.iconAfter] an icon name shown after the label
 * @param {boolean} [props.loading]
 * @param {boolean} [props.disabled]
 * @param {boolean} [props.softDisabled] looks disabled, stays focusable
 * @param {string} [props.class]
 */
export function Button({
  variant = 'secondary', size = 'm', icon, iconAfter, loading = false, disabled = false,
  softDisabled = false, class: cls = '', type = 'button', onClick, children, buttonRef, ...rest
}) {
  const inert = loading || softDisabled;
  const handle = (event) => {
    if (inert) {
      event.preventDefault();
      return;
    }
    if (onClick) onClick(event);
  };
  return html`<button ref=${buttonRef} type=${type}
    class=${`cx-button cx-button--${variant} cx-button--${size} ${loading ? 'is-loading' : ''} ${cls}`}
    disabled=${disabled} aria-disabled=${inert ? 'true' : undefined}
    aria-busy=${loading ? 'true' : undefined} onClick=${handle} ...${rest}>
    ${loading ? html`<span class="cx-spinner" aria-hidden="true"></span>`
      : icon ? html`<${Icon} name=${icon} />` : null}
    <span class="cx-button__label">${children}</span>
    ${loading ? html`<span class="cx-visually-hidden">${t('common.loading')}</span>` : null}
    ${iconAfter && !loading ? html`<${Icon} name=${iconAfter} />` : null}
  </button>`;
}

/**
 * A button showing only an icon. Its `label` is its accessible name and its
 * tooltip; it is required.
 * @param {{icon: string, label: string, variant?: string, size?: 'm'|'s',
 *          pressed?: boolean, loading?: boolean, disabled?: boolean}} props
 */
export function IconButton({
  icon, label, variant = 'ghost', size = 'm', pressed, loading = false, disabled = false,
  class: cls = '', onClick, buttonRef, tooltip = true, ...rest
}) {
  const handle = (event) => {
    if (loading) {
      event.preventDefault();
      return;
    }
    if (onClick) onClick(event);
  };
  const button = html`<button ref=${buttonRef} type="button"
    class=${`cx-icon-button cx-button--${variant} cx-icon-button--${size} ${loading ? 'is-loading' : ''} ${cls}`}
    aria-label=${label} aria-pressed=${pressed === undefined ? undefined : String(pressed)}
    disabled=${disabled} aria-busy=${loading ? 'true' : undefined}
    aria-disabled=${loading ? 'true' : undefined} onClick=${handle} ...${rest}>
    ${loading ? html`<span class="cx-spinner" aria-hidden="true"></span>`
      : html`<${Icon} name=${icon} size=${size === 's' ? 16 : 16} />`}
  </button>`;
  return tooltip && !disabled ? html`<${Tooltip} text=${label} decorative>${button}<//>` : button;
}
