// SPDX-License-Identifier: MIT
/**
 * MapFrame: points on a map, drawn on a canvas, with pan, zoom, hit testing
 * and a highlighted selection.
 *
 * What it draws is a **scene**: layers of points in data coordinates, each
 * point with a colour (an index into the layer's palette of colours or
 * `--cx-*` token names) and optional labels. How it draws is a **renderer**:
 * `createCanvas2DRenderer()` here; a WebGL renderer with the same three
 * methods (`resize`, `draw`, `destroy`) can replace it for larger maps. The
 * frame owns the view (the transform from data to screen), the events and the
 * hit testing, and calls the renderer once per animation frame when something
 * changed.
 *
 *   layer = {id, x: Float32Array, y: Float32Array, color: Uint16Array,
 *            palette: string[], radius: number, highlight?: Uint8Array,
 *            labels?: [{x, y, text}]}
 *
 * The frame is one tab stop: arrows pan, + and − zoom, 0 fits the map again.
 * A drag pans, the wheel zooms around the pointer, a click picks the nearest
 * point (`onPick`), hovering reports it (`onHover`). `frameRef.current` gives
 * `fit()`, `zoomBy(factor)`, `panBy(dx, dy)`, `hitTest(px, py)`, `view()` and
 * `redraw()`. Every listener, observer and frame is released on unmount.
 */
import { html, useEffect, useLayoutEffect, useRef } from '../core/preact.js';
import { useUid } from '../core/dom.js';

/** Pixels around a point within which a pointer hits it. */
const HIT_RADIUS = 6;
const MIN_SCALE = 0.25;
const MAX_SCALE = 400;

/** A token name's colour (`--cx-hue-3`) on *el*, or the string itself when it is a colour. */
function resolveColor(el, value, cache) {
  if (!value || !value.startsWith('--')) return value;
  if (!cache.has(value)) {
    cache.set(value, getComputedStyle(el).getPropertyValue(value).trim() || '#808080');
  }
  return cache.get(value);
}

/**
 * The Canvas 2D renderer: circles grouped by colour, one path per colour, the
 * highlighted points on top with a ring in the accent colour.
 */
export function createCanvas2DRenderer(canvas) {
  const ctx = canvas.getContext('2d', { alpha: false });
  let dpr = 1;
  let cache = new Map();
  return {
    resize(width, height, ratio) {
      dpr = ratio;
      canvas.width = Math.max(1, Math.round(width * ratio));
      canvas.height = Math.max(1, Math.round(height * ratio));
    },
    /** Forget resolved token colours (the theme changed). */
    invalidate() {
      cache = new Map();
    },
    draw(scene, view) {
      const color = (v) => resolveColor(canvas, v, cache);
      const { width, height } = view;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.globalAlpha = 1;
      ctx.fillStyle = color('--cx-surface');
      ctx.fillRect(0, 0, width, height);
      const sx = view.scale;
      const ox = view.tx;
      const oy = view.ty;
      const anyHighlight = scene.layers.some((l) => l.highlight && l.highlightCount);
      for (const layer of scene.layers) {
        const { x, y } = layer;
        const n = x.length;
        const r = layer.radius || 2.5;
        const buckets = new Map();
        for (let i = 0; i < n; i += 1) {
          const px = x[i] * sx + ox;
          const py = oy - y[i] * sx;
          if (px < -r || py < -r || px > width + r || py > height + r) continue;
          const c = layer.color ? layer.color[i] : 0;
          let bucket = buckets.get(c);
          if (!bucket) {
            bucket = [];
            buckets.set(c, bucket);
          }
          bucket.push(px, py);
        }
        ctx.globalAlpha = anyHighlight ? 0.28 : (layer.alpha || 0.9);
        for (const [c, pts] of buckets) {
          ctx.fillStyle = color(layer.palette[c] || layer.palette[0]);
          ctx.beginPath();
          for (let k = 0; k < pts.length; k += 2) {
            ctx.moveTo(pts[k] + r, pts[k + 1]);
            ctx.arc(pts[k], pts[k + 1], r, 0, Math.PI * 2);
          }
          ctx.fill();
        }
      }
      if (anyHighlight) {
        ctx.globalAlpha = 1;
        ctx.lineWidth = 2;
        ctx.strokeStyle = color('--cx-accent');
        for (const layer of scene.layers) {
          if (!layer.highlight || !layer.highlightCount) continue;
          const r = (layer.radius || 2.5) + 1.5;
          const { x, y } = layer;
          const byColor = new Map();
          for (let i = 0; i < x.length; i += 1) {
            if (!layer.highlight[i]) continue;
            const c = layer.color ? layer.color[i] : 0;
            if (!byColor.has(c)) byColor.set(c, []);
            byColor.get(c).push(x[i] * sx + ox, oy - y[i] * sx);
          }
          for (const [c, pts] of byColor) {
            ctx.fillStyle = color(layer.palette[c] || layer.palette[0]);
            ctx.beginPath();
            for (let k = 0; k < pts.length; k += 2) {
              ctx.moveTo(pts[k] + r, pts[k + 1]);
              ctx.arc(pts[k], pts[k + 1], r, 0, Math.PI * 2);
            }
            ctx.fill();
            ctx.stroke();
          }
        }
      }
      ctx.globalAlpha = 1;
      if (scene.labels && scene.labels.length) {
        ctx.font = `600 12px ${getComputedStyle(canvas).fontFamily || 'sans-serif'}`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.lineJoin = 'round';
        ctx.lineWidth = 3;
        ctx.strokeStyle = color('--cx-surface');
        ctx.fillStyle = color('--cx-text');
        for (const label of scene.labels) {
          const px = label.x * sx + ox;
          const py = oy - label.y * sx;
          if (px < 0 || py < 0 || px > width || py > height) continue;
          ctx.strokeText(label.text, px, py);
          ctx.fillText(label.text, px, py);
        }
      }
    },
    destroy() {
      cache = new Map();
    },
  };
}

/** A grid of the points of each layer, for hit testing in data coordinates. */
function buildGrid(layers, bounds) {
  const span = Math.max(bounds.xmax - bounds.xmin, bounds.ymax - bounds.ymin, 1e-9);
  const cell = span / 64;
  const cells = new Map();
  layers.forEach((layer, li) => {
    for (let i = 0; i < layer.x.length; i += 1) {
      const key = `${Math.floor(layer.x[i] / cell)},${Math.floor(layer.y[i] / cell)}`;
      let list = cells.get(key);
      if (!list) {
        list = [];
        cells.set(key, list);
      }
      list.push(li, i);
    }
  });
  return { cell, cells };
}

/**
 * @param {object} props
 * @param {{layers: Array<object>, labels?: Array<object>, bounds: object}} props.scene
 * @param {string} props.label the map's accessible name
 * @param {string} [props.status] what the map shows now, said to assistive technology
 * @param {(hit: {layer: string, index: number}|null) => void} [props.onPick]
 * @param {(hit: {layer: string, index: number}|null, point: {x, y}|null) => void} [props.onHover]
 * @param {{current: any}} [props.frameRef] receives the frame's methods
 * @param {(ratio: number, dpr: number) => object} [props.renderer] makes a renderer for a canvas
 */
export function MapFrame({ scene, label, status = '', onPick, onHover, frameRef, renderer,
  class: cls = '' }) {
  const box = useRef(null);
  const canvas = useRef(null);
  const state = useRef(null);
  const id = useUid('cx-map');
  const latest = useRef({ scene, onPick, onHover });
  latest.current = { scene, onPick, onHover };

  useLayoutEffect(() => {
    const el = box.current;
    const cv = canvas.current;
    const draw = (renderer || createCanvas2DRenderer)(cv);
    const s = {
      view: { scale: 1, tx: 0, ty: 0, width: 0, height: 0 },
      fitted: false,
      frame: 0,
      drag: null,
      grid: null,
      gridScene: null,
      frames: 0,
    };
    state.current = s;
    const request = () => {
      if (s.frame) return;
      s.frame = requestAnimationFrame(() => {
        s.frame = 0;
        s.frames += 1;
        draw.draw(latest.current.scene, s.view);
      });
    };
    const fit = () => {
      const b = latest.current.scene.bounds;
      const { width, height } = s.view;
      if (!b || b.xmin === null || b.xmin === undefined || !width || !height) return;
      const w = Math.max(b.xmax - b.xmin, 1e-9);
      const h = Math.max(b.ymax - b.ymin, 1e-9);
      const scale = Math.min((width - 32) / w, (height - 32) / h);
      s.view.scale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, scale));
      s.view.tx = width / 2 - ((b.xmin + b.xmax) / 2) * s.view.scale;
      s.view.ty = height / 2 + ((b.ymin + b.ymax) / 2) * s.view.scale;
      s.fitted = true;
      request();
    };
    const zoomAt = (factor, px, py) => {
      const v = s.view;
      const next = Math.max(MIN_SCALE, Math.min(MAX_SCALE, v.scale * factor));
      const k = next / v.scale;
      v.tx = px - (px - v.tx) * k;
      v.ty = py - (py - v.ty) * k;
      v.scale = next;
      request();
    };
    const panBy = (dx, dy) => {
      s.view.tx += dx;
      s.view.ty += dy;
      request();
    };
    const hitTest = (px, py) => {
      const sc = latest.current.scene;
      if (!sc || !sc.layers.length) return null;
      if (s.gridScene !== sc) {
        s.grid = buildGrid(sc.layers, sc.bounds);
        s.gridScene = sc;
      }
      const v = s.view;
      const dx = (px - v.tx) / v.scale;
      const dy = (v.ty - py) / v.scale;
      const reach = HIT_RADIUS / v.scale;
      const { cell, cells } = s.grid;
      let best = null;
      let bestD = reach * reach;
      for (let gx = Math.floor((dx - reach) / cell); gx <= Math.floor((dx + reach) / cell); gx += 1) {
        for (let gy = Math.floor((dy - reach) / cell); gy <= Math.floor((dy + reach) / cell); gy += 1) {
          const list = cells.get(`${gx},${gy}`);
          if (!list) continue;
          for (let k = 0; k < list.length; k += 2) {
            const layer = sc.layers[list[k]];
            const i = list[k + 1];
            const ex = layer.x[i] - dx;
            const ey = layer.y[i] - dy;
            const d = ex * ex + ey * ey;
            if (d <= bestD) {
              bestD = d;
              best = { layer: layer.id, index: i };
            }
          }
        }
      }
      return best;
    };
    const resize = () => {
      const width = el.clientWidth;
      const height = el.clientHeight;
      const ratio = window.devicePixelRatio || 1;
      const was = s.view.width;
      s.view.width = width;
      s.view.height = height;
      draw.resize(width, height, ratio);
      if (!s.fitted || !was) fit();
      else request();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(el);
    resize();

    const point = (event) => {
      const rect = el.getBoundingClientRect();
      return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    };
    const onPointerDown = (event) => {
      if (event.button !== 0) return;
      const p = point(event);
      s.drag = { x: p.x, y: p.y, moved: false, id: event.pointerId };
      el.setPointerCapture(event.pointerId);
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
      if (latest.current.onHover) latest.current.onHover(hitTest(p.x, p.y), p);
    };
    const onPointerUp = (event) => {
      const drag = s.drag;
      s.drag = null;
      if (el.hasPointerCapture(event.pointerId)) el.releasePointerCapture(event.pointerId);
      if (drag && !drag.moved && latest.current.onPick) {
        const p = point(event);
        latest.current.onPick(hitTest(p.x, p.y));
      }
    };
    const onPointerLeave = () => {
      if (!s.drag && latest.current.onHover) latest.current.onHover(null, null);
    };
    const onWheel = (event) => {
      event.preventDefault();
      const p = point(event);
      zoomAt(Math.exp(-event.deltaY * 0.0015), p.x, p.y);
    };
    const onKeyDown = (event) => {
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
      draw.invalidate();
      request();
    };
    const media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
    const themeObserver = new MutationObserver(onTheme);
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'style'] });
    if (media) media.addEventListener('change', onTheme);
    el.addEventListener('pointerdown', onPointerDown);
    el.addEventListener('pointermove', onPointerMove);
    el.addEventListener('pointerup', onPointerUp);
    el.addEventListener('pointercancel', onPointerUp);
    el.addEventListener('pointerleave', onPointerLeave);
    el.addEventListener('wheel', onWheel, { passive: false });
    el.addEventListener('keydown', onKeyDown);

    s.api = {
      fit,
      zoomBy: (factor) => zoomAt(factor, s.view.width / 2, s.view.height / 2),
      panBy,
      hitTest,
      view: () => ({ ...s.view }),
      redraw: request,
      frames: () => s.frames,
    };
    if (frameRef) frameRef.current = s.api;
    return () => {
      cancelAnimationFrame(s.frame);
      observer.disconnect();
      themeObserver.disconnect();
      if (media) media.removeEventListener('change', onTheme);
      el.removeEventListener('pointerdown', onPointerDown);
      el.removeEventListener('pointermove', onPointerMove);
      el.removeEventListener('pointerup', onPointerUp);
      el.removeEventListener('pointercancel', onPointerUp);
      el.removeEventListener('pointerleave', onPointerLeave);
      el.removeEventListener('wheel', onWheel);
      el.removeEventListener('keydown', onKeyDown);
      draw.destroy();
      if (frameRef) frameRef.current = null;
      state.current = null;
    };
  }, []);

  // A new scene: draw it (fit it when its bounds are new).
  const lastBounds = useRef(null);
  useEffect(() => {
    const s = state.current;
    if (!s || !s.api) return;
    const b = scene.bounds;
    const key = b ? `${b.xmin},${b.xmax},${b.ymin},${b.ymax}` : '';
    if (key !== lastBounds.current) {
      lastBounds.current = key;
      s.api.fit();
    } else {
      s.api.redraw();
    }
  }, [scene]);

  return html`<div class=${`cx-map-frame ${cls}`}>
    <div ref=${box} class="cx-map-frame__box" tabindex="0" role="group" aria-label=${label}
      aria-describedby=${`${id}-status`}>
      <canvas ref=${canvas} class="cx-map-frame__canvas" aria-hidden="true"></canvas>
    </div>
    <p class="cx-visually-hidden" id=${`${id}-status`} aria-live="polite">${status}</p>
  </div>`;
}
