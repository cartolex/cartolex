// SPDX-License-Identifier: MIT
/**
 * The map's controller: the view, the events and the frame loop around a
 * renderer, without any library, so that the app's MapFrame and the offline
 * site share it.
 *
 * The box is one tab stop: arrows pan, + and − zoom, 0 fits the map again. A
 * drag pans, the wheel zooms around the pointer, a click picks the nearest
 * point shown (`onPick`), hovering reports it with the pointer's place
 * (`onHover`). The renderer is drawn once per animation frame when something
 * changed. `destroy()` releases every listener, observer and frame.
 */
import {
  buildGrid, centreView, createView, fitView, hitTest, zoomOf, zoomView,
} from './core.js';
import { createCanvas2DRenderer } from './canvas2d.js';
import { createWebGLRenderer } from './webgl.js';

/**
 * The renderer of *canvas*: `'webgl'` (null without WebGL), `'canvas2d'`,
 * `'auto'` (WebGL when the browser has it, else Canvas 2D) or a factory.
 */
export function makeRenderer(canvas, kind = 'auto') {
  if (typeof kind === 'function') return kind(canvas);
  if (kind === 'canvas2d') return createCanvas2DRenderer(canvas);
  const gl = createWebGLRenderer(canvas);
  if (gl || kind === 'webgl') return gl;
  return createCanvas2DRenderer(canvas);
}

/**
 * @param {object} options
 * @param {HTMLElement} options.box the focusable element that holds the canvas
 * @param {HTMLCanvasElement} options.canvas
 * @param {() => object} options.scene the scene to draw now
 * @param {string|Function} [options.renderer] see makeRenderer
 * @param {(hit: object|null) => void} [options.onPick]
 * @param {(hit: object|null, point: {x, y}|null) => void} [options.onHover]
 * @param {(view: object) => void} [options.onView] after each change of the view
 */
export function createMapController(options) {
  const { box, canvas } = options;
  let renderer = makeRenderer(canvas, options.renderer);
  if (!renderer) renderer = createCanvas2DRenderer(canvas);
  const s = {
    view: createView(),
    fitted: false,
    frame: 0,
    drag: null,
    grid: null,
    gridScene: null,
    frames: 0,
    drawMs: [],
  };
  const scene = () => options.scene();
  const request = () => {
    if (s.frame) return;
    s.frame = requestAnimationFrame(() => {
      s.frame = 0;
      s.frames += 1;
      const start = performance.now();
      renderer.draw(scene(), s.view);
      s.drawMs.push(performance.now() - start);
      if (s.drawMs.length > 240) s.drawMs.shift();
      if (options.onView) options.onView(s.view);
    });
  };
  const fit = () => {
    if (fitView(s.view, scene().bounds)) s.fitted = true;
    request();
  };
  const zoomAt = (factor, px, py) => {
    zoomView(s.view, factor, px, py);
    request();
  };
  const panBy = (dx, dy) => {
    s.view.tx += dx;
    s.view.ty += dy;
    request();
  };
  const hit = (px, py) => {
    const sc = scene();
    if (!sc || !sc.layers.length) return null;
    if (s.gridScene !== sc) {
      s.grid = buildGrid(sc.layers, sc.bounds);
      s.gridScene = sc;
    }
    return hitTest(sc, s.grid, s.view, px, py);
  };
  const resize = () => {
    const width = box.clientWidth;
    const height = box.clientHeight;
    const ratio = window.devicePixelRatio || 1;
    const was = s.view.width;
    const keep = s.fitted && was ? zoomOf(s.view) : 0;
    const centre = keep ? [(was / 2 - s.view.tx) / s.view.scale, (s.view.ty - s.view.height / 2) / s.view.scale] : null;
    s.view.width = width;
    s.view.height = height;
    renderer.resize(width, height, ratio);
    if (!s.fitted || !was) {
      fit();
    } else {
      // Keep the zoom and the centre; the fitted scale follows the new size.
      fitView(s.view, scene().bounds);
      centreView(s.view, centre[0], centre[1], keep);
      request();
    }
  };
  const observer = new ResizeObserver(resize);
  observer.observe(box);
  resize();

  const point = (event) => {
    const rect = box.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };
  const onPointerDown = (event) => {
    if (event.button !== 0) return;
    const p = point(event);
    s.drag = { x: p.x, y: p.y, moved: false, id: event.pointerId };
    if (box.setPointerCapture) box.setPointerCapture(event.pointerId);
  };
  const onPointerMove = (event) => {
    const p = point(event);
    if (s.drag) {
      const dx = p.x - s.drag.x;
      const dy = p.y - s.drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 2) s.drag.moved = true;
      s.drag.x = p.x;
      s.drag.y = p.y;
      panBy(dx, dy);
      return;
    }
    if (options.onHover) options.onHover(hit(p.x, p.y), p);
  };
  const onPointerUp = (event) => {
    const drag = s.drag;
    s.drag = null;
    if (box.hasPointerCapture && box.hasPointerCapture(event.pointerId)) box.releasePointerCapture(event.pointerId);
    if (drag && !drag.moved && options.onPick) {
      const p = point(event);
      options.onPick(hit(p.x, p.y));
    }
  };
  const onPointerLeave = () => {
    if (!s.drag && options.onHover) options.onHover(null, null);
  };
  const onWheel = (event) => {
    event.preventDefault();
    const p = point(event);
    zoomAt(Math.exp(-event.deltaY * 0.0015), p.x, p.y);
  };
  const onKeyDown = (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const step = 40;
    const { width, height } = s.view;
    if (event.key === 'ArrowLeft') panBy(step, 0);
    else if (event.key === 'ArrowRight') panBy(-step, 0);
    else if (event.key === 'ArrowUp') panBy(0, step);
    else if (event.key === 'ArrowDown') panBy(0, -step);
    else if (event.key === '+' || event.key === '=') zoomAt(1.25, width / 2, height / 2);
    else if (event.key === '-' || event.key === '_') zoomAt(0.8, width / 2, height / 2);
    else if (event.key === '0') fit();
    else return;
    event.preventDefault();
  };
  // A theme switch changes the token colours: resolve them again.
  const onTheme = () => {
    renderer.invalidate();
    request();
  };
  const media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  const themeObserver = new MutationObserver(onTheme);
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'style'] });
  if (media) media.addEventListener('change', onTheme);
  box.addEventListener('pointerdown', onPointerDown);
  box.addEventListener('pointermove', onPointerMove);
  box.addEventListener('pointerup', onPointerUp);
  box.addEventListener('pointercancel', onPointerUp);
  box.addEventListener('pointerleave', onPointerLeave);
  box.addEventListener('wheel', onWheel, { passive: false });
  box.addEventListener('keydown', onKeyDown);

  return {
    renderer: renderer.name || 'custom',
    dimensions: 2,
    fit,
    zoomBy: (factor) => zoomAt(factor, s.view.width / 2, s.view.height / 2),
    panBy,
    /** Centre on the data point (x, y), at *zoom* (relative to the fitted map) when given. */
    centreOn(x, y, zoom) {
      centreView(s.view, x, y, zoom);
      request();
    },
    /** Where (x, y) is on screen now: `[px, py]`. */
    project: (x, y) => [x * s.view.scale + s.view.tx, s.view.ty - y * s.view.scale],
    hitTest: hit,
    view: () => ({ ...s.view, zoom: zoomOf(s.view) }),
    /** Draw again (the scene changed); *refit* fits it first. */
    redraw(refit = false) {
      if (refit || !s.fitted) fit();
      else request();
    },
    frames: () => s.frames,
    /** The draw times of the last frames, in milliseconds. */
    stats: () => ({ frames: s.frames, drawMs: s.drawMs.slice() }),
    destroy() {
      cancelAnimationFrame(s.frame);
      observer.disconnect();
      themeObserver.disconnect();
      if (media) media.removeEventListener('change', onTheme);
      box.removeEventListener('pointerdown', onPointerDown);
      box.removeEventListener('pointermove', onPointerMove);
      box.removeEventListener('pointerup', onPointerUp);
      box.removeEventListener('pointercancel', onPointerUp);
      box.removeEventListener('pointerleave', onPointerLeave);
      box.removeEventListener('wheel', onWheel);
      box.removeEventListener('keydown', onKeyDown);
      renderer.destroy();
    },
  };
}
