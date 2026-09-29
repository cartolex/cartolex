// SPDX-License-Identifier: MIT
/**
 * MapFrame: points, regions, lines and labels on a map, with pan, zoom, hit
 * testing, a hover card, a permanent legend and a highlighted selection.
 *
 * What it draws is a **scene** (see `map/core.js`): layers of points in data
 * coordinates, each with a shape, a colour per point (an index into the
 * layer's palette of colours or `--cx-*` token names), ranks that the zoom
 * reveals, a highlight; convex regions; lines; labels shown on selection and
 * with the zoom. How it draws is a **renderer** (`resize`, `draw`,
 * `invalidate`, `destroy`): WebGL when the browser has it (10⁵ points pan
 * at the frame rate), else Canvas 2D; `renderer="canvas2d"` or a factory
 * chooses. The view, the events and the frame loop are the controller's
 * (`map/controller.js`), which the offline site uses without Preact.
 *
 * The frame is one tab stop: arrows pan, + and − zoom, 0 fits the map again.
 * A drag pans, the wheel zooms around the pointer, a click picks the nearest
 * point (`onPick`), hovering reports it (`onHover`) and shows `hoverCard(hit)`
 * beside the pointer. `frameRef.current` gives `fit()`, `zoomBy(factor)`,
 * `panBy(dx, dy)`, `centreOn(x, y, zoom)`, `hitTest(px, py)`, `view()`,
 * `redraw()`, `frames()`, `stats()` and `renderer` (`webgl` or `canvas2d`).
 * Every listener, observer and frame is released on unmount.
 */
import { html, useEffect, useLayoutEffect, useRef, useState } from '../core/preact.js';
import { useUid } from '../core/dom.js';
import { createMapController } from './map/controller.js';

export { createCanvas2DRenderer } from './map/canvas2d.js';
export { createWebGLRenderer } from './map/webgl.js';
export { convexHull } from './map/core.js';

/** The symbol of a shape, for legends and lists (an inline SVG, 12 px). */
export function MapSymbol({ shape = 'circle', color = '--cx-text-muted' }) {
  const fill = color.startsWith('--') ? `var(${color})` : color;
  const style = { '--cx-symbol': fill };
  const body = {
    square: html`<rect x="2" y="2" width="8" height="8" />`,
    triangle: html`<path d="M6 1.5L10.8 10H1.2z" />`,
    diamond: html`<path d="M6 1l5 5-5 5-5-5z" />`,
    ring: html`<circle cx="6" cy="6" r="3.8" class="cx-map-symbol__ring" />`,
    plus: html`<path d="M4.8 1.5h2.4v3.3h3.3v2.4H7.2v3.3H4.8V7.2H1.5V4.8h3.3z" />`,
  }[shape] || html`<circle cx="6" cy="6" r="4.5" />`;
  return html`<svg class="cx-map-symbol" viewBox="0 0 12 12" width="12" height="12" aria-hidden="true"
    style=${style}>${body}</svg>`;
}

/**
 * @param {object} props
 * @param {{layers: Array<object>, regions?, lines?, labels?, bounds: object}} props.scene
 * @param {string} props.label the map's accessible name
 * @param {string} [props.status] what the map shows now, said to assistive technology
 * @param {(hit: {layer: string, index: number}|null) => void} [props.onPick]
 * @param {(hit: {layer: string, index: number}|null, point: {x, y}|null) => void} [props.onHover]
 * @param {(hit: {layer: string, index: number}) => any} [props.hoverCard] the card of a hovered point
 * @param {any} [props.legend] the legend, always shown over the map's corner
 * @param {{current: any}} [props.frameRef] receives the frame's methods
 * @param {string|Function} [props.renderer] `auto` (default), `webgl`, `canvas2d` or a factory
 *   of a canvas's renderer (`resize(width, height, ratio)`, `draw(scene, view)`,
 *   `invalidate()`, `destroy()`)
 */
export function MapFrame({ scene, label, status = '', onPick, onHover, hoverCard, legend, frameRef,
  renderer = 'auto', class: cls = '' }) {
  const box = useRef(null);
  const canvas = useRef(null);
  const control = useRef(null);
  const id = useUid('cx-map');
  const [hover, setHover] = useState(null);
  const latest = useRef({ scene, onPick, onHover, hoverCard });
  latest.current = { scene, onPick, onHover, hoverCard };

  useLayoutEffect(() => {
    const el = box.current;
    const api = createMapController({
      box: el,
      canvas: canvas.current,
      renderer,
      scene: () => latest.current.scene,
      onPick: (hit) => latest.current.onPick && latest.current.onPick(hit),
      onHover: (hit, point) => {
        if (latest.current.onHover) latest.current.onHover(hit, point);
        setHover(hit && latest.current.hoverCard ? { hit, point } : null);
      },
    });
    control.current = api;
    el.cxMap = api; // for measures in the browser tests
    el.dataset.renderer = api.renderer;
    if (frameRef) frameRef.current = api;
    return () => {
      api.destroy();
      delete el.cxMap;
      if (frameRef) frameRef.current = null;
      control.current = null;
    };
  }, []);

  // A new scene: draw it (fit it when its bounds are new).
  const lastBounds = useRef(null);
  useEffect(() => {
    const api = control.current;
    if (!api) return;
    const b = scene.bounds;
    const key = b ? `${b.xmin},${b.xmax},${b.ymin},${b.ymax}` : '';
    const refit = key !== lastBounds.current;
    lastBounds.current = key;
    api.redraw(refit);
  }, [scene]);

  let card = null;
  if (hover && hoverCard) {
    const content = hoverCard(hover.hit);
    if (content) {
      const el = box.current;
      const right = el && hover.point.x > el.clientWidth - 260;
      const below = el && hover.point.y < 120;
      card = html`<div class=${`cx-map-frame__card ${right ? 'is-left' : ''} ${below ? 'is-below' : ''}`}
        style=${{ '--cx-card-x': `${hover.point.x}px`, '--cx-card-y': `${hover.point.y}px` }}
        aria-hidden="true">${content}</div>`;
    }
  }
  return html`<div class=${`cx-map-frame ${cls}`}>
    <div ref=${box} class="cx-map-frame__box" tabindex="0" role="group" aria-label=${label}
      aria-describedby=${`${id}-status`}>
      <canvas ref=${canvas} class="cx-map-frame__canvas" aria-hidden="true"></canvas>
      ${card}
    </div>
    ${legend ? html`<div class="cx-map-frame__legend">${legend}</div>` : null}
    <p class="cx-visually-hidden" id=${`${id}-status`} aria-live="polite">${status}</p>
  </div>`;
}
