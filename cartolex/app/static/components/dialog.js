// SPDX-License-Identifier: MIT
/**
 * Dialog and Drawer: modal windows on the native `<dialog>` element.
 *
 * Opening one moves the focus into it (to `initialFocus`, else its first
 * field or button); Tab and Shift+Tab stay inside; Escape closes it (unless
 * it is not `dismissible`); closing gives the focus back to what had it
 * before. The page behind is inert while it is open. A Drawer is the same
 * window as a panel on the side of the screen.
 */
import { html, useEffect, useLayoutEffect, useRef } from '../core/preact.js';
import { t } from '../core/i18n.js';
import { focusables, trapTab, useUid } from '../core/dom.js';
import { IconButton } from './button.js';

function restore(target) {
  if (target && target.isConnected && typeof target.focus === 'function') {
    target.focus();
  }
}

/**
 * @param {object} props
 * @param {boolean} props.open
 * @param {(reason: 'close'|'escape'|'backdrop') => void} props.onClose
 * @param {any} props.title
 * @param {any} [props.description] a line under the title (tied with aria-describedby)
 * @param {any} [props.footer] the actions
 * @param {'s'|'m'|'l'} [props.size]
 * @param {{current: HTMLElement}} [props.initialFocus]
 * @param {boolean} [props.dismissible] Escape and the close button close it
 * @param {boolean} [props.closeOnBackdrop]
 * @param {'dialog'|'drawer'} [props.kind]
 * @param {'dialog'|'alertdialog'} [props.role]
 */
export function Dialog({
  open, onClose, title, description, footer, size = 'm', initialFocus, dismissible = true,
  closeOnBackdrop = false, kind = 'dialog', role = 'dialog', class: cls = '', children,
}) {
  const ref = useRef(null);
  const returnTo = useRef(null);
  const id = useUid(`cx-${kind}`);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) {
      returnTo.current = document.activeElement;
      el.showModal();
      const body = el.querySelector('.cx-dialog__body');
      const target = (initialFocus && initialFocus.current)
        || focusables(body)[0] || focusables(el)[0] || el;
      target.focus();
    } else if (!open && el.open) {
      el.close();
      restore(returnTo.current);
      returnTo.current = null;
    }
  }, [open]);

  useEffect(() => () => {
    const el = ref.current;
    if (el && el.open) {
      el.close();
      restore(returnTo.current);
    }
  }, []);

  const onCancel = (event) => {
    event.preventDefault();
    if (dismissible) onClose('escape');
  };
  const onKeyDown = (event) => {
    if (ref.current) trapTab(event, ref.current);
  };
  const onClick = (event) => {
    if (closeOnBackdrop && event.target === ref.current) onClose('backdrop');
  };

  return html`<dialog ref=${ref} role=${role === 'alertdialog' ? 'alertdialog' : undefined}
    class=${`cx-dialog cx-dialog--${kind} cx-dialog--${size} ${cls}`}
    aria-labelledby=${`${id}-title`} aria-describedby=${description ? `${id}-desc` : undefined}
    aria-modal="true" tabindex="-1"
    onCancel=${onCancel} onKeyDown=${onKeyDown} onClick=${onClick}>
    ${open ? html`<div class="cx-dialog__frame">
      <header class="cx-dialog__header">
        <div class="cx-dialog__heading">
          <h2 class="cx-dialog__title" id=${`${id}-title`}>${title}</h2>
          ${description ? html`<p class="cx-dialog__description" id=${`${id}-desc`}>
            ${description}</p>` : null}
        </div>
        ${dismissible ? html`<${IconButton} icon="close" label=${t('common.close')}
          onClick=${() => onClose('close')} tooltip=${false} />` : null}
      </header>
      <div class="cx-dialog__body">${children}</div>
      ${footer ? html`<footer class="cx-dialog__footer">${footer}</footer>` : null}
    </div>` : null}
  </dialog>`;
}

/** A Dialog shown as a panel on the side of the screen. */
export function Drawer(props) {
  return html`<${Dialog} size="m" ...${props} kind="drawer" closeOnBackdrop=${true} />`;
}

/**
 * A question with two answers (« Leave without saving? »). Resolves through
 * `onAnswer(true|false)`; Escape answers false. The safe answer has the focus.
 */
export function ConfirmDialog({ open, title, children, confirmLabel, cancelLabel, danger = false,
  onAnswer }) {
  const safe = useRef(null);
  return html`<${Dialog} open=${open} role="alertdialog" size="s" title=${title}
    onClose=${() => onAnswer(false)} initialFocus=${safe}
    footer=${html`
      <button ref=${safe} type="button" class="cx-button cx-button--secondary cx-button--m"
        onClick=${() => onAnswer(false)}><span class="cx-button__label">
        ${cancelLabel || t('common.cancel')}</span></button>
      <button type="button"
        class=${`cx-button cx-button--${danger ? 'danger' : 'primary'} cx-button--m`}
        onClick=${() => onAnswer(true)}><span class="cx-button__label">
        ${confirmLabel || t('common.confirm')}</span></button>`}>
    ${children}
  <//>`;
}
