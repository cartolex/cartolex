// SPDX-License-Identifier: MIT
/**
 * Tabs: one tab stop for the tab list; Left/Right (and Home/End) move between
 * tabs and select them (automatic activation), or only move the focus with
 * `manual` activation (Enter or Space then selects). The panel follows.
 */
import { html, useRef } from '../core/preact.js';
import { useUid } from '../core/dom.js';

/**
 * @param {object} props
 * @param {Array<{id: string, label: any, disabled?: boolean, count?: any}>} props.tabs
 * @param {string} props.selected the selected tab's id
 * @param {(id: string) => void} props.onSelect
 * @param {string} props.label the tab list's accessible name
 * @param {boolean} [props.manual] focus moves without selecting
 * @param {(id: string) => any} props.panel renders the selected tab's panel
 */
export function Tabs({ tabs, selected, onSelect, label, manual = false, panel, class: cls = '' }) {
  const base = useUid('cx-tabs');
  const list = useRef(null);
  const enabled = tabs.filter((tab) => !tab.disabled);

  const focusTab = (id) => {
    const el = list.current && list.current.querySelector(`[data-tab="${CSS.escape(id)}"]`);
    if (el) el.focus();
    if (!manual) onSelect(id);
  };
  const onKeyDown = (event) => {
    const current = event.target.getAttribute('data-tab');
    const index = enabled.findIndex((tab) => tab.id === current);
    if (index < 0) return;
    let next = null;
    if (event.key === 'ArrowRight') next = enabled[(index + 1) % enabled.length];
    else if (event.key === 'ArrowLeft') next = enabled[(index - 1 + enabled.length) % enabled.length];
    else if (event.key === 'Home') [next] = enabled;
    else if (event.key === 'End') next = enabled[enabled.length - 1];
    else if (manual && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault();
      onSelect(current);
      return;
    }
    if (next) {
      event.preventDefault();
      focusTab(next.id);
    }
  };

  return html`<div class=${`cx-tabs ${cls}`}>
    <div class="cx-tabs__list" role="tablist" aria-label=${label} ref=${list} onKeyDown=${onKeyDown}>
      ${tabs.map((tab) => {
        const on = tab.id === selected;
        return html`<button type="button" role="tab" key=${tab.id} data-tab=${tab.id}
          id=${`${base}-tab-${tab.id}`} aria-selected=${String(on)}
          aria-controls=${`${base}-panel`} tabindex=${on ? '0' : '-1'} disabled=${tab.disabled}
          class=${`cx-tabs__tab ${on ? 'is-selected' : ''}`}
          onClick=${() => onSelect(tab.id)}>
          <span>${tab.label}</span>
          ${tab.count !== undefined ? html`<span class="cx-tabs__count">${tab.count}</span>` : null}
        </button>`;
      })}
    </div>
    <div class="cx-tabs__panel" role="tabpanel" id=${`${base}-panel`}
      aria-labelledby=${`${base}-tab-${selected}`} tabindex="0">
      ${panel(selected)}
    </div>
  </div>`;
}
