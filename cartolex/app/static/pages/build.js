// SPDX-License-Identifier: MIT
/**
 * The build (`/build`). Its modules are in `pages/build/`:
 *
 * - `preflight.js`: the pre-flight sheet (what runs and why, the cost, consent, refusal);
 * - `run.js`: the tracker while the job runs, with Stop, and the result when it ends;
 * - `words.js`: sizes, names, the one-sentence result, the error card of a failure;
 * - `page.js`: the page, which puts them together.
 */
import { html } from '../core/preact.js';
import { definePage } from '../core/page.js';
import { BuildPage } from './build/page.js';

export const page = definePage(() => html`<${BuildPage} />`, { styles: ['/static/css/build.css'] });
