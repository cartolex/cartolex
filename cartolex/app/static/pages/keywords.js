// SPDX-License-Identifier: MIT
/**
 * The keywords screen (`/keywords`). Its modules are in `pages/keywords/`:
 *
 * - `page.js`: the screen, its bands, the warning, the dialogs;
 * - `list.js`: one band's list, its filters and bulk actions;
 * - `dialogs.js`: merging keywords, the history of the decisions;
 * - `handoff.js`: AI filtering in a browser (export, answer, review);
 * - `api.js`: AI filtering by API (what is sent, the estimate, consent);
 * - `common.js`: bands, routes, reasons, the decisions' requests.
 *
 * The corpus screen's styles lay out the head, the tabs and the list.
 */

import { html } from '../core/preact.js';
import { definePage } from '../core/page.js';
import { KeywordsScreen } from './keywords/page.js';

export const page = definePage(() => html`<${KeywordsScreen} />`,
  { styles: ['/static/css/corpus.css', '/static/css/keywords.css'] });
