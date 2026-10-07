// SPDX-License-Identifier: MIT
/**
 * The Canvas 2D renderer of a map in three dimensions: the fallback when WebGL is not
 * available, and the renderer of saved views. Every frame projects the points (`space.js`),
 * draws the regions (the hull of their members' projection), the lines, then the points
 * from the back to the front (a point further back is smaller and fainter), the focus on
 * top with its accent ring, then the labels. Same interface as the 2D renderers.
 */
import { convexHull, detailLimit, resolveColor, zoomOf } from './core.js';
import { drawLabels, tracePoint } from './canvas2d.js';
import { createProjections, depthShare, projectPoint, screenRegion } from './space.js';

/** How much the back of the map fades (0: not at all, 1: to nothing). */
export const DEPTH_FADE = 0.55;

/** The radius on screen of a point of radius *r* at *size* (1 at the target's depth). */
export function spaceRadius(r, size) {
  return r * Math.max(0.45, Math.min(3, size));
}

/**
 * The points of *scene* to draw, from the back to the front: `[{li, i, px, py, depth,
 * size, lit}]` (*lit*: drawn in the focus's pass), within the view and its detail.
 */
export function spaceOrder(scene, view, projected) {
  const zoom = zoomOf(view);
  const anyHighlight = scene.layers.some((l) => l.highlight && l.highlightCount);
  const out = [];
  scene.layers.forEach((layer, li) => {
    const p = projected[li];
    const limit = detailLimit(layer, zoom);
    const r = (layer.radius || 2.5) * 3;
    for (let i = 0; i < layer.x.length; i += 1) {
      const depth = p[4 * i + 2];
      if (!(depth > 0)) continue;
      const lit = anyHighlight && layer.highlight && layer.highlight[i] > 0;
      if (layer.rank && layer.rank[i] > (lit ? 1.5 : limit)) continue;
      const px = p[4 * i];
      const py = p[4 * i + 1];
      if (px < -r || py < -r || px > view.width + r || py > view.height + r) continue;
      out.push({ li, i, px, py, depth, size: p[4 * i + 3], lit });
    }
  });
  out.sort((a, b) => b.depth - a.depth);
  return out;
}

/** Draw a 3D scene's regions, lines and points on *ctx* (in CSS pixels); *color* resolves. */
export function drawSpace(ctx, scene, view, projected, color) {
  for (const region of scene.regions || []) {
    const p = region.members ? screenRegion(view, region.members, convexHull) : null;
    if (!p) continue;
    ctx.beginPath();
    ctx.moveTo(p[0], p[1]);
    for (let k = 2; k < p.length; k += 2) ctx.lineTo(p[k], p[k + 1]);
    ctx.closePath();
    ctx.globalAlpha = region.alpha === undefined ? 0.16 : region.alpha;
    ctx.fillStyle = color(region.color);
    ctx.fill();
    ctx.globalAlpha = 0.7;
    ctx.strokeStyle = color(region.color);
    ctx.lineWidth = 1;
    ctx.stroke();
  }
  const a = [0, 0, 0, 0];
  const b = [0, 0, 0, 0];
  for (const line of scene.lines || []) {
    ctx.globalAlpha = line.alpha === undefined ? 0.6 : line.alpha;
    ctx.strokeStyle = color(line.color);
    ctx.lineWidth = line.width || 1;
    ctx.setLineDash(line.dash ? [line.dash, line.dash] : []);
    ctx.beginPath();
    for (let k = 0; k + 1 < line.x.length; k += 2) {
      const zs = line.z;
      if (!projectPoint(view, line.x[k], line.y[k], zs ? zs[k] : 0, a)) continue;
      if (!projectPoint(view, line.x[k + 1], line.y[k + 1], zs ? zs[k + 1] : 0, b)) continue;
      ctx.moveTo(a[0], a[1]);
      ctx.lineTo(b[0], b[1]);
    }
    ctx.stroke();
  }
  ctx.setLineDash([]);
  const anyHighlight = scene.layers.some((l) => l.highlight && l.highlightCount);
  const order = spaceOrder(scene, view, projected);
  const accent = color('--cx-accent');
  let fill = '';
  for (const pass of anyHighlight ? [0, 1] : [0]) {
    for (const pt of order) {
      if (pass === 1 && !pt.lit) continue;
      const layer = scene.layers[pt.li];
      const shape = layer.shape || 'circle';
      const r = spaceRadius((layer.radius || 2.5) * (layer.size ? layer.size[pt.i] : 1), pt.size);
      const fade = 1 - DEPTH_FADE * depthShare(view, pt.depth);
      const c = color(layer.palette[layer.color ? layer.color[pt.i] : 0] || layer.palette[0]);
      if (c !== fill) {
        fill = c;
        ctx.fillStyle = c;
        ctx.strokeStyle = c;
      }
      ctx.beginPath();
      if (pass === 1) {
        const ringed = layer.highlight[pt.i] >= (layer.ringFrom || 1);
        ctx.globalAlpha = 1;
        tracePoint(ctx, shape === 'ring' ? 'circle' : shape, pt.px, pt.py, r + (ringed ? 1.5 : 0));
        ctx.fill();
        if (ringed) {
          ctx.strokeStyle = accent;
          ctx.lineWidth = 2;
          ctx.stroke();
          fill = '';
        }
        continue;
      }
      const base = anyHighlight ? (layer.dim === undefined ? 0.3 : layer.dim) : (layer.alpha || 0.9);
      ctx.globalAlpha = base * fade;
      tracePoint(ctx, shape, pt.px, pt.py, r);
      if (shape === 'ring') {
        ctx.lineWidth = Math.max(1.5, r * 0.5);
        ctx.stroke();
      } else ctx.fill();
    }
  }
  ctx.globalAlpha = 1;
}

export function createCanvas3DRenderer(canvas) {
  const ctx = canvas.getContext('2d', { alpha: false });
  let dpr = 1;
  let cache = new Map();
  const projections = createProjections();
  return {
    name: 'canvas3d',
    resize(width, height, ratio) {
      dpr = ratio;
      canvas.width = Math.max(1, Math.round(width * ratio));
      canvas.height = Math.max(1, Math.round(height * ratio));
    },
    invalidate() {
      cache = new Map();
    },
    draw(scene, view) {
      const color = (v) => resolveColor(canvas, v, cache);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.globalAlpha = 1;
      ctx.fillStyle = color('--cx-surface');
      ctx.fillRect(0, 0, view.width, view.height);
      drawSpace(ctx, scene, view, projections.get(scene, view), color);
      drawLabels(ctx, scene, view, color, getComputedStyle(canvas).fontFamily || 'sans-serif');
    },
    /** The projections of the scene's layers in *view* (shared with hit testing). */
    projected: (scene, view) => projections.get(scene, view),
    destroy() {
      cache = new Map();
    },
  };
}
