// SPDX-License-Identifier: MIT
/**
 * Tooltip (a short hint on hover and focus) and Help (a « ? » button that
 * opens an explanation).
 *
 * A tooltip appears on pointer hover after a short delay and at once on
 * keyboard focus; it stays while the pointer is over it, and Escape hides it
 * (WCAG 1.4.13). A `decorative` tooltip repeats its trigger's accessible name
 * (an IconButton's label) and is hidden from assistive technology; any other
 * tooltip describes its trigger through `aria-describedby`.
 */
import { cloneElement, html, isValidElement, useEffect, useLayoutEffect, useRef, useState }
  from '../core/preact.js';
import { t } from '../core/i18n.js';
import { placeFloating, useEscape, useOutsidePress, useUid } from '../core/dom.js';
import { Icon } from './icons.js';

const SHOW_DELAY = 400;
const HIDE_DELAY = 150;

/**
 * @param {{text: string, decorative?: boolean, open?: boolean, children: any}} props
 * `open` forces the tooltip visible (the gallery uses it to show the state).
 */
export function Tooltip({ text, decorative = false, open: forced, children }) {
  const id = useUid('cx-tip');
  const [open, setOpen] = useState(false);
  const timer = useRef(0);
  const anchor = useRef(null);
  const bubble = useRef(null);
  const shown = forced || open;

  const later = (value, delay) => {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setOpen(value), delay);
  };
  useEffect(() => () => clearTimeout(timer.current), []);
  useEscape(() => setOpen(false), open);
  useLayoutEffect(() => {
    if (shown && bubble.current && anchor.current) {
      const target = anchor.current.firstElementChild || anchor.current;
      placeFloating(bubble.current, target, { prefer: 'above', gap: 6 });
    }
  }, [shown, text]);

  const child = isValidElement(children) && !decorative
    ? cloneElement(children, { 'aria-describedby': id }) : children;
  return html`<span class="cx-tooltip-anchor" ref=${anchor}
    onPointerEnter=${() => later(true, SHOW_DELAY)}
    onPointerLeave=${() => later(false, HIDE_DELAY)}
    onFocusIn=${(e) => {
      if (e.target.matches(':focus-visible')) later(true, 0);
    }}
    onFocusOut=${() => later(false, 0)}>
    ${child}
    <span ref=${bubble} id=${id} role=${decorative ? undefined : 'tooltip'}
      aria-hidden=${decorative ? 'true' : undefined}
      class=${`cx-tooltip ${shown ? 'is-open' : ''}`}>${text}</span>
  </span>`;
}

/**
 * A « ? » button that opens an explanation next to it. `topic` names what
 * the help is about (it completes the button's accessible name).
 * @param {{topic: string, children: any, open?: boolean}} props
 */
export function Help({ topic, children, open: initial = false }) {
  const id = useUid('cx-help');
  const [open, setOpen] = useState(initial);
  const button = useRef(null);
  const panel = useRef(null);
  const close = (refocus) => {
    setOpen(false);
    if (refocus && button.current) button.current.focus();
  };
  useEscape(() => close(true), open);
  useOutsidePress([button, panel], () => close(false), open);
  useLayoutEffect(() => {
    if (open && panel.current && button.current) placeFloating(panel.current, button.current);
  }, [open]);
  return html`<span class="cx-help">
    <button ref=${button} type="button" class="cx-icon-button cx-button--ghost cx-icon-button--s"
      aria-expanded=${String(open)} aria-controls=${id}
      aria-label=${t('help.button', { topic })} onClick=${() => setOpen(!open)}>
      <${Icon} name="help" />
    </button>
    <div ref=${panel} id=${id} class=${`cx-help__panel ${open ? 'is-open' : ''}`}
      role="region" aria-label=${t('help.button', { topic })} hidden=${!open}>
      ${children}
    </div>
  </span>`;
}
