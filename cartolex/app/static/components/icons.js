// SPDX-License-Identifier: MIT
/**
 * Line icons drawn in `currentColor` on a 16-unit grid.
 *
 * An icon is decorative (`aria-hidden`) unless given a `label`, which makes it
 * an image with that accessible name. Icons are built as elements, never from
 * markup strings.
 */
import { html } from '../core/preact.js';

const PATHS = {
  close: ['M4 4l8 8', 'M12 4l-8 8'],
  check: ['M3.5 8.5l3 3 6-7'],
  cross: ['M4 4l8 8', 'M12 4l-8 8'],
  dash: ['M4 8h8'],
  plus: ['M8 3v10', 'M3 8h10'],
  more: [],
  dot: [],
  'chevron-down': ['M4 6l4 4 4-4'],
  'chevron-up': ['M4 10l4-4 4 4'],
  'chevron-right': ['M6 4l4 4-4 4'],
  'chevron-left': ['M10 4l-4 4 4 4'],
  'sort-none': ['M5 6l3-3 3 3', 'M5 10l3 3 3-3'],
  'sort-asc': ['M5 7l3-3 3 3', 'M8 4v9'],
  'sort-desc': ['M5 9l3 3 3-3', 'M8 3v9'],
  copy: ['M5.5 5.5h7v7h-7z', 'M3.5 10.5v-7h7'],
  download: ['M8 2.5v8', 'M4.5 7.5L8 11l3.5-3.5', 'M3 13.5h10'],
  upload: ['M8 11V3', 'M4.5 6L8 2.5 11.5 6', 'M3 13.5h10'],
  external: ['M9 3h4v4', 'M13 3L7 9', 'M11 9.5v3.5H3V5h3.5'],
  info: ['M8 7v4.5', 'M8 4.6v.1'],
  help: ['M6.2 6.2a1.9 1.9 0 1 1 2.6 1.8c-.5.2-.8.6-.8 1.1v.6', 'M8 11.6v.1'],
  warning: ['M8 2.5L14 13H2z', 'M8 6.5v3', 'M8 11.3v.1'],
  error: ['M8 4.8v3.8', 'M8 10.9v.1'],
  sun: ['M8 1.5v1.5', 'M8 13v1.5', 'M1.5 8H3', 'M13 8h1.5', 'M3.4 3.4l1 1', 'M11.6 11.6l1 1',
    'M3.4 12.6l1-1', 'M11.6 4.4l1-1'],
  moon: ['M12.5 9.8A5 5 0 0 1 6.2 3.5a5 5 0 1 0 6.3 6.3z'],
  settings: ['M2.5 4.5h7', 'M12.5 4.5h1', 'M2.5 11.5h1', 'M6.5 11.5h7'],
  activity: ['M1.5 8h3l2-4.5 3 9 2-4.5h3'],
  search: ['M11 11l3.5 3.5'],
  menu: ['M2.5 4h11', 'M2.5 8h11', 'M2.5 12h11'],
  file: ['M4 1.5h5.5L12.5 4.5V14.5H4z', 'M9.5 1.5v3h3'],
  language: ['M1.5 8h13', 'M8 1.5c2 2 2.7 4.2 2.7 6.5S10 12.5 8 14.5c-2-2-2.7-4.2-2.7-6.5S6 3.5 8 1.5'],
};

const CIRCLES = {
  info: [[8, 8, 6.5]],
  error: [[8, 8, 6.5]],
  help: [[8, 8, 6.5]],
  sun: [[8, 8, 3]],
  search: [[7, 7, 4.5]],
  more: [[3.5, 8, 0.9, true], [8, 8, 0.9, true], [12.5, 8, 0.9, true]],
  dot: [[8, 8, 3, true]],
  language: [[8, 8, 6.5]],
  settings: [[11, 4.5, 1.5], [5, 11.5, 1.5]],
};

/** Names of every icon (the gallery shows them all). */
export const ICON_NAMES = Object.keys(PATHS);

/**
 * An icon.
 * @param {{name: string, label?: string, size?: 16|20|24, class?: string}} props
 */
export function Icon({ name, label, size = 16, class: cls = '' }) {
  const paths = PATHS[name] || [];
  const circles = CIRCLES[name] || [];
  const a11y = label ? { role: 'img', 'aria-label': label } : { 'aria-hidden': 'true' };
  return html`<svg class=${`cx-icon cx-icon--${size} ${cls}`} viewBox="0 0 16 16" width=${size}
    height=${size} focusable="false" ...${a11y}>
    ${circles.map(([cx, cy, r, filled]) => html`<circle cx=${cx} cy=${cy} r=${r}
      class=${filled ? 'cx-icon__fill' : ''} />`)}
    ${paths.map((d) => html`<path d=${d} />`)}
  </svg>`;
}
