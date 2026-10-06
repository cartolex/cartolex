// SPDX-License-Identifier: MIT
/**
 * The treemap of the themes, the way into the atlas: the top-level themes with their
 * children inside; a theme opens into its children (each listing its strongest keywords),
 * and a theme without children opens into its keywords. One click focuses a block, a
 * double click (or Shift+Enter) opens it, ↑ goes back up. With a person or an organisation
 * in focus, the blocks take that focus's own weights. The treemap is one tab stop: the
 * arrows move among its blocks, Home and End go to the first and last, Enter focuses,
 * Shift+Enter opens, Backspace goes up.
 */
import { squarify } from '../components/treemap-layout.js';
import { listen, svg, uid } from './dom.js';
import { nameIn, pathTo, sharesOf } from './data.js';
import { inkOn, shade } from './schemes.js';

const DOUBLE_MS = 450;
const MAX_KEYWORD_BLOCKS = 80;
const HEAD = 20;

/** Text cut to the width it has (an estimate of the font's width). */
function fitted(text, width, size) {
  const most = Math.floor((width - 8) / (size * 0.56));
  if (most < 2) return null;
  return text.length > most ? `${text.slice(0, most - 1)}…` : text;
}

/** The weight of a node: its share in the focus's own use (at the node's level) or its
 * weight in the field. */
function weigher(index, personal) {
  if (!personal) return (id) => Math.max((index.nodes.get(id) || {}).weight || 0, 0);
  const cache = new Map();
  return (id) => {
    const level = (index.nodes.get(id) || {}).level || 1;
    if (!cache.has(level)) cache.set(level, sharesOf(index, personal, level));
    return cache.get(level)[id] || 0;
  };
}

/**
 * The treemap in *box* (an element the treemap fills). *onFocus(sel)* when a block is
 * focused, *onOpen(id)* when one is opened ('' for the top). Answers `{update(view),
 * title(view), focusBlock(), destroy()}`; *view* is `{index, state, colours, t, locale}`.
 */
export function createTreemap(box, { onFocus, onOpen, onUp, label }) {
  const root = svg('svg', { class: 'cx-atlas-tree__svg', role: 'group', 'aria-label': label, tabindex: '0',
    'aria-roledescription': 'treemap' });
  box.appendChild(root);
  let blocks = [];
  let active = 0;
  let last = { key: '', at: 0 };
  let view = null;

  const press = (b, open) => {
    if (open && b.open) b.open();
    else b.focus();
  };
  const offs = [
    listen(root, 'click', (e) => {
      const g = e.target.closest('[data-block]');
      if (!g) return;
      const k = Number(g.dataset.block);
      const b = blocks[k];
      if (!b) return;
      active = k;
      const now = performance.now();
      if (b.open && last.key === b.key && now - last.at < DOUBLE_MS) {
        last = { key: '', at: 0 };
        b.open();
      } else {
        last = { key: b.key, at: now };
        b.focus();
      }
    }),
    listen(root, 'keydown', (e) => {
      if (!blocks.length) return;
      if (['ArrowRight', 'ArrowDown', 'ArrowLeft', 'ArrowUp', 'Home', 'End'].includes(e.key)) {
        e.preventDefault();
        if (e.key === 'Home') active = 0;
        else if (e.key === 'End') active = blocks.length - 1;
        else active = Math.max(0, Math.min(blocks.length - 1, active + (e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : -1)));
        mark();
      } else if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        press(blocks[active], e.shiftKey);
      } else if (e.key === 'Backspace') {
        e.preventDefault();
        onUp();
      }
    }),
  ];

  function mark() {
    root.querySelectorAll('.is-active').forEach((el) => el.classList.remove('is-active'));
    const b = blocks[active];
    if (!b) return;
    const el = root.querySelector(`[data-block="${active}"]`);
    if (el) {
      el.classList.add('is-active');
      root.setAttribute('aria-activedescendant', el.id);
    }
  }

  function block(parent, rect, { key, name, fill, stroke, text, size = 11, weight = 500, lines = [], focus, open, dim }) {
    const k = blocks.length;
    const g = svg('g', { 'data-block': k, id: uid('cx-atlas-tb'), role: 'button', 'aria-label': text,
      class: `cx-atlas-tree__block${dim ? ' is-dim' : ''}` });
    g.appendChild(svg('rect', { x: rect.x + 1, y: rect.y + 1, width: Math.max(0, rect.w - 2), height: Math.max(0, rect.h - 2),
      rx: 2, fill, class: stroke ? 'is-focus' : '' }));
    const ink = inkOn(fill);
    const first = rect.h > 15 ? fitted(name, rect.w, size) : null;
    if (first) g.appendChild(svg('text', { x: rect.x + 6, y: rect.y + 4 + size, fill: ink, 'font-size': size, 'font-weight': weight, text: first }));
    lines.forEach((line, m) => {
      const y = rect.y + 4 + size + 15 * (m + 1);
      if (y + 4 > rect.y + rect.h || rect.w < 70) return;
      const cut = fitted(line, rect.w - 6, 10.5);
      if (cut) g.appendChild(svg('text', { x: rect.x + 9, y, fill: ink, 'font-size': 10.5, class: 'cx-atlas-tree__line', text: cut }));
    });
    parent.appendChild(g);
    blocks.push({ key, focus, open });
    return g;
  }

  function draw() {
    if (!view) return;
    const { index, state, colours, t, locale } = view;
    while (root.firstChild) root.removeChild(root.firstChild);
    const prevKey = blocks[active] ? blocks[active].key : '';
    blocks = [];
    const W = Math.round(box.clientWidth);
    const H = Math.round(box.clientHeight);
    if (W < 20 || H < 20 || !index.tops.length) return;
    root.setAttribute('viewBox', `0 0 ${W} ${H}`);
    const sel = state.sel;
    const personal = sel && (sel.kind === 'person' || sel.kind === 'organisation' || sel.kind === 'projected') ? sel : null;
    const weightOf = weigher(index, personal);
    const focusNode = sel && sel.kind === 'theme' ? sel.id
      : sel && sel.kind === 'keyword' && index.byTerm.has(sel.id) ? index.keywords[index.byTerm.get(sel.id)].node : null;
    const focusPath = focusNode ? pathTo(index, focusNode) : [];
    const colourOfNode = (id) => {
      const i = index.colourOf(id);
      return i < colours.themes.length ? colours.themes[i] : colours.neutral;
    };
    const share = (id) => (personal ? ` ${t('atlas.tree.share', { share: weightOf(id) })}` : '');
    const nodeName = (id) => nameIn(index.nodes.get(id).names, locale);
    const focusSel = (id) => () => onFocus({ kind: 'theme', id });
    const openSel = (id) => () => onOpen(id);
    const dark = colours.dark;
    const open = state.open && index.nodes.has(state.open) ? state.open : '';
    const kids = index.children.get(open || null) || [];
    const opensHint = t('atlas.tree.opens');

    if (kids.length) {
      // nodes with their children nested (or, at the last level, their strongest keywords)
      const items = kids.map((id) => ({ id, value: weightOf(id) })).filter((it) => it.value > 0);
      for (const cell of squarify(items, { x: 0, y: 0, w: W, h: H })) {
        const id = cell.item.id;
        const base = colourOfNode(id);
        const grand = index.children.get(id) || [];
        const dim = Boolean(focusNode) && !focusPath.includes(id) && !pathTo(index, id).includes(focusNode);
        const g = svg('g', { class: dim ? 'cx-atlas-tree__group is-dim' : 'cx-atlas-tree__group' });
        root.appendChild(g);
        const fill = grand.length ? shade(base, dark ? -24 : 16) : shade(base, dark ? -6 : 6);
        const terms = grand.length ? [] : (index.nodeKeywords.get(id) || []).slice(0, 8).map((k) => `· ${index.keywords[k].term}`);
        const name = nodeName(id) + share(id);
        const headRect = grand.length && cell.h > HEAD + 14 ? { x: cell.x, y: cell.y, w: cell.w, h: cell.h } : cell;
        block(g, headRect, { key: `n:${id}`, name, fill, text: `${name}. ${opensHint}`, size: 12.5, weight: 600,
          lines: terms, stroke: focusNode === id, focus: focusSel(id), open: openSel(id) });
        if (grand.length && cell.h > HEAD + 14 && cell.w > 30) {
          const inner = { x: cell.x + 2, y: cell.y + HEAD + 1, w: cell.w - 4, h: cell.h - HEAD - 3 };
          const sub = grand.map((c) => ({ id: c, value: weightOf(c) })).filter((it) => it.value > 0);
          squarify(sub, inner).forEach((q, n) => {
            const cid = q.item.id;
            const subName = nodeName(cid) + share(cid);
            block(g, q, { key: `n:${cid}`, name: nodeName(cid), fill: shade(base, n % 2 ? -6 : 4),
              text: `${subName}. ${opensHint}`, stroke: focusNode === cid, focus: focusSel(cid), open: openSel(cid) });
          });
        }
      }
    } else if (open) {
      // a node without children: its keywords, by weight
      const base = colourOfNode(open);
      const list = (index.nodeKeywords.get(open) || []).slice(0, MAX_KEYWORD_BLOCKS);
      const items = list.map((k) => ({ k, value: Math.max(index.keywords[k].weight || 0, 1e-6) }));
      const focusTerm = sel && sel.kind === 'keyword' ? sel.id : null;
      squarify(items, { x: 0, y: 0, w: W, h: H }).forEach((cell, n) => {
        const kw = index.keywords[cell.item.k];
        block(root, cell, { key: `k:${kw.term}`, name: kw.term, fill: shade(base, n % 3 === 0 ? 8 : n % 3 === 1 ? -2 : -10),
          text: kw.term, size: 12, stroke: focusTerm === kw.term,
          focus: () => onFocus({ kind: 'keyword', id: kw.term }), open: null });
      });
    }
    const again = blocks.findIndex((b) => b.key === prevKey);
    active = again >= 0 ? again : Math.min(active, Math.max(0, blocks.length - 1));
    mark();
  }

  let frame = 0;
  const observer = new ResizeObserver(() => {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      draw();
    });
  });
  observer.observe(box);
  return {
    update(next) {
      view = next;
      draw();
    },
    /** Move the keyboard to the treemap. */
    focusBlock() {
      root.focus();
    },
    destroy() {
      cancelAnimationFrame(frame);
      observer.disconnect();
      offs.forEach((off) => off());
      root.remove();
    },
  };
}

/** The treemap's title for *view*: the field's themes, a focus's own, or what is open. */
export function treemapTitle({ index, state, t, locale, nameOfSel }) {
  const open = state.open && index.nodes.has(state.open) ? state.open : '';
  if (open) {
    const name = nameIn(index.nodes.get(open).names, locale);
    return (index.children.get(open) || []).length ? t('atlas.tree.inside', { name }) : t('atlas.tree.keywords_of', { name });
  }
  const sel = state.sel;
  if (sel && (sel.kind === 'person' || sel.kind === 'organisation' || sel.kind === 'projected')) {
    return t('atlas.tree.themes_of', { name: nameOfSel(sel) });
  }
  return t('atlas.tree.field');
}
