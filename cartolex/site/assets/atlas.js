// SPDX-License-Identifier: MIT
/**
 * The atlas page (`#/map?sel=person:s3&…`): the app's own atlas
 * (`window.CartolexAtlas`, the modules of `cartolex/app/static/atlas/` as one
 * classic script), mounted over the site's data source (`source.js`) and the
 * site as its host (`docs/dev/atlas.md`, « The host's capabilities »):
 *
 * - `t`, `locale`: the site's catalogues, which carry the app's `atlas.*` messages;
 * - `address`: the query of the fragment (`#/map?…`), replaced in place, so a view
 *   can be shared inside the folder and survives a reload;
 * - `prefs`: the browser's storage (the layout, the colour scheme), when it allows it;
 * - `look`: the site's light / dark choice;
 * - `label`: the pseudonyms (« Person 12 », « Placed person 3 »);
 * - `links`: a person's and an organisation's printable page in the site;
 * - `fileStem`: the site's title, for saved views.
 *
 * The atlas has its own Find, Back, Home, panes and full screen; the site gives
 * it the whole page below its header.
 */
(function () {
  'use strict';

  const S = window.CxSite;
  const h = S.h;
  const t = S.t;
  /** The site's theme changes: what the atlas's look listens to. */
  const lookListeners = new Set();
  S.lookChanged = function lookChanged() {
    lookListeners.forEach((fn) => {
      try {
        fn();
      } catch (e) {
        // A listener that fails must not keep the others from hearing.
      }
    });
  };

  /** Whether the site shows its dark look now. */
  S.isDark = function isDark() {
    const chosen = document.documentElement.dataset.theme;
    if (chosen) return chosen === 'dark';
    return Boolean(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
  };

  const PREFS = 'cx-site-atlas.';

  /** The colours of the top-level themes in the scheme the reader chose (a Map), or null. */
  S.themeColours = function themeColours() {
    const A = window.CartolexAtlas;
    if (!A || !A.themeColours) return null;
    let scheme = null;
    try {
      scheme = JSON.parse(S.store(`${PREFS}colour_scheme`) || 'null');
    } catch (e) {
      scheme = null;
    }
    try {
      const tops = S.ix.tops.map((id) => S.ix.nodes.get(id));
      const out = A.themeColours(scheme || undefined, tops, { dark: S.isDark() });
      if (out instanceof Map) return out;
      if (Array.isArray(out)) return new Map(tops.map((n, i) => [n.id, out[i]]));
      return out ? new Map(Object.entries(out)) : null;
    } catch (e) {
      return null;
    }
  };

  function translator() {
    const A = window.CartolexAtlas;
    const messages = (S.data.i18n || {})[S.lang] || {};
    const fallback = (S.data.i18n || {}).en || {};
    if (A && A.createTranslator) return A.createTranslator(messages, fallback, S.lang);
    return S.t;
  }

  /** The site as the atlas's host. */
  S.atlasHost = function atlasHost() {
    const ix = S.ix;
    return {
      t: translator(),
      locale: S.lang,
      address: {
        read() {
          const hash = window.location.hash;
          const cut = hash.indexOf('?');
          return new URLSearchParams(cut >= 0 ? hash.slice(cut + 1) : '');
        },
        write(params) {
          const query = params.toString();
          const next = `#/map${query ? `?${query}` : ''}`;
          if (window.location.hash !== next) window.history.replaceState(null, '', next);
        },
      },
      prefs: {
        get(key) {
          try {
            return JSON.parse(S.store(PREFS + key) || 'null');
          } catch (e) {
            return null;
          }
        },
        set(key, value) {
          S.store(PREFS + key, value === null || value === undefined ? null : JSON.stringify(value));
        },
      },
      look: {
        dark: S.isDark,
        subscribe(fn) {
          lookListeners.add(fn);
          const media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
          if (media) media.addEventListener('change', fn);
          return () => {
            lookListeners.delete(fn);
            if (media) media.removeEventListener('change', fn);
          };
        },
      },
      label(kind, id) {
        if (kind === 'person' && ix.byPerson.has(id)) return S.personName(ix.byPerson.get(id));
        if (kind === 'projected' && ix.byProjected.has(id)) return S.projectedName(ix.byProjected.get(id));
        return null;
      },
      links: {
        person: (id) => (ix.byPerson.has(id) ? `#/person/${id}` : null),
        organisation: (id) => (ix.byOrg.has(id) ? `#/org/${id}` : null),
      },
      navigate(href) {
        window.location.hash = href;
      },
      fileStem: ix.core.title,
    };
  };

  S.pages.map = function mapPage(main) {
    main.classList.add('cx-main--atlas');
    const A = window.CartolexAtlas;
    const title = h('h1', { class: 'cx-visually-hidden', tabindex: '-1', text: t('nav.map') });
    const root = h('div', { class: 'cx-atlas-host' });
    main.append(title, root);
    if (!A || !A.mountAtlas) {
      main.append(S.missingNote());
      return () => main.classList.remove('cx-main--atlas');
    }
    const atlas = A.mountAtlas(root, { source: S.atlasSource(), host: S.atlasHost() });
    return () => {
      atlas.destroy();
      main.classList.remove('cx-main--atlas');
    };
  };
}());
