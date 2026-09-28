// SPDX-License-Identifier: MIT
/**
 * Menu, MenuButton and ContextMenuArea.
 *
 * A menu opens on its button (Enter, Space, ArrowDown: first item; ArrowUp:
 * last item) or, for a context menu, on a right click, Shift+F10 or the
 * Menu key. Inside: Up/Down move (and wrap), Home/End jump, a letter jumps to
 * the next item starting with it, Enter or Space chooses, Escape closes and
 * gives the focus back, Tab closes. Items can be plain, radio or checkbox
 * items, in labelled groups, with separators.
 *
 * Items: `{id, label, icon?, disabled?, danger?, kind?: 'item'|'radio'|
 * 'checkbox'|'separator'|'group', checked?, items? (a group's items)}`.
 */
import { html, useLayoutEffect, useRef, useState } from '../core/preact.js';
import { placeFloating, useOutsidePress, useUid } from '../core/dom.js';
import { Icon } from './icons.js';

function flatten(items) {
  const out = [];
  for (const item of items) {
    if (item.kind === 'group') out.push(...flatten(item.items || []));
    else if (item.kind !== 'separator') out.push(item);
  }
  return out;
}

const ROLES = { radio: 'menuitemradio', checkbox: 'menuitemcheckbox' };

/**
 * The open menu itself, placed next to *anchor* (an element or a point).
 * @param {object} props
 * @param {Array<object>} props.items
 * @param {(item: object) => void} props.onSelect
 * @param {(reason: string) => void} props.onClose reason: 'select', 'escape', 'tab', 'outside'
 * @param {Element|{x: number, y: number}} props.anchor
 * @param {string} [props.labelledBy] the id of the element naming the menu
 * @param {string} [props.label] the menu's name when no element names it
 * @param {'first'|'last'} [props.focus] which item has the focus first
 * @param {boolean} [props.inline] render in place (the gallery), not floating
 */
export function Menu({ items, onSelect, onClose, anchor, labelledBy, label, focus = 'first', id,
  inline = false, class: cls = '' }) {
  const ref = useRef(null);
  const typed = useRef({ text: '', at: 0 });
  const flat = flatten(items).filter((item) => !item.disabled);

  useLayoutEffect(() => {
    if (!inline && anchor) placeFloating(ref.current, anchor);
    if (!inline) {
      const all = [...ref.current.querySelectorAll('[role^="menuitem"]:not([aria-disabled="true"])')];
      const target = focus === 'last' ? all[all.length - 1] : all[0];
      (target || ref.current).focus();
    }
  }, []);
  useOutsidePress([ref], () => onClose && onClose('outside'), !inline);

  const move = (delta, to) => {
    const all = [...ref.current.querySelectorAll('[role^="menuitem"]:not([aria-disabled="true"])')];
    if (!all.length) return;
    const index = all.indexOf(document.activeElement);
    let next;
    if (to === 'first') next = 0;
    else if (to === 'last') next = all.length - 1;
    else next = (index + delta + all.length) % all.length;
    all[next].focus();
  };
  const choose = (item) => {
    if (!item || item.disabled) return;
    onSelect(item);
    if (onClose) onClose('select');
  };
  const onKeyDown = (event) => {
    const { key } = event;
    if (key === 'ArrowDown') move(1);
    else if (key === 'ArrowUp') move(-1);
    else if (key === 'Home') move(0, 'first');
    else if (key === 'End') move(0, 'last');
    else if (key === 'Escape') {
      if (onClose) onClose('escape');
    } else if (key === 'Tab') {
      if (onClose) onClose('tab');
      return;
    } else if (key === 'Enter' || key === ' ') {
      const current = document.activeElement && document.activeElement.dataset.item;
      choose(flat.find((item) => item.id === current));
    } else if (key.length === 1 && /\S/.test(key)) {
      const now = Date.now();
      typed.current = { text: (now - typed.current.at < 700 ? typed.current.text : '') + key, at: now };
      const all = [...ref.current.querySelectorAll('[role^="menuitem"]:not([aria-disabled="true"])')];
      const start = all.indexOf(document.activeElement);
      const prefix = typed.current.text.toLocaleLowerCase();
      for (let i = 1; i <= all.length; i += 1) {
        const el = all[(start + i) % all.length];
        if (el.textContent.trim().toLocaleLowerCase().startsWith(prefix)) {
          el.focus();
          break;
        }
      }
    } else {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
  };

  const renderItem = (item) => {
    if (item.kind === 'separator') {
      return html`<div role="separator" class="cx-menu__separator" key=${item.id}></div>`;
    }
    if (item.kind === 'group') {
      const gid = `${id || 'cx-menu'}-${item.id}`;
      return html`<div role="group" aria-labelledby=${gid} class="cx-menu__group" key=${item.id}>
        <div class="cx-menu__group-label" id=${gid} role="presentation">${item.label}</div>
        ${(item.items || []).map(renderItem)}
      </div>`;
    }
    const role = ROLES[item.kind] || 'menuitem';
    const checkable = role !== 'menuitem';
    return html`<div role=${role} key=${item.id} data-item=${item.id} tabindex="-1" lang=${item.lang}
      class=${`cx-menu__item ${item.danger ? 'cx-menu__item--danger' : ''}`}
      aria-disabled=${item.disabled ? 'true' : undefined}
      aria-checked=${checkable ? String(Boolean(item.checked)) : undefined}
      onClick=${() => choose(item)}
      onPointerMove=${(e) => {
        if (!item.disabled && document.activeElement !== e.currentTarget) e.currentTarget.focus();
      }}>
      <span class="cx-menu__check" aria-hidden="true">
        ${checkable && item.checked
          ? html`<${Icon} name=${item.kind === 'radio' ? 'dot' : 'check'} />` : null}
      </span>
      ${item.icon ? html`<${Icon} name=${item.icon} />` : null}
      <span class="cx-menu__label">${item.label}</span>
      ${item.hint ? html`<span class="cx-menu__hint">${item.hint}</span>` : null}
    </div>`;
  };

  return html`<div ref=${ref} id=${id} role="menu" tabindex="-1"
    class=${`cx-menu ${inline ? 'cx-menu--inline' : ''} ${cls}`}
    aria-labelledby=${labelledBy} aria-label=${labelledBy ? undefined : label}
    onKeyDown=${onKeyDown}>
    ${items.map(renderItem)}
  </div>`;
}

/**
 * A button that opens a menu.
 * @param {object} props
 * @param {any} props.label the button's text (or its accessible name with `icon` only)
 * @param {string} [props.icon] an icon; with `iconOnly` the label is not shown
 * @param {boolean} [props.iconOnly]
 * @param {Array<object>} props.items
 * @param {(item: object) => void} props.onSelect
 * @param {'secondary'|'ghost'|'primary'} [props.variant]
 */
export function MenuButton({ label, icon, iconOnly = false, items, onSelect, variant = 'secondary',
  size = 'm', class: cls = '' }) {
  const [open, setOpen] = useState(null);
  const button = useRef(null);
  const id = useUid('cx-menubutton');
  const close = (reason) => {
    setOpen(null);
    if (reason !== 'outside' && reason !== 'tab' && button.current) button.current.focus();
  };
  const onKeyDown = (event) => {
    if (event.key === 'ArrowDown' || event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      setOpen('first');
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setOpen('last');
    }
  };
  return html`<span class="cx-menubutton">
    <button ref=${button} type="button" id=${id}
      class=${`${iconOnly ? 'cx-icon-button' : 'cx-button'} cx-button--${variant} ${iconOnly
        ? `cx-icon-button--${size}` : `cx-button--${size}`} ${cls}`}
      aria-haspopup="menu" aria-expanded=${String(Boolean(open))}
      aria-controls=${open ? `${id}-menu` : undefined}
      aria-label=${iconOnly ? label : undefined}
      onClick=${() => setOpen(open ? null : 'first')} onKeyDown=${onKeyDown}>
      ${icon ? html`<${Icon} name=${icon} />` : null}
      ${iconOnly ? null : html`<span class="cx-button__label">${label}</span>`}
      ${iconOnly ? null : html`<${Icon} name="chevron-down" />`}
    </button>
    ${open ? html`<${Menu} id=${`${id}-menu`} items=${items} labelledBy=${id}
      anchor=${button.current} focus=${open} onSelect=${onSelect} onClose=${close} />` : null}
  </span>`;
}

/**
 * A region with a context menu: right click, Shift+F10 or the Menu key open
 * it at the pointer or at the focused element. `items` may be a function of
 * the element the menu was opened on.
 * @param {object} props
 * @param {Array<object>|((target: Element) => Array<object>)} props.items
 * @param {(item: object, target: Element) => void} props.onSelect
 * @param {string} props.label the menu's accessible name
 */
export function ContextMenuArea({ items, onSelect, label, class: cls = '', children }) {
  const [state, setState] = useState(null);
  const id = useUid('cx-context');
  const open = (target, point) => {
    setState({ target, point, returnTo: document.activeElement });
  };
  const onContextMenu = (event) => {
    event.preventDefault();
    open(event.target, { x: event.clientX, y: event.clientY });
  };
  const onKeyDown = (event) => {
    if ((event.key === 'F10' && event.shiftKey) || event.key === 'ContextMenu') {
      event.preventDefault();
      const rect = event.target.getBoundingClientRect();
      open(event.target, { x: rect.left + 8, y: rect.bottom });
    }
  };
  const close = (reason) => {
    const back = state && state.returnTo;
    setState(null);
    if (reason !== 'outside' && back && back.isConnected) back.focus();
  };
  const list = state ? (typeof items === 'function' ? items(state.target) : items) : [];
  return html`<div class=${`cx-context-area ${cls}`} onContextMenu=${onContextMenu}
    onKeyDown=${onKeyDown}>
    ${children}
    ${state ? html`<${Menu} id=${id} items=${list} label=${label} anchor=${state.point}
      onSelect=${(item) => onSelect(item, state.target)} onClose=${close} />` : null}
  </div>`;
}
