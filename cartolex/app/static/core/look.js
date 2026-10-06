// SPDX-License-Identifier: MIT
/**
 * The colour scheme of the themes across the app: the person's choice (the preference
 * `colour_scheme`, chosen in the atlas) sets the hue tokens `--cx-hue-1` … `--cx-hue-12` on
 * the document for the page's light or dark look, so the Themes screen, the legends and
 * every other screen colour a theme as the atlas does. A one-scale scheme orders the themes
 * as the atlas last placed them (the preference `colour_order`). The default scheme leaves
 * the tokens of `css/tokens.css` alone.
 */
import { effect } from './preact.js';
import { DEFAULT_SCHEME, schemeOf, themeColours } from '../atlas/schemes.js';
import { effectiveTheme } from './stores/prefs.js';

const HUES = 12;

/** Apply the scheme of *prefs* now and at every change (of the scheme, the order, the theme
 * or the system's look); answers the function that stops it. */
export function followScheme(prefs, root = document.documentElement) {
  const media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  const apply = () => {
    const id = prefs.get('colour_scheme') || DEFAULT_SCHEME;
    const dark = effectiveTheme(prefs.theme.value) === 'dark';
    if (schemeOf(id).id === DEFAULT_SCHEME) {
      for (let i = 1; i <= HUES; i += 1) root.style.removeProperty(`--cx-hue-${i}`);
      return;
    }
    const raw = String(prefs.get('colour_order') || '').split(',').map(Number);
    const order = raw.length && raw.every(Number.isFinite) ? raw : null;
    const count = order ? Math.max(order.length, 1) : HUES;
    const colours = themeColours(id, count, { dark, order });
    for (let i = 1; i <= HUES; i += 1) root.style.setProperty(`--cx-hue-${i}`, colours[(i - 1) % colours.length]);
  };
  const stop = effect(() => {
    void prefs.other.value;
    void prefs.theme.value;
    apply();
  });
  if (media) media.addEventListener('change', apply);
  return () => {
    stop();
    if (media) media.removeEventListener('change', apply);
  };
}
