// SPDX-License-Identifier: MIT
/**
 * The controller of a map in three dimensions: an orbit camera (`space.js`) about a target,
 * the events and the frame loop around a renderer (`webgl3d.js`, else `canvas3d.js`). It
 * answers what the 2D controller answers (`controller.js`), so a host drives either the same
 * way, and adds the turn.
 *
 * The box is one tab stop: the arrows turn the map, Shift and the arrows pan, + and − zoom,
 * 0 fits it again, the space bar starts or stops turning. A drag turns, a drag with Shift
 * (or the right or middle button) pans, the wheel zooms toward the pointer, a click picks
 * the front-most point under it (`onPick`), hovering reports it (`onHover`). Turning on its
 * own never starts by itself, and is slower when the person asks for reduced motion.
 */
import { convexHull, detailLimit, zoomOf } from './core.js';
import { createCanvas3DRenderer } from './canvas3d.js';
import { createWebGL3DRenderer } from './webgl3d.js';
import {
  centreView3, createProjections, createView3, fitView3, hitTest3, panView3, projectPoint, sceneSphere,
  screenRegion, turnView3, updateView3, zoomView3,
} from './space.js';

/** Radians turned per pixel dragged, per arrow press, and per frame while turning. */
const TURN_DRAG = 0.008;
const TURN_KEY = 0.12;
const TURN_SPIN = 0.004;

/** The renderer of a 3D map on *canvas*: `'webgl'` (null without WebGL), `'canvas2d'`, `'auto'`
 * or a factory. */
export function makeRenderer3D(canvas, kind = 'auto') {
  if (typeof kind === 'function') return kind(canvas);
  if (kind === 'canvas2d') return createCanvas3DRenderer(canvas);
  const gl = createWebGL3DRenderer(canvas);
  if (gl || kind === 'webgl') return gl;
  return createCanvas3DRenderer(canvas);
}

/**
 * Options as `createMapController`'s, and *onTurn(on)* told when the turning starts or
 * stops.
 */
export function createMapController3D(options) {
  const { box, canvas } = options;
  let renderer = makeRenderer3D(canvas, options.renderer);
  if (!renderer) renderer = createCanvas3DRenderer(canvas);
  const s = {
    view: createView3(),
    fitted: false,
    frame: 0,
    drag: null,
    frames: 0,
    drawMs: [],
    turning: false,
  };
  const projections = createProjections();
  const reduced = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
  const scene = () => options.scene();
  const request = () => {
    if (s.frame) return;
    s.frame = requestAnimationFrame(() => {
      s.frame = 0;
      s.frames += 1;
      if (s.turning && !s.drag) {
        turnView3(s.view, TURN_SPIN * (reduced && reduced.matches ? 0.25 : 1), 0);
      }
      const start = performance.now();
      renderer.draw(scene(), s.view);
      s.drawMs.push(performance.now() - start);
      if (s.drawMs.length > 240) s.drawMs.shift();
      if (options.onView) options.onView(s.view);
      if (s.turning) request();
    });
  };
  // the sphere the map fits in, kept per scene's bounds (the points move little between scenes)
  let sphere = { key: '', value: null };
  const sphereNow = (sc) => {
    const b = sc.bounds;
    const key = b ? `${b.xmin},${b.xmax},${b.ymin},${b.ymax},${b.zmin},${b.zmax}` : '';
    if (sphere.key !== key || !sphere.value) sphere = { key, value: b && sc.layers.length ? sceneSphere(sc) : null };
    return sphere.value;
  };
  const fit = () => {
    const sc = scene();
    if (fitView3(s.view, sc.bounds, 16, sphereNow(sc))) s.fitted = true;
    request();
  };
  const zoomAt = (factor, px, py) => {
    zoomView3(s.view, factor, px, py);
    request();
  };
  const panBy = (dx, dy) => {
    panView3(s.view, dx, dy);
    request();
  };
  const turnBy = (dyaw, dpitch) => {
    turnView3(s.view, dyaw, dpitch);
    request();
  };
  const setTurning = (on) => {
    const next = Boolean(on);
    if (next === s.turning) return;
    s.turning = next;
    if (options.onTurn) options.onTurn(next);
    request();
  };
  const hit = (px, py) => {
    const sc = scene();
    if (!sc || !sc.layers.length) return null;
    const zoom = zoomOf(s.view);
    const projected = renderer.projected ? renderer.projected(sc, s.view) : projections.get(sc, s.view);
    return hitTest3(sc, projected, sc.layers.map((layer) => detailLimit(layer, zoom)), px, py);
  };
  const resize = () => {
    const width = box.clientWidth;
    const height = box.clientHeight;
    const ratio = window.devicePixelRatio || 1;
    const was = s.view.width;
    s.view.width = width;
    s.view.height = height;
    renderer.resize(width, height, ratio);
    if (!s.fitted || !was) fit();
    else {
      // keep the camera; the fitted distance follows the new size
      const keep = { dist: s.view.dist / s.view.fitDist, tx: s.view.tx, ty: s.view.ty, tz: s.view.tz };
      fitView3(s.view, scene().bounds, 16, sphereNow(scene()));
      s.view.dist = s.view.fitDist * keep.dist;
      s.view.tx = keep.tx;
      s.view.ty = keep.ty;
      s.view.tz = keep.tz;
      updateView3(s.view);
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
    if (event.button > 2) return;
    const p = point(event);
    s.drag = { x: p.x, y: p.y, moved: false, id: event.pointerId, pan: event.shiftKey || event.button > 0,
      button: event.button };
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
      if (s.drag.pan) panBy(dx, dy);
      else {
        setTurning(false);
        turnBy(dx * TURN_DRAG, dy * TURN_DRAG);
      }
      return;
    }
    if (options.onHover) options.onHover(hit(p.x, p.y), p);
  };
  const onPointerUp = (event) => {
    const drag = s.drag;
    s.drag = null;
    if (box.hasPointerCapture && box.hasPointerCapture(event.pointerId)) box.releasePointerCapture(event.pointerId);
    if (drag && !drag.moved && drag.button === 0 && options.onPick) {
      const p = point(event);
      options.onPick(hit(p.x, p.y));
    }
  };
  const onPointerLeave = () => {
    if (!s.drag && options.onHover) options.onHover(null, null);
  };
  const onContextMenu = (event) => event.preventDefault();
  const onWheel = (event) => {
    event.preventDefault();
    const p = point(event);
    zoomAt(Math.exp(-event.deltaY * 0.0015), p.x, p.y);
  };
  const onKeyDown = (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const step = 40;
    const { width, height } = s.view;
    const arrows = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    if (arrows[event.key]) {
      const [ax, ay] = arrows[event.key];
      if (event.shiftKey) panBy(-ax * step, -ay * step);
      else turnBy(ax * TURN_KEY, ay * TURN_KEY);
    } else if (event.key === '+' || event.key === '=') zoomAt(1.25, width / 2, height / 2);
    else if (event.key === '-' || event.key === '_') zoomAt(0.8, width / 2, height / 2);
    else if (event.key === '0') fit();
    else if (event.key === ' ' && event.target === box) setTurning(!s.turning);
    else return;
    event.preventDefault();
  };
  const onTheme = () => {
    renderer.invalidate();
    request();
  };
  const media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  const themeObserver = new MutationObserver(onTheme);
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'style'] });
  if (media) media.addEventListener('change', onTheme);
  const events = [['pointerdown', onPointerDown], ['pointermove', onPointerMove], ['pointerup', onPointerUp],
    ['pointercancel', onPointerUp], ['pointerleave', onPointerLeave], ['contextmenu', onContextMenu],
    ['keydown', onKeyDown]];
  for (const [type, fn] of events) box.addEventListener(type, fn);
  box.addEventListener('wheel', onWheel, { passive: false });

  return {
    renderer: renderer.name || 'custom',
    dimensions: 3,
    fit,
    zoomBy: (factor) => zoomAt(factor, s.view.width / 2, s.view.height / 2),
    panBy,
    /** Turn by *dyaw* and *dpitch* radians. */
    turnBy,
    /** Start (true) or stop (false) turning; without an argument, the other way. */
    turn(on) {
      setTurning(on === undefined ? !s.turning : on);
    },
    turning: () => s.turning,
    /** The front view: x to the right, y up, as the flat map; the zoom and target are kept. */
    front() {
      setTurning(false);
      s.view.yaw = 0;
      s.view.pitch = 0;
      updateView3(s.view);
      request();
    },
    /** Centre on (x, y, z), at *zoom* (relative to the fitted map) when given. */
    centreOn(x, y, zoom, z = 0) {
      centreView3(s.view, x, y, z, zoom);
      request();
    },
    /** Where (x, y, z) is on screen now: `[px, py]`, or null behind the camera. */
    project(x, y, z = 0) {
      const at = projectPoint(s.view, x, y, z);
      return at ? [at[0], at[1]] : null;
    },
    /** The screen polygon of a region's members now (for the browser tests). */
    regionOnScreen: (members) => screenRegion(s.view, members, convexHull),
    hitTest: hit,
    view: () => ({ ...s.view, zoom: zoomOf(s.view) }),
    redraw(refit = false) {
      if (refit || !s.fitted) fit();
      else request();
    },
    frames: () => s.frames,
    stats: () => ({ frames: s.frames, drawMs: s.drawMs.slice() }),
    destroy() {
      cancelAnimationFrame(s.frame);
      s.turning = false;
      observer.disconnect();
      themeObserver.disconnect();
      if (media) media.removeEventListener('change', onTheme);
      for (const [type, fn] of events) box.removeEventListener(type, fn);
      box.removeEventListener('wheel', onWheel);
      renderer.destroy();
    },
  };
}
