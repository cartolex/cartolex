// SPDX-License-Identifier: MIT
/**
 * The colour schemes of the themes, each with a dark and a bright version. A scheme gives
 * one colour per top-level theme: either one colour per theme from a list (`kind: 'each'`),
 * or the themes placed along one scale (`kind: 'scale'`), ordered by their place on the map,
 * left to right. The bright version is the « paper » one: darker colours on white, for print.
 * The map, the treemap, the legends, saved views and the app's Themes screen all take their
 * colours here, so a theme has the same colour everywhere.
 */

/** Vivid: the twelve hue families of the interface's tokens, bright and dark. */
const VIVID_BRIGHT = ['#c25d58', '#00987c', '#a763ab', '#7e8814', '#5a7dce', '#ba6826', '#0095a5', '#bb5c84',
  '#47944c', '#866ec5', '#a37800', '#008bc2'];
const VIVID_DARK = ['#ed8c84', '#42c3a6', '#d190d4', '#a8b455', '#86a9f7', '#e5955d', '#26c0cf', '#e68aaf',
  '#78bf7b', '#b09bee', '#cda448', '#53b6eb'];

/** Every scheme: its id, its kind, its colours (`list`) or scale (`stops`, and the part of
 * the scale each version uses), and the catalogue key of its name. */
export const SCHEMES = [
  { id: 'vivid', kind: 'each', bright: VIVID_BRIGHT, dark: VIVID_DARK },
  { id: 'soft', kind: 'each', bright: VIVID_BRIGHT, dark: VIVID_DARK, soften: true },
  { id: 'cvd', kind: 'each', safe: true, list: ['#CC6677', '#332288', '#DDCC77', '#117733', '#88CCEE', '#882255', '#44AA99', '#999933', '#AA4499'], rest: '#BBBBBB' },
  { id: 'okabe', kind: 'each', safe: true, list: ['#E69F00', '#56B4E9', '#009E73', '#F0E442', '#0072B2', '#D55E00', '#CC79A7', '#999999'] },
  { id: 'tolbright', kind: 'each', list: ['#4477AA', '#EE6677', '#228833', '#CCBB44', '#66CCEE', '#AA3377', '#BBBBBB'] },
  { id: 'tableau', kind: 'each', list: ['#4E79A7', '#F28E2B', '#E15759', '#76B7B2', '#59A14F', '#EDC948', '#B07AA1', '#FF9DA7', '#9C755F', '#BAB0AC'] },
  { id: 'dark2', kind: 'each', list: ['#1B9E77', '#D95F02', '#7570B3', '#E7298A', '#66A61E', '#E6AB02', '#A6761D', '#666666'] },
  { id: 'set2', kind: 'each', list: ['#66C2A5', '#FC8D62', '#8DA0CB', '#E78AC3', '#A6D854', '#FFD92F', '#E5C494', '#B3B3B3'] },
  { id: 'paired', kind: 'each', list: ['#A6CEE3', '#1F78B4', '#B2DF8A', '#33A02C', '#FB9A99', '#E31A1C', '#FDBF6F', '#FF7F00', '#CAB2D6', '#6A3D9A'] },
  { id: 'viridis', kind: 'scale', stops: ['#440154', '#482878', '#3e4989', '#31688e', '#26828e', '#1f9e89', '#35b779', '#6ece58', '#b5de2b', '#fde725'], brightPart: [0, 0.86], darkPart: [0.3, 1] },
  { id: 'cividis', kind: 'scale', safe: true, stops: ['#00224e', '#123570', '#3b496c', '#575d6d', '#707173', '#8a8678', '#a59c74', '#c3b369', '#e1cc55', '#fee838'], brightPart: [0, 0.85], darkPart: [0.3, 1] },
  { id: 'magma', kind: 'scale', stops: ['#000004', '#1c1044', '#4f127b', '#812581', '#b5367a', '#e55964', '#fb8761', '#fec287', '#fcfdbf'], brightPart: [0.12, 0.8], darkPart: [0.32, 1] },
  { id: 'inferno', kind: 'scale', stops: ['#000004', '#1b0c41', '#4a0c6b', '#781c6d', '#a52c60', '#cf4446', '#ed6925', '#fb9b06', '#f7d13d', '#fcffa4'], brightPart: [0.12, 0.8], darkPart: [0.32, 1] },
  { id: 'plasma', kind: 'scale', stops: ['#0d0887', '#46039f', '#7201a8', '#9c179e', '#bd3786', '#d8576b', '#ed7953', '#fb9f3a', '#fdca26', '#f0f921'], brightPart: [0, 0.8], darkPart: [0.25, 1] },
  { id: 'ylgnbu', kind: 'scale', stops: ['#ffffd9', '#edf8b1', '#c7e9b4', '#7fcdbb', '#41b6c4', '#1d91c0', '#225ea8', '#253494', '#081d58'], brightPart: [0.3, 1], darkPart: [0.05, 0.72] },
  { id: 'ylorrd', kind: 'scale', stops: ['#ffffcc', '#ffeda0', '#fed976', '#feb24c', '#fd8d3c', '#fc4e2a', '#e31a1c', '#bd0026', '#800026'], brightPart: [0.3, 1], darkPart: [0.05, 0.78] },
  { id: 'greens', kind: 'scale', stops: ['#f7fcf5', '#e5f5e0', '#c7e9c0', '#a1d99b', '#74c476', '#41ab5d', '#238b45', '#006d2c', '#00441b'], brightPart: [0.32, 1], darkPart: [0.05, 0.7] },
  { id: 'blues', kind: 'scale', stops: ['#f7fbff', '#deebf7', '#c6dbef', '#9ecae1', '#6baed6', '#4292c6', '#2171b5', '#08519c', '#08306b'], brightPart: [0.32, 1], darkPart: [0.05, 0.7] },
  { id: 'purples', kind: 'scale', stops: ['#fcfbfd', '#efedf5', '#dadaeb', '#bcbddc', '#9e9ac8', '#807dba', '#6a51a3', '#54278f', '#3f007d'], brightPart: [0.32, 1], darkPart: [0.05, 0.7] },
  { id: 'oranges', kind: 'scale', stops: ['#fff5eb', '#fee6ce', '#fdd0a2', '#fdae6b', '#fd8d3c', '#f16913', '#d94801', '#a63603', '#7f2704'], brightPart: [0.3, 1], darkPart: [0.05, 0.7] },
  { id: 'reds', kind: 'scale', stops: ['#fff5f0', '#fee0d2', '#fcbba1', '#fc9272', '#fb6a4a', '#ef3b2c', '#cb181d', '#a50f15', '#67000d'], brightPart: [0.3, 1], darkPart: [0.05, 0.7] },
  { id: 'greys', kind: 'scale', stops: ['#ffffff', '#f0f0f0', '#d9d9d9', '#bdbdbd', '#969696', '#737373', '#525252', '#252525', '#000000'], brightPart: [0.38, 1], darkPart: [0, 0.62] },
];

/** The scheme used when none is chosen (the interface's own hues). */
export const DEFAULT_SCHEME = 'vivid';

/** A scheme by its id (the default one for an id it does not know). */
export function schemeOf(id) {
  return SCHEMES.find((s) => s.id === id) || SCHEMES[0];
}

function hexRgb(hex) {
  const v = (k) => parseInt(hex.slice(k, k + 2), 16);
  return [v(1), v(3), v(5)];
}

function rgbHex(rgb) {
  return `#${rgb.map((c) => Math.max(0, Math.min(255, Math.round(c))).toString(16).padStart(2, '0')).join('')}`;
}

/** `#rrggbb` as `[hue 0…360, saturation 0…1, lightness 0…1]`. */
export function hexToHsl(hex) {
  const [r, g, b] = hexRgb(hex).map((c) => c / 255);
  const mx = Math.max(r, g, b);
  const mn = Math.min(r, g, b);
  const l = (mx + mn) / 2;
  let hue = 0;
  let s = 0;
  if (mx !== mn) {
    const d = mx - mn;
    s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
    if (mx === r) hue = (g - b) / d + (g < b ? 6 : 0);
    else if (mx === g) hue = (b - r) / d + 2;
    else hue = (r - g) / d + 4;
    hue *= 60;
  }
  return [hue, s, l];
}

/** `[hue, saturation, lightness]` (0…360, 0…1, 0…1) as `#rrggbb`. */
export function hslToHex(hsl) {
  const [hue, s, l] = hsl;
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((hue / 60) % 2) - 1));
  const m = l - c / 2;
  const [r, g, b] = hue < 60 ? [c, x, 0] : hue < 120 ? [x, c, 0] : hue < 180 ? [0, c, x]
    : hue < 240 ? [0, x, c] : hue < 300 ? [x, 0, c] : [c, 0, x];
  return rgbHex([(r + m) * 255, (g + m) * 255, (b + m) * 255]);
}

/** *hex* lighter (amount > 0) or darker (amount < 0), in points of lightness (0…100). */
export function shade(hex, amount) {
  const [hue, s, l] = hexToHsl(hex);
  return hslToHex([hue, s, Math.max(0.06, Math.min(0.94, l + amount / 100))]);
}

/** Black or white, whichever reads better on *hex*. */
export function inkOn(hex) {
  const [r, g, b] = hexRgb(hex).map((c) => {
    const v = c / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return lum > 0.22 ? '#101216' : '#ffffff';
}

function scaleAt(stops, x) {
  const pos = Math.max(0, Math.min(1, x)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(pos));
  const f = pos - i;
  const a = hexRgb(stops[i]);
  const b = hexRgb(stops[i + 1]);
  return rgbHex(a.map((v, k) => v + (b[k] - v) * f));
}

/** One colour of a list for the dark or bright version: lighter on dark, darker on paper;
 * past the list's end, the colours come round again lighter or darker. */
function listColour(list, i, dark) {
  const [hue, s, l] = hexToHsl(list[i % list.length]);
  const round = Math.floor(i / list.length) * (dark ? 0.14 : -0.14);
  const base = dark ? Math.max(l, 0.55) : Math.min(l, 0.5);
  return hslToHex([hue, s, Math.max(0.12, Math.min(0.9, base + round))]);
}

/**
 * The colour of each of *count* top-level themes under scheme *id*, for the dark or bright
 * look: `#rrggbb` strings, the i-th for the i-th theme in the tree's order. *order* gives,
 * for a scale, each theme's rank along it (by default its place in the tree); a scheme of
 * one colour per theme ignores it.
 */
export function themeColours(id, count, { dark = false, order = null } = {}) {
  const scheme = schemeOf(id);
  const out = [];
  for (let i = 0; i < count; i += 1) {
    if (scheme.kind === 'scale') {
      const [lo, hi] = dark ? scheme.darkPart : scheme.brightPart;
      const rank = order ? order[i] : i;
      out.push(scaleAt(scheme.stops, lo + ((hi - lo) * rank) / Math.max(1, count - 1)));
    } else if (scheme.list) {
      out.push(scheme.rest && i >= scheme.list.length ? scheme.rest : listColour(scheme.list, i, dark));
    } else {
      const base = (dark ? scheme.dark : scheme.bright)[i % 12];
      const round = Math.floor(i / 12) * (dark ? 12 : -12);
      if (!scheme.soften) out.push(round ? shade(base, round) : base);
      else {
        const [hue, s, l] = hexToHsl(base);
        out.push(hslToHex([hue, s * 0.45, Math.max(0.2, Math.min(0.85, l + round / 100 + (dark ? 0.02 : 0.04)))]));
      }
    }
  }
  return out;
}

/** The neutral colour of what no top-level theme holds, for the dark or bright look. */
export function neutralColour(dark) {
  return dark ? '#8a8d93' : '#6f7277';
}

/** The ranks of themes along a scale from their places: the leftmost first. *xs* are the
 * themes' horizontal places (null: unplaced, last). */
export function orderByPlace(xs) {
  const idx = xs.map((x, i) => [x === null || x === undefined ? Infinity : x, i]).sort((a, b) => a[0] - b[0]);
  const out = new Array(xs.length);
  idx.forEach(([, i], r) => { out[i] = r; });
  return out;
}
