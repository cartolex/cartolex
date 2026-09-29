// SPDX-License-Identifier: MIT
/**
 * The Canvas 2D renderer: the fallback when WebGL is not available, and the
 * renderer of small maps. Regions, then lines, then each layer's points
 * grouped by colour (one path per colour and shape), the highlighted points
 * on top with a ring in the accent colour, then the labels.
 *
 * A renderer has four methods: `resize(width, height, ratio)`,
 * `draw(scene, view)`, `invalidate()` (the theme changed: resolve the colours
 * again) and `destroy()`; `name` says which it is.
 */
import { detailLimit, placeLabels, resolveColor, zoomOf } from './core.js';

/** Trace one point of *shape* at (x, y), of radius *r*, into the current path. */
export function tracePoint(ctx, shape, x, y, r) {
  if (shape === 'square') {
    ctx.rect(x - r * 0.9, y - r * 0.9, r * 1.8, r * 1.8);
  } else if (shape === 'triangle') {
    ctx.moveTo(x, y - r * 1.1);
    ctx.lineTo(x + r * 1.05, y + r * 0.8);
    ctx.lineTo(x - r * 1.05, y + r * 0.8);
    ctx.closePath();
  } else if (shape === 'diamond') {
    ctx.moveTo(x, y - r * 1.15);
    ctx.lineTo(x + r * 1.15, y);
    ctx.lineTo(x, y + r * 1.15);
    ctx.lineTo(x - r * 1.15, y);
    ctx.closePath();
  } else if (shape === 'plus') {
    const a = r * 0.35;
    ctx.rect(x - r, y - a, 2 * r, 2 * a);
    ctx.rect(x - a, y - r, 2 * a, 2 * r);
  } else {
    ctx.moveTo(x + r, y);
    ctx.arc(x, y, r, 0, Math.PI * 2);
  }
}

/** Draw a scene's labels on a 2D context (shared with the WebGL renderer's overlay). */
export function drawLabels(ctx, scene, view, color, font) {
  if (!scene.labels || !scene.labels.length) return;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.lineJoin = 'round';
  ctx.lineWidth = 3;
  const regular = `500 12px ${font}`;
  const strong = `600 13px ${font}`;
  const placed = placeLabels(scene.labels, view, (text, isStrong) => {
    ctx.font = isStrong ? strong : regular;
    return ctx.measureText(text).width;
  });
  ctx.strokeStyle = color('--cx-surface');
  ctx.fillStyle = color('--cx-text');
  for (const label of placed) {
    ctx.font = label.strong ? strong : regular;
    ctx.strokeText(label.text, label.px, label.py);
    ctx.fillText(label.text, label.px, label.py);
  }
}

export function createCanvas2DRenderer(canvas) {
  const ctx = canvas.getContext('2d', { alpha: false });
  let dpr = 1;
  let cache = new Map();
  return {
    name: 'canvas2d',
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
      const sx = view.scale;
      const ox = view.tx;
      const oy = view.ty;
      const zoom = zoomOf(view);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.globalAlpha = 1;
      ctx.fillStyle = color('--cx-surface');
      ctx.fillRect(0, 0, width, height);
      for (const region of scene.regions || []) {
        const p = region.polygon;
        if (p.length < 6) continue;
        ctx.beginPath();
        ctx.moveTo(p[0] * sx + ox, oy - p[1] * sx);
        for (let k = 2; k < p.length; k += 2) ctx.lineTo(p[k] * sx + ox, oy - p[k + 1] * sx);
        ctx.closePath();
        ctx.globalAlpha = region.alpha === undefined ? 0.16 : region.alpha;
        ctx.fillStyle = color(region.color);
        ctx.fill();
        ctx.globalAlpha = 0.7;
        ctx.strokeStyle = color(region.color);
        ctx.lineWidth = 1;
        ctx.stroke();
      }
      for (const line of scene.lines || []) {
        ctx.globalAlpha = line.alpha === undefined ? 0.6 : line.alpha;
        ctx.strokeStyle = color(line.color);
        ctx.lineWidth = line.width || 1;
        ctx.beginPath();
        for (let k = 0; k + 1 < line.x.length; k += 2) {
          ctx.moveTo(line.x[k] * sx + ox, oy - line.y[k] * sx);
          ctx.lineTo(line.x[k + 1] * sx + ox, oy - line.y[k + 1] * sx);
        }
        ctx.stroke();
      }
      const anyHighlight = scene.layers.some((l) => l.highlight && l.highlightCount);
      for (const layer of scene.layers) {
        const { x, y } = layer;
        const n = x.length;
        const r = layer.radius || 2.5;
        const limit = detailLimit(layer, zoom);
        const shape = layer.shape || 'circle';
        const buckets = new Map();
        for (let i = 0; i < n; i += 1) {
          if (layer.rank && layer.rank[i] > limit) continue;
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
        ctx.globalAlpha = anyHighlight ? 0.3 : (layer.alpha || 0.9);
        for (const [c, pts] of buckets) {
          const fill = color(layer.palette[c] || layer.palette[0]);
          ctx.beginPath();
          for (let k = 0; k < pts.length; k += 2) tracePoint(ctx, shape, pts[k], pts[k + 1], r);
          if (shape === 'ring') {
            ctx.strokeStyle = fill;
            ctx.lineWidth = Math.max(1.5, r * 0.5);
            ctx.stroke();
          } else {
            ctx.fillStyle = fill;
            ctx.fill();
          }
        }
      }
      if (anyHighlight) {
        ctx.globalAlpha = 1;
        ctx.lineWidth = 2;
        ctx.strokeStyle = color('--cx-accent');
        for (const layer of scene.layers) {
          if (!layer.highlight || !layer.highlightCount) continue;
          const r = (layer.radius || 2.5) + 1.5;
          const shape = layer.shape === 'ring' ? 'circle' : (layer.shape || 'circle');
          const { x, y } = layer;
          const byColor = new Map();
          for (let i = 0; i < x.length; i += 1) {
            if (!layer.highlight[i] || (layer.rank && layer.rank[i] > 1.5)) continue;
            const c = layer.color ? layer.color[i] : 0;
            if (!byColor.has(c)) byColor.set(c, []);
            byColor.get(c).push(x[i] * sx + ox, oy - y[i] * sx);
          }
          for (const [c, pts] of byColor) {
            ctx.fillStyle = color(layer.palette[c] || layer.palette[0]);
            ctx.beginPath();
            for (let k = 0; k < pts.length; k += 2) tracePoint(ctx, shape, pts[k], pts[k + 1], r);
            ctx.fill();
            ctx.stroke();
          }
        }
      }
      ctx.globalAlpha = 1;
      drawLabels(ctx, scene, view, color, getComputedStyle(canvas).fontFamily || 'sans-serif');
    },
    destroy() {
      cache = new Map();
    },
  };
}
