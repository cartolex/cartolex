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

/** Base64 little-endian float32 values as a Float32Array. */
function float32Of(b64) {
  const bin = window.atob(b64 || '');
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
  return new Float32Array(bytes.buffer);
}

/** The answer of an API call as a source gives it: the data, or `{error}`. */
const unwrap = (r) => (r.ok ? r.data : { error: r.error || { code: 'network' } });

/**
 * The API-backed source. *first* is the bundle the page already read (served once, then
 * read again on a refresh); *base()* the base map in the address ('' for none). The reads
 * of places take the map `version` the atlas shows (none: the pinned one).
 */
export function apiSource(ctx, { first, base }) {
  let kept = first;
  const withBase = (query = {}) => (base() ? { ...query, base: base() } : query);
  const placed = ({ version, ...query } = {}) => withBase(version ? { ...query, version } : query);
  return {
    layouts: true, // bundle({version}) reads another built map version
    bundle({ version } = {}) {
      if (kept && (!version || version === kept.map_version)) {
        const b = kept;
        kept = null;
        return Promise.resolve(b);
      }
      kept = null;
      return ctx.api.get('/api/atlas', { query: placed({ version }) }).then((r) => {
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
    texts: ({ version } = {}) => ctx.api.get('/api/atlas/texts', { query: placed({ version }) }).then(unwrap),
    textsOf: ({ kind, id, net = 0, limit, version }) => ctx.api.get('/api/atlas/texts',
      { query: placed({ focus: `${kind}:${id}`, net, ...(limit ? { limit } : {}), version }) }).then(unwrap),
    windows: ({ person, version } = {}) => ctx.api.get('/api/atlas/windows',
      { query: placed({ ...(person ? { person } : {}), version }) }).then(unwrap),
    vectors: (kind) => ctx.api.get('/api/atlas/vectors', { query: { kind } })
      .then((r) => (r.ok ? { dim: r.data.dim, values: int8Of(r.data.values) } : { error: r.error })),
    links: (kind) => ctx.api.get('/api/atlas/links', { query: { kind } }).then(unwrap),
    similarity: (body) => ctx.api.post('/api/atlas/similarity', body)
      .then((r) => (r.ok ? { ...r.data, values: float32Of(r.data.values) } : { error: r.error })),
    similarPairs: (body) => ctx.api.post('/api/atlas/similar-pairs', body).then(unwrap),
    measure: () => ctx.api.get('/api/params').then((r) => (r.ok && r.data.global && r.data.global.similarity
      ? r.data.global.similarity.value : null)),
    land: () => ctx.keep(fetch(LAND).then((r) => (r.ok ? r.json() : null)).catch(() => null))
      .then((doc) => (doc && Array.isArray(doc.rings) ? doc.rings : { error: { code: 'land' } })),
  };
}

/** The app as the atlas's host. *title* names the field (the project); *onReady* is told
 * each bundle the atlas shows (a change of « Layout » reads another). */
export function appHost(ctx, { title, stem, onReady = null }) {
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
    ...(onReady ? { onReady } : {}),
  };
}
