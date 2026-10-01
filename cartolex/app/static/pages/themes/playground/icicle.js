// SPDX-License-Identifier: MIT
/**
 * The previewed tree as an icicle: one column per level, each node's children beside it in
 * the next column, in one group whose total height is the parent's. A block's height follows
 * its keyword count, never below the room its label needs; a node's own keywords (on the node
 * itself) show as a band at the foot of its block and in its label (« N its own »). The top
 * level takes its hue family, the levels below tints of it. A parent of many children shows
 * its largest and « + N more » inside its own span. The whole icicle scrolls as one, so the
 * rows stay lined up; hovering or focusing a node highlights its path (its ancestors and its
 * descendants). A block opens its top keywords in a popover.
 *
 * Keyboard: one tab stop; ↑ and ↓ move within a level, ← and → to the parent and the first
 * child, Home and End, Enter or Space open the popover, Escape closes it.
 */

import { html, useLayoutEffect, useMemo, useRef, useState } from '../../../core/preact.js';
import { formatNumber, locale, t } from '../../../core/i18n.js';
import { placeFloating, useEscape, useOutsidePress } from '../../../core/dom.js';
import { IconButton } from '../../../components/index.js';
import { lang2, levelName, nodeName, pathOf } from '../model.js';

/** A parent shows at most this many children (the largest); the top level, MAX_TOP. */
export const MAX_CHILDREN = 20;
export const MAX_TOP = 60;
/** The room of a block's label, and of a « + N more » line (pixels). */
export const MIN_PX = 44;
const MORE_PX = 26;
/** The height the whole tree gets at least, spread by keyword count (pixels). */
const BASE_PX = 640;
/** The keywords a popover lists. */
const TOP_KEYWORDS = 12;

/**
 * The layout of *index*: `blocks` (`{id, level, y, h, own}`), `more` (`{parent, level, y, h,
 * n}`), the `height` of the whole, and per level the ids shown from the top (`columns`).
 * The children a parent shows fill exactly its span, from its top.
 */
export function layoutIcicle(index) {
  const placed = Math.max(1, Object.keys(index.tree.keywords || {}).length);
  const unit = BASE_PX / placed;
  const kids = new Map();
  const hidden = new Map();
  const pick = (parent, cap) => {
    const list = index.children.get(parent) || [];
    if (list.length <= cap) return [list, 0];
    const keep = new Set([...list].sort((a, b) => index.under.get(b) - index.under.get(a)).slice(0, cap));
    return [list.filter((id) => keep.has(id)), list.length - cap];
  };
  const need = new Map();
  const measure = (id) => {
    const [shown, more] = pick(id, MAX_CHILDREN);
    kids.set(id, shown);
    hidden.set(id, more);
    const below = shown.reduce((s, k) => s + measure(k), 0) + (more ? MORE_PX : 0);
    const h = Math.max(MIN_PX, index.under.get(id) * unit, below);
    need.set(id, h);
    return h;
  };
  const [tops, topMore] = pick(null, MAX_TOP);
  const total = tops.reduce((s, id) => s + measure(id), 0) + (topMore ? MORE_PX : 0);
  const blocks = [];
  const more = [];
  const columns = Array.from({ length: index.depth }, () => []);
  // each child gets the room it needs, and the rest of its parent's span by keyword count
  const place = (ids, hiddenCount, parent, level, y, span) => {
    const base = ids.reduce((s, k) => s + need.get(k), 0) + (hiddenCount ? MORE_PX : 0);
    const weight = ids.reduce((s, k) => s + index.under.get(k), 0) || 1;
    const extra = Math.max(0, span - base);
    let at = y;
    for (const id of ids) {
      const h = need.get(id) + (extra * index.under.get(id)) / weight;
      blocks.push({ id, level, y: at, h, own: index.keywordsOn.get(id).length });
      columns[level - 1].push(id);
      if (level < index.depth) place(kids.get(id), hidden.get(id), id, level + 1, at, h);
      at += h;
    }
    if (hiddenCount) more.push({ parent, level, y: at, h: MORE_PX, n: hiddenCount });
  };
  place(tops, topMore, null, 1, 0, total);
  return { blocks, more, height: total, columns };
}

/** The keywords on or under *id*, the most used first. */
function topKeywords(index, id, n = TOP_KEYWORDS) {
  const all = [];
  const walk = (at) => {
    all.push(...index.keywordsOn.get(at));
    for (const kid of index.children.get(at) || []) walk(kid);
  };
  walk(id);
  return all.sort((a, b) => (index.weightOf(b) - index.weightOf(a)) || (a < b ? -1 : 1)).slice(0, n);
}

/** *id*, its ancestors and its descendants. */
function pathSet(index, id) {
  const out = new Set(pathOf(index, id));
  const walk = (at) => {
    for (const kid of index.children.get(at) || []) {
      out.add(kid);
      walk(kid);
    }
  };
  walk(id);
  return out;
}

function Popover({ index, id, anchor, onClose }) {
  const box = useRef(null);
  const lang = lang2(locale.value);
  useLayoutEffect(() => {
    if (box.current && anchor) placeFloating(box.current, anchor, { prefer: 'below' });
  }, [id]);
  useEscape(() => onClose(true), true);
  useOutsidePress([box], () => onClose(false), true);
  const node = index.nodes.get(id);
  const words = topKeywords(index, id);
  const path = pathOf(index, id).slice(0, -1).map((p) => nodeName(index.nodes.get(p), lang));
  return html`<div class="cx-pg-pop" ref=${box} role="dialog" aria-label=${nodeName(node, lang)}>
    <div class="cx-pg-pop__head">
      <strong>${nodeName(node, lang)}</strong>
      <${IconButton} icon="close" size="s" label=${t('common.close')} onClick=${() => onClose(true)} />
    </div>
    ${path.length ? html`<p class="cx-settings__muted">${path.join(' › ')}</p>` : null}
    <p class="cx-settings__muted cx-num">${t('playground.node.keywords', { n: index.under.get(id) })}</p>
    <ol class="cx-pg-pop__list">${words.map((k) => html`<li key=${k}>${k}</li>`)}</ol>
  </div>`;
}

const px = (v) => `${Math.round(v * 10) / 10}px`;

/** The icicle of *index* (a preview's tree indexed with the keywords' use). */
export function Icicle({ index }) {
  const lang = lang2(locale.value);
  const layout = useMemo(() => layoutIcicle(index), [index]);
  const { columns } = layout;
  const [active, setActive] = useState(() => (columns[0] && columns[0][0]) || null);
  const [open, setOpen] = useState(null);
  const [lit, setLit] = useState(null);
  const refs = useRef(new Map());
  const focusId = columns.some((c) => c.includes(active)) ? active : (columns[0] && columns[0][0]) || null;
  const path = useMemo(() => (lit && index.nodes.has(lit) ? pathSet(index, lit) : null), [lit, index]);
  const placeOf = (id) => {
    for (let c = 0; c < columns.length; c += 1) {
      const i = columns[c].indexOf(id);
      if (i >= 0) return [c, i];
    }
    return null;
  };
  const move = (id) => {
    if (!id) return;
    setActive(id);
    const el = refs.current.get(id);
    if (el) {
      el.focus();
      el.scrollIntoView({ block: 'nearest' });
    }
  };
  const onKey = (event, id) => {
    const at = placeOf(id);
    if (!at) return;
    const [c, i] = at;
    const col = columns[c];
    let next = null;
    if (event.key === 'ArrowDown') next = col[Math.min(col.length - 1, i + 1)];
    else if (event.key === 'ArrowUp') next = col[Math.max(0, i - 1)];
    else if (event.key === 'Home') [next] = col;
    else if (event.key === 'End') next = col[col.length - 1];
    else if (event.key === 'ArrowLeft') next = index.nodes.get(id).parent;
    else if (event.key === 'ArrowRight') next = (columns[c + 1] || []).find((k) => index.nodes.get(k).parent === id) || null;
    else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      setOpen(id);
      return;
    } else return;
    event.preventDefault();
    if (next && placeOf(next)) move(next);
  };
  return html`<div class="cx-pg-icicle" role="group" aria-label=${t('playground.tree')}
      style=${{ '--cx-pg-depth': String(index.depth) }} onMouseLeave=${() => setLit(null)}>
    <div class="cx-pg-icicle__heads">
      ${columns.map((_, i) => html`<h4 class="cx-pg-col__title" key=${i}>${levelName(index.tree, i + 1, lang)}
        <span class="cx-settings__muted cx-num">${formatNumber(index.order.filter((id) => index.level.get(id) === i + 1).length)}</span></h4>`)}
    </div>
    <div class=${`cx-pg-icicle__body ${path ? 'is-lit' : ''}`} style=${{ '--cx-pg-height': px(layout.height) }}>
      ${layout.blocks.map((b) => {
        const node = index.nodes.get(b.id);
        const kids = (index.children.get(b.id) || []).length;
        const hue = (index.hue.get(b.id) || 0) + 1;
        const band = kids && b.own ? Math.min(0.5, b.own / Math.max(1, index.under.get(b.id))) : 0;
        return html`<div key=${b.id} data-node=${b.id} data-parent=${node.parent || ''}
            class=${`cx-pg-block cx-pg-block--l${b.level} ${path && path.has(b.id) ? 'is-path' : ''}`}
            style=${{ '--cx-pg-y': px(b.y), '--cx-pg-h': px(b.h), '--cx-pg-col': String(b.level - 1),
              '--cx-pg-hue': `var(--cx-hue-${hue})`, '--cx-pg-band': String(band) }}>
          <button type="button" class=${`cx-pg-block__button ${open === b.id ? 'is-open' : ''}`}
            tabindex=${b.id === focusId ? 0 : -1} aria-haspopup="dialog" aria-expanded=${open === b.id ? 'true' : 'false'}
            ref=${(el) => { if (el) refs.current.set(b.id, el); else refs.current.delete(b.id); }}
            onClick=${() => { setActive(b.id); setOpen(open === b.id ? null : b.id); }}
            onMouseEnter=${() => setLit(b.id)}
            onFocus=${() => { setActive(b.id); setLit(b.id); }} onBlur=${() => setLit(null)}
            onKeyDown=${(e) => onKey(e, b.id)}>
            <span class="cx-pg-block__name">${nodeName(node, lang)}</span>
            <span class="cx-pg-block__count">${t('playground.node.keywords', { n: index.under.get(b.id) })}${
              kids && b.own ? html` · ${t('playground.node.own', { n: b.own })}` : null}</span>
            ${band ? html`<span class="cx-pg-block__own" aria-hidden="true"></span>` : null}
          </button></div>`;
      })}
      ${layout.more.map((m) => html`<div key=${`more-${m.parent}`} class="cx-pg-block cx-pg-block--more"
          style=${{ '--cx-pg-y': px(m.y), '--cx-pg-h': px(m.h), '--cx-pg-col': String(m.level - 1) }}>
        <span>${t('playground.more', { n: m.n })}</span></div>`)}
    </div>
    ${open && index.nodes.has(open) ? html`<${Popover} index=${index} id=${open} anchor=${refs.current.get(open)}
      onClose=${(back) => {
        const was = open;
        setOpen(null);
        if (back) move(was);
      }} />` : null}
  </div>`;
}
