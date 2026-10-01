// SPDX-License-Identifier: MIT
/**
 * The atlas (`/map`). Its modules are in `pages/map/`:
 *
 * - `page.js`: the screen, its header, legend, hover card and dialogs;
 * - `state.js`: the state kept in the address;
 * - `model.js`: the atlas bundle indexed (themes, people, organisations);
 * - `scene.js`: the map's scene (layers, regions, lines, labels) and the world view;
 * - `controls.js`: kinds, points or regions, level, filters, period, reset;
 * - `tree.js`: the treemap; `panel.js`: the side panel; `find.js`: « Find on the map »;
 * - `versions.js`: map versions and base maps.
 *
 * The map itself is the MapFrame (`components/map-frame.js`), over the
 * library-free modules of `components/map/` that the offline site reuses.
 */

import { html } from '../core/preact.js';
import { definePage } from '../core/page.js';
import { AtlasScreen } from './map/page.js';

export const page = definePage(() => html`<${AtlasScreen} />`, { styles: ['/static/css/map.css', '/static/css/settings.css', '/static/css/keywords.css', '/static/css/tune.css'] });
