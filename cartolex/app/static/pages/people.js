// SPDX-License-Identifier: MIT
/**
 * The People screen (`/people`). Its modules are in `pages/people/`:
 *
 * - `page.js`: the screen, its tabs and dialogs;
 * - `people-tab.js`: people, their roles, identities and coverage, filters, bulk roles;
 * - `identities.js`: the identity queue, keyboard first;
 * - `orgs-tab.js`: organisations, and the people of institutions;
 * - `texts-tab.js`: texts, their parts per provider, merges and versions;
 * - `collaborators-tab.js`: collaborators round by round, decisions;
 * - `coverage-tab.js`: the coverage report and its actions;
 * - `sheet.js`: a person's sheet; `import.js`, `collect.js`: the dialogs;
 * - `duplicates-tab.js`, `duplicates-compare.js`, `duplicates-auto.js`: groups of people
 *   who may be one, side by side, and the automatic merges; `same-person.js`: people
 *   said to be one by hand (« Same person… »);
 * - `org-drawer.js`, `org-review.js`, `institutions.js`: organisations as decided;
 * - `common.js`: state shapes, names, a list paged on the server.
 */

import { html } from '../core/preact.js';
import { definePage } from '../core/page.js';
import { CorpusScreen } from './people/page.js';

export const page = definePage(() => html`<${CorpusScreen} />`, { styles: ['/static/css/corpus.css', '/static/css/identity.css', '/static/css/settings.css', '/static/css/keywords.css', '/static/css/tune.css'] });
