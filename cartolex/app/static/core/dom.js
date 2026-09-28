// SPDX-License-Identifier: MIT
/**
 * Small DOM helpers the components share: ids, focusable elements, focus
 * traps, outside clicks, positioning through CSS custom properties.
 */
import { useEffect, useRef } from './preact.js';

let counter = 0;

/** A document-unique id with a readable prefix (`cx-menu-3`). */
export function uid(prefix = 'cx') {
  counter += 1;
  return `${prefix}-${counter}`;
}

/** A stable unique id for the life of a component. */
export function useUid(prefix) {
  const ref = useRef(null);
  if (ref.current === null) ref.current = uid(prefix);
  return ref.current;
}

const FOCUSABLE = [
  'a[href]', 'area[href]', 'button:not([disabled])', 'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])', 'textarea:not([disabled])', 'iframe', 'summary',
  '[tabindex]:not([tabindex="-1"])', '[contenteditable="true"]',
].join(',');

/** The elements inside *root* that Tab reaches, in order. */
export function focusables(root) {
  if (!root) return [];
  return [...root.querySelectorAll(FOCUSABLE)].filter(
    (el) => !el.closest('[inert]') && !el.hasAttribute('hidden') && el.getClientRects().length > 0,
  );
}

/**
 * Keep Tab and Shift+Tab inside *root* (a modal). Call from a keydown handler;
 * returns true when it moved the focus.
 */
export function trapTab(event, root) {
  if (event.key !== 'Tab') return false;
  const items = focusables(root);
  if (!items.length) {
    event.preventDefault();
    root.focus();
    return true;
  }
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement;
  if (event.shiftKey && (active === first || !root.contains(active))) {
    event.preventDefault();
    last.focus();
    return true;
  }
  if (!event.shiftKey && (active === last || !root.contains(active))) {
    event.preventDefault();
    first.focus();
    return true;
  }
  return false;
}

/** Call *handler* on a pointer press outside every element of *refs* while *active*. */
export function useOutsidePress(refs, handler, active) {
  const saved = useRef(handler);
  saved.current = handler;
  useEffect(() => {
    if (!active) return undefined;
    const onDown = (event) => {
      const inside = refs.some((r) => r.current && r.current.contains(event.target));
      if (!inside) saved.current(event);
    };
    document.addEventListener('pointerdown', onDown, true);
    return () => document.removeEventListener('pointerdown', onDown, true);
  }, [active]);
}

/** Call *handler* on Escape anywhere while *active*. */
export function useEscape(handler, active) {
  const saved = useRef(handler);
  saved.current = handler;
  useEffect(() => {
    if (!active) return undefined;
    const onKey = (event) => {
      if (event.key === 'Escape' && !event.defaultPrevented) saved.current(event);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [active]);
}

/**
 * Place *floating* next to *anchor* (below, aligned left; above or shifted
 * when it would leave the viewport), through CSS custom properties only.
 * Pass a point `{x, y}` as *anchor* for a context menu.
 */
export function placeFloating(floating, anchor, { gap = 4, prefer = 'below' } = {}) {
  if (!floating) return;
  const rect = anchor instanceof Element
    ? anchor.getBoundingClientRect()
    : { left: anchor.x, right: anchor.x, top: anchor.y, bottom: anchor.y, width: 0, height: 0 };
  const box = floating.getBoundingClientRect();
  const vw = document.documentElement.clientWidth;
  const vh = document.documentElement.clientHeight;
  let top = prefer === 'above' ? rect.top - box.height - gap : rect.bottom + gap;
  if (top + box.height > vh - 8 && rect.top - box.height - gap >= 8) top = rect.top - box.height - gap;
  if (top < 8 && rect.bottom + gap + box.height <= vh - 8) top = rect.bottom + gap;
  let left = rect.left;
  if (left + box.width > vw - 8) left = Math.max(8, vw - 8 - box.width);
  floating.style.setProperty('--cx-float-x', `${Math.round(left)}px`);
  floating.style.setProperty('--cx-float-y', `${Math.round(Math.max(8, top))}px`);
}

/** Copy *text* to the clipboard; resolves to true when it worked. */
export async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fall through to the selection-based copy.
  }
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.className = 'cx-visually-hidden';
  document.body.append(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch {
    ok = false;
  }
  area.remove();
  return ok;
}

/** Offer *content* as a file download named *filename*. */
export function downloadFile(filename, content, type = 'application/json') {
  const blob = content instanceof Blob ? content : new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.rel = 'external';
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
