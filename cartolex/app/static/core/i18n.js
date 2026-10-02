// SPDX-License-Identifier: MIT
/**
 * Interface language: catalogues, messages and locale-aware formatting.
 *
 * The interface language is a user preference, chosen apart from a project's
 * keyword languages. A locale's messages are the merge, in order, of the
 * catalogues the manifest lists for it (the base first, then each extension's
 * overrides) and of catalogues an extension registers at run time. Messages
 * use a subset of ICU MessageFormat:
 *
 *   {name}                         the argument as text
 *   {n, number} {n, number, percent} {n, number, integer}
 *   {d, date} {d, date, short|medium|long|full}   also `time` and `datetime`
 *   {items, list} {items, list, or}               Intl.ListFormat
 *   {n, plural, =0 {…} one {# item} other {# items}}   `#` is n, formatted
 *   {n, selectordinal, one {#st} other {#th}}
 *   {kind, select, build {…} other {…}}
 *
 * A quote escapes ICU syntax as in ICU: '{' is a brace, '' is a quote; any
 * other apostrophe (French « l'arbre ») is plain text.
 */
import { computed, signal } from './preact.js';

/** The interface language in use (a BCP 47 tag from the manifest's `available`). */
export const locale = signal('en');
const messages = signal(Object.create(null));
const fallbackMessages = signal(Object.create(null));
/** Catalogues registered at run time (by extensions), by locale, merged over the files. */
const extra = new Map();
const missing = new Set();
const parsed = new Map();

/** Changes whenever the language or the messages change: read it to re-render on a switch. */
export const catalogueVersion = computed(() => [locale.value, messages.value]);

/** Keys asked for that no catalogue holds (the browser tests expect none). */
export function missingKeys() {
  return [...missing];
}

/**
 * Pick the interface language: the user's choice, else the browser's first
 * language the app offers (exact tag, then the same base language), else the
 * app's default.
 */
export function pickLocale(available, { preferred, browser = [], fallback = 'en' } = {}) {
  const list = [preferred, ...browser].filter(Boolean);
  for (const tag of list) {
    const exact = available.find((a) => a.toLowerCase() === String(tag).toLowerCase());
    if (exact) return exact;
  }
  for (const tag of list) {
    const base = String(tag).toLowerCase().split('-')[0];
    const near = available.find((a) => a.toLowerCase().split('-')[0] === base);
    if (near) return near;
  }
  return available.includes(fallback) ? fallback : available[0];
}

/** Merge catalogue objects in order; keys starting with `$` are metadata and skipped. */
export function mergeCatalogues(list) {
  const out = Object.create(null);
  for (const cat of list) {
    for (const [key, value] of Object.entries(cat || {})) {
      if (!key.startsWith('$') && typeof value === 'string') out[key] = value;
    }
  }
  return out;
}

/** Fetch one catalogue file; a file that is not a catalogue is refused. */
async function fetchCatalogue(url, signalOpt) {
  const response = await fetch(url, {
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
    signal: signalOpt,
  });
  if (!response.ok) throw new Error(`catalogue ${url}: HTTP ${response.status}`);
  const data = await response.json();
  if (data.$format !== 'cartolex-i18n/1') throw new Error(`catalogue ${url}: not cartolex-i18n/1`);
  return data;
}

/**
 * Load a locale's catalogues and make it the interface language.
 *
 * `catalogues` maps each locale to its list of catalogue URLs (the
 * manifest's `locales.catalogues`); `fallback` is the default locale, whose
 * messages stand in for a key an extension's catalogue lacks.
 */
export async function loadLocale(code, catalogues, { fallback = 'en', signal: abort } = {}) {
  const urls = catalogues[code] || [];
  const loaded = await Promise.all(urls.map((u) => fetchCatalogue(u, abort)));
  let fallbackLoaded = [];
  if (fallback && fallback !== code && catalogues[fallback]) {
    fallbackLoaded = await Promise.all(catalogues[fallback].map((u) => fetchCatalogue(u, abort)));
  }
  applyLocale(code, loaded, fallbackLoaded, fallback);
}

/** Install already-loaded catalogues (used by `loadLocale` and by tests). */
export function applyLocale(code, loaded, fallbackLoaded = [], fallback = 'en') {
  parsed.clear();
  fallbackMessages.value = mergeCatalogues([...fallbackLoaded, ...(extra.get(fallback) || [])]);
  messages.value = mergeCatalogues([...loaded, ...(extra.get(code) || [])]);
  locale.value = code;
  if (typeof document !== 'undefined') document.documentElement.lang = code;
}

/**
 * Register messages for a locale at run time (an extension's `register(api)`);
 * they are merged over the files' messages now and at every later switch.
 */
export function addMessages(code, dict) {
  if (!extra.has(code)) extra.set(code, []);
  extra.get(code).push(dict);
  if (code === locale.value) {
    parsed.clear();
    messages.value = mergeCatalogues([messages.value, dict]);
  }
}

/** Whether a message exists for *key* in the current language. */
export function has(key) {
  return key in messages.value || key in fallbackMessages.value;
}

/**
 * The message *key* in the interface language, with *params* filled in.
 * A missing key returns the key itself (and is recorded, see `missingKeys`).
 */
export function t(key, params) {
  const source = messages.value[key] ?? fallbackMessages.value[key];
  if (source === undefined) {
    missing.add(key);
    return key;
  }
  let ast = parsed.get(key);
  if (!ast) {
    try {
      ast = parseMessage(source);
    } catch (err) {
      missing.add(`${key} (${err.message})`);
      return source;
    }
    parsed.set(key, ast);
  }
  return formatAst(ast, params || {}, null);
}

// ── ICU subset: parser ─────────────────────────────────────────────────────

/** Parse an ICU message into a list of nodes (strings and argument objects). */
export function parseMessage(text) {
  const state = { text, i: 0 };
  const nodes = parseNodes(state, false);
  if (state.i < text.length) throw new Error(`unexpected "}" at ${state.i}`);
  return nodes;
}

function parseNodes(state, inPlural) {
  const nodes = [];
  let buf = '';
  const { text } = state;
  while (state.i < text.length) {
    const ch = text[state.i];
    if (ch === "'") {
      const next = text[state.i + 1];
      if (next === "'") {
        buf += "'";
        state.i += 2;
      } else if (next === '{' || next === '}' || (inPlural && next === '#')) {
        const end = text.indexOf("'", state.i + 1);
        if (end < 0) throw new Error('unterminated quote');
        buf += text.slice(state.i + 1, end);
        state.i = end + 1;
      } else {
        buf += ch;
        state.i += 1;
      }
    } else if (ch === '{') {
      if (buf) nodes.push(buf);
      buf = '';
      nodes.push(parseArgument(state));
    } else if (ch === '}') {
      break;
    } else if (ch === '#' && inPlural) {
      if (buf) nodes.push(buf);
      buf = '';
      nodes.push({ type: 'pound' });
      state.i += 1;
    } else {
      buf += ch;
      state.i += 1;
    }
  }
  if (buf) nodes.push(buf);
  return nodes;
}

function skipSpace(state) {
  while (state.i < state.text.length && /\s/.test(state.text[state.i])) state.i += 1;
}

function readWord(state) {
  skipSpace(state);
  const m = /^[^\s{},]+/.exec(state.text.slice(state.i));
  if (!m) throw new Error(`expected a word at ${state.i}`);
  state.i += m[0].length;
  return m[0];
}

function expect(state, ch) {
  skipSpace(state);
  if (state.text[state.i] !== ch) throw new Error(`expected "${ch}" at ${state.i}`);
  state.i += 1;
}

function parseArgument(state) {
  expect(state, '{');
  const name = readWord(state);
  skipSpace(state);
  if (state.text[state.i] === '}') {
    state.i += 1;
    return { type: 'arg', name };
  }
  expect(state, ',');
  const kind = readWord(state);
  skipSpace(state);
  if (kind === 'plural' || kind === 'selectordinal' || kind === 'select') {
    expect(state, ',');
    let offset = 0;
    const options = {};
    skipSpace(state);
    if (state.text.startsWith('offset:', state.i)) {
      state.i += 7;
      offset = Number(readWord(state));
    }
    for (;;) {
      skipSpace(state);
      if (state.text[state.i] === '}') {
        state.i += 1;
        break;
      }
      const key = readWord(state);
      expect(state, '{');
      options[key] = parseNodes(state, kind !== 'select');
      expect(state, '}');
    }
    if (!('other' in options)) throw new Error(`{${name}, ${kind}} has no "other" case`);
    return { type: kind, name, offset, options };
  }
  let style = '';
  if (state.text[state.i] === ',') {
    state.i += 1;
    style = readWord(state);
  }
  expect(state, '}');
  if (!['number', 'date', 'time', 'datetime', 'list'].includes(kind)) {
    throw new Error(`unknown argument type "${kind}"`);
  }
  return { type: kind, name, style };
}

// ── ICU subset: formatter ─────────────────────────────────────────────────

const formatters = new Map();

function cached(kind, options, make) {
  const key = `${locale.value}|${kind}|${JSON.stringify(options)}`;
  let f = formatters.get(key);
  if (!f) {
    f = make(locale.value, options);
    formatters.set(key, f);
  }
  return f;
}

const DATE_STYLES = { short: 'short', medium: 'medium', long: 'long', full: 'full' };

function formatAst(nodes, params, pound) {
  let out = '';
  for (const node of nodes) {
    if (typeof node === 'string') {
      out += node;
      continue;
    }
    const value = node.name === undefined ? undefined : params[node.name];
    switch (node.type) {
      case 'pound':
        out += pound === null ? '#' : formatNumber(pound);
        break;
      case 'arg':
        out += value === undefined || value === null ? '' : String(value);
        break;
      case 'number':
        out +=
          node.style === 'percent'
            ? formatPercent(value)
            : formatNumber(value, node.style === 'integer' ? { maximumFractionDigits: 0 } : {});
        break;
      case 'date':
      case 'time':
      case 'datetime':
        out += formatDate(value, node.type, DATE_STYLES[node.style] || 'medium');
        break;
      case 'list':
        out += formatList(value || [], node.style === 'or' ? 'disjunction' : 'conjunction');
        break;
      case 'select': {
        const branch = node.options[String(value)] || node.options.other;
        out += formatAst(branch, params, pound);
        break;
      }
      case 'plural':
      case 'selectordinal': {
        const n = Number(value) || 0;
        const exact = node.options[`=${n}`];
        let branch = exact;
        if (!branch) {
          const rules = cached('plural', { type: node.type === 'plural' ? 'cardinal' : 'ordinal' },
            (loc, o) => new Intl.PluralRules(loc, o));
          branch = node.options[rules.select(n - node.offset)] || node.options.other;
        }
        out += formatAst(branch, params, n - node.offset);
        break;
      }
      default:
        break;
    }
  }
  return out;
}

/** A number in the interface language (grouping, decimal mark). */
export function formatNumber(value, options = {}) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '';
  return cached('number', options, (loc, o) => new Intl.NumberFormat(loc, o)).format(n);
}

const BYTE_UNITS = ['byte', 'kilobyte', 'megabyte', 'gigabyte', 'terabyte'];

/**
 * A size in bytes in the interface language, in the largest unit under a thousand
 * (« 741 GB », « 741 Go »); with *perSecond*, a speed (« 69 MB/s »).
 */
export function formatBytes(bytes, { perSecond = false } = {}) {
  let value = Number(bytes);
  if (!Number.isFinite(value)) return '';
  let i = 0;
  while (value >= 1000 && i < BYTE_UNITS.length - 1) {
    value /= 1000;
    i += 1;
  }
  const unit = perSecond ? `${BYTE_UNITS[i]}-per-second` : BYTE_UNITS[i];
  return formatNumber(value, { style: 'unit', unit, unitDisplay: 'short',
    maximumFractionDigits: value < 10 ? 1 : 0 });
}

/** A fraction (0…1) as a percentage in the interface language (« 45 % », « 45% »). */
export function formatPercent(fraction, options = { maximumFractionDigits: 0 }) {
  const n = Number(fraction);
  if (!Number.isFinite(n)) return '';
  return cached('percent', options, (loc, o) =>
    new Intl.NumberFormat(loc, { style: 'percent', ...o })).format(n);
}

/** A date, a time or both, from a Date, a timestamp or an ISO string. */
export function formatDate(value, kind = 'date', style = 'medium') {
  if (value === undefined || value === null || value === '') return '';
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return '';
  const options = kind === 'time' ? { timeStyle: style === 'full' ? 'long' : style }
    : kind === 'datetime' ? { dateStyle: style, timeStyle: 'short' } : { dateStyle: style };
  return cached('date', options, (loc, o) => new Intl.DateTimeFormat(loc, o)).format(d);
}

/** A list of strings joined as the language joins them (« a, b et c »). */
export function formatList(items, type = 'conjunction') {
  return cached('list', { type }, (loc, o) => new Intl.ListFormat(loc, o)).format(
    items.map(String));
}

/** A duration in seconds, rounded to the largest sensible unit (« 2 min », « 1 h 5 min »). */
export function formatDuration(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  const unit = (value, u) =>
    cached('unit', { u }, (loc) => new Intl.NumberFormat(loc, {
      style: 'unit', unit: u, unitDisplay: 'narrow' })).format(value);
  if (s < 60) return unit(s, 'second');
  if (s < 3600) return unit(Math.round(s / 60), 'minute');
  const h = Math.floor(s / 3600);
  const m = Math.round((s % 3600) / 60);
  return m ? `${unit(h, 'hour')} ${unit(m, 'minute')}` : unit(h, 'hour');
}

/** A language's own name, written in that language (« Français », « Português (Brasil) »). */
export function autonym(code) {
  try {
    const name = new Intl.DisplayNames([code], { type: 'language' }).of(code);
    return name ? name.charAt(0).toLocaleUpperCase(code) + name.slice(1) : code;
  } catch {
    return code;
  }
}
