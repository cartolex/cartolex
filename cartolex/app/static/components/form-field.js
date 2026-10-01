// SPDX-License-Identifier: MIT
/**
 * FormField: a label, an optional help text, the control, and an error.
 *
 * The label is a real `<label>`; the help and the error are tied to the
 * control with `aria-describedby`; an error sets `aria-invalid` and is shown
 * with an icon and the word « Error », never by colour alone; a required
 * field says « required » in words.
 *
 * The control is a render function receiving the attributes to spread on it:
 *
 *   html`<${FormField} label=${t('x')} help=${t('y')}>
 *     ${(field) => html`<${Input} ...${field} value=${v} onInput=${…} />`}
 *   <//>`
 */
import { html } from '../core/preact.js';
import { t } from '../core/i18n.js';
import { useUid } from '../core/dom.js';
import { Icon } from './icons.js';

/**
 * @param {object} props
 * @param {any} props.label
 * @param {any} [props.help]
 * @param {any} [props.error] the error message (shown and announced)
 * @param {boolean} [props.required]
 * @param {(field: object) => any} props.children
 */
export function FormField({ label, help, error, required = false, class: cls = '', children }) {
  const id = useUid('cx-field');
  const helpId = `${id}-help`;
  const errorId = `${id}-error`;
  const describedBy = [help ? helpId : null, error ? errorId : null].filter(Boolean).join(' ');
  const field = {
    id,
    required,
    'aria-describedby': describedBy || undefined,
    'aria-invalid': error ? 'true' : undefined,
  };
  return html`<div class=${`cx-field ${error ? 'is-invalid' : ''} ${cls}`}>
    <label class="cx-field__label" for=${id}>
      ${label}
      ${required ? html`<span class="cx-field__required">${t('field.required')}</span>` : null}
    </label>
    ${help ? html`<p class="cx-field__help" id=${helpId}>${help}</p>` : null}
    ${children(field)}
    <p class="cx-field__error" id=${errorId} hidden=${!error}>
      ${error ? html`<${Icon} name="warning" /><span class="cx-field__error-word">
        ${t('field.error')}</span> ${error}` : null}
    </p>
  </div>`;
}

/** A text input styled for FormField. */
export function Input({ class: cls = '', type = 'text', ...rest }) {
  return html`<input class=${`cx-input ${cls}`} type=${type} ...${rest} />`;
}

/** A multi-line text input styled for FormField. */
export function Textarea({ class: cls = '', rows = 4, ...rest }) {
  return html`<textarea class=${`cx-input cx-textarea ${cls}`} rows=${rows} ...${rest}></textarea>`;
}

/**
 * A select styled for FormField; an option may be `disabled` (shown, not chosen).
 * @param {{options: Array<{value: string, label: any, disabled?: boolean}>}} props
 */
export function Select({ options, class: cls = '', ...rest }) {
  return html`<span class="cx-select">
    <select class=${`cx-input cx-select__control ${cls}`} ...${rest}>
      ${options.map((o) => html`<option value=${o.value} key=${o.value} disabled=${o.disabled}>${o.label}</option>`)}
    </select>
    <${Icon} name="chevron-down" class="cx-select__chevron" />
  </span>`;
}

/** A checkbox with its label on the right (its own label; not inside FormField). */
export function Checkbox({ label, class: cls = '', ...rest }) {
  return html`<label class=${`cx-checkbox ${cls}`}>
    <input type="checkbox" class="cx-checkbox__control" ...${rest} />
    <span class="cx-checkbox__box" aria-hidden="true"><${Icon} name="check" /></span>
    <span class="cx-checkbox__label">${label}</span>
  </label>`;
}
