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
import {
  dateIn, formatParsed, listIn, numberIn, parseMessage, percentIn,
} from './messages.js';

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

/** Parse an ICU message into a list of nodes (strings and argument objects). */
export { parseMessage };

function formatAst(nodes, params, pound) {
  return formatParsed(nodes, params, locale.value, pound);
}

/** A number in the interface language (grouping, decimal mark). */
export function formatNumber(value, options = {}) {
  return numberIn(locale.value, value, options);
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
  return percentIn(locale.value, fraction, options);
}

/** A date, a time or both, from a Date, a timestamp or an ISO string. */
export function formatDate(value, kind = 'date', style = 'medium') {
  return dateIn(locale.value, value, kind, style);
}

/** A list of strings joined as the language joins them (« a, b et c »). */
export function formatList(items, type = 'conjunction') {
  return listIn(locale.value, items, type);
}

/** A duration in seconds, rounded to the largest sensible unit (« 2 min », « 1 h 5 min »). */
export function formatDuration(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  const unit = (value, u) => numberIn(locale.value, value, { style: 'unit', unit: u, unitDisplay: 'narrow' });
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
