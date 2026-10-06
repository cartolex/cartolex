// SPDX-License-Identifier: MIT
/**
 * The addresses that open what the map shows on another screen, and back: a person, an
 * organisation or a text in People, a keyword in Keywords or in Themes, a theme in Themes,
 * and anything on the map (`/map?sel=kind:id`, the map centring on it).
 */
import { html } from '../../core/preact.js';

const at = (path, query) => `${path}?${new URLSearchParams(query).toString()}`;

/** The address of each screen that can show *kind* *id*. */
export const linkTo = {
  person: (id) => at('/people', { person: id }),
  organisation: (id) => at('/people', { tab: 'organisations', org: id }),
  text: (id) => at('/people', { tab: 'texts', text: id }),
  keyword: (term) => at('/keywords', { q: term }),
  themesKeyword: (term) => at('/themes', { keyword: term }),
  themesNode: (id) => at('/themes', { node: id }),
  map: (kind, id) => at('/map', { sel: `${kind}:${id}` }),
};

/** A row of links to other screens: `[{href, label}]`. */
export function Links({ links, label }) {
  const shown = links.filter(Boolean);
  if (!shown.length) return null;
  return html`<ul class="cx-atlas-links" aria-label=${label}>
    ${shown.map((l) => html`<li key=${l.href}>
      <a class="cx-button cx-button--secondary cx-button--s" href=${l.href}>${l.label}</a></li>`)}
  </ul>`;
}
