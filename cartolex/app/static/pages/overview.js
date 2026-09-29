// SPDX-License-Identifier: MIT
/**
 * The overview (`/overview`). Its modules are in `pages/overview/`:
 *
 * - `cards.js`: the one next step, the health panel, the recent shared builds;
 * - `preview.js`: a small preview of the map;
 * - `page.js`: the page, which puts them together with the stage tracker.
 */
import { html } from '../core/preact.js';
import { definePage } from '../core/page.js';
import { Overview } from './overview/page.js';

export const page = definePage((props) => html`<${Overview} ...${props} />`,
  { styles: ['/static/css/overview.css'] });
