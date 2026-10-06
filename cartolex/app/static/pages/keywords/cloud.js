// SPDX-License-Identifier: MIT
/**
 * The lexicon's word cloud as an image the server draws (and caches): its address for
 * the page's light or dark look and the person's colour scheme (`GET
 * /api/keywords/lexicon/cloud`). Shared by the Lexicon tab and the overview.
 */
import { effectiveTheme } from '../../core/stores/prefs.js';
import { schemeHues } from '../../core/look.js';

/**
 * The cloud's address. It reads the theme and the colour scheme, so a component that
 * calls it while rendering is drawn again when either changes.
 * @param {object} prefs the preferences store
 * @param {{run: string, language: string, by?: string, colour?: string}} options
 */
export function cloudSrc(prefs, { run, language, by = 'score', colour = 'theme' }) {
  void prefs.other.value;
  const theme = effectiveTheme(prefs.theme.value);
  const hues = colour === 'theme' ? schemeHues(prefs) : null;
  return `/api/keywords/lexicon/cloud?by=${by}&colour=${colour}&theme=${theme}`
    + `&language=${encodeURIComponent(language)}&run=${encodeURIComponent(run || '')}`
    + (hues ? `&hues=${hues.map((h) => h.replace('#', '')).join(',')}` : '');
}
