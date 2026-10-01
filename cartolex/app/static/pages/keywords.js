// SPDX-License-Identifier: MIT
/**
 * The keywords screen (`/keywords`). Its modules are in `pages/keywords/`:
 *
 * - `page.js`: the screen, its bands, the warning, the dialogs;
 * - `list.js`: one band's list, its filters and bulk actions;
 * - `dialogs.js`: merging keywords, the history of the decisions;
 * - `copilot.js`: triage with an AI copilot (the bundle, its results, the review);
 * - `review.js`: the review of a proposal, term by term;
 * - `api.js`: triage by API (what is sent, the estimate, consent);
 * - `common.js`: bands, routes, reasons, the decisions' requests.
 *
 * The corpus screen's styles lay out the head, the tabs and the list.
 */

import { html } from '../core/preact.js';
import { definePage } from '../core/page.js';
import { KeywordsScreen } from './keywords/page.js';

export const page = definePage(() => html`<${KeywordsScreen} />`,
  { styles: ['/static/css/corpus.css', '/static/css/keywords.css', '/static/css/settings.css', '/static/css/tune.css'] });
