// SPDX-License-Identifier: MIT
/**
 * The Map screen's « Distances » pane (`/map?pane=distances&of=person:…`): the shared
 * Distances (`static/distances/`, the offline site mounts the same code), over the app's source
 * (`source.js`: the bundle the page read, and `GET /api/atlas/vectors`, `GET /api/atlas/links`
 * and the project's measure when a view first needs them) and the app as its host; Compare and
 * a theme open the atlas pane (`/map?sel=…&with=…`).
 */
import { html, useEffect, useRef } from '../../core/preact.js';
import { locale } from '../../core/i18n.js';
import { mountDistances } from '../../distances/distances.js';
import { apiSource, appHost } from './source.js';

/** The address of the atlas pane with *sel* in focus (and *other* compared with it). */
export function atlasAddress(sel, other) {
  const q = new URLSearchParams({ sel: `${sel.kind}:${sel.id}` });
  if (other) q.set('with', `${other.kind}:${other.id}`);
  return `/map?${q}`;
}

/** Distances, mounted again when the bundle or the interface's language changes. */
export function DistancesMount({ ctx, bundle, title }) {
  const ref = useRef(null);
  const lang = locale.value;
  useEffect(() => {
    const host = {
      ...appHost(ctx, { title, stem: () => `${bundle.map_version || 'map'}` }),
      atlas: atlasAddress,
    };
    const view = mountDistances(ref.current, { source: apiSource(ctx, { first: bundle, base: () => '' }), host });
    return () => view.destroy();
  }, [bundle, lang]);
  return html`<div ref=${ref} class="cx-distances-host"></div>`;
}
