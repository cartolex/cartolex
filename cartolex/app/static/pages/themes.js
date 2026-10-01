/**
 * The theme editor (`/themes`). Its modules are in `pages/themes/`:
 *
 * - `model.js`: the tree's index, search, patches, optimistic operations;
 * - `labels.js`: the words of the undo list, languages, shares;
 * - `store.js`: the editor's state and its autosaved draft;
 * - `rows.js`, `outline.js`, `review.js`: the outline and the review queue;
 * - `treemap.js`, `map.js`, `centre.js`: the treemap and map panels;
 * - `panel.js`: the side panel;
 * - `dialogs.js`, `operations.js`: the dialogs, and those of the operations;
 * - `actions.js`: selection, operations and menus, shared by every panel;
 * - `versions.js`: the versions;
 * - `copilot.js`, `proposal.js`: curating with an AI copilot, and the review of its changes;
 * - `editor.js`: the page, which puts them together.
 *
 * Static modules are cached after the first load; the budget of a navigation
 * counts API calls.
 */

import { html } from '../core/preact.js';
import { definePage } from '../core/page.js';
import { ThemesEditor } from './themes/editor.js';

export const page = definePage(() => html`<${ThemesEditor} />`, { styles: ['/static/css/themes.css', '/static/css/settings.css', '/static/css/keywords.css', '/static/css/tune.css'] });
