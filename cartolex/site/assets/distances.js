// SPDX-License-Identifier: MIT
/**
 * The « Distances » page (`#/distances?d=rank&of=person:s3&…`): the app's Distances
 * (`window.CartolexAtlas.mountDistances`, `cartolex/app/static/distances/`), mounted over the
 * site's data source (`source.js`: the vectors' parts, the links, the bundle) and the site as
 * its host, as the atlas page is (`atlas.js`): its catalogues, look, storage (the colour
 * scheme the reader chose in the atlas), pseudonyms and pages; the state in the fragment's
 * query; Compare and a theme open the atlas. Everything is computed in the browser.
 */
(function () {
  'use strict';

  const S = window.CxSite;
  const h = S.h;
  const t = S.t;

  /** The atlas's address with *sel* in focus (and *other* compared with it). */
  function atlasAddress(sel, other) {
    const q = new URLSearchParams({ sel: `${sel.kind}:${sel.id}` });
    if (other) q.set('with', `${other.kind}:${other.id}`);
    return `#/map?${q}`;
  }

  /** The site as Distances' host: the atlas's host, with its own address. */
  S.distancesHost = function distancesHost() {
    const host = S.atlasHost();
    return Object.assign(host, {
      address: {
        read() {
          const hash = window.location.hash;
          const cut = hash.indexOf('?');
          return new URLSearchParams(cut >= 0 ? hash.slice(cut + 1) : '');
        },
        write(params) {
          const query = params.toString();
          const next = `#/distances${query ? `?${query}` : ''}`;
          if (window.location.hash !== next) window.history.replaceState(null, '', next);
        },
      },
      atlas: atlasAddress,
      navigate(href) {
        window.location.hash = href;
      },
    });
  };

  S.pages.distances = function distancesPage(main) {
    const A = window.CartolexAtlas;
    main.append(h('h1', { class: 'cx-page__title', tabindex: '-1', text: t('nav.distances') }));
    if (!A || !A.mountDistances) {
      main.append(S.missingNote());
      return null;
    }
    const root = h('div', { class: 'cx-distances-host' });
    main.append(root);
    const view = A.mountDistances(root, { source: S.atlasSource(), host: S.distancesHost() });
    return () => view.destroy();
  };
}());
