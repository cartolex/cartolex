// SPDX-License-Identifier: MIT
/**
 * The map's core: the view (data to screen), fitting, zooming, the detail a
 * zoom shows, hit testing, convex hulls, label placement and colours.
 *
 * This module and its siblings (`canvas2d.js`, `webgl.js`, `controller.js`)
 * use no library and no DOM beyond the canvas they are given, import only
 * each other, and declare everything they export with `export function` or
 * `export const`: the offline site can load them as they are (ES modules,
 * served over HTTP) or turned into one classic script
 * (`cartolex.app.static_files.classic_script`), which is what a page opened
 * from `file://` needs.
 *
 * A **scene** is what a renderer draws:
 *
 *   {layers: [{id, x, y (Float32Array), color (Uint16Array, into palette),
 *              palette (colours or `--cx-*` token names), radius, alpha,
 *              shape ('circle' | 'square' | 'triangle' | 'diamond' | 'ring' | 'plus' | 'tile'),
 *              size? (Float32Array: each point's radius as a multiple of the layer's),
 *              rank? (Float32Array: 0 the most important, 1 the least, 2 hidden),
 *              detail? (the share of ranks shown at the fitted zoom),
 *              highlight? (Uint8Array: 1 lit, 2 and more the focus), highlightCount?,
 *              dim? (the alpha of the points not lit while some are; 0.3 by default),
 *              ringFrom? (the highlight from which a point gets the accent ring; 1)}],
 *    regions?: [{polygon: Float32Array [x0, y0, x1, y1…] (convex), color, alpha}],
 *    lines?: [{x, y (Float32Array: pairs of points, one segment each), color, alpha, width}],
 *    labels?: [{x, y, text, minZoom? (shown from this zoom on), strong? (always, first),
 *               offset? (pixels above the point: the label of a point, not over it),
 *               color? (a colour or a token; the text's colour by default),
 *               size? (pixels; 12, 13 when strong)}],
 *    bounds: {xmin, xmax, ymin, ymax}}
 *
 * A scene in three dimensions says `dimensions: 3`: its layers carry `z` (Float32Array) beside
 * `x` and `y`, its lines `z` pairs, its labels `z`, its bounds `zmin` and `zmax`, and its
 * regions `members` (`{x, y, z}`: the member points; their projection is hulled on screen at
 * each frame) in place of a `polygon`. It is drawn by `controller3d.js` (`webgl3d.js`,
 * `canvas3d.js`); everything else of the scene means what it means in 2D.
 *
 * A **view** is `{scale, tx, ty, width, height, fitScale}`: a data point
 * (x, y) is on screen at (x · scale + tx, ty − y · scale); its `zoom` is
 * `scale / fitScale` (1 when the whole map fits). A 3D view (`space.js`, `dims: 3`) is an
 * orbit camera with the same `scale`, `fitScale` and zoom.
 */
import { projectPoint } from './space.js';

export const MIN_SCALE = 1e-4;
export const MAX_ZOOM = 400;
/** Pixels around a point within which a pointer hits it. */
export const HIT_RADIUS = 6;
/** The shapes a layer's points take, and their number in the shaders. */
export const SHAPES = { circle: 0, square: 1, triangle: 2, diamond: 3, ring: 4, plus: 5, tile: 6 };

/** A new view, before its first fit. */
export function createView() {
  return { scale: 1, tx: 0, ty: 0, width: 0, height: 0, fitScale: 1 };
}

/** Fit *bounds* in the view, with *pad* pixels around; sets `fitScale`. */
export function fitView(view, bounds, pad = 16) {
  if (!bounds || bounds.xmin === null || bounds.xmin === undefined || !view.width || !view.height) {
    return false;
  }
  const w = Math.max(bounds.xmax - bounds.xmin, 1e-9);
  const h = Math.max(bounds.ymax - bounds.ymin, 1e-9);
  const scale = Math.max(MIN_SCALE, Math.min((view.width - 2 * pad) / w, (view.height - 2 * pad) / h));
  view.scale = scale;
  view.fitScale = scale;
  view.tx = view.width / 2 - ((bounds.xmin + bounds.xmax) / 2) * scale;
  view.ty = view.height / 2 + ((bounds.ymin + bounds.ymax) / 2) * scale;
  return true;
}

/** Zoom by *factor* around the screen point (px, py), within the zoom limits. */
export function zoomView(view, factor, px, py) {
  const lo = view.fitScale / 4;
  const hi = view.fitScale * MAX_ZOOM;
  const next = Math.max(lo, Math.min(hi, view.scale * factor));
  const k = next / view.scale;
  view.tx = px - (px - view.tx) * k;
  view.ty = py - (py - view.ty) * k;
  view.scale = next;
}

/** Centre the view on the data point (x, y), at *zoom* (relative to the fit) when given. */
export function centreView(view, x, y, zoom) {
  if (zoom) view.scale = Math.max(view.fitScale / 4, Math.min(view.fitScale * MAX_ZOOM, view.fitScale * zoom));
  view.tx = view.width / 2 - x * view.scale;
  view.ty = view.height / 2 + y * view.scale;
}

/** The zoom of a view: 1 when the map fits. */
export function zoomOf(view) {
  return view.scale / (view.fitScale || view.scale || 1);
}

/**
 * The largest rank a layer shows at *zoom*: its `detail` at the fitted zoom,
 * growing with the square of the zoom (a zoom of 2 shows four times as many),
 * up to every rank below 1; ranks of 2 (filtered out) are never shown.
 */
export function detailLimit(layer, zoom) {
  if (!layer.rank) return 1.5;
  const base = layer.detail === undefined ? 1 : layer.detail;
  return Math.min(1.5, base * Math.max(zoom, 1) * Math.max(zoom, 1));
}

/** A grid of the points of each layer, for hit testing in data coordinates. */
export function buildGrid(layers, bounds) {
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
 * The point nearest the screen point (px, py) within `HIT_RADIUS` (plus its
 * radius) among the points the view shows: `{layer, index}` or null.
 */
export function hitTest(scene, grid, view, px, py) {
  if (!scene || !scene.layers.length || !grid) return null;
  const zoom = zoomOf(view);
  const limits = scene.layers.map((layer) => detailLimit(layer, zoom));
  const dx = (px - view.tx) / view.scale;
  const dy = (view.ty - py) / view.scale;
  const biggest = Math.max(4, ...scene.layers.map((l) => (l.radius || 2.5) * (l.sizeMax || 1)));
  const reach = (HIT_RADIUS + biggest) / view.scale;
  const { cell, cells } = grid;
  let best = null;
  let bestD = reach * reach;
  for (let gx = Math.floor((dx - reach) / cell); gx <= Math.floor((dx + reach) / cell); gx += 1) {
    for (let gy = Math.floor((dy - reach) / cell); gy <= Math.floor((dy + reach) / cell); gy += 1) {
      const list = cells.get(`${gx},${gy}`);
      if (!list) continue;
      for (let k = 0; k < list.length; k += 2) {
        const li = list[k];
        const layer = scene.layers[li];
        const i = list[k + 1];
        if (layer.rank && layer.rank[i] > limits[li]) continue;
        if (layer.pickable === false) continue;
        const ex = layer.x[i] - dx;
        const ey = layer.y[i] - dy;
        const d = ex * ex + ey * ey;
        const r = (HIT_RADIUS + (layer.radius || 2.5) * (layer.size ? layer.size[i] : 1)) / view.scale;
        if (d <= Math.min(bestD, r * r)) {
          bestD = d;
          best = { layer: layer.id, index: i };
        }
      }
    }
  }
  return best;
}

/** The convex hull of points (x[i], y[i]), counter-clockwise, as `[x0, y0, x1, y1…]`. */
export function convexHull(xs, ys) {
  const pts = [];
  for (let i = 0; i < xs.length; i += 1) pts.push([xs[i], ys[i]]);
  pts.sort((a, b) => (a[0] - b[0]) || (a[1] - b[1]));
  if (pts.length < 3) return Float32Array.from(pts.flat());
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  const upper = [];
  for (let i = pts.length - 1; i >= 0; i -= 1) {
    const p = pts[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  upper.pop();
  lower.pop();
  return Float32Array.from(lower.concat(upper).flat());
}

/**
 * The labels to draw: the strong ones first, then the others in their order
 * once the zoom reaches their `minZoom`; a label that would overlap one
 * already placed is left out. *measure(text, strong)* gives a label's width
 * in pixels. Returns `[{text, px, py, w, strong}]`.
 */
export function placeLabels(labels, view, measure) {
  if (!labels || !labels.length) return [];
  const zoom = zoomOf(view);
  const placed = [];
  const order = labels.filter((l) => l.strong).concat(labels.filter((l) => !l.strong));
  const space = view.dims === 3;
  const q = [0, 0, 0, 0];
  for (const label of order) {
    if (!label.strong && (label.minZoom || 0) > zoom) continue;
    let px = 0;
    let py = 0;
    if (space) {
      if (!projectPoint(view, label.x, label.y, label.z || 0, q)) continue;
      px = q[0];
      py = q[1] - (label.offset || 0);
    } else {
      px = label.x * view.scale + view.tx;
      py = view.ty - label.y * view.scale - (label.offset || 0);
    }
    if (px < 0 || py < 0 || px > view.width || py > view.height) continue;
    const w = measure(label.text, Boolean(label.strong), label) + 8;
    const half = Math.max(9, (label.size || 12) * 0.75);
    const box = [px - w / 2, py - half, px + w / 2, py + half];
    if (placed.some((o) => box[0] < o.box[2] && box[2] > o.box[0] && box[1] < o.box[3] && box[3] > o.box[1])) {
      continue;
    }
    placed.push({ text: label.text, px, py, w, strong: Boolean(label.strong), box, color: label.color,
      size: label.size });
  }
  return placed;
}

/** A token name's colour (`--cx-hue-3`) on *el*, or the string itself when it is a colour. */
export function resolveColor(el, value, cache) {
  if (!value || !value.startsWith('--')) return value || '#808080';
  if (!cache.has(value)) {
    cache.set(value, getComputedStyle(el).getPropertyValue(value).trim() || '#808080');
  }
  return cache.get(value);
}

/** `#rgb`, `#rrggbb`, `#rrggbbaa`, `rgb(…)` and `rgba(…)` as `[r, g, b, a]` in 0..1, else null. */
export function parseColor(color) {
  const c = String(color).trim();
  let m = /^#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i.exec(c);
  if (m) {
    let hex = m[1];
    if (hex.length === 3) hex = hex.split('').map((h) => h + h).join('');
    const v = (k) => parseInt(hex.slice(k, k + 2), 16) / 255;
    return [v(0), v(2), v(4), hex.length === 8 ? v(6) : 1];
  }
  m = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)(?:[\s,/]+([\d.]+)(%?))?\s*\)$/i.exec(c);
  if (m) {
    const a = m[4] === undefined ? 1 : Number(m[4]) / (m[5] ? 100 : 1);
    return [Number(m[1]) / 255, Number(m[2]) / 255, Number(m[3]) / 255, a];
  }
  return null;
}

/**
 * A CSS colour as `[r, g, b, a]` in 0..1, through a 2D context (which reads
 * every colour syntax the browser knows); `cache` keeps what was read.
 */
export function colorToRgba(color, cache, probe) {
  if (cache.has(color)) return cache.get(color);
  let out = parseColor(color);
  if (out) {
    cache.set(color, out);
    return out;
  }
  out = [0.5, 0.5, 0.5, 1];
  const ctx = probe || (typeof document !== 'undefined'
    ? document.createElement('canvas').getContext('2d', { willReadFrequently: true }) : null);
  if (ctx) {
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = '#000';
    ctx.fillStyle = color;
    ctx.fillRect(0, 0, 1, 1);
    const d = ctx.getImageData(0, 0, 1, 1).data;
    out = [d[0] / 255, d[1] / 255, d[2] / 255, d[3] / 255];
  }
  cache.set(color, out);
  return out;
}
