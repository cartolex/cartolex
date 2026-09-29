// SPDX-License-Identifier: MIT
/**
 * Table: a virtualised grid for long lists (10⁵ rows and more).
 *
 * Only the rows in view (and a few around them) are in the page. Rows are
 * keyed by `rowKey`, so an update keeps each row's element, the selection
 * (a set of keys), the active row and the scroll position: the row that was
 * at the top of the view (or the active row, when it was in view) stays where
 * it was.
 *
 * Keyboard (the grid is one tab stop; the sort buttons follow it):
 *   Up/Down          move the active row      Shift+Up/Down  extend the range
 *   PageUp/PageDown  a page up or down        Home/End       first or last row
 *   Space            select or unselect       Ctrl/Cmd+A     select every row
 *   Enter            open the active row      Escape         clear the selection
 *   Shift+F10, Menu  the rows' context menu
 * Mouse: click selects a row, Shift+click a range, Ctrl/Cmd+click adds or
 * removes a row, double click opens it, right click opens the menu.
 */
import { html, useEffect, useLayoutEffect, useMemo, useRef, useState } from '../core/preact.js';
import { formatNumber, locale, t } from '../core/i18n.js';
import { useUid } from '../core/dom.js';
import { EmptyState } from './empty-state.js';
import { ErrorCard } from './error-card.js';
import { Icon } from './icons.js';
import { Menu } from './menu.js';

const collators = new Map();
function collator() {
  const key = locale.value;
  if (!collators.has(key)) collators.set(key, new Intl.Collator(key, { numeric: true, sensitivity: 'base' }));
  return collators.get(key);
}

/** *rows* sorted by the column and direction of *sort* (stable; empty values last). */
export function sortRows(rows, sort, columns) {
  if (!sort || !sort.column) return rows;
  const column = columns.find((c) => c.id === sort.column);
  if (!column) return rows;
  const value = column.sortValue || ((row) => row[column.id]);
  const dir = sort.direction === 'descending' ? -1 : 1;
  const coll = collator();
  const decorated = rows.map((row, i) => [value(row), i, row]);
  decorated.sort((a, b) => {
    const [va, ia] = a;
    const [vb, ib] = b;
    const ea = va === null || va === undefined || va === '';
    const eb = vb === null || vb === undefined || vb === '';
    if (ea || eb) return ea === eb ? ia - ib : ea ? 1 : -1;
    const c = typeof va === 'number' && typeof vb === 'number' ? va - vb : coll.compare(String(va), String(vb));
    return c ? c * dir : ia - ib;
  });
  return decorated.map((d) => d[2]);
}

function useControlled(value, onChange, initial) {
  const [own, setOwn] = useState(initial);
  const controlled = value !== undefined;
  return [controlled ? value : own, (next) => {
    if (!controlled) setOwn(next);
    if (onChange) onChange(next);
  }];
}

/**
 * @param {object} props
 * @param {Array<{id: string, label: any, width?: string, align?: 'start'|'end',
 *   sortable?: boolean, numeric?: boolean, sortValue?: Function, render?: Function}>} props.columns
 * @param {Array<object>} props.rows
 * @param {(row: object) => string} [props.rowKey]
 * @param {string} props.label the grid's accessible name
 * @param {{column: string, direction: 'ascending'|'descending'}|null} [props.sort]
 * @param {Function} [props.onSortChange]
 * @param {'client'|'server'} [props.sortMode] 'server': rows arrive sorted
 * @param {Set<string>} [props.selection] selected row keys (controlled)
 * @param {Function} [props.onSelectionChange]
 * @param {(row: object) => void} [props.onActivate] Enter or double click
 * @param {(keys: string[]) => Array<object>} [props.rowMenu] the context menu's items
 * @param {(item: object, keys: string[]) => void} [props.onRowMenu]
 * @param {boolean} [props.loading]
 * @param {object} [props.error] an error model: shown instead of the rows
 * @param {any} [props.empty] shown when there are no rows (an EmptyState)
 * @param {number} [props.rowHeight] pixels
 * @param {'s'|'m'|'l'|'fill'} [props.size] the viewport's height ('fill': its container's)
 * @param {(range: {first: number, last: number}) => void} [props.onRange] the rows in view
 *   changed (a list paged on the server fetches them); rows not fetched yet are
 *   `{$pending: true}` and shown as placeholders
 * @param {(row: object|null) => void} [props.onActiveChange] the active row changed
 */
export function Table({
  columns, rows, rowKey = (row) => row.id, label, sort: sortProp, onSortChange,
  defaultSort = null, sortMode = 'client', selection: selectionProp, onSelectionChange,
  onActivate, rowMenu, onRowMenu, loading = false, error = null, onRetry, empty,
  rowHeight = 36, overscan = 6, size = 'm', class: cls = '', onRange, onActiveChange,
}) {
  const id = useUid('cx-grid');
  const scroller = useRef(null);
  const head = useRef(null);
  const [sort, setSort] = useControlled(sortProp, onSortChange, defaultSort);
  const [selection, setSelection] = useControlled(selectionProp, onSelectionChange, new Set());
  const [activeKey, setActiveKey] = useState(null);
  const [view, setView] = useState({ top: 0, height: 0 });
  const [menu, setMenu] = useState(null);
  const anchorKey = useRef(null);
  const lastView = useRef(null);
  const lastActiveIndex = useRef(0);
  const frame = useRef(0);

  const display = useMemo(
    () => (sortMode === 'client' ? sortRows(rows, sort, columns) : rows),
    [rows, sort && sort.column, sort && sort.direction, sortMode, locale.value],
  );
  const index = useMemo(() => {
    const map = new Map();
    display.forEach((row, i) => map.set(rowKey(row), i));
    return map;
  }, [display]);

  const headHeight = () => (head.current ? head.current.offsetHeight : rowHeight);
  const bodyHeight = () => Math.max(0, view.height - headHeight());

  // Follow the viewport's size.
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return undefined;
    const measure = () => setView({ top: el.scrollTop, height: el.clientHeight });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame.current);
    };
  }, [Boolean(error)]);

  // Keep the anchor row where it was when the rows change.
  const previous = useRef(display);
  useLayoutEffect(() => {
    if (previous.current === display) return;
    previous.current = display;
    const el = scroller.current;
    if (activeKey !== null && !index.has(activeKey)) {
      const i = Math.min(lastActiveIndex.current, display.length - 1);
      setActiveKey(i >= 0 ? rowKey(display[i]) : null);
    }
    if (!el || !lastView.current) return;
    const at = index.get(lastView.current.key);
    if (at !== undefined) {
      el.scrollTop = Math.max(0, at * rowHeight - lastView.current.offset);
      setView({ top: el.scrollTop, height: el.clientHeight });
    }
  }, [display]);

  const n = display.length;
  const first = Math.max(0, Math.floor(view.top / rowHeight) - overscan);
  const last = Math.min(n - 1, Math.ceil((view.top + bodyHeight()) / rowHeight) + overscan);
  const activeIndex = activeKey === null ? -1 : index.has(activeKey) ? index.get(activeKey) : -1;
  if (activeIndex >= 0) lastActiveIndex.current = activeIndex;

  // Tell a paged list which rows are in view, and the page which row is active.
  useEffect(() => {
    if (onRange && !loading && n) onRange({ first, last });
  }, [first, last, n, loading]);
  const activeRow = activeIndex >= 0 ? display[activeIndex] : null;
  useEffect(() => {
    if (onActiveChange) onActiveChange(activeRow);
  }, [activeKey, activeRow]);

  // After each render, remember which row anchors the view (the active row when in
  // view, else the top row) and where: the next change of rows puts it back there.
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el || !n) {
      lastView.current = null;
      return;
    }
    const top = el.scrollTop;
    const room = Math.max(0, el.clientHeight - headHeight());
    const inView = activeIndex >= 0 && activeIndex * rowHeight >= top
      && (activeIndex + 1) * rowHeight <= top + room;
    const anchor = inView ? activeIndex : Math.min(n - 1, Math.floor(top / rowHeight));
    lastView.current = { key: rowKey(display[anchor]), offset: anchor * rowHeight - top };
  });

  const onScroll = () => {
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => {
      const el = scroller.current;
      if (el) setView({ top: el.scrollTop, height: el.clientHeight });
    });
  };

  const reveal = (i) => {
    const el = scroller.current;
    if (!el) return;
    const top = i * rowHeight;
    const room = bodyHeight();
    let next = el.scrollTop;
    if (top < next) next = top;
    else if (top + rowHeight > next + room) next = top + rowHeight - room;
    if (next !== el.scrollTop) {
      el.scrollTop = next;
      setView({ top: el.scrollTop, height: el.clientHeight });
    }
  };

  const keyAt = (i) => rowKey(display[i]);
  const selectRange = (from, to) => {
    const [a, b] = from < to ? [from, to] : [to, from];
    const next = new Set();
    for (let i = a; i <= b; i += 1) next.add(keyAt(i));
    setSelection(next);
  };
  const moveTo = (i, { extend = false } = {}) => {
    if (!n) return;
    const target = Math.max(0, Math.min(n - 1, i));
    const key = keyAt(target);
    setActiveKey(key);
    if (extend) {
      const anchor = anchorKey.current !== null && index.has(anchorKey.current)
        ? index.get(anchorKey.current) : (activeIndex >= 0 ? activeIndex : target);
      if (anchorKey.current === null) anchorKey.current = keyAt(anchor);
      selectRange(anchor, target);
    } else {
      anchorKey.current = key;
    }
    reveal(target);
  };
  const toggle = (key) => {
    const next = new Set(selection);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setSelection(next);
  };
  const selectedKeys = () => display.map(rowKey).filter((k) => selection.has(k));
  const openMenu = (point) => {
    if (!rowMenu) return;
    const keys = selectedKeys();
    setMenu({ point, keys, items: rowMenu(keys) });
  };

  const onKeyDown = (event) => {
    if (event.target !== scroller.current) return;
    const page = Math.max(1, Math.floor(bodyHeight() / rowHeight) - 1);
    const cur = activeIndex >= 0 ? activeIndex : -1;
    const mod = event.ctrlKey || event.metaKey;
    switch (event.key) {
      case 'ArrowDown':
        moveTo(cur + 1, { extend: event.shiftKey });
        break;
      case 'ArrowUp':
        moveTo(cur < 0 ? 0 : cur - 1, { extend: event.shiftKey });
        break;
      case 'PageDown':
        moveTo(cur + page, { extend: event.shiftKey });
        break;
      case 'PageUp':
        moveTo(cur - page, { extend: event.shiftKey });
        break;
      case 'Home':
        moveTo(0, { extend: event.shiftKey });
        break;
      case 'End':
        moveTo(n - 1, { extend: event.shiftKey });
        break;
      case ' ':
        if (cur >= 0) {
          if (event.shiftKey && anchorKey.current !== null && index.has(anchorKey.current)) {
            selectRange(index.get(anchorKey.current), cur);
          } else {
            toggle(keyAt(cur));
            anchorKey.current = keyAt(cur);
          }
        }
        break;
      case 'Enter':
        if (cur >= 0 && onActivate) onActivate(display[cur]);
        break;
      case 'Escape':
        if (!selection.size) return;
        setSelection(new Set());
        break;
      case 'a':
      case 'A':
        if (!mod) return;
        setSelection(new Set(display.map(rowKey)));
        break;
      case 'F10':
        if (!event.shiftKey) return;
        openMenuAtActive();
        break;
      case 'ContextMenu':
        openMenuAtActive();
        break;
      default:
        return;
    }
    event.preventDefault();
  };
  function openMenuAtActive() {
    if (!rowMenu) return;
    const el = scroller.current;
    const row = activeIndex >= 0 && el.querySelector(`#${CSS.escape(`${id}-row-${activeIndex}`)}`);
    const rect = (row || el).getBoundingClientRect();
    if (activeIndex >= 0 && !selection.has(activeKey)) setSelection(new Set([activeKey]));
    const keys = activeIndex >= 0 && !selection.has(activeKey) ? [activeKey] : selectedKeys();
    setMenu({ point: { x: rect.left + 16, y: rect.bottom }, keys, items: rowMenu(keys) });
  }

  const onRowClick = (event, i) => {
    const key = keyAt(i);
    setActiveKey(key);
    if (event.shiftKey && anchorKey.current !== null && index.has(anchorKey.current)) {
      selectRange(index.get(anchorKey.current), i);
    } else if (event.ctrlKey || event.metaKey) {
      toggle(key);
      anchorKey.current = key;
    } else {
      setSelection(new Set([key]));
      anchorKey.current = key;
    }
    if (scroller.current && document.activeElement !== scroller.current) {
      scroller.current.focus({ preventScroll: true });
    }
  };
  const onRowMenuEvent = (event, i) => {
    if (!rowMenu) return;
    event.preventDefault();
    const key = keyAt(i);
    setActiveKey(key);
    let keys = selectedKeys();
    if (!selection.has(key)) {
      setSelection(new Set([key]));
      anchorKey.current = key;
      keys = [key];
    }
    if (scroller.current) scroller.current.focus({ preventScroll: true });
    setMenu({ point: { x: event.clientX, y: event.clientY }, keys, items: rowMenu(keys) });
  };

  const onHeaderSort = (column) => {
    const direction = sort && sort.column === column.id && sort.direction === 'ascending'
      ? 'descending' : 'ascending';
    setSort({ column: column.id, direction });
  };

  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  const template = columns.map((c) => c.width || 'minmax(8rem, 1fr)').join(' ');
  const selectedCount = selection.size ? display.reduce((c, r) => c + (selection.has(rowKey(r)) ? 1 : 0), 0) : 0;

  if (error) {
    return html`<div class=${`cx-table cx-table--error ${cls}`}>
      <${ErrorCard} error=${error} onRetry=${onRetry} compact />
    </div>`;
  }

  const visible = [];
  if (!loading) {
    for (let i = first; i <= last; i += 1) {
      const row = display[i];
      const key = rowKey(row);
      const selected = selection.has(key);
      visible.push(html`<div role="row" key=${key} id=${`${id}-row-${i}`} aria-rowindex=${i + 2}
        aria-selected=${String(selected)}
        class=${`cx-table__row ${selected ? 'is-selected' : ''} ${key === activeKey ? 'is-active' : ''}`}
        style=${{ '--cx-row-y': `${i * rowHeight}px` }}
        onClick=${(e) => onRowClick(e, i)}
        onDblClick=${() => onActivate && onActivate(row)}
        onContextMenu=${(e) => onRowMenuEvent(e, i)}>
        ${columns.map((c) => html`<div role="gridcell" key=${c.id}
          class=${`cx-table__cell ${c.align === 'end' || c.numeric ? 'cx-table__cell--end' : ''}`}>
          ${row.$pending ? html`<span class="cx-skeleton__line" aria-hidden="true"></span>`
            : c.render ? c.render(row) : c.numeric ? formatNumber(row[c.id]) : row[c.id]}
        </div>`)}
      </div>`);
    }
  }

  return html`<div class=${`cx-table cx-table--${size} ${!loading && !n ? 'is-empty' : ''} ${cls}`}
    style=${{ '--cx-table-columns': template, '--cx-table-row': `${rowHeight}px` }}>
    <div ref=${scroller} class="cx-table__scroller" role="grid" id=${id} tabindex="0"
      aria-label=${label} aria-rowcount=${loading ? -1 : n + 1} aria-colcount=${columns.length}
      aria-multiselectable="true" aria-busy=${loading ? 'true' : undefined}
      aria-activedescendant=${activeIndex >= first && activeIndex <= last && !loading
        ? `${id}-row-${activeIndex}` : undefined}
      onScroll=${onScroll} onKeyDown=${onKeyDown}>
      <div role="rowgroup" class="cx-table__head" ref=${head}>
        <div role="row" aria-rowindex="1" class="cx-table__row cx-table__row--head">
          ${columns.map((c) => {
            const on = sort && sort.column === c.id;
            const ariaSort = c.sortable ? (on ? sort.direction : 'none') : undefined;
            return html`<div role="columnheader" key=${c.id} aria-sort=${ariaSort}
              class=${`cx-table__cell cx-table__cell--head ${c.align === 'end' || c.numeric ? 'cx-table__cell--end' : ''}`}>
              ${c.sortable ? html`<button type="button" class="cx-table__sort"
                onClick=${() => onHeaderSort(c)}>
                <span>${c.label}</span>
                <${Icon} name=${on ? (sort.direction === 'descending' ? 'sort-desc' : 'sort-asc') : 'sort-none'} />
              </button>` : html`<span>${c.label}</span>`}
            </div>`;
          })}
        </div>
      </div>
      <div role="rowgroup" class="cx-table__body"
        style=${{ '--cx-table-height': `${(loading ? 6 : n) * rowHeight}px` }}>
        ${loading ? [0, 1, 2, 3, 4, 5].map((i) => html`<div class="cx-table__row cx-table__row--skeleton"
            role="row" aria-hidden="true" key=${`s${i}`} style=${{ '--cx-row-y': `${i * rowHeight}px` }}>
            ${columns.map((c) => html`<div role="gridcell" key=${c.id} class="cx-table__cell">
              <span class="cx-skeleton__line"></span></div>`)}
          </div>`) : visible}
      </div>
    </div>
    ${!loading && !n ? html`<div class="cx-table__empty">${empty || html`<${EmptyState}
      title=${t('table.empty')} />`}</div>` : null}
    <p class="cx-table__status" aria-live="polite">
      ${loading ? t('common.loading') : t('table.rows', { count: n })}
      ${selectedCount ? html`${' · '}${t('table.selected', { count: selectedCount })}` : null}
    </p>
    ${menu ? html`<${Menu} items=${menu.items} label=${t('table.menu')} anchor=${menu.point}
      onSelect=${(item) => onRowMenu && onRowMenu(item, menu.keys)}
      onClose=${(reason) => {
        setMenu(null);
        if (reason !== 'outside' && scroller.current) scroller.current.focus({ preventScroll: true });
      }} />` : null}
  </div>`;
}
