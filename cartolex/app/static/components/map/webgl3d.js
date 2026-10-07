// SPDX-License-Identifier: MIT
/**
 * The WebGL renderer of a map in three dimensions: 10⁵ points and more, turned, panned and
 * zoomed at the frame rate. As in 2D (`webgl.js`), each layer's positions (x, y, z),
 * colours, ranks and highlight go to the GPU once and a frame only sets the camera; the
 * vertex shader projects each point (`space.js`), sizes it by its depth and fades it toward
 * the background the further back it is. A depth buffer orders what is drawn: the points
 * write it (the faded traces around a focus do not, so they never hide anything), the lines
 * are tested against it. Regions are hulled on screen at each frame from their members'
 * projection and drawn first; labels are drawn on a 2D canvas laid over the map.
 *
 * `createWebGL3DRenderer(canvas)` answers null when the browser gives no WebGL context
 * with a depth buffer: the caller then uses the Canvas 2D one (`canvas3d.js`).
 */
import { SHAPES, colorToRgba, convexHull, detailLimit, resolveColor, zoomOf } from './core.js';
import { drawLabels } from './canvas2d.js';
import { DEPTH_FADE } from './canvas3d.js';
import { PRECISION, compile } from './webgl.js';
import { screenRegion } from './space.js';

const SPACE_PALETTE = 32;

// The camera: u_target, u_turn (cos yaw, sin yaw, cos pitch, sin pitch), u_cam (distance,
// focal length, near, far), u_fog (the depth of the map's front, its depth span).
const SPACE_PROJECT = `
uniform vec2 u_res;
uniform vec3 u_target;
uniform vec4 u_turn;
uniform vec4 u_cam;
vec4 project(vec3 at) {
  vec3 d = at - u_target;
  float x1 = u_turn.x * d.x + u_turn.y * d.z;
  float z1 = -u_turn.y * d.x + u_turn.x * d.z;
  float y2 = u_turn.z * d.y - u_turn.w * z1;
  float z2 = u_turn.w * d.y + u_turn.z * z1;
  float depth = u_cam.x - z2;
  float s = u_cam.y / max(depth, 1e-6);
  return vec4(u_res.x * 0.5 + x1 * s, u_res.y * 0.5 - y2 * s, depth, u_cam.x / max(depth, 1e-6));
}
vec4 clipOf(vec2 p, float depth) {
  return vec4(p.x / u_res.x * 2.0 - 1.0, 1.0 - p.y / u_res.y * 2.0, clamp(depth / u_cam.w, 0.0, 1.0) * 2.0 - 1.0, 1.0);
}`;

const SPACE_POINT_VS = `
attribute vec3 a_pos;
attribute float a_color;
attribute float a_rank;
attribute float a_hl;
attribute float a_size;
uniform float u_radius;
uniform float u_grow;
uniform float u_dpr;
uniform float u_limit;
uniform float u_pass;
uniform float u_ringFrom;
uniform float u_alpha;
uniform vec2 u_fog;
uniform float u_fade;
uniform vec3 u_bg;
uniform vec4 u_palette[${SPACE_PALETTE}];
uniform vec4 u_ring;
varying vec4 v_color;
varying float v_r;
${SPACE_PROJECT}
void main() {
  float need = u_pass < 1.5 ? u_ringFrom : 0.5;
  vec4 p = project(a_pos);
  bool hide = a_rank > u_limit || (u_pass > 0.5 && a_hl < need - 0.01) || p.z <= u_cam.z;
  float r = u_radius * a_size * clamp(p.w, 0.45, 3.0) + (u_pass > 0.5 && a_hl >= u_ringFrom - 0.01 ? u_grow : 0.0);
  v_r = r;
  gl_Position = hide ? vec4(2.0, 2.0, 2.0, 1.0) : clipOf(p.xy, p.z);
  gl_PointSize = hide ? 0.0 : (r + 1.0) * 2.0 * u_dpr;
  vec4 c = u_palette[int(a_color + 0.5)];
  float fog = clamp((p.z - u_fog.x) / u_fog.y, 0.0, 1.0) * u_fade;
  v_color = u_pass > 0.5 && u_pass < 1.5 ? u_ring : vec4(mix(c.rgb, u_bg, u_pass > 0.5 ? 0.0 : fog), c.a * u_alpha);
}`;

const SPACE_POINT_FS = `${PRECISION}
varying vec4 v_color;
varying float v_r;
uniform float u_shape;
uniform float u_dpr;
uniform float u_cut;
void main() {
  vec2 q = (gl_PointCoord * 2.0 - 1.0) * (v_r + 1.0) / v_r;
  vec2 a = abs(q);
  float d;
  if (u_shape < 0.5) d = length(q);
  else if (u_shape < 1.5) d = max(a.x, a.y) * 1.1;
  else if (u_shape < 2.5) d = max(q.y * 1.15 + 0.1, 2.0 * a.x - q.y * 0.95 + 0.05);
  else if (u_shape < 3.5) d = (a.x + a.y) * 0.87;
  else if (u_shape < 4.5) d = length(q);
  else if (u_shape < 5.5) d = max(min(a.x, a.y) / 0.35, max(a.x, a.y));
  else {
    vec2 k = a - vec2(0.55);
    d = 1.0 + length(max(k, 0.0)) + min(max(k.x, k.y), 0.0) - 0.35;
  }
  if (u_shape > 3.5 && u_shape < 4.5 && d < 0.55) discard;
  float alpha = clamp((1.0 - d) * v_r * u_dpr + 0.5, 0.0, 1.0);
  if (alpha <= u_cut) discard;
  gl_FragColor = vec4(v_color.rgb, v_color.a * alpha);
}`;

// A segment between two points in space, widened (and dashed) in screen pixels.
const SPACE_STROKE_VS = `
attribute vec3 a_a;
attribute vec3 a_b;
attribute vec2 a_corner;
attribute vec4 a_rgba;
attribute vec2 a_style;
varying vec4 v_color;
varying float v_along;
varying float v_dash;
${SPACE_PROJECT}
void main() {
  vec4 pa = project(a_a);
  vec4 pb = project(a_b);
  vec2 d = pb.xy - pa.xy;
  float len = length(d);
  vec2 dir = len > 0.0 ? d / len : vec2(1.0, 0.0);
  vec2 p = mix(pa.xy, pb.xy, a_corner.x) + vec2(-dir.y, dir.x) * a_corner.y * a_style.x * 0.5;
  bool hide = pa.z <= u_cam.z || pb.z <= u_cam.z;
  gl_Position = hide ? vec4(2.0, 2.0, 2.0, 1.0) : clipOf(p, mix(pa.z, pb.z, a_corner.x));
  v_color = a_rgba;
  v_along = a_corner.x * len;
  v_dash = a_style.y;
}`;

const SPACE_STROKE_FS = `${PRECISION}
varying vec4 v_color;
varying float v_along;
varying float v_dash;
void main() {
  if (v_dash > 0.0 && mod(v_along, v_dash * 2.0) > v_dash) discard;
  gl_FragColor = v_color;
}`;

// Regions: triangles already on screen (pixels).
const SPACE_FLAT_VS = `
attribute vec2 a_pos;
attribute vec4 a_rgba;
uniform vec2 u_res;
varying vec4 v_color;
void main() {
  gl_Position = vec4(a_pos.x / u_res.x * 2.0 - 1.0, 1.0 - a_pos.y / u_res.y * 2.0, 0.0, 1.0);
  v_color = a_rgba;
}`;

const SPACE_FLAT_FS = `${PRECISION}
varying vec4 v_color;
void main() { gl_FragColor = v_color; }`;

const SPACE_OPTIONS = { antialias: true, alpha: false, premultipliedAlpha: false, depth: true };
let spaceUsable = null;

function spaceContext(canvas) {
  return canvas.getContext('webgl2', SPACE_OPTIONS) || canvas.getContext('webgl', SPACE_OPTIONS);
}

function spacePrograms(gl) {
  return {
    points: compile(gl, SPACE_POINT_VS, SPACE_POINT_FS),
    stroke: compile(gl, SPACE_STROKE_VS, SPACE_STROKE_FS),
    flat: compile(gl, SPACE_FLAT_VS, SPACE_FLAT_FS),
  };
}

/** Whether this browser draws a 3D map with WebGL (tried once, on a canvas of its own). */
export function webgl3dUsable() {
  if (spaceUsable === null) {
    spaceUsable = false;
    try {
      const gl = spaceContext(document.createElement('canvas'));
      if (gl && gl.getContextAttributes().depth) {
        spacePrograms(gl);
        spaceUsable = true;
        const lose = gl.getExtension('WEBGL_lose_context');
        if (lose) lose.loseContext();
      }
    } catch (err) {
      spaceUsable = false;
    }
  }
  return spaceUsable;
}

export function createWebGL3DRenderer(canvas) {
  if (!webgl3dUsable()) return null;
  const gl = spaceContext(canvas);
  if (!gl) return null;
  let programs = spacePrograms(gl);
  const overlay = document.createElement('canvas');
  overlay.className = 'cx-map-frame__overlay';
  overlay.setAttribute('aria-hidden', 'true');
  canvas.after(overlay);
  const labels = overlay.getContext('2d');
  const probe = document.createElement('canvas').getContext('2d', { willReadFrequently: true });
  let dpr = 1;
  let tokens = new Map();
  let rgba = new Map();
  let lost = false;
  let buffers = new Map();
  let lines = { of: null, strip: null };
  let regionBuffer = null;
  const onLost = (event) => {
    event.preventDefault();
    lost = true;
  };
  const onRestored = () => {
    programs = spacePrograms(gl);
    buffers = new Map();
    lines = { of: null, strip: null };
    regionBuffer = null;
    lost = false;
  };
  canvas.addEventListener('webglcontextlost', onLost);
  canvas.addEventListener('webglcontextrestored', onRestored);

  const colorOf = (value) => colorToRgba(resolveColor(canvas, value, tokens), rgba, probe);
  const bufferOf = (array, used) => {
    let entry = buffers.get(array);
    if (!entry) {
      entry = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, entry);
      gl.bufferData(gl.ARRAY_BUFFER, array, gl.STATIC_DRAW);
      buffers.set(array, entry);
    }
    used.add(array);
    return entry;
  };
  const attribute = (loc, array, size, type, used, missing = 0) => {
    if (loc < 0 || loc === undefined) return;
    if (!array) {
      gl.disableVertexAttribArray(loc);
      if (size === 1) gl.vertexAttrib1f(loc, missing);
      return;
    }
    gl.bindBuffer(gl.ARRAY_BUFFER, bufferOf(array, used));
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, size, type, false, 0, 0);
  };
  /** A layer's (x, y, z) interleaved, kept on the layer. */
  const positions = (layer) => {
    if (!layer.xyz) {
      const n = layer.x.length;
      const xyz = new Float32Array(n * 3);
      for (let i = 0; i < n; i += 1) {
        xyz[3 * i] = layer.x[i];
        xyz[3 * i + 1] = layer.y[i];
        xyz[3 * i + 2] = layer.z ? layer.z[i] : 0;
      }
      Object.defineProperty(layer, 'xyz', { value: xyz, enumerable: false });
    }
    return layer.xyz;
  };
  /** The lines as strips: 14 floats per vertex (both ends, corner, colour, width and dash). */
  const stripOf = (sceneLines) => {
    if (lines.of !== sceneLines) {
      const out = [];
      const corners = [[0, -1], [1, -1], [1, 1], [0, -1], [1, 1], [0, 1]];
      for (const line of sceneLines) {
        const [r, g, b] = colorOf(line.color);
        const a = line.alpha === undefined ? 0.6 : line.alpha;
        const width = Math.max(1, line.width || 1);
        const dash = line.dash || 0;
        const zs = line.z;
        for (let k = 0; k + 1 < line.x.length; k += 2) {
          for (const [end, side] of corners) {
            out.push(line.x[k], line.y[k], zs ? zs[k] : 0, line.x[k + 1], line.y[k + 1], zs ? zs[k + 1] : 0,
              end, side, r, g, b, a, width, dash);
          }
        }
      }
      lines = { of: sceneLines, strip: Float32Array.from(out) };
    }
    return lines.strip;
  };
  const setCamera = (prog, view, far) => {
    gl.uniform2f(prog.u_res, view.width, view.height);
    gl.uniform3f(prog.u_target, view.tx, view.ty, view.tz);
    gl.uniform4f(prog.u_turn, view.cy, view.sy, view.cp, view.sp);
    gl.uniform4f(prog.u_cam, view.dist, view.focal, view.near, far);
  };
  const drawRegions = (scene, view) => {
    const tri = [];
    for (const region of scene.regions || []) {
      const p = region.members ? screenRegion(view, region.members, convexHull) : null;
      if (!p) continue;
      const [r, g, b] = colorOf(region.color);
      const fill = region.alpha === undefined ? 0.16 : region.alpha;
      for (let k = 2; k + 3 < p.length; k += 2) {
        tri.push(p[0], p[1], r, g, b, fill, p[k], p[k + 1], r, g, b, fill, p[k + 2], p[k + 3], r, g, b, fill);
      }
    }
    if (!tri.length) return;
    if (!regionBuffer) regionBuffer = gl.createBuffer();
    const { flat } = programs;
    gl.useProgram(flat.program);
    gl.uniform2f(flat.u_res, view.width, view.height);
    gl.bindBuffer(gl.ARRAY_BUFFER, regionBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, Float32Array.from(tri), gl.DYNAMIC_DRAW);
    gl.enableVertexAttribArray(flat.a_pos);
    gl.vertexAttribPointer(flat.a_pos, 2, gl.FLOAT, false, 24, 0);
    gl.enableVertexAttribArray(flat.a_rgba);
    gl.vertexAttribPointer(flat.a_rgba, 4, gl.FLOAT, false, 24, 8);
    gl.drawArrays(gl.TRIANGLES, 0, tri.length / 6);
    gl.disableVertexAttribArray(flat.a_pos);
    gl.disableVertexAttribArray(flat.a_rgba);
  };
  const drawLines = (scene, view, far, used) => {
    const array = stripOf(scene.lines || []);
    if (!array.length) return;
    const { stroke } = programs;
    gl.useProgram(stroke.program);
    setCamera(stroke, view, far);
    gl.bindBuffer(gl.ARRAY_BUFFER, bufferOf(array, used));
    const parts = [[stroke.a_a, 3, 0], [stroke.a_b, 3, 12], [stroke.a_corner, 2, 24], [stroke.a_rgba, 4, 32],
      [stroke.a_style, 2, 48]];
    for (const [loc, size, offset] of parts) {
      if (loc === undefined || loc < 0) continue;
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 56, offset);
    }
    gl.drawArrays(gl.TRIANGLES, 0, array.length / 14);
    for (const [loc] of parts) if (loc !== undefined && loc >= 0) gl.disableVertexAttribArray(loc);
  };

  return {
    name: 'webgl3d',
    resize(width, height, ratio) {
      dpr = ratio;
      for (const cv of [canvas, overlay]) {
        cv.width = Math.max(1, Math.round(width * ratio));
        cv.height = Math.max(1, Math.round(height * ratio));
      }
      gl.viewport(0, 0, canvas.width, canvas.height);
    },
    invalidate() {
      tokens = new Map();
      rgba = new Map();
      lines = { of: null, strip: null };
    },
    draw(scene, view) {
      if (lost) return;
      const used = new Set();
      const bg = colorOf('--cx-surface');
      gl.clearColor(bg[0], bg[1], bg[2], 1);
      gl.depthMask(true);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.enable(gl.BLEND);
      gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      const r = view.radius || 1;
      const far = view.dist + 3 * r;

      gl.disable(gl.DEPTH_TEST);
      drawRegions(scene, view);

      const anyHighlight = scene.layers.some((l) => l.highlight && l.highlightCount);
      const { points } = programs;
      const zoom = zoomOf(view);
      const pointPass = (pass) => {
        gl.useProgram(points.program);
        setCamera(points, view, far);
        gl.uniform1f(points.u_dpr, dpr);
        gl.uniform4fv(points.u_ring, colorOf('--cx-accent'));
        gl.uniform3f(points.u_bg, bg[0], bg[1], bg[2]);
        gl.uniform2f(points.u_fog, view.dist - r, 2 * r);
        gl.uniform1f(points.u_fade, DEPTH_FADE);
        for (const layer of scene.layers) {
          const n = layer.x.length;
          if (!n || (pass > 0 && !(layer.highlight && layer.highlightCount))) continue;
          const palette = new Float32Array(SPACE_PALETTE * 4);
          (layer.palette || []).slice(0, SPACE_PALETTE).forEach((c, k) => palette.set(colorOf(c), k * 4));
          gl.uniform4fv(points.u_palette, palette);
          const shape = SHAPES[layer.shape || 'circle'] || 0;
          gl.uniform1f(points.u_shape, pass > 0 && shape === SHAPES.ring ? SHAPES.circle : shape);
          gl.uniform1f(points.u_radius, layer.radius || 2.5);
          gl.uniform1f(points.u_grow, pass === 1 ? 2.5 : pass === 2 ? 1.5 : 0);
          gl.uniform1f(points.u_ringFrom, layer.ringFrom || 1);
          gl.uniform1f(points.u_pass, pass);
          // the traces around a focus are faint and write no depth; the other points do
          gl.uniform1f(points.u_cut, pass === 0 && anyHighlight ? 0 : 0.35);
          gl.uniform1f(points.u_alpha, pass === 2 ? 1
            : anyHighlight ? (layer.dim === undefined ? 0.3 : layer.dim) : (layer.alpha || 0.9));
          gl.uniform1f(points.u_limit, pass > 0 ? 1.5 : detailLimit(layer, zoom));
          attribute(points.a_pos, positions(layer), 3, gl.FLOAT, used);
          attribute(points.a_color, layer.color || null, 1, gl.UNSIGNED_SHORT, used);
          attribute(points.a_rank, layer.rank || null, 1, gl.FLOAT, used);
          attribute(points.a_hl, pass > 0 ? layer.highlight : null, 1, gl.UNSIGNED_BYTE, used);
          attribute(points.a_size, layer.size || null, 1, gl.FLOAT, used, 1);
          gl.drawArrays(gl.POINTS, 0, n);
        }
      };
      gl.enable(gl.DEPTH_TEST);
      gl.depthFunc(gl.LEQUAL);
      gl.depthMask(!anyHighlight);
      pointPass(0);
      gl.depthMask(false);
      drawLines(scene, view, far, used);
      if (anyHighlight) {
        gl.depthMask(true);
        pointPass(1);
        pointPass(2);
      }
      gl.disable(gl.DEPTH_TEST);
      gl.depthMask(true);
      for (const [array, buffer] of buffers) {
        if (!used.has(array)) {
          gl.deleteBuffer(buffer);
          buffers.delete(array);
        }
      }
      labels.setTransform(dpr, 0, 0, dpr, 0, 0);
      labels.clearRect(0, 0, view.width, view.height);
      drawLabels(labels, scene, view, (value) => resolveColor(canvas, value, tokens),
        getComputedStyle(canvas).fontFamily || 'sans-serif');
    },
    destroy() {
      for (const buffer of buffers.values()) gl.deleteBuffer(buffer);
      buffers = new Map();
      if (regionBuffer) gl.deleteBuffer(regionBuffer);
      canvas.removeEventListener('webglcontextlost', onLost);
      canvas.removeEventListener('webglcontextrestored', onRestored);
      overlay.remove();
      const lose = gl.getExtension('WEBGL_lose_context');
      if (lose) lose.loseContext();
    },
  };
}
