// SPDX-License-Identifier: MIT
/**
 * The map in three dimensions: an orbit camera about a target, its projection to the
 * screen, fitting, zooming toward the pointer, panning, turning, hit testing on the
 * projected points (the front-most first) and the screen hulls of regions.
 *
 * A **3D view** is `{dims: 3, width, height, yaw, pitch, dist, fitDist, tx, ty, tz, …}`:
 * the camera turns about the target (tx, ty, tz) by *yaw* (about the vertical axis) and
 * *pitch*, at *dist* from it, with a perspective of `FOV` radians. A data point
 * (x, y, z) is at depth `d` from the camera and on screen at
 * (width / 2 + x′ · focal / d, height / 2 − y′ · focal / d), x′ and y′ its offsets from the
 * target along the camera's right and up. `scale` (pixels per data unit at the target) and
 * `fitScale` (the same when the whole map fits) keep the meaning they have in 2D, so the
 * zoom (`zoomOf`, `detailLimit`) works on both. Yaw and pitch of 0 look along −z: the
 * « front view » shows x to the right and y up, as the flat map does.
 *
 * Same rules as its siblings (`core.js`): no library, no DOM, declarations only.
 */

/** The camera's field of view, in radians. */
export const FOV = Math.PI / 6;
/** The zoom limits of the 3D view, relative to the fitted view. */
const SPACE_ZOOM = [0.25, 400];
/** The pitch limit (a little short of straight above or below). */
const PITCH_MAX = 1.45;
/** Pixels around a projected point within which a pointer hits it. */
const SPACE_HIT = 6;

/** A new 3D view, before its first fit: turned a little, to show its depth. */
export function createView3() {
  const view = { dims: 3, width: 0, height: 0, yaw: 0.55, pitch: 0.3, dist: 1, fitDist: 1, radius: 1,
    tx: 0, ty: 0, tz: 0, focal: 1, scale: 1, fitScale: 1, near: 0.01 };
  updateView3(view);
  return view;
}

/** Derive what follows from the camera (focal length, scale, the turn's sines and cosines). */
export function updateView3(view) {
  view.focal = Math.max(1, Math.min(view.width || 1, view.height || 1) / 2) / Math.tan(FOV / 2);
  view.scale = view.focal / view.dist;
  view.fitScale = view.focal / view.fitDist;
  view.cy = Math.cos(view.yaw);
  view.sy = Math.sin(view.yaw);
  view.cp = Math.cos(view.pitch);
  view.sp = Math.sin(view.pitch);
  view.near = view.dist * 0.02;
  return view;
}

/** The centre and radius of the sphere around *bounds* (`zmin`, `zmax` 0 when absent). */
export function boundsSphere(bounds) {
  const z0 = bounds.zmin === undefined || bounds.zmin === null ? 0 : bounds.zmin;
  const z1 = bounds.zmax === undefined || bounds.zmax === null ? 0 : bounds.zmax;
  const hx = (bounds.xmax - bounds.xmin) / 2;
  const hy = (bounds.ymax - bounds.ymin) / 2;
  const hz = (z1 - z0) / 2;
  // the points of a map rarely fill the corners of their box: a little less than its half diagonal
  const radius = Math.max(1e-9, 0.8 * Math.sqrt(hx * hx + hy * hy + hz * hz));
  return { x: (bounds.xmin + bounds.xmax) / 2, y: (bounds.ymin + bounds.ymax) / 2, z: (z0 + z1) / 2, radius };
}

/**
 * The sphere around a scene's points: about the centre of its bounds, out to the distance
 * that holds all but the farthest hundredth of its points (a few strays do not shrink the
 * map), or the bounds' own sphere without points.
 */
export function sceneSphere(scene) {
  const s = boundsSphere(scene.bounds);
  const d = [];
  for (const layer of scene.layers || []) {
    const n = layer.x.length;
    const step = Math.max(1, Math.floor(n / 20000));
    for (let i = 0; i < n; i += step) {
      const z = layer.z ? layer.z[i] : 0;
      d.push((layer.x[i] - s.x) ** 2 + (layer.y[i] - s.y) ** 2 + (z - s.z) ** 2);
    }
  }
  if (d.length < 3) return s;
  d.sort((a, b) => a - b);
  const r = Math.sqrt(d[Math.min(d.length - 1, Math.floor(d.length * 0.99))]) * 1.04;
  return r > 0 ? { ...s, radius: Math.min(s.radius, r) } : s;
}

/** Fit *bounds* (or the *sphere* given) in the 3D view, with *pad* pixels around; the turn
 * is kept. */
export function fitView3(view, bounds, pad = 16, sphere = null) {
  if (!bounds || bounds.xmin === null || bounds.xmin === undefined || !view.width || !view.height) return false;
  const s = sphere || boundsSphere(bounds);
  updateView3(view);
  const half = Math.max(8, Math.min(view.width, view.height) / 2 - pad);
  const angle = Math.atan(half / view.focal);
  view.radius = s.radius;
  view.fitDist = s.radius / Math.sin(angle);
  view.dist = view.fitDist;
  view.tx = s.x;
  view.ty = s.y;
  view.tz = s.z;
  updateView3(view);
  return true;
}

/**
 * Where (x, y, z) is on screen: `out` = [px, py, depth, size] (size: how much nearer than the
 * target, 1 at the target's depth), or null when it is behind the camera.
 */
export function projectPoint(view, x, y, z, out = [0, 0, 0, 0]) {
  const dx = x - view.tx;
  const dy = y - view.ty;
  const dz = (z || 0) - view.tz;
  const x1 = view.cy * dx + view.sy * dz;
  const z1 = -view.sy * dx + view.cy * dz;
  const y2 = view.cp * dy - view.sp * z1;
  const z2 = view.sp * dy + view.cp * z1;
  const d = view.dist - z2;
  if (d <= view.near) return null;
  const s = view.focal / d;
  out[0] = view.width / 2 + x1 * s;
  out[1] = view.height / 2 - y2 * s;
  out[2] = d;
  out[3] = view.dist / d;
  return out;
}

/** The camera's right, up and towards-the-camera axes, in data units. */
export function cameraAxes(view) {
  return {
    right: [view.cy, 0, view.sy],
    up: [view.sp * view.sy, view.cp, -view.sp * view.cy],
    back: [-view.cp * view.sy, view.sp, view.cp * view.cy],
  };
}

/** Move the target by (dx, dy) pixels on screen: the map follows the pointer. */
export function panView3(view, dx, dy) {
  const { right, up } = cameraAxes(view);
  const k = 1 / view.scale;
  view.tx += (-right[0] * dx + up[0] * dy) * k;
  view.ty += (-right[1] * dx + up[1] * dy) * k;
  view.tz += (-right[2] * dx + up[2] * dy) * k;
}

/** Zoom by *factor* toward the screen point (px, py) (what is under it at the target's depth
 * stays under it), within the zoom limits. */
export function zoomView3(view, factor, px, py) {
  const lo = view.fitDist / SPACE_ZOOM[1];
  const hi = view.fitDist / SPACE_ZOOM[0];
  const next = Math.max(lo, Math.min(hi, view.dist / factor));
  const k = view.dist / next;
  if (k === 1) return;
  const ox = (px - view.width / 2) / view.scale;
  const oy = -(py - view.height / 2) / view.scale;
  const { right, up } = cameraAxes(view);
  const m = 1 - 1 / k;
  view.tx += (right[0] * ox + up[0] * oy) * m;
  view.ty += (right[1] * ox + up[1] * oy) * m;
  view.tz += (right[2] * ox + up[2] * oy) * m;
  view.dist = next;
  updateView3(view);
}

/** Turn the camera by *dyaw* and *dpitch* radians (the pitch held short of the poles). */
export function turnView3(view, dyaw, dpitch) {
  view.yaw = (view.yaw + dyaw) % (Math.PI * 2);
  view.pitch = Math.max(-PITCH_MAX, Math.min(PITCH_MAX, view.pitch + dpitch));
  updateView3(view);
}

/** Centre the 3D view on (x, y, z), at *zoom* (relative to the fit) when given. */
export function centreView3(view, x, y, z, zoom) {
  view.tx = x;
  view.ty = y;
  view.tz = z || 0;
  if (zoom) view.dist = view.fitDist / Math.max(SPACE_ZOOM[0], Math.min(SPACE_ZOOM[1], zoom));
  updateView3(view);
}

/** A key that changes whenever the projection does. */
export function viewKey(view) {
  return `${view.width}|${view.height}|${view.yaw}|${view.pitch}|${view.dist}|${view.tx}|${view.ty}|${view.tz}`;
}

/**
 * Every point of *layer* projected: a Float32Array of (px, py, depth, size) per point, depth
 * NaN for the points behind the camera.
 */
export function projectLayer(view, layer) {
  const n = layer.x.length;
  const out = new Float32Array(n * 4);
  const q = [0, 0, 0, 0];
  const zs = layer.z;
  for (let i = 0; i < n; i += 1) {
    const at = projectPoint(view, layer.x[i], layer.y[i], zs ? zs[i] : 0, q);
    if (!at) {
      out[4 * i + 2] = NaN;
      continue;
    }
    out[4 * i] = at[0];
    out[4 * i + 1] = at[1];
    out[4 * i + 2] = at[2];
    out[4 * i + 3] = at[3];
  }
  return out;
}

/**
 * The projections of a scene's layers, kept while the view and the scene stay the same:
 * `get(scene, view)` → one array of `projectLayer` per layer.
 */
export function createProjections() {
  let key = '';
  let scene = null;
  let list = [];
  return {
    get(sc, view) {
      const k = viewKey(view);
      if (sc !== scene || k !== key) {
        scene = sc;
        key = k;
        list = sc.layers.map((layer) => projectLayer(view, layer));
      }
      return list;
    },
  };
}

/**
 * The point under the screen point (px, py) among those shown (*limits*: each layer's detail
 * limit): within `SPACE_HIT` pixels (plus its radius), the front-most first. `{layer, index}`
 * or null.
 */
export function hitTest3(scene, projected, limits, px, py) {
  let best = null;
  let bestDepth = Infinity;
  scene.layers.forEach((layer, li) => {
    if (layer.pickable === false) return;
    const p = projected[li];
    const r0 = layer.radius || 2.5;
    for (let i = 0; i < layer.x.length; i += 1) {
      const d = p[4 * i + 2];
      if (!(d < bestDepth)) continue; // also skips NaN (behind)
      if (layer.rank && layer.rank[i] > limits[li]) continue;
      const reach = SPACE_HIT + r0 * (layer.size ? layer.size[i] : 1) * Math.min(3, p[4 * i + 3]);
      const ex = p[4 * i] - px;
      const ey = p[4 * i + 1] - py;
      if (ex * ex + ey * ey > reach * reach) continue;
      best = { layer: layer.id, index: i };
      bestDepth = d;
    }
  });
  return best;
}

/** The members of a region (`{x, y, z}` arrays) projected, as a convex polygon on screen
 * (`[px0, py0, px1, py1…]`), or null when fewer than three are in front of the camera. */
export function screenRegion(view, members, hull) {
  const xs = [];
  const ys = [];
  const q = [0, 0, 0, 0];
  for (let i = 0; i < members.x.length; i += 1) {
    const at = projectPoint(view, members.x[i], members.y[i], members.z ? members.z[i] : 0, q);
    if (!at) continue;
    xs.push(at[0]);
    ys.push(at[1]);
  }
  if (xs.length < 3) return null;
  const polygon = hull(xs, ys);
  return polygon.length >= 6 ? polygon : null;
}

/** How far a depth is between the front and the back of the map: 0 in front, 1 at the back. */
export function depthShare(view, depth) {
  const r = view.radius || 1;
  return Math.max(0, Math.min(1, (depth - (view.dist - r)) / (2 * r)));
}
