// SPDX-License-Identifier: MIT
/**
 * « Save the view »: the map as it is on screen (its pan and zoom, the layers shown, their
 * colours, the selection, the labels the zoom shows), as a PNG image (drawn again at twice
 * the screen's resolution by the Canvas 2D renderer) or an SVG image (every point, region,
 * line and label a vector shape), with or without its legend. A map in three dimensions is
 * saved as it is projected on screen now (its turn, its depth). Nothing leaves the browser:
 * the file is made here and handed to the browser to save.
 */
import { createCanvas2DRenderer, tracePoint } from '../components/map/canvas2d.js';
import { DEPTH_FADE, createCanvas3DRenderer, spaceOrder, spaceRadius } from '../components/map/canvas3d.js';
import { convexHull, detailLimit, placeLabels, resolveColor, zoomOf } from '../components/map/core.js';
import { depthShare, projectLayer, projectPoint, screenRegion } from '../components/map/space.js';

/** The pixels of the legend's rows and its margins. */
const LEGEND_ROW = 18;
const LEGEND_PAD = 10;
const LEGEND_SWATCH = 10;

/** A copy of the frame's view (its size, scale and offsets; in three dimensions, its camera). */
function savedView(frame) {
  const v = frame.view();
  if (v.dims === 3) return { ...v };
  return { width: v.width, height: v.height, scale: v.scale, tx: v.tx, ty: v.ty, fitScale: v.fitScale };
}

function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** The file's name: the host's stem and the time, without characters a file system refuses. */
function savedName(stem, ext) {
  const time = new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-');
  return `map-${String(stem || 'view').replace(/[^\w.-]/g, '')}-${time}.${ext}`;
}

/** The legend's size in pixels: `{w, h}` for *legend* (`{kinds: [{shape, text}], entries:
 * [{color, text}]}`), measured with *measure(text)*. */
function legendSize(legend, measure) {
  const rows = [...legend.kinds, ...legend.entries];
  const w = Math.max(80, ...rows.map((r) => measure(r.text))) + LEGEND_SWATCH + 3 * LEGEND_PAD;
  return { w, h: rows.length * LEGEND_ROW + 2 * LEGEND_PAD };
}

// ── PNG ──────────────────────────────────────────────────────────────────────

function pngLegend(ctx, legend, view, color, font) {
  ctx.font = `500 12px ${font}`;
  const { w, h } = legendSize(legend, (text) => ctx.measureText(text).width);
  const x0 = LEGEND_PAD;
  const y0 = view.height - h - LEGEND_PAD;
  ctx.globalAlpha = 0.92;
  ctx.fillStyle = color('--cx-surface');
  ctx.fillRect(x0, y0, w, h);
  ctx.globalAlpha = 1;
  ctx.strokeStyle = color('--cx-border');
  ctx.lineWidth = 1;
  ctx.strokeRect(x0 + 0.5, y0 + 0.5, w - 1, h - 1);
  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';
  let y = y0 + LEGEND_PAD + LEGEND_ROW / 2;
  for (const row of [...legend.kinds, ...legend.entries]) {
    ctx.fillStyle = color(row.color || '--cx-text-muted');
    ctx.beginPath();
    if (row.shape) tracePoint(ctx, row.shape === 'ring' ? 'circle' : row.shape, x0 + LEGEND_PAD + LEGEND_SWATCH / 2, y, 4);
    else ctx.rect(x0 + LEGEND_PAD, y - LEGEND_SWATCH / 2, LEGEND_SWATCH, LEGEND_SWATCH);
    ctx.fill();
    ctx.fillStyle = color('--cx-text');
    ctx.fillText(row.text, x0 + 2 * LEGEND_PAD + LEGEND_SWATCH, y);
    y += LEGEND_ROW;
  }
}

/** The view as a PNG file, at *ratio* times its size on screen. */
export function savePng({ frame, box, scene, legend = null, stem, ratio = 2 }) {
  const view = savedView(frame);
  const canvas = document.createElement('canvas');
  canvas.className = 'cx-atlas-save';
  box.appendChild(canvas); // inside the page: the colour tokens resolve on it
  try {
    const renderer = view.dims === 3 ? createCanvas3DRenderer(canvas) : createCanvas2DRenderer(canvas);
    renderer.resize(view.width, view.height, ratio);
    renderer.draw(scene, view);
    if (legend) {
      const ctx = canvas.getContext('2d');
      const cache = new Map();
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      pngLegend(ctx, legend, view, (v) => resolveColor(canvas, v, cache),
        getComputedStyle(canvas).fontFamily || 'sans-serif');
    }
    return new Promise((resolve) => {
      canvas.toBlob((blob) => {
        canvas.remove();
        if (blob) downloadBlob(blob, savedName(stem, 'png'));
        resolve(Boolean(blob));
      }, 'image/png');
    });
  } catch (error) {
    canvas.remove();
    throw error;
  }
}

// ── SVG ──────────────────────────────────────────────────────────────────────

const esc = (text) => String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const n2 = (v) => (Math.round(v * 100) / 100).toString();

/** One point of *shape* as SVG path data. */
function pointPath(shape, x, y, r) {
  if (shape === 'tile') {
    const a = r * 0.9;
    const c = r * 0.35;
    return `M${n2(x - a + c)} ${n2(y - a)}h${n2(2 * (a - c))}a${n2(c)} ${n2(c)} 0 0 1 ${n2(c)} ${n2(c)}v${n2(2 * (a - c))}a${n2(c)} ${n2(c)} 0 0 1 ${n2(-c)} ${n2(c)}h${n2(-2 * (a - c))}a${n2(c)} ${n2(c)} 0 0 1 ${n2(-c)} ${n2(-c)}v${n2(-2 * (a - c))}a${n2(c)} ${n2(c)} 0 0 1 ${n2(c)} ${n2(-c)}z`;
  }
  if (shape === 'square') {
    const a = r * 0.9;
    return `M${n2(x - a)} ${n2(y - a)}h${n2(2 * a)}v${n2(2 * a)}h${n2(-2 * a)}z`;
  }
  if (shape === 'triangle') {
    return `M${n2(x)} ${n2(y - r * 1.1)}L${n2(x + r * 1.05)} ${n2(y + r * 0.8)}L${n2(x - r * 1.05)} ${n2(y + r * 0.8)}z`;
  }
  if (shape === 'diamond') {
    const a = r * 1.15;
    return `M${n2(x)} ${n2(y - a)}L${n2(x + a)} ${n2(y)}L${n2(x)} ${n2(y + a)}L${n2(x - a)} ${n2(y)}z`;
  }
  if (shape === 'plus') {
    const a = r * 0.35;
    return `M${n2(x - r)} ${n2(y - a)}h${n2(2 * r)}v${n2(2 * a)}h${n2(-2 * r)}zM${n2(x - a)} ${n2(y - r)}h${n2(2 * a)}v${n2(2 * r)}h${n2(-2 * a)}z`;
  }
  return `M${n2(x - r)} ${n2(y)}a${n2(r)} ${n2(r)} 0 1 0 ${n2(2 * r)} 0a${n2(r)} ${n2(r)} 0 1 0 ${n2(-2 * r)} 0z`;
}

/** A 3D scene's regions, lines and points as SVG elements, projected as on screen: the
 * points from the back to the front, as the Canvas 2D renderer of 3D draws them. */
function svgSpace(out, view, scene, color) {
  for (const region of scene.regions || []) {
    const p = region.members ? screenRegion(view, region.members, convexHull) : null;
    if (!p) continue;
    const pts = [];
    for (let k = 0; k < p.length; k += 2) pts.push(`${n2(p[k])},${n2(p[k + 1])}`);
    const c = esc(color(region.color));
    out.push(`<polygon points="${pts.join(' ')}" fill="${c}" fill-opacity="${region.alpha === undefined ? 0.16 : region.alpha}" stroke="${c}" stroke-opacity="0.7"/>`);
  }
  const a = [0, 0, 0, 0];
  const b = [0, 0, 0, 0];
  for (const line of scene.lines || []) {
    let d = '';
    for (let k = 0; k + 1 < line.x.length; k += 2) {
      const zs = line.z;
      if (!projectPoint(view, line.x[k], line.y[k], zs ? zs[k] : 0, a)) continue;
      if (!projectPoint(view, line.x[k + 1], line.y[k + 1], zs ? zs[k + 1] : 0, b)) continue;
      d += `M${n2(a[0])} ${n2(a[1])}L${n2(b[0])} ${n2(b[1])}`;
    }
    if (d) {
      out.push(`<path d="${d}" fill="none" stroke="${esc(color(line.color))}" stroke-width="${line.width || 1}" stroke-opacity="${line.alpha === undefined ? 0.6 : line.alpha}" stroke-linecap="round" stroke-linejoin="round"/>`);
    }
  }
  const anyHighlight = scene.layers.some((l) => l.highlight && l.highlightCount);
  const order = spaceOrder(scene, view, scene.layers.map((layer) => projectLayer(view, layer)));
  const ring = esc(color('--cx-accent'));
  // runs of points of one look, in depth order: one path each
  let run = null;
  const flush = () => {
    if (run && run.d) out.push(run.tag.replace('%D', run.d));
    run = null;
  };
  for (const pass of anyHighlight ? [0, 1] : [0]) {
    for (const pt of order) {
      if (pass === 1 && !pt.lit) continue;
      const layer = scene.layers[pt.li];
      const shape = layer.shape || 'circle';
      const r = spaceRadius((layer.radius || 2.5) * (layer.size ? layer.size[pt.i] : 1), pt.size);
      const fill = esc(color(layer.palette[layer.color ? layer.color[pt.i] : 0] || layer.palette[0]));
      let tag;
      let path;
      if (pass === 1) {
        const ringed = layer.highlight[pt.i] >= (layer.ringFrom || 1);
        path = pointPath(shape === 'ring' ? 'circle' : shape, pt.px, pt.py, r + (ringed ? 1.5 : 0));
        tag = `<path d="%D" fill="${fill}"${ringed ? ` stroke="${ring}" stroke-width="2"` : ''}/>`;
      } else {
        const base = anyHighlight ? (layer.dim === undefined ? 0.3 : layer.dim) : (layer.alpha || 0.9);
        const alpha = n2(base * (1 - DEPTH_FADE * Math.round(depthShare(view, pt.depth) * 8) / 8));
        path = pointPath(shape === 'ring' ? 'circle' : shape, pt.px, pt.py, r);
        tag = shape === 'ring'
          ? `<path d="%D" fill="none" stroke="${fill}" stroke-width="${n2(Math.max(1.5, r * 0.5))}" opacity="${alpha}"/>`
          : `<path d="%D" fill="${fill}" opacity="${alpha}"/>`;
      }
      if (!run || run.tag !== tag) {
        flush();
        run = { tag, d: '' };
      }
      run.d += path;
    }
    flush();
  }
}

/** The view as SVG markup: the same order and rules as the Canvas 2D renderer. */
export function svgOf({ view, scene, legend, color, font, measure }) {
  const { width, height, scale: sx, tx: ox, ty: oy } = view;
  const zoom = zoomOf(view);
  const out = [`<svg xmlns="http://www.w3.org/2000/svg" width="${n2(width)}" height="${n2(height)}" viewBox="0 0 ${n2(width)} ${n2(height)}" font-family="${esc(font)}">`,
    `<rect width="100%" height="100%" fill="${esc(color('--cx-surface'))}"/>`];
  if (view.dims === 3) svgSpace(out, view, scene, color);
  else svgFlat(out, { view, scene, color, zoom, sx, ox, oy });
  labelsAndLegend(out, { view, scene, legend, color, measure });
  out.push('</svg>');
  return out.join('\n');
}

/** A flat scene's regions, lines and points as SVG elements. */
function svgFlat(out, { view, scene, color, zoom, sx, ox, oy }) {
  const { width, height } = view;
  for (const region of scene.regions || []) {
    const p = region.polygon;
    if (p.length < 6) continue;
    const pts = [];
    for (let k = 0; k < p.length; k += 2) pts.push(`${n2(p[k] * sx + ox)},${n2(oy - p[k + 1] * sx)}`);
    const c = esc(color(region.color));
    out.push(`<polygon points="${pts.join(' ')}" fill="${c}" fill-opacity="${region.alpha === undefined ? 0.16 : region.alpha}" stroke="${c}" stroke-opacity="0.7"/>`);
  }
  for (const line of scene.lines || []) {
    let d = '';
    for (let k = 0; k + 1 < line.x.length; k += 2) {
      d += `M${n2(line.x[k] * sx + ox)} ${n2(oy - line.y[k] * sx)}L${n2(line.x[k + 1] * sx + ox)} ${n2(oy - line.y[k + 1] * sx)}`;
    }
    if (d) {
      out.push(`<path d="${d}" fill="none" stroke="${esc(color(line.color))}" stroke-width="${line.width || 1}" stroke-opacity="${line.alpha === undefined ? 0.6 : line.alpha}" stroke-linecap="round" stroke-linejoin="round"/>`);
    }
  }
  const anyHighlight = scene.layers.some((l) => l.highlight && l.highlightCount);
  for (const layer of scene.layers) {
    const r = layer.radius || 2.5;
    const limit = detailLimit(layer, zoom);
    const shape = layer.shape || 'circle';
    const buckets = new Map();
    for (let i = 0; i < layer.x.length; i += 1) {
      if (layer.rank && layer.rank[i] > limit) continue;
      const px = layer.x[i] * sx + ox;
      const py = oy - layer.y[i] * sx;
      if (px < -r || py < -r || px > width + r || py > height + r) continue;
      const c = layer.color ? layer.color[i] : 0;
      if (!buckets.has(c)) buckets.set(c, []);
      buckets.get(c).push(pointPath(shape === 'ring' ? 'circle' : shape, px, py, layer.size ? r * layer.size[i] : r));
    }
    const alpha = anyHighlight ? (layer.dim === undefined ? 0.3 : layer.dim) : (layer.alpha || 0.9);
    for (const [c, paths] of buckets) {
      const fill = esc(color(layer.palette[c] || layer.palette[0]));
      out.push(shape === 'ring'
        ? `<path d="${paths.join('')}" fill="none" stroke="${fill}" stroke-width="${n2(Math.max(1.5, r * 0.5))}" opacity="${alpha}"/>`
        : `<path d="${paths.join('')}" fill="${fill}" opacity="${alpha}"/>`);
    }
  }
  if (anyHighlight) {
    const ring = esc(color('--cx-accent'));
    for (const layer of scene.layers) {
      if (!layer.highlight || !layer.highlightCount) continue;
      const r = layer.radius || 2.5;
      const shape = layer.shape === 'ring' ? 'circle' : (layer.shape || 'circle');
      const from = layer.ringFrom || 1;
      const byColor = new Map();
      for (let i = 0; i < layer.x.length; i += 1) {
        if (!layer.highlight[i] || (layer.rank && layer.rank[i] > 1.5)) continue;
        const ringed = layer.highlight[i] >= from;
        const key = `${layer.color ? layer.color[i] : 0}|${ringed ? 1 : 0}`;
        if (!byColor.has(key)) byColor.set(key, []);
        byColor.get(key).push(pointPath(shape, layer.x[i] * sx + ox, oy - layer.y[i] * sx,
          (layer.size ? r * layer.size[i] : r) + (ringed ? 1.5 : 0)));
      }
      for (const [key, paths] of byColor) {
        const [c, ringed] = key.split('|').map(Number);
        out.push(`<path d="${paths.join('')}" fill="${esc(color(layer.palette[c] || layer.palette[0]))}"${ringed ? ` stroke="${ring}" stroke-width="2"` : ''}/>`);
      }
    }
  }
}

/** The labels the view shows, and the legend, as SVG elements. */
function labelsAndLegend(out, { view, scene, legend, color, measure }) {
  const { height } = view;
  const labels = placeLabels(scene.labels || [], view, measure);
  const halo = esc(color('--cx-surface'));
  const ink = esc(color('--cx-text'));
  for (const label of labels) {
    const fillText = label.color ? esc(color(label.color)) : ink;
    out.push(`<text x="${n2(label.px)}" y="${n2(label.py)}" text-anchor="middle" dominant-baseline="middle" font-size="${label.size || (label.strong ? 13 : 12)}" font-weight="${label.strong ? 600 : 500}" fill="${fillText}" stroke="${halo}" stroke-width="3" stroke-linejoin="round" paint-order="stroke">${esc(label.text)}</text>`);
  }
  if (legend) {
    const { w, h } = legendSize(legend, (text) => measure(text, false));
    const x0 = LEGEND_PAD;
    const y0 = height - h - LEGEND_PAD;
    out.push(`<g class="legend"><rect x="${x0 + 0.5}" y="${n2(y0 + 0.5)}" width="${n2(w - 1)}" height="${n2(h - 1)}" fill="${halo}" fill-opacity="0.92" stroke="${esc(color('--cx-border'))}"/>`);
    let y = y0 + LEGEND_PAD + LEGEND_ROW / 2;
    for (const row of [...legend.kinds, ...legend.entries]) {
      const fill = esc(color(row.color || '--cx-text-muted'));
      out.push(row.shape ? `<path d="${pointPath(row.shape === 'ring' ? 'circle' : row.shape, x0 + LEGEND_PAD + LEGEND_SWATCH / 2, y, 4)}" fill="${fill}"/>`
        : `<rect x="${x0 + LEGEND_PAD}" y="${n2(y - LEGEND_SWATCH / 2)}" width="${LEGEND_SWATCH}" height="${LEGEND_SWATCH}" fill="${fill}"/>`);
      out.push(`<text x="${n2(x0 + 2 * LEGEND_PAD + LEGEND_SWATCH)}" y="${n2(y)}" dominant-baseline="middle" font-size="12" fill="${ink}">${esc(row.text)}</text>`);
      y += LEGEND_ROW;
    }
    out.push('</g>');
  }
}

/** The view as an SVG file. */
export function saveSvg({ frame, box, scene, legend = null, stem }) {
  const view = savedView(frame);
  const probe = document.createElement('canvas');
  probe.className = 'cx-atlas-save';
  box.appendChild(probe);
  try {
    const cache = new Map();
    const font = getComputedStyle(probe).fontFamily || 'sans-serif';
    const ctx = probe.getContext('2d');
    const measure = (text, strong, label) => {
      ctx.font = `${strong ? 600 : 500} ${(label && label.size) || (strong ? 13 : 12)}px ${font}`;
      return ctx.measureText(text).width;
    };
    const markup = svgOf({ view, scene, legend, color: (v) => resolveColor(probe, v, cache), font, measure });
    downloadBlob(new Blob([markup], { type: 'image/svg+xml' }), savedName(stem, 'svg'));
  } finally {
    probe.remove();
  }
}
