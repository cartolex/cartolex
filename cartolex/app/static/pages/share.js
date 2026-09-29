// SPDX-License-Identifier: MIT
/**
 * The share screen (`/share`). Its modules are in `pages/share/`:
 *
 * - `page.js`: the screen: the reads of its navigation, the job it follows;
 * - `site.js`: building the offline site (the name question, the texts, the
 *   title and language, the privacy summary and the checks before publishing);
 * - `builds.js`: the builds (latest, stale, open, download as one zip);
 * - `exports.js`: figures and tables, the map bundle, the project as one file.
 */

import { html } from '../core/preact.js';
import { definePage } from '../core/page.js';
import { ShareScreen } from './share/page.js';

export const page = definePage(() => html`<${ShareScreen} />`, { styles: ['/static/css/share.css'] });
