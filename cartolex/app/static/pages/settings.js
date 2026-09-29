// SPDX-License-Identifier: MIT
/**
 * The settings (`/settings`). Its modules are in `pages/settings/`:
 *
 * - `screen.js`: the page, its sections and their addresses;
 * - `common.js`: loading a resource with its version, a card, a state;
 * - `interface.js`, `project.js`, `languages.js`, `ai.js`, `sources.js`,
 *   `build.js`, `words.js` (stop words and prompts), `privacy.js`, `care.js`
 *   (backup, restore, diagnostics, reset): one module per section.
 */

import { html } from '../core/preact.js';
import { definePage } from '../core/page.js';
import { SettingsScreen } from './settings/screen.js';

export const page = definePage(() => html`<${SettingsScreen} />`, { styles: ['/static/css/settings.css'] });
