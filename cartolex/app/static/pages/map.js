// SPDX-License-Identifier: MIT
/**
 * The atlas (`/map`). The atlas itself is shared with the offline site (`static/atlas/`,
 * `docs/dev/atlas.md`); the page's own modules are in `pages/map/`:
 *
 * - `page.js`: the screen, the atlas mounted, the dialogs;
 * - `source.js`: the atlas's source (the API) and host (messages, address, preferences, links);
 * - `preview.js`: a layout previewed on the map; `versions.js`: map versions and base maps;
 * - `links.js`: the addresses of the other screens; `fullscreen.js`: full screen (the themes screen).
 */

import { html } from '../core/preact.js';
import { definePage } from '../core/page.js';
import { AtlasScreen } from './map/page.js';

export const page = definePage(() => html`<${AtlasScreen} />`, { styles: ['/static/css/atlas.css', '/static/css/map.css', '/static/css/settings.css', '/static/css/keywords.css', '/static/css/tune.css', '/static/css/share.css'] });
