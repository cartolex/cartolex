// SPDX-License-Identifier: MIT
/**
 * TreeView: a virtualised tree for long outlines (thousands of rows).
 *
 * The caller gives the visible rows, flattened in order (`rows`), each with
 * its `key`, `level` (1 = top), whether it can be expanded and is, its place
 * among its siblings, its parent's key and its text (for type-ahead). Only the
 * rows in view are in the page; the tree is one tab stop and names the active
 * row with `aria-activedescendant`.
 *
 * Keyboard:
 *   Up/Down        the previous or next row     Shift+Up/Down  extend the selection
 *   Right          expand, or go to the first child
 *   Left           collapse, or go to the parent
 *   Home/End       the first or last row        PageUp/PageDown  a page
 *   a letter       the next row starting with the letters typed
 *   Space          select or unselect (rows marked `multi`)
 *   Ctrl/Cmd+A     select every `multi` row next to the active one (same parent)
 *   Enter          open the active row          F2  rename it
 *   Delete         the rows' delete action      Shift+F10, Menu  the context menu
 *   Escape         clear the selection
 * Mouse: click selects, Shift+click selects a range, Ctrl/Cmd+click adds a row,
 * double click opens, right click opens the menu. Rows may be dragged and
 * dropped on other rows (`dragData`, `canDrop`, `onDrop`): only a shortcut,
 * every move has a menu item too.
 */
import { html, useEffect, useLayoutEffect, useMemo, useRef, useState } from '../core/preact.js';
import { useUid } from '../core/dom.js';
import { Menu } from './menu.js';

/** The MIME type of what a TreeView drags (JSON). */
export const DRAG_TYPE = 'application/x-cartolex-tree';

/**
 * @param {object} props
 * @param {Array<{key: string, level: number, expandable?: boolean, expanded?: boolean,
 *   setsize?: number, posinset?: number, parent?: string|null, text?: string,
 *   multi?: boolean}>} props.rows the visible rows, in order
 * @param {(row: object, state: {active: boolean, selected: boolean}) => any} props.renderRow
 * @param {string} props.label the tree's accessible name
 * @param {string|null} props.activeKey the active row (controlled)
 * @param {(key: string) => void} props.onActiveChange
 * @param {Set<string>} props.selection the selected rows (controlled)
 * @param {(keys: Set<string>) => void} props.onSelectionChange
 * @param {(row: object, expanded: boolean) => void} [props.onToggle]
 * @param {(row: object) => void} [props.onOpen] Enter or double click
 * @param {(row: object) => void} [props.onRename] F2
 * @param {(keys: string[]) => void} [props.onDelete] Delete
 * @param {(keys: string[], row: object) => Array<object>} [props.rowMenu] the context menu
 * @param {(item: object, keys: string[]) => void} [props.onRowMenu]
 * @param {(keys: string[], row: object) => any} [props.dragData] what dragging these rows carries
 * @param {(row: object, data: any) => boolean} [props.canDrop]
 * @param {(row: object, data: any) => void} [props.onDrop]
 * @param {(event: KeyboardEvent, row: object|null) => boolean} [props.onKeyCommand] a key of the
 *   caller's own (a letter for an action): return true when it was handled
 * @param {number} [props.rowHeight] pixels
 * @param {any} [props.empty] shown when there is no row
 */
export function TreeView({
  rows, renderRow, label, activeKey, onActiveChange, selection, onSelectionChange, onToggle,
  onOpen, onRename, onDelete, rowMenu, onRowMenu, dragData, canDrop, onDrop, onKeyCommand, rowHeight = 32,
  overscan = 8, empty = null, class: cls = '', treeRef,
}) {
  const id = useUid('cx-tree');
  const scroller = useRef(null);
  const [view, setView] = useState({ top: 0, height: 0 });
  const [menu, setMenu] = useState(null);
  const [dropKey, setDropKey] = useState(null);
  const anchor = useRef(null);
  const typed = useRef({ text: '', at: 0 });
  const frame = useRef(0);
  const dragging = useRef(null);

  const index = useMemo(() => {
    const map = new Map();
    rows.forEach((row, i) => map.set(row.key, i));
    return map;
  }, [rows]);
  const n = rows.length;
  const activeIndex = activeKey !== null && index.has(activeKey) ? index.get(activeKey) : -1;

  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return undefined;
    if (treeRef) treeRef.current = el;
    const measure = () => setView({ top: el.scrollTop, height: el.clientHeight });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame.current);
    };
  }, []);

  // Keep the active row in view when it changes from outside (a search, a click on the map).
  const lastActive = useRef(activeKey);
  useLayoutEffect(() => {
    if (lastActive.current !== activeKey && activeIndex >= 0) reveal(activeIndex);
    lastActive.current = activeKey;
  }, [activeKey, activeIndex]);

  const onScroll = () => {
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => {
      const el = scroller.current;
      if (el) setView({ top: el.scrollTop, height: el.clientHeight });
    });
  };
  function reveal(i) {
    const el = scroller.current;
    if (!el) return;
    const top = i * rowHeight;
    let next = el.scrollTop;
    if (top < next) next = top;
    else if (top + rowHeight > next + el.clientHeight) next = top + rowHeight - el.clientHeight;
    if (next !== el.scrollTop) {
      el.scrollTop = next;
      setView({ top: el.scrollTop, height: el.clientHeight });
    }
  }

  const select = (keys) => onSelectionChange(new Set(keys));
  const moveTo = (i, { extend = false } = {}) => {
    if (!n) return;
    const target = Math.max(0, Math.min(n - 1, i));
    const row = rows[target];
    onActiveChange(row.key);
    if (extend && row.multi) {
      const from = anchor.current !== null && index.has(anchor.current) ? index.get(anchor.current) : target;
      const [a, b] = from < target ? [from, target] : [target, from];
      select(rows.slice(a, b + 1).filter((r) => r.multi).map((r) => r.key));
    } else if (!extend) {
      anchor.current = row.key;
      select([row.key]);
    }
    reveal(target);
  };
  const selectedKeys = () => rows.filter((r) => selection.has(r.key)).map((r) => r.key);
  const keysFor = (row) => (selection.has(row.key) ? selectedKeys() : [row.key]);

  function openMenuAt(row, point) {
    if (!rowMenu || !row) return;
    const keys = keysFor(row);
    if (!selection.has(row.key)) {
      select([row.key]);
      anchor.current = row.key;
    }
    const items = rowMenu(keys, row);
    if (items && items.length) setMenu({ point, keys, items });
  }

  const typeAhead = (key) => {
    const now = Date.now();
    typed.current = { text: (now - typed.current.at < 700 ? typed.current.text : '') + key, at: now };
    const prefix = typed.current.text.toLocaleLowerCase();
    const start = activeIndex;
    const single = prefix.length === 1;
    for (let k = single ? 1 : 0; k <= n; k += 1) {
      const row = rows[(start + k + n) % n];
      if (row && String(row.text || '').toLocaleLowerCase().startsWith(prefix)) {
        moveTo(index.get(row.key));
        return;
      }
    }
  };

  const onKeyDown = (event) => {
    if (event.target !== scroller.current) return;
    const page = Math.max(1, Math.floor(view.height / rowHeight) - 1);
    const cur = activeIndex;
    const row = cur >= 0 ? rows[cur] : null;
    const mod = event.ctrlKey || event.metaKey;
    if (onKeyCommand && onKeyCommand(event, row)) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }
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
      case 'ArrowRight':
        if (!row) return;
        if (row.expandable && !row.expanded) {
          if (onToggle) onToggle(row, true);
        } else if (row.expandable && cur + 1 < n && rows[cur + 1].parent === row.key) moveTo(cur + 1);
        break;
      case 'ArrowLeft':
        if (!row) return;
        if (row.expandable && row.expanded) {
          if (onToggle) onToggle(row, false);
        } else if (row.parent && index.has(row.parent)) moveTo(index.get(row.parent));
        break;
      case ' ':
        if (!row) return;
        if (row.multi) {
          if (event.shiftKey && anchor.current !== null && index.has(anchor.current)) {
            const from = index.get(anchor.current);
            const [a, b] = from < cur ? [from, cur] : [cur, from];
            select(rows.slice(a, b + 1).filter((r) => r.multi).map((r) => r.key));
          } else {
            const next = new Set(selection);
            if (next.has(row.key)) next.delete(row.key);
            else next.add(row.key);
            onSelectionChange(next);
            anchor.current = row.key;
          }
        } else {
          select([row.key]);
        }
        break;
      case 'Enter':
        if (row && onOpen) onOpen(row);
        break;
      case 'F2':
        if (row && onRename) onRename(row);
        break;
      case 'Delete':
        if (row && onDelete) onDelete(keysFor(row));
        break;
      case 'Escape':
        if (selection.size <= 1) return;
        select(row ? [row.key] : []);
        break;
      case 'F10':
        if (!event.shiftKey) return;
        openMenuAtActive();
        break;
      case 'ContextMenu':
        openMenuAtActive();
        break;
      default:
        if (mod && (event.key === 'a' || event.key === 'A')) {
          if (!row || !row.multi) return;
          select(rows.filter((r) => r.multi && r.parent === row.parent).map((r) => r.key));
          break;
        }
        if (!mod && !event.altKey && event.key.length === 1 && /\S/.test(event.key)) {
          typeAhead(event.key);
          break;
        }
        return;
    }
    event.preventDefault();
    event.stopPropagation();
  };
  function openMenuAtActive() {
    if (activeIndex < 0) return;
    const el = scroller.current;
    const rowEl = el.querySelector(`#${CSS.escape(`${id}-row-${activeIndex}`)}`);
    const rect = (rowEl || el).getBoundingClientRect();
    openMenuAt(rows[activeIndex], { x: rect.left + 24, y: rect.bottom });
  }

  const onRowClick = (event, i) => {
    const row = rows[i];
    onActiveChange(row.key);
    if (event.shiftKey && row.multi && anchor.current !== null && index.has(anchor.current)) {
      const from = index.get(anchor.current);
      const [a, b] = from < i ? [from, i] : [i, from];
      select(rows.slice(a, b + 1).filter((r) => r.multi).map((r) => r.key));
    } else if ((event.ctrlKey || event.metaKey) && row.multi) {
      const next = new Set(selection);
      if (next.has(row.key)) next.delete(row.key);
      else next.add(row.key);
      onSelectionChange(next);
      anchor.current = row.key;
    } else {
      select([row.key]);
      anchor.current = row.key;
    }
    if (scroller.current && document.activeElement !== scroller.current) {
      scroller.current.focus({ preventScroll: true });
    }
  };

  // Drag and drop (a shortcut for « Move to… »).
  const onDragStart = (event, i) => {
    const row = rows[i];
    const data = dragData ? dragData(keysFor(row), row) : null;
    if (!data) {
      event.preventDefault();
      return;
    }
    dragging.current = data;
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData(DRAG_TYPE, JSON.stringify(data));
    event.dataTransfer.setData('text/plain', String(row.text || row.key));
  };
  const dropData = (event) => {
    if (dragging.current) return dragging.current;
    try {
      return JSON.parse(event.dataTransfer.getData(DRAG_TYPE));
    } catch {
      return null;
    }
  };
  const onDragOver = (event, i) => {
    const types = event.dataTransfer ? [...event.dataTransfer.types] : [];
    if (!canDrop || !types.includes(DRAG_TYPE)) return;
    const data = dragging.current || TreeView.shared;
    if (data && !canDrop(rows[i], data)) {
      event.dataTransfer.dropEffect = 'none';
      if (dropKey !== null) setDropKey(null);
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    if (dropKey !== rows[i].key) setDropKey(rows[i].key);
  };
  const onDropRow = (event, i) => {
    event.preventDefault();
    setDropKey(null);
    const data = dropData(event);
    dragging.current = null;
    if (data && onDrop && (!canDrop || canDrop(rows[i], data))) onDrop(rows[i], data);
  };
  const onDragEnd = () => {
    dragging.current = null;
    TreeView.shared = null;
    setDropKey(null);
  };

  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  const first = Math.max(0, Math.floor(view.top / rowHeight) - overscan);
  const last = Math.min(n - 1, Math.ceil((view.top + view.height) / rowHeight) + overscan);
  const visible = [];
  for (let i = first; i <= last; i += 1) {
    const row = rows[i];
    const active = row.key === activeKey;
    const selected = selection.has(row.key);
    const draggable = Boolean(dragData && row.draggable !== false);
    visible.push(html`<div role="treeitem" key=${row.key} id=${`${id}-row-${i}`}
      aria-level=${row.level} aria-setsize=${row.setsize} aria-posinset=${row.posinset}
      aria-expanded=${row.expandable ? String(Boolean(row.expanded)) : undefined}
      aria-selected=${String(selected)}
      class=${`cx-tree__row ${active ? 'is-active' : ''} ${selected ? 'is-selected' : ''} ${
        dropKey === row.key ? 'is-drop' : ''} ${row.class || ''}`}
      style=${{ '--cx-row-y': `${i * rowHeight}px`, '--cx-tree-level': row.level }}
      draggable=${draggable ? 'true' : undefined}
      onDragStart=${draggable ? (e) => {
        TreeView.shared = dragData(keysFor(row), row);
        onDragStart(e, i);
      } : undefined}
      onDragEnd=${draggable ? onDragEnd : undefined}
      onDragOver=${(e) => onDragOver(e, i)}
      onDragLeave=${() => dropKey === row.key && setDropKey(null)}
      onDrop=${(e) => onDropRow(e, i)}
      onClick=${(e) => onRowClick(e, i)}
      onDblClick=${() => onOpen && onOpen(row)}
      onContextMenu=${(e) => {
        e.preventDefault();
        onActiveChange(row.key);
        if (scroller.current) scroller.current.focus({ preventScroll: true });
        openMenuAt(row, { x: e.clientX, y: e.clientY });
      }}>
      ${row.expandable ? html`<span class="cx-tree__twisty" aria-hidden="true"
        onClick=${(e) => {
          e.stopPropagation();
          if (onToggle) onToggle(row, !row.expanded);
        }}></span>` : html`<span class="cx-tree__twisty cx-tree__twisty--leaf" aria-hidden="true"></span>`}
      ${renderRow(row, { active, selected })}
    </div>`);
  }

  return html`<div class=${`cx-tree ${cls}`} style=${{ '--cx-tree-row': `${rowHeight}px` }}>
    <div ref=${scroller} class="cx-tree__scroller" role="tree" id=${id} tabindex="0"
      aria-label=${label} aria-multiselectable="true"
      aria-activedescendant=${activeIndex >= first && activeIndex <= last ? `${id}-row-${activeIndex}` : undefined}
      onScroll=${onScroll} onKeyDown=${onKeyDown}
      onFocus=${() => {
        if (activeIndex < 0 && n) onActiveChange(rows[0].key);
      }}>
      <div class="cx-tree__body" style=${{ '--cx-tree-height': `${n * rowHeight}px` }}>
        ${visible}
      </div>
    </div>
    ${!n && empty ? html`<div class="cx-tree__empty">${empty}</div>` : null}
    ${menu ? html`<${Menu} items=${menu.items} label=${label} anchor=${menu.point}
      onSelect=${(item) => onRowMenu && onRowMenu(item, menu.keys)}
      onClose=${(reason) => {
        setMenu(null);
        if (reason !== 'outside' && reason !== 'tab' && scroller.current) {
          scroller.current.focus({ preventScroll: true });
        }
      }} />` : null}
  </div>`;
}

/** What is being dragged from any TreeView, for drop targets outside it (a treemap). */
TreeView.shared = null;
