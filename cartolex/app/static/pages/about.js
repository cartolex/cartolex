// SPDX-License-Identifier: MIT
/**
 * The About page (`/about`, where the logo leads). Its modules are in `pages/about/`:
 *
 * - `page.js`: what cartolex is for, its authors and how to cite it, its licence and build;
 * - `figure.js`: the pipeline, one SVG figure drawn with the theme's tokens;
 * - `references.js`: the scientific background, as bibliographic records.
 */
import { html } from '../core/preact.js';
import { definePage } from '../core/page.js';
import { About } from './about/page.js';

export const page = definePage((props) => html`<${About} ...${props} />`,
  { styles: ['/static/css/about.css'] });
