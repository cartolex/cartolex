// SPDX-License-Identifier: MIT
/**
 * The atlas's panes: the treemap on the left, the map in the centre, the card on the right
 * or below the map. A divider resizes its pane by dragging or with the arrows (Home and End
 * for the smallest and largest); « Hide » sends a pane to a thin rail that brings it back.
 * The layout belongs to the person: it is kept in the host's preferences. Full screen is
 * offered for the whole atlas, the map alone and the treemap alone: the browser's own when it
 * allows it, else the part fixed over the window; Escape or the button inside leaves it.
 */
import { h, listen } from './dom.js';

/** The layout when the person has none: widths in pixels, both panes shown, the card right. */
export const LAYOUT_DEFAULT = { tree: 300, card: 330, below: 240, treeOn: true, cardOn: true, at: 'right' };
const LIMITS = { tree: [160, 760], card: [220, 700], below: [110, 560] };
const PREF_KEYS = { tree: 'atlas.tree', card: 'atlas.card', below: 'atlas.below', treeOn: 'atlas.tree_on',
  cardOn: 'atlas.card_on', at: 'atlas.card_at' };

const clampTo = (v, [lo, hi]) => Math.max(lo, Math.min(hi, v));

/** The person's layout from the host's preferences (`get(key)`), the defaults filling in. */
export function readLayout(prefs) {
  const out = { ...LAYOUT_DEFAULT };
  if (!prefs || !prefs.get) return out;
  for (const [field, key] of Object.entries(PREF_KEYS)) {
    const v = prefs.get(key);
    if (v === undefined || v === null) continue;
    if (field === 'at') out.at = v === 'below' ? 'below' : 'right';
    else if (field === 'treeOn' || field === 'cardOn') out[field] = Boolean(v);
    else if (Number.isFinite(Number(v))) out[field] = clampTo(Number(v), LIMITS[field]);
  }
  return out;
}

/** Keep *layout* in the host's preferences (`set(key, value)`), only what changed. */
export function writeLayout(prefs, layout, before) {
  if (!prefs || !prefs.set) return;
  for (const [field, key] of Object.entries(PREF_KEYS)) {
    if (!before || before[field] !== layout[field]) prefs.set(key, layout[field]);
  }
}

/**
 * A divider: dragging it or its arrows call *onMove(dx, dy, start)* (`start`: the layout when
 * the drag began); *onEnd()* when it is let go. Answers the release of its listeners.
 */
export function divider(el, { onMove, onEnd, get }) {
  let start = null;
  const offs = [
    listen(el, 'pointerdown', (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      start = { x: e.clientX, y: e.clientY, layout: get() };
      el.classList.add('is-active');
      if (el.setPointerCapture) el.setPointerCapture(e.pointerId);
    }),
    listen(el, 'pointermove', (e) => {
      if (!start) return;
      onMove(e.clientX - start.x, e.clientY - start.y, start.layout);
    }),
    listen(el, 'pointerup', () => {
      if (!start) return;
      start = null;
      el.classList.remove('is-active');
      onEnd();
    }),
    listen(el, 'keydown', (e) => {
      const step = e.shiftKey ? 60 : 20;
      const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step],
        Home: [-2000, -2000], End: [2000, 2000] }[e.key];
      if (!d) return;
      e.preventDefault();
      onMove(d[0], d[1], get());
      onEnd();
    }),
  ];
  return () => offs.forEach((off) => off());
}

/** Clamp a pane's size to its limits. */
export function paneSize(field, value) {
  return clampTo(value, LIMITS[field]);
}

/** The limits of a pane's size (for its divider's `aria-valuemin`/`max`). */
export function paneLimits(field) {
  return LIMITS[field];
}

/**
 * Full screen for *el*: `toggle()`, `leave()`, `on()`; *onChange(on)* after each change. The
 * browser's Fullscreen API, else the element fixed over the window (`is-blown`) with Escape
 * to leave (unless a dialog or a menu takes it first). *exitLabel* names the button inside.
 */
export function createFullscreen(el, { onChange, exitLabel }) {
  let mode = '';
  const exit = h('button', { type: 'button', class: 'cx-atlas-btn cx-atlas-exitfull', hidden: true, text: exitLabel,
    onClick: () => leave() });
  el.appendChild(exit);
  const set = (next) => {
    mode = next;
    el.classList.toggle('is-blown', mode === 'fixed');
    el.classList.toggle('is-full', mode !== '');
    exit.hidden = mode === '';
    if (onChange) onChange(mode !== '');
  };
  const onFsChange = () => {
    if (document.fullscreenElement === el) set('native');
    else if (mode === 'native') set('');
  };
  const onKey = (event) => {
    if (mode !== 'fixed' || event.key !== 'Escape' || event.defaultPrevented) return;
    if (document.querySelector('dialog[open], [role=menu]:not([hidden]), [role=listbox]:not([hidden])')) return;
    event.preventDefault();
    set('');
  };
  const offs = [listen(document, 'fullscreenchange', onFsChange), listen(document, 'keydown', onKey)];
  function enter() {
    if (document.fullscreenEnabled && el.requestFullscreen) {
      el.requestFullscreen().then(() => set('native')).catch(() => set('fixed'));
    } else set('fixed');
  }
  function leave() {
    if (mode === 'native' && document.fullscreenElement) document.exitFullscreen().catch(() => {});
    set('');
  }
  return {
    on: () => mode !== '',
    toggle: () => (mode ? leave() : enter()),
    leave,
    setLabel(text) {
      exit.textContent = text;
    },
    destroy() {
      offs.forEach((off) => off());
      if (mode === 'native' && document.fullscreenElement === el) document.exitFullscreen().catch(() => {});
      exit.remove();
    },
  };
}
