// SPDX-License-Identifier: MIT
/**
 * The method screen (`/method`, in the header's settings menu). Its modules
 * are in `pages/method/`:
 *
 * - `screen.js`: the steps in pipeline order, one step at a time
 *   (`/method?step=<id>`): its parameters, what it produced, « rebuild from here »;
 * - `params.js`: editing parameters (shared with the settings' build options);
 * - `texts.js`, `keywords.js`, `space.js`, `grouping.js`, `layout.js`: each
 *   step's diagnostic;
 * - `charts.js`: the figures (bars, lines, a dendrogram) drawn in SVG from data;
 * - `common.js`: the steps, their states, facts, names.
 */

import { html } from '../core/preact.js';
import { definePage } from '../core/page.js';
import { MethodScreen } from './method/screen.js';

export const page = definePage(() => html`<${MethodScreen} />`,
  { styles: ['/static/css/settings.css', '/static/css/corpus.css', '/static/css/keywords.css', '/static/css/method.css'] });
