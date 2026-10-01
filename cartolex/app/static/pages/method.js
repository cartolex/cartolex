// SPDX-License-Identifier: MIT
/**
 * `/method`, the former method screen: an address kept for links and bookmarks.
 * Its parameters now live in the « Tune » panel of the page each step shapes
 * (`pages/tune/`), and every value is listed in the Build page's Recipe. The
 * page sends `/method` to the Recipe and `/method?step=<id>` to that step's
 * page with its panel open, replacing the address in the history.
 */

import { PANELS } from './tune/common.js';

/** The page of each former step. */
const STEP_PAGE = { texts: 'texts', keywords: 'keywords', space: 'themes', grouping: 'themes', layout: 'map' };

/** Where `/method?step=…` goes now. */
export function methodTarget(query) {
  const step = query && query.get('step');
  const panel = Object.hasOwn(STEP_PAGE, step || '') ? PANELS[STEP_PAGE[step]] : null;
  return panel ? panel.href : '/build?tab=recipe';
}

export const page = {
  mount(ctx) {
    ctx.deferReady(); // never ready: the page it sends to is
    ctx.navigate(methodTarget(ctx.query), { replace: true });
  },
};
