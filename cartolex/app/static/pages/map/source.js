// SPDX-License-Identifier: MIT
/**
 * What the app gives the atlas and Distances (`docs/dev/atlas.md`): its data source, read from
 * the API (`GET /api/atlas` and the routes beside it), and its host: the interface's messages and
 * language, the address of the page, the person's preferences kept by the app, the app's
 * Dark / Bright, and the links to the People, Keywords and Themes screens.
 */
import { locale, t } from '../../core/i18n.js';
import { linkTo } from './links.js';

const LAND = '/static/data/world-land-110m.json';

/** Base64 bytes as an Int8Array. */
function int8Of(b64) {
  const bin = window.atob(b64 || '');
  const out = new Int8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) out[i] = (bin.charCodeAt(i) << 24) >> 24;
  return out;
}

/** The answer of an API call as a source gives it: the data, or `{error}`. */
const unwrap = (r) => (r.ok ? r.data : { error: r.error || { code: 'network' } });

/**
 * The API-backed source. *first* is the bundle the page already read (served once, then
 * read again on a refresh); *base()* the base map in the address ('' for none).
 */
export function apiSource(ctx, { first, base }) {
  let kept = first;
  const withBase = (query = {}) => (base() ? { ...query, base: base() } : query);
  return {
    bundle() {
      if (kept) {
        const b = kept;
        kept = null;
        return Promise.resolve(b);
      }
      return ctx.api.get('/api/atlas', { query: withBase() }).then((r) => {
        if (!r.ok) return { error: r.error };
        return r.data.available ? r.data : { error: { code: 'atlas_unavailable' } };
      });
    },
    keywordUsers: (term, { limit }) => ctx.api.get('/api/atlas/keyword-people', { query: { term, limit } }).then(unwrap),
    coauthors({ kind, id, circle, pages = [] }) {
      const query = { kind, id, circle };
      ['', '2', '3'].forEach((suffix, k) => {
        if (!pages[k]) return;
        query[`offset${suffix}`] = pages[k][0];
        query[`limit${suffix}`] = Math.min(500, pages[k][1]);
      });
      return ctx.api.get('/api/atlas/coauthors', { query }).then(unwrap);
    },
    compare: (a, b) => ctx.api.get('/api/atlas/compare', { query: { a: `${a.kind}:${a.id}`, b: `${b.kind}:${b.id}` } }).then(unwrap),
    keywordsOf: (kind, ids) => ctx.api.get('/api/atlas/regions', { query: { kind, ids: ids.join(',') } })
      .then((r) => (r.ok ? r.data.keywords || {} : { error: r.error })),
    texts: () => ctx.api.get('/api/atlas/texts', { query: withBase() }).then(unwrap),
    textsOf: ({ kind, id, net = 0, limit }) => ctx.api.get('/api/atlas/texts',
      { query: withBase({ focus: `${kind}:${id}`, net, ...(limit ? { limit } : {}) }) }).then(unwrap),
    windows: ({ person } = {}) => ctx.api.get('/api/atlas/windows', { query: withBase(person ? { person } : {}) }).then(unwrap),
    vectors: (kind) => ctx.api.get('/api/atlas/vectors', { query: { kind } })
      .then((r) => (r.ok ? { dim: r.data.dim, values: int8Of(r.data.values) } : { error: r.error })),
    links: (kind) => ctx.api.get('/api/atlas/links', { query: { kind } }).then(unwrap),
    measure: () => ctx.api.get('/api/params').then((r) => (r.ok && r.data.global && r.data.global.similarity
      ? r.data.global.similarity.value : null)),
    land: () => ctx.keep(fetch(LAND).then((r) => (r.ok ? r.json() : null)).catch(() => null))
      .then((doc) => (doc && Array.isArray(doc.rings) ? doc.rings : { error: { code: 'land' } })),
  };
}

/** The app as the atlas's host. *title* names the field (the project). */
export function appHost(ctx, { title, stem }) {
  const { app } = ctx;
  const prefs = app.stores.prefs;
  return {
    t,
    locale: locale.value,
    title,
    address: {
      read: () => new URLSearchParams(window.location.search),
      write(query) {
        const url = new URL(window.location.href);
        const q = query.toString();
        const next = url.pathname + (q ? `?${q}` : '') + url.hash;
        if (next !== url.pathname + url.search + url.hash) window.history.replaceState(window.history.state, '', next);
      },
    },
    prefs: { get: (key) => prefs.get(key), set: (key, value) => prefs.set(key, value) },
    look: {
      dark: () => {
        const theme = prefs.theme.value;
        if (theme === 'dark' || theme === 'light') return theme === 'dark';
        return Boolean(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
      },
      set: (dark) => app.setTheme(dark ? 'dark' : 'light'),
    },
    links: {
      person: linkTo.person,
      organisation: linkTo.organisation,
      text: linkTo.text,
      keyword: linkTo.keyword,
      themesKeyword: linkTo.themesKeyword,
      themesNode: linkTo.themesNode,
    },
    navigate: (href) => ctx.navigate(href),
    fileStem: stem,
  };
}
