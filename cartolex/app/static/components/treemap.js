// SPDX-License-Identifier: MIT
/**
 * Treemap: nested rectangles, one per node of a tree, sized by its weight.
 *
 * The rectangles are laid out by the squarified algorithm (Bruls, Huizing and
 * van Wijk), each node inside its parent, below a strip that holds the
 * parent's label when there is room. The top level is coloured by hue family
 * (the `--cx-hue-<k>` tokens, lighter at the top, deeper below); the accent
 * outlines the selection only. Labels that do not fit are cut with an
 * ellipsis, or left out on rectangles too small to read.
 *
 * The treemap is one tab stop. Keyboard: arrows move the selection among
 * siblings (Up and Down to the parent's or the first child's level: Down
 * enters the selected node's children, Up goes to its parent), Enter zooms
 * into the selected node, Escape or Backspace zooms out one level. A click
 * selects; a double click zooms. Rows dragged from a TreeView may be dropped
 * on a rectangle (`canDrop`, `onDrop`).
 */
import { html, useLayoutEffect, useMemo, useRef, useState } from '../core/preact.js';
import { useUid } from '../core/dom.js';
import { DRAG_TYPE, TreeView } from './tree-view.js';
import { layoutTree } from './treemap-layout.js';

export { layoutTree, squarify } from './treemap-layout.js';

/** How many hue tokens the style sheet defines (`--cx-hue-1` … `--cx-hue-12`). */
export const HUES = 12;

/**
 * @param {object} props
 * @param {string|null} props.root the node whose children fill the treemap (null: the top level)
 * @param {(id: string|null) => string[]} props.childrenOf children in order
 * @param {(id: string) => number} props.weightOf a node's weight
 * @param {(id: string) => number} [props.ownWeightOf] the weight of a node's own keywords
 * @param {(id: string) => number} props.hueOf the hue family (0-based, top-level rank)
 * @param {(id: string) => string} props.nameOf
 * @param {(id: string) => string} [props.detailOf] a short text after the name (a share)
 * @param {(id: string) => (string|null)} props.parentOf
 * @param {string|null} props.selected
 * @param {(id: string) => void} props.onSelect
 * @param {(id: string|null) => void} props.onZoom
 * @param {Set<string>} [props.marked] nodes to mark (search matches)
 * @param {string} props.label the treemap's accessible name
 * @param {string} [props.status] the selection, said to assistive technology
 * @param {(id: string, data: any) => boolean} [props.canDrop]
 * @param {(id: string, data: any) => void} [props.onDrop]
 */
export function Treemap({
  root = null, childrenOf, weightOf, ownWeightOf, hueOf, nameOf, detailOf, parentOf, selected,
  onSelect, onZoom, marked, label, status = '', canDrop, onDrop, class: cls = '',
}) {
  const box = useRef(null);
  const id = useUid('cx-treemap');
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [dropId, setDropId] = useState(null);

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return undefined;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const cells = useMemo(
    () => (size.w && size.h ? layoutTree({ root, childrenOf, weightOf, ownWeightOf, width: size.w, height: size.h }) : []),
    [root, childrenOf, weightOf, ownWeightOf, size.w, size.h],
  );

  const siblings = (nid) => childrenOf(parentOf(nid));
  const onKeyDown = (event) => {
    const current = selected && cells.some((c) => c.id === selected) ? selected : null;
    let next = null;
    switch (event.key) {
      case 'ArrowRight':
      case 'ArrowLeft': {
        const list = current ? siblings(current) : childrenOf(root);
        const i = current ? list.indexOf(current) : -1;
        const step = event.key === 'ArrowRight' ? 1 : -1;
        next = list[(i + step + list.length) % list.length] || null;
        break;
      }
      case 'ArrowDown':
        next = current ? (childrenOf(current)[0] || null) : (childrenOf(root)[0] || null);
        break;
      case 'ArrowUp':
        next = current && parentOf(current) !== root ? parentOf(current) : null;
        break;
      case 'Enter':
        if (current && childrenOf(current).length) onZoom(current);
        event.preventDefault();
        return;
      case 'Escape':
      case 'Backspace':
        if (root === null) return;
        onZoom(parentOf(root));
        event.preventDefault();
        return;
      default:
        return;
    }
    event.preventDefault();
    if (next) onSelect(next);
  };

  const onDragOver = (event, cell) => {
    const types = event.dataTransfer ? [...event.dataTransfer.types] : [];
    if (!canDrop || !types.includes(DRAG_TYPE)) return;
    const target = cell.owner || cell.id;
    const data = TreeView.shared;
    if (data && !canDrop(target, data)) {
      if (dropId !== null) setDropId(null);
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = 'move';
    if (dropId !== target) setDropId(target);
  };
  const onDropCell = (event, cell) => {
    event.preventDefault();
    event.stopPropagation();
    setDropId(null);
    const target = cell.owner || cell.id;
    let data = TreeView.shared;
    if (!data) {
      try {
        data = JSON.parse(event.dataTransfer.getData(DRAG_TYPE));
      } catch {
        data = null;
      }
    }
    TreeView.shared = null;
    if (data && onDrop && (!canDrop || canDrop(target, data))) onDrop(target, data);
  };

  return html`<div class=${`cx-treemap ${cls}`}>
    <div ref=${box} class="cx-treemap__box" role="group" tabindex="0" aria-label=${label}
      aria-describedby=${`${id}-status`} onKeyDown=${onKeyDown}
      onDragLeave=${(e) => {
        if (!box.current.contains(e.relatedTarget)) setDropId(null);
      }}>
      ${cells.map((cell) => {
        const target = cell.owner || cell.id;
        const hue = (hueOf(target) % HUES) + 1;
        const on = !cell.own && cell.id === selected;
        return html`<div key=${cell.id} aria-hidden="true" data-node=${cell.own ? undefined : cell.id}
          class=${`cx-treemap__cell cx-treemap__cell--d${Math.min(cell.depth, 4)} ${
            cell.own ? 'cx-treemap__cell--own' : ''} ${on ? 'is-selected' : ''} ${
            cell.parent ? 'is-parent' : ''} ${marked && marked.has(target) ? 'is-marked' : ''} ${
            dropId === target ? 'is-drop' : ''}`}
          style=${{
            '--cx-tm-x': `${cell.x}px`, '--cx-tm-y': `${cell.y}px`,
            '--cx-tm-w': `${cell.w}px`, '--cx-tm-h': `${cell.h}px`,
            '--cx-tm-hue': `var(--cx-hue-${hue})`,
          }}
          onClick=${(e) => {
            e.stopPropagation();
            onSelect(target);
            box.current.focus({ preventScroll: true });
          }}
          onDblClick=${(e) => {
            e.stopPropagation();
            if (!cell.own && childrenOf(cell.id).length) onZoom(cell.id);
          }}
          onDragOver=${(e) => onDragOver(e, cell)} onDrop=${(e) => onDropCell(e, cell)}>
          ${cell.label ? html`<span class="cx-treemap__label">
            <span class="cx-treemap__name">${nameOf(cell.id)}</span>
            ${detailOf ? html`<span class="cx-treemap__detail">${detailOf(cell.id)}</span>` : null}
          </span>` : null}
        </div>`;
      })}
    </div>
    <p class="cx-visually-hidden" id=${`${id}-status`} aria-live="polite">${status}</p>
  </div>`;
}
