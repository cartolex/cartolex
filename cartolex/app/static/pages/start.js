// SPDX-License-Identifier: MIT
/**
 * The start screen (`/start`): recent projects, the demo project, a new
 * project (`/start?new=1`). Its modules are in `pages/start/`: `screen.js`
 * (the screen) and `new.js` (the new project form); they share the settings'
 * helpers (`pages/settings/common.js`) and style sheet.
 */

import { html } from '../core/preact.js';
import { definePage } from '../core/page.js';
import { StartScreen } from './start/screen.js';

export const page = definePage(() => html`<${StartScreen} />`, { styles: ['/static/css/settings.css'] });
