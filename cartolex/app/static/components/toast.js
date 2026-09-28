// SPDX-License-Identifier: MIT
/**
 * Toasts: short messages in a corner, read out by screen readers.
 *
 * Two live regions exist from the start (a message added to a region that
 * appears at the same time is not read): a polite one for information and an
 * assertive one for errors. Information disappears after a few seconds (not
 * while the pointer or the focus is on it); **errors stay until dismissed**.
 */
import { html, signal } from '../core/preact.js';
import { t } from '../core/i18n.js';
import { IconButton } from './button.js';
import { Icon } from './icons.js';

const DEFAULT_TIMEOUT = 6000;
let counter = 0;

/** A toast store: its `items` signal and the functions that change it. */
export function createToaster() {
  const items = signal([]);
  const timers = new Map();

  const dismiss = (id) => {
    clearTimeout(timers.get(id));
    timers.delete(id);
    items.value = items.value.filter((item) => item.id !== id);
  };
  const schedule = (item) => {
    if (item.kind === 'error' || item.timeout === 0) return;
    clearTimeout(timers.get(item.id));
    timers.set(item.id, setTimeout(() => dismiss(item.id), item.timeout || DEFAULT_TIMEOUT));
  };
  /**
   * Show a toast; returns its id.
   * @param {{kind?: 'info'|'success'|'warning'|'error', title: string, message?: string,
   *          action?: {label: string, onClick: Function}, timeout?: number}} toast
   */
  const show = (toast) => {
    counter += 1;
    const item = { kind: 'info', ...toast, id: toast.id || `toast-${counter}` };
    items.value = [...items.value.filter((i) => i.id !== item.id), item];
    schedule(item);
    return item.id;
  };
  const hold = (id) => {
    clearTimeout(timers.get(id));
    timers.delete(id);
  };
  const resume = (id) => {
    const item = items.value.find((i) => i.id === id);
    if (item) schedule(item);
  };
  const clear = () => {
    timers.forEach((timer) => clearTimeout(timer));
    timers.clear();
    items.value = [];
  };
  return { items, show, dismiss, hold, resume, clear };
}

const ICONS = { info: 'info', success: 'check', warning: 'warning', error: 'error' };

function ToastItem({ item, toaster }) {
  const word = t(`toast.kind.${item.kind}`);
  return html`<div class=${`cx-toast cx-toast--${item.kind}`} data-toast=${item.id}
    onPointerEnter=${() => toaster.hold(item.id)} onPointerLeave=${() => toaster.resume(item.id)}
    onFocusIn=${() => toaster.hold(item.id)} onFocusOut=${() => toaster.resume(item.id)}>
    <span class="cx-toast__icon"><${Icon} name=${ICONS[item.kind] || 'info'} /></span>
    <div class="cx-toast__text">
      <p class="cx-toast__title">
        ${item.kind === 'error' || item.kind === 'warning'
          ? html`<span class="cx-toast__kind">${word}</span> ` : html`<span
            class="cx-visually-hidden">${word}</span> `}
        ${item.title}
      </p>
      ${item.message ? html`<p class="cx-toast__message">${item.message}</p>` : null}
      ${item.action ? html`<button type="button" class="cx-link-button"
        onClick=${() => {
          item.action.onClick();
          toaster.dismiss(item.id);
        }}>${item.action.label}</button>` : null}
    </div>
    <${IconButton} icon="close" size="s" label=${t('common.dismiss')} tooltip=${false}
      onClick=${() => toaster.dismiss(item.id)} />
  </div>`;
}

/**
 * The two live regions and the toasts in them. Render it once, in the shell.
 * `preview` draws the toasts in place, without live regions (the gallery).
 */
export function Toaster({ toaster, preview = false }) {
  const items = toaster.items.value;
  if (preview) {
    return html`<div class="cx-toaster cx-toaster--preview">
      ${items.map((item) => html`<${ToastItem} key=${item.id} item=${item} toaster=${toaster} />`)}
    </div>`;
  }
  const polite = items.filter((i) => i.kind !== 'error');
  const urgent = items.filter((i) => i.kind === 'error');
  return html`<div class="cx-toaster" aria-label=${t('toast.region')} role="region">
    <div class="cx-toaster__stack" aria-live="polite" aria-relevant="additions text">
      ${polite.map((item) => html`<${ToastItem} key=${item.id} item=${item} toaster=${toaster} />`)}
    </div>
    <div class="cx-toaster__stack" role="alert" aria-live="assertive" aria-relevant="additions text">
      ${urgent.map((item) => html`<${ToastItem} key=${item.id} item=${item} toaster=${toaster} />`)}
    </div>
  </div>`;
}
