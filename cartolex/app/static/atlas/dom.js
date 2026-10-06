// SPDX-License-Identifier: MIT
/**
 * The atlas's DOM without a library: `h()` and `svg()` make elements (attributes, `on*`
 * listeners, CSS custom properties through the CSSOM, never a `style` string), `listen()`
 * keeps a listener to release, `symbol()` draws a kind's map symbol. Text always goes in as
 * text nodes: nothing here parses markup.
 */

const SVG_NS = 'http://www.w3.org/2000/svg';

function applyProps(el, props) {
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') el.setAttribute('class', value);
    else if (key === 'vars') {
      for (const [name, v] of Object.entries(value)) if (v !== undefined && v !== null) el.style.setProperty(name, String(v));
    } else if (key === 'dataset') Object.assign(el.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === 'text') el.textContent = value;
    else if (key === 'value' && 'value' in el) el.value = value;
    else if (key === 'checked' || key === 'selected' || key === 'disabled' || key === 'hidden') {
      el[key] = Boolean(value);
      if (value === true && key !== 'checked' && key !== 'selected') el.setAttribute(key, '');
    } else el.setAttribute(key, value === true ? '' : String(value));
  }
}

function appendKids(el, kids) {
  for (const kid of kids) {
    if (kid === null || kid === undefined || kid === false) continue;
    if (Array.isArray(kid)) appendKids(el, kid);
    else if (typeof kid === 'string' || typeof kid === 'number') el.appendChild(document.createTextNode(String(kid)));
    else el.appendChild(kid);
  }
}

/** An HTML element: `h('button', {class, onClick, 'aria-label'}, 'text', child…)`. */
export function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  applyProps(el, props);
  appendKids(el, kids);
  return el;
}

/** An SVG element, as `h`. */
export function svg(tag, props, ...kids) {
  const el = document.createElementNS(SVG_NS, tag);
  applyProps(el, props);
  appendKids(el, kids);
  return el;
}

/** Replace every child of *el* by *kids*. */
export function fill(el, ...kids) {
  while (el.firstChild) el.removeChild(el.firstChild);
  appendKids(el, kids);
  return el;
}

/** Add a listener; answers the function that removes it. */
export function listen(target, type, fn, options) {
  target.addEventListener(type, fn, options);
  return () => target.removeEventListener(type, fn, options);
}

/** The small symbol of a map kind's shape (12 px), in *color* (a colour or a token). */
export function symbol(shape, color) {
  const paint = !color ? 'var(--cx-text-muted)' : color.startsWith('--') ? `var(${color})` : color;
  const body = {
    square: () => svg('rect', { x: 2, y: 2, width: 8, height: 8 }),
    tile: () => svg('rect', { x: 1.5, y: 1.5, width: 9, height: 9, rx: 2.5 }),
    triangle: () => svg('path', { d: 'M6 1.5L10.8 10H1.2z' }),
    diamond: () => svg('path', { d: 'M6 1l5 5-5 5-5-5z' }),
    ring: () => svg('circle', { cx: 6, cy: 6, r: 3.8, class: 'cx-atlas-symbol__ring' }),
    plus: () => svg('path', { d: 'M4.8 1.5h2.4v3.3h3.3v2.4H7.2v3.3H4.8V7.2H1.5V4.8h3.3z' }),
  }[shape];
  return svg('svg', { class: 'cx-atlas-symbol', viewBox: '0 0 12 12', width: 12, height: 12, 'aria-hidden': 'true',
    vars: { '--cx-symbol': paint } }, body ? body() : svg('circle', { cx: 6, cy: 6, r: 4.5 }));
}

/** A small swatch of a colour (a theme's). */
export function swatch(color) {
  return h('span', { class: 'cx-atlas-swatch', 'aria-hidden': 'true', vars: { '--cx-chip': color } });
}

/** Accents and case folded away, for search. */
export function folded(text) {
  return String(text || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

/** A counter of ids unique in the page (for labels and ARIA references). */
let atlasUid = 0;
export function uid(prefix) {
  atlasUid += 1;
  return `${prefix}-${atlasUid}`;
}

/** Run *render* (which rebuilds *el*'s children) and give the keyboard back to the element of
 * the same `data-key` when it had it. */
export function keepFocus(el, render) {
  const had = document.activeElement && el.contains(document.activeElement) ? document.activeElement.dataset.key : null;
  render();
  if (had) {
    const again = el.querySelector(`[data-key="${CSS.escape(had)}"]`);
    if (again) again.focus();
  }
}
