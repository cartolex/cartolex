// SPDX-License-Identifier: MIT
/**
 * A heatmap on a canvas: rows and columns with their names and a band in their main theme's
 * colour (ordered by theme, so the blocks show), cells painted along the colour scheme's
 * scale (Viridis for a scheme of one colour per theme), a legend from the smallest value to
 * the largest. A click on a cell or on a row's name calls the caller; so does the keyboard:
 * the arrows (Page Up/Down, Home/End) move a cursor that a live line reads out, Enter acts on
 * the cell, Shift+Enter on the row. Hovering shows the cell's names and value.
 */
import { h, listen } from '../atlas/dom.js';
import { scaleColour } from '../atlas/schemes.js';

const LABEL = 190;
const BAND = 6;
const GAP = 4;
const TOP_LABELS = 120;
const MIN_CELL = 3;
const MAX_CELL = 26;

/** The smallest and largest finite values of *values* (the diagonal left out when *square*). */
export function heatRange(values, n, square) {
  let lo = Infinity;
  let hi = -Infinity;
  for (let k = 0; k < values.length; k += 1) {
    if (square && Math.floor(k / n) === k % n) continue;
    const v = values[k];
    if (Number.isNaN(v)) continue;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  if (lo === Infinity) return [0, 1];
  return lo === hi ? [lo - 0.5, hi + 0.5] : [lo, hi];
}

/**
 * The heatmap in a new element. *spec*: `{rows, cols, values, square, label, describe(r, c),
 * onCell(r, c), onRow(r)}`, rows and cols being `{name, colour}`; *look*: `{scheme(), fmt}`.
 * Answers `{el, repaint(), destroy()}`.
 */
export function createHeatmap(spec, look) {
  const { rows, cols, values } = spec;
  const nr = rows.length;
  const nc = cols.length;
  const [lo, hi] = heatRange(values, nc, spec.square);
  const canvas = h('canvas', { class: 'cx-dist-heat__canvas' });
  const tip = h('div', { class: 'cx-dist-heat__tip', hidden: true, 'aria-hidden': 'true' });
  const cursorLine = h('p', { class: 'cx-atlas-note cx-dist-heat__cursor', 'aria-live': 'polite' });
  const box = h('div', { class: 'cx-dist-heat__box', tabindex: '0', role: 'group', 'aria-label': spec.label,
    'aria-roledescription': look.t('atlas.dist.heatmap') }, canvas, tip);
  const stops = Array.from({ length: 11 }, (_, k) => scaleColour(look.scheme(), k / 10)).join(', ');
  const legend = h('div', { class: 'cx-dist-heat__legend' },
    h('span', { class: 'cx-dist-heat__end', text: look.fmt(lo) }),
    h('span', { class: 'cx-dist-heat__ramp', 'aria-hidden': 'true', vars: { '--cx-ramp': `linear-gradient(90deg, ${stops})` } }),
    h('span', { class: 'cx-dist-heat__end', text: look.fmt(hi) }));
  const el = h('div', { class: 'cx-dist-heat' }, legend, box, cursorLine);
  let geo = null;
  let cursor = null;

  function layout() {
    const width = Math.max(240, el.clientWidth || 800);
    const cell = Math.max(MIN_CELL, Math.min(MAX_CELL, Math.floor((width - LABEL - BAND - GAP) / Math.max(1, nc))));
    const labels = cell >= 9;
    const top = labels ? TOP_LABELS : BAND + GAP;
    const left = labels ? LABEL : BAND + GAP;
    // the columns' names lean right, past the last column
    const lean = labels ? Math.round(TOP_LABELS * 0.6) : 0;
    return { cell, labels, top, left, w: left + BAND + GAP + nc * cell + lean, hgt: top + BAND + GAP + nr * cell };
  }

  function paint() {
    geo = layout();
    const { cell, labels, top, left } = geo;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(geo.w * ratio);
    canvas.height = Math.round(geo.hgt * ratio);
    canvas.style.setProperty('width', `${geo.w}px`);
    canvas.style.setProperty('height', `${geo.hgt}px`);
    const g = canvas.getContext('2d');
    if (!g) return;
    g.setTransform(ratio, 0, 0, ratio, 0, 0);
    const css = getComputedStyle(el);
    const ink = css.getPropertyValue('--cx-text').trim() || '#000';
    const empty = css.getPropertyValue('--cx-surface-alt').trim() || '#eee';
    const x0 = left + BAND + GAP;
    const y0 = top + BAND + GAP;
    g.clearRect(0, 0, geo.w, geo.hgt);
    for (let r = 0; r < nr; r += 1) {
      for (let c = 0; c < nc; c += 1) {
        const v = values[r * nc + c];
        g.fillStyle = Number.isNaN(v) ? empty : scaleColour(look.scheme(), (v - lo) / (hi - lo));
        g.fillRect(x0 + c * cell, y0 + r * cell, cell, cell);
      }
    }
    rows.forEach((row, r) => {
      g.fillStyle = row.colour;
      g.fillRect(left, y0 + r * cell, BAND, cell);
    });
    cols.forEach((col, c) => {
      g.fillStyle = col.colour;
      g.fillRect(x0 + c * cell, top, cell, BAND);
    });
    if (labels) {
      g.fillStyle = ink;
      g.font = `${Math.min(13, cell)}px ${css.getPropertyValue('--cx-font') || 'sans-serif'}`;
      g.textBaseline = 'middle';
      const cut = (text, room) => {
        let s = String(text);
        if (g.measureText(s).width <= room) return s;
        while (s.length > 1 && g.measureText(`${s}…`).width > room) s = s.slice(0, -1);
        return `${s}…`;
      };
      g.textAlign = 'right';
      rows.forEach((row, r) => g.fillText(cut(row.name, left - GAP * 2), left - GAP, y0 + r * cell + cell / 2));
      g.save();
      g.textAlign = 'left';
      cols.forEach((col, c) => {
        g.save();
        g.translate(x0 + c * cell + cell / 2, top - GAP);
        g.rotate(-Math.PI / 3);
        g.fillText(cut(col.name, TOP_LABELS * 1.1), 0, 0);
        g.restore();
      });
      g.restore();
    }
    if (cursor) {
      g.strokeStyle = ink;
      g.lineWidth = 2;
      g.strokeRect(x0 + cursor[1] * cell - 1, y0 + cursor[0] * cell - 1, cell + 2, cell + 2);
    }
  }

  /** The row and column under a point of the canvas (`[r, c]`, a column of -1 on a row's name). */
  function hit(e) {
    if (!geo) return null;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left - (geo.left + BAND + GAP);
    const y = e.clientY - rect.top - (geo.top + BAND + GAP);
    const r = Math.floor(y / geo.cell);
    if (r < 0 || r >= nr) return null;
    if (x < 0) return e.clientX - rect.left < geo.left + BAND ? [r, -1] : null;
    const c = Math.floor(x / geo.cell);
    return c < nc ? [r, c] : null;
  }

  function say(r, c) {
    cursorLine.textContent = spec.describe(r, c);
  }

  const offs = [
    listen(canvas, 'mousemove', (e) => {
      const at = hit(e);
      if (!at || at[1] < 0) {
        tip.hidden = true;
        canvas.classList.toggle('is-row', Boolean(at));
        return;
      }
      canvas.classList.remove('is-row');
      tip.textContent = spec.describe(at[0], at[1]);
      tip.hidden = false;
      const rect = box.getBoundingClientRect();
      tip.style.setProperty('left', `${Math.min(rect.width - 220, e.clientX - rect.left + 14)}px`);
      tip.style.setProperty('top', `${e.clientY - rect.top + box.scrollTop + 14}px`);
    }),
    listen(canvas, 'mouseleave', () => { tip.hidden = true; }),
    listen(canvas, 'click', (e) => {
      const at = hit(e);
      if (!at) return;
      if (at[1] < 0) spec.onRow(at[0]);
      else spec.onCell(at[0], at[1]);
    }),
    listen(box, 'focus', () => {
      if (!cursor) cursor = [0, 0];
      paint();
      say(cursor[0], cursor[1]);
    }),
    listen(box, 'keydown', (e) => {
      if (!cursor) cursor = [0, 0];
      let [r, c] = cursor;
      const page = 10;
      if (e.key === 'ArrowDown') r += 1;
      else if (e.key === 'ArrowUp') r -= 1;
      else if (e.key === 'ArrowRight') c += 1;
      else if (e.key === 'ArrowLeft') c -= 1;
      else if (e.key === 'PageDown') r += page;
      else if (e.key === 'PageUp') r -= page;
      else if (e.key === 'Home') c = 0;
      else if (e.key === 'End') c = nc - 1;
      else if (e.key === 'Enter') {
        e.preventDefault();
        if (e.shiftKey) spec.onRow(r);
        else spec.onCell(r, c);
        return;
      } else return;
      e.preventDefault();
      cursor = [Math.max(0, Math.min(nr - 1, r)), Math.max(0, Math.min(nc - 1, c))];
      paint();
      say(cursor[0], cursor[1]);
    }),
  ];
  let frame = 0;
  let width = 0;
  const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(() => {
    if (el.clientWidth === width) return;
    width = el.clientWidth;
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(paint);
  }) : null;
  if (observer) observer.observe(el);
  requestAnimationFrame(paint);
  return {
    el,
    repaint: paint,
    destroy() {
      cancelAnimationFrame(frame);
      if (observer) observer.disconnect();
      offs.forEach((off) => off());
    },
  };
}
