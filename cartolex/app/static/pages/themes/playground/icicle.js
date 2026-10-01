// SPDX-License-Identifier: MIT
/**
 * The previewed tree as columns, one per level (an icicle): each node a block whose height
 * follows its keyword count, the top level in its hue family and the levels below in tints
 * of it, with its name, its keyword count and « N its own » (the keywords on the node
 * itself). A long level scrolls; a level of many nodes shows the largest and « + N more ».
 * A block opens its top keywords in a popover.
 *
 * Keyboard: one tab stop; ↑ and ↓ move within a level, ← and → to the parent and the first
 * child, Home and End, Enter or Space open the popover, Escape closes it.
 */

import { html, useLayoutEffect, useRef, useState } from '../../../core/preact.js';
import { formatNumber, locale, t } from '../../../core/i18n.js';
import { placeFloating, useEscape, useOutsidePress } from '../../../core/dom.js';
import { IconButton } from '../../../components/index.js';
import { lang2, levelName, nodeName, pathOf } from '../model.js';

/** A level shows at most this many nodes (the largest, in the tree's order). */
export const MAX_BLOCKS = 60;
/** The keywords a popover lists. */
const TOP_KEYWORDS = 12;

/** The columns of *index*: per level, the nodes shown and how many more there are. */
export function columnsOf(index) {
  const out = [];
  for (let lv = 1; lv <= index.depth; lv += 1) {
    const ids = index.order.filter((id) => index.level.get(id) === lv);
    let shown = ids;
    if (ids.length > MAX_BLOCKS) {
      const keep = new Set([...ids].sort((a, b) => index.under.get(b) - index.under.get(a)).slice(0, MAX_BLOCKS));
      shown = ids.filter((id) => keep.has(id));
    }
    out.push({ level: lv, ids: shown, more: ids.length - shown.length, total: ids.length });
  }
  return out;
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

/** The icicle of *index* (a preview's tree indexed with the keywords' use). */
export function Icicle({ index }) {
  const lang = lang2(locale.value);
  const columns = columnsOf(index);
  const [active, setActive] = useState(() => (columns[0] && columns[0].ids[0]) || null);
  const [open, setOpen] = useState(null);
  const refs = useRef(new Map());
  const focusId = index.nodes.has(active) ? active : (columns[0] && columns[0].ids[0]) || null;
  const placeOf = (id) => {
    for (let c = 0; c < columns.length; c += 1) {
      const i = columns[c].ids.indexOf(id);
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
    const col = columns[c].ids;
    let next = null;
    if (event.key === 'ArrowDown') next = col[Math.min(col.length - 1, i + 1)];
    else if (event.key === 'ArrowUp') next = col[Math.max(0, i - 1)];
    else if (event.key === 'Home') [next] = col;
    else if (event.key === 'End') next = col[col.length - 1];
    else if (event.key === 'ArrowLeft') next = index.nodes.get(id).parent;
    else if (event.key === 'ArrowRight') {
      next = (index.children.get(id) || []).find((k) => columns[c + 1] && columns[c + 1].ids.includes(k)) || null;
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      setOpen(id);
      return;
    } else return;
    event.preventDefault();
    if (next && placeOf(next)) move(next);
  };
  const largest = Math.max(1, ...index.order.map((id) => index.under.get(id)));
  return html`<div class="cx-pg-icicle" role="group" aria-label=${t('playground.tree')}
      style=${{ '--cx-pg-max': String(largest) }}>
    ${columns.map((col) => html`<section class="cx-pg-col" key=${col.level}
        aria-label=${t('playground.column', { level: levelName(index.tree, col.level, lang), n: col.total })}>
      <h4 class="cx-pg-col__title">${levelName(index.tree, col.level, lang)}
        <span class="cx-settings__muted cx-num">${formatNumber(col.total)}</span></h4>
      <ul class="cx-pg-col__list">
        ${col.ids.map((id) => {
          const own = index.keywordsOn.get(id).length;
          const kids = (index.children.get(id) || []).length;
          const hue = (index.hue.get(id) || 0) + 1;
          return html`<li key=${id} class=${`cx-pg-block cx-pg-block--l${col.level}`}
              style=${{ '--cx-pg-k': String(Math.max(1, index.under.get(id))), '--cx-pg-hue': `var(--cx-hue-${hue})` }}>
            <button type="button" class=${`cx-pg-block__button ${open === id ? 'is-open' : ''}`}
              tabindex=${id === focusId ? 0 : -1} aria-haspopup="dialog" aria-expanded=${open === id ? 'true' : 'false'}
              ref=${(el) => { if (el) refs.current.set(id, el); else refs.current.delete(id); }}
              onClick=${() => { setActive(id); setOpen(open === id ? null : id); }}
              onFocus=${() => setActive(id)} onKeyDown=${(e) => onKey(e, id)}>
              <span class="cx-pg-block__name">${nodeName(index.nodes.get(id), lang)}</span>
              <span class="cx-pg-block__count">${t('playground.node.keywords', { n: index.under.get(id) })}${
                kids && own ? html` · ${t('playground.node.own', { n: own })}` : null}</span>
            </button></li>`;
        })}
        ${col.more ? html`<li class="cx-pg-block cx-pg-block--more"><span>${t('playground.more', { n: col.more })}</span></li>` : null}
      </ul>
    </section>`)}
    ${open && index.nodes.has(open) ? html`<${Popover} index=${index} id=${open} anchor=${refs.current.get(open)}
      onClose=${(back) => {
        const was = open;
        setOpen(null);
        if (back) move(was);
      }} />` : null}
  </div>`;
}
