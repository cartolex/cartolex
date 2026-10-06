// SPDX-License-Identifier: MIT
/**
 * The WebGL renderer: 10⁵ points and more, panned and zoomed at the frame
 * rate. Each layer's positions, colours, ranks and highlight go to the GPU
 * once (buffers are kept per array and dropped when no scene uses them); a
 * frame only sets the view. The points' shapes are drawn by the fragment
 * shader; the ranks against the zoom's detail limit hide points in the
 * vertex shader. Regions (convex polygons) and lines are triangles and
 * segments below the points (a line wider than a pixel, or dashed, is a strip
 * of two triangles per segment, widened and dashed in screen pixels by its own
 * shaders); the labels are drawn on a 2D canvas laid over the map.
 *
 * `createWebGLRenderer(canvas)` answers null when the browser gives no WebGL
 * context: the caller then uses the Canvas 2D renderer.
 */
import { SHAPES, colorToRgba, detailLimit, resolveColor, zoomOf } from './core.js';
import { drawLabels } from './canvas2d.js';

const PALETTE_SIZE = 32;

const POINT_VS = `
attribute vec2 a_pos;
attribute float a_color;
attribute float a_rank;
attribute float a_hl;
attribute float a_size;
uniform vec2 u_res;
uniform vec3 u_view;
uniform float u_radius;
uniform float u_grow;
uniform float u_dpr;
uniform float u_limit;
uniform float u_pass;
uniform float u_ringFrom;
uniform float u_alpha;
uniform vec4 u_palette[${PALETTE_SIZE}];
uniform vec4 u_ring;
varying vec4 v_color;
varying float v_r;
void main() {
  float need = u_pass < 1.5 ? u_ringFrom : 0.5;
  bool hide = a_rank > u_limit || (u_pass > 0.5 && a_hl < need - 0.01);
  vec2 p = vec2(a_pos.x * u_view.x + u_view.y, u_view.z - a_pos.y * u_view.x);
  float r = u_radius * a_size + (u_pass > 0.5 && a_hl >= u_ringFrom - 0.01 ? u_grow : 0.0);
  v_r = r;
  gl_Position = hide ? vec4(2.0, 2.0, 2.0, 1.0)
    : vec4(p.x / u_res.x * 2.0 - 1.0, 1.0 - p.y / u_res.y * 2.0, 0.0, 1.0);
  gl_PointSize = hide ? 0.0 : (r + 1.0) * 2.0 * u_dpr;
  vec4 c = u_palette[int(a_color + 0.5)];
  v_color = u_pass > 0.5 && u_pass < 1.5 ? u_ring : vec4(c.rgb, c.a * u_alpha);
}`;

const PRECISION = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif`;

const POINT_FS = `${PRECISION}
varying vec4 v_color;
varying float v_r;
uniform float u_shape;
uniform float u_dpr;
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
  if (alpha <= 0.0) discard;
  gl_FragColor = vec4(v_color.rgb, v_color.a * alpha);
}`;

const FLAT_VS = `
attribute vec2 a_pos;
attribute vec4 a_rgba;
uniform vec2 u_res;
uniform vec3 u_view;
varying vec4 v_color;
void main() {
  vec2 p = vec2(a_pos.x * u_view.x + u_view.y, u_view.z - a_pos.y * u_view.x);
  gl_Position = vec4(p.x / u_res.x * 2.0 - 1.0, 1.0 - p.y / u_res.y * 2.0, 0.0, 1.0);
  v_color = a_rgba;
}`;

const FLAT_FS = `${PRECISION}
varying vec4 v_color;
void main() { gl_FragColor = v_color; }`;

// A wide or dashed segment: each vertex knows both ends (in map units), its end (0 or 1)
// and side (-1 or 1), and the line's width and dash (pixels; 0: solid).
const STROKE_VS = `
attribute vec2 a_a;
attribute vec2 a_b;
attribute vec2 a_corner;
attribute vec4 a_rgba;
attribute vec2 a_style;
uniform vec2 u_res;
uniform vec3 u_view;
varying vec4 v_color;
varying float v_along;
varying float v_dash;
void main() {
  vec2 pa = vec2(a_a.x * u_view.x + u_view.y, u_view.z - a_a.y * u_view.x);
  vec2 pb = vec2(a_b.x * u_view.x + u_view.y, u_view.z - a_b.y * u_view.x);
  vec2 d = pb - pa;
  float len = length(d);
  vec2 dir = len > 0.0 ? d / len : vec2(1.0, 0.0);
  vec2 p = mix(pa, pb, a_corner.x) + vec2(-dir.y, dir.x) * a_corner.y * a_style.x * 0.5;
  gl_Position = vec4(p.x / u_res.x * 2.0 - 1.0, 1.0 - p.y / u_res.y * 2.0, 0.0, 1.0);
  v_color = a_rgba;
  v_along = a_corner.x * len;
  v_dash = a_style.y;
}`;

const STROKE_FS = `${PRECISION}
varying vec4 v_color;
varying float v_along;
varying float v_dash;
void main() {
  if (v_dash > 0.0 && mod(v_along, v_dash * 2.0) > v_dash) discard;
  gl_FragColor = v_color;
}`;

/** Whether a line is drawn as a strip (wider than a pixel, or dashed). */
const stroked = (line) => (line.width || 1) > 1.01 || Boolean(line.dash);

function compile(gl, vs, fs) {
  const program = gl.createProgram();
  for (const [type, source] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]]) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      throw new Error(`map shader: ${gl.getShaderInfoLog(shader)}`);
    }
    gl.attachShader(program, shader);
    gl.deleteShader(shader);
  }
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    throw new Error(`map program: ${gl.getProgramInfoLog(program)}`);
  }
  const loc = { program };
  const count = gl.getProgramParameter(program, gl.ACTIVE_UNIFORMS);
  for (let i = 0; i < count; i += 1) {
    const name = gl.getActiveUniform(program, i).name.replace(/\[0\]$/, '');
    loc[name] = gl.getUniformLocation(program, name);
  }
  const attributes = gl.getProgramParameter(program, gl.ACTIVE_ATTRIBUTES);
  for (let i = 0; i < attributes; i += 1) {
    const name = gl.getActiveAttrib(program, i).name;
    loc[name] = gl.getAttribLocation(program, name);
  }
  return loc;
}

const OPTIONS = { antialias: true, alpha: false, premultipliedAlpha: false, depth: false };
let usable = null;

function context(canvas) {
  return canvas.getContext('webgl2', OPTIONS) || canvas.getContext('webgl', OPTIONS);
}

/**
 * Whether this browser draws the map with WebGL: a context and both programs,
 * tried once on a canvas of its own (a canvas that gave a WebGL context can
 * no longer give a 2D one, so the map's own canvas is only asked when it works).
 */
export function webglUsable() {
  if (usable === null) {
    usable = false;
    try {
      const gl = context(document.createElement('canvas'));
      if (gl) {
        compile(gl, POINT_VS, POINT_FS);
        compile(gl, FLAT_VS, FLAT_FS);
        compile(gl, STROKE_VS, STROKE_FS);
        usable = true;
        const lose = gl.getExtension('WEBGL_lose_context');
        if (lose) lose.loseContext();
      }
    } catch (err) {
      usable = false;
    }
  }
  return usable;
}

export function createWebGLRenderer(canvas) {
  if (!webglUsable()) return null;
  const gl = context(canvas);
  if (!gl) return null;
  let points = compile(gl, POINT_VS, POINT_FS);
  let flat = compile(gl, FLAT_VS, FLAT_FS);
  let stroke = compile(gl, STROKE_VS, STROKE_FS);
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
  // Buffers per typed array (positions, colours, ranks, highlights) and per scene part.
  let buffers = new Map();
  let shapes = { regions: null, regionBuf: null, lines: null, lineBuf: null };
  const onLost = (event) => {
    event.preventDefault();
    lost = true;
  };
  const onRestored = () => {
    points = compile(gl, POINT_VS, POINT_FS);
    flat = compile(gl, FLAT_VS, FLAT_FS);
    stroke = compile(gl, STROKE_VS, STROKE_FS);
    buffers = new Map();
    shapes = { regions: null, regionBuf: null, lines: null, lineBuf: null };
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
    if (loc < 0) return;
    if (!array) {
      gl.disableVertexAttribArray(loc);
      if (size === 1) gl.vertexAttrib1f(loc, missing);
      return;
    }
    gl.bindBuffer(gl.ARRAY_BUFFER, bufferOf(array, used));
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, size, type, false, 0, 0);
  };
  const positions = (layer) => {
    if (!layer.xy) {
      const xy = new Float32Array(layer.x.length * 2);
      for (let i = 0; i < layer.x.length; i += 1) {
        xy[2 * i] = layer.x[i];
        xy[2 * i + 1] = layer.y[i];
      }
      Object.defineProperty(layer, 'xy', { value: xy, enumerable: false });
    }
    return layer.xy;
  };
  /** Regions as triangles (a fan per convex polygon) and outlines, lines as segments. */
  const flatBuffers = (scene, used) => {
    const regions = scene.regions || [];
    if (shapes.regions !== regions) {
      const tri = [];
      const edge = [];
      for (const region of regions) {
        const p = region.polygon;
        const [r, g, b] = colorOf(region.color);
        const fill = region.alpha === undefined ? 0.16 : region.alpha;
        for (let k = 2; k + 3 < p.length; k += 2) {
          tri.push(p[0], p[1], r, g, b, fill, p[k], p[k + 1], r, g, b, fill, p[k + 2], p[k + 3], r, g, b, fill);
        }
        for (let k = 0; k < p.length; k += 2) {
          const m = (k + 2) % p.length;
          edge.push(p[k], p[k + 1], r, g, b, 0.7, p[m], p[m + 1], r, g, b, 0.7);
        }
      }
      shapes.regions = regions;
      shapes.regionBuf = { tri: Float32Array.from(tri), edge: Float32Array.from(edge) };
    }
    const lines = scene.lines || [];
    if (shapes.lines !== lines) {
      const seg = [];
      const strip = [];
      // the two triangles of a segment: (end, side) at each of their corners
      const corners = [[0, -1], [1, -1], [1, 1], [0, -1], [1, 1], [0, 1]];
      for (const line of lines) {
        const [r, g, b] = colorOf(line.color);
        const a = line.alpha === undefined ? 0.6 : line.alpha;
        if (!stroked(line)) {
          for (let k = 0; k < line.x.length; k += 1) seg.push(line.x[k], line.y[k], r, g, b, a);
          continue;
        }
        const width = line.width || 1;
        const dash = line.dash || 0;
        for (let k = 0; k + 1 < line.x.length; k += 2) {
          for (const [end, side] of corners) {
            strip.push(line.x[k], line.y[k], line.x[k + 1], line.y[k + 1], end, side, r, g, b, a, width, dash);
          }
        }
      }
      shapes.lines = lines;
      shapes.lineBuf = Float32Array.from(seg);
      shapes.stripBuf = Float32Array.from(strip);
    }
    used.add(shapes.regionBuf.tri);
    used.add(shapes.regionBuf.edge);
    used.add(shapes.lineBuf);
    used.add(shapes.stripBuf);
  };
  /** The wide and dashed lines: 12 floats per vertex (both ends, corner, colour, style). */
  const drawStrips = (array, used) => {
    if (!array.length) return;
    gl.bindBuffer(gl.ARRAY_BUFFER, bufferOf(array, used));
    const parts = [[stroke.a_a, 2, 0], [stroke.a_b, 2, 8], [stroke.a_corner, 2, 16], [stroke.a_rgba, 4, 24],
      [stroke.a_style, 2, 40]];
    for (const [loc, size, offset] of parts) {
      if (loc < 0) continue;
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 48, offset);
    }
    gl.drawArrays(gl.TRIANGLES, 0, array.length / 12);
    for (const [loc] of parts) if (loc >= 0) gl.disableVertexAttribArray(loc);
  };
  const drawFlat = (array, mode, used) => {
    if (!array.length) return;
    gl.bindBuffer(gl.ARRAY_BUFFER, bufferOf(array, used));
    gl.enableVertexAttribArray(flat.a_pos);
    gl.vertexAttribPointer(flat.a_pos, 2, gl.FLOAT, false, 24, 0);
    gl.enableVertexAttribArray(flat.a_rgba);
    gl.vertexAttribPointer(flat.a_rgba, 4, gl.FLOAT, false, 24, 8);
    gl.drawArrays(mode, 0, array.length / 6);
  };

  return {
    name: 'webgl',
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
      shapes = { regions: null, regionBuf: null, lines: null, lineBuf: null };
    },
    draw(scene, view) {
      if (lost) return;
      const used = new Set();
      const [br, bg, bb] = colorOf('--cx-surface');
      gl.clearColor(br, bg, bb, 1);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.enable(gl.BLEND);
      gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      const res = [view.width, view.height];
      const v = [view.scale, view.tx, view.ty];

      flatBuffers(scene, used);
      gl.useProgram(flat.program);
      gl.uniform2fv(flat.u_res, res);
      gl.uniform3fv(flat.u_view, v);
      drawFlat(shapes.regionBuf.tri, gl.TRIANGLES, used);
      drawFlat(shapes.regionBuf.edge, gl.LINES, used);
      drawFlat(shapes.lineBuf, gl.LINES, used);
      gl.disableVertexAttribArray(flat.a_rgba);
      if (shapes.stripBuf.length) {
        gl.useProgram(stroke.program);
        gl.uniform2fv(stroke.u_res, res);
        gl.uniform3fv(stroke.u_view, v);
        drawStrips(shapes.stripBuf, used);
      }

      gl.useProgram(points.program);
      gl.uniform2fv(points.u_res, res);
      gl.uniform3fv(points.u_view, v);
      gl.uniform1f(points.u_dpr, dpr);
      gl.uniform4fv(points.u_ring, colorOf('--cx-accent'));
      const zoom = zoomOf(view);
      const anyHighlight = scene.layers.some((l) => l.highlight && l.highlightCount);
      const passes = anyHighlight ? [0, 1, 2] : [0];
      for (const pass of passes) {
        for (const layer of scene.layers) {
          const n = layer.x.length;
          if (!n || (pass > 0 && !(layer.highlight && layer.highlightCount))) continue;
          const palette = new Float32Array(PALETTE_SIZE * 4);
          (layer.palette || []).slice(0, PALETTE_SIZE).forEach((c, k) => palette.set(colorOf(c), k * 4));
          gl.uniform4fv(points.u_palette, palette);
          const r = layer.radius || 2.5;
          const shape = SHAPES[layer.shape || 'circle'] || 0;
          gl.uniform1f(points.u_shape, pass > 0 && shape === SHAPES.ring ? SHAPES.circle : shape);
          gl.uniform1f(points.u_radius, r);
          gl.uniform1f(points.u_grow, pass === 1 ? 2.5 : pass === 2 ? 1.5 : 0);
          gl.uniform1f(points.u_ringFrom, layer.ringFrom || 1);
          gl.uniform1f(points.u_pass, pass);
          gl.uniform1f(points.u_alpha, pass === 2 ? 1
            : anyHighlight ? (layer.dim === undefined ? 0.3 : layer.dim) : (layer.alpha || 0.9));
          gl.uniform1f(points.u_limit, pass > 0 ? 1.5 : detailLimit(layer, zoom));
          attribute(points.a_pos, positions(layer), 2, gl.FLOAT, used);
          attribute(points.a_color, layer.color || null, 1, gl.UNSIGNED_SHORT, used);
          attribute(points.a_rank, layer.rank || null, 1, gl.FLOAT, used);
          attribute(points.a_hl, pass > 0 ? layer.highlight : null, 1, gl.UNSIGNED_BYTE, used);
          attribute(points.a_size, layer.size || null, 1, gl.FLOAT, used, 1);
          gl.drawArrays(gl.POINTS, 0, n);
        }
      }
      // Buffers no array of this scene uses any more.
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
      canvas.removeEventListener('webglcontextlost', onLost);
      canvas.removeEventListener('webglcontextrestored', onRestored);
      overlay.remove();
      const lose = gl.getExtension('WEBGL_lose_context');
      if (lose) lose.loseContext();
    },
  };
}
