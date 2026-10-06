// SPDX-License-Identifier: MIT
/**
 * Messages in the ICU subset of the interface, without any library: the parser, the
 * formatter and the locale-aware formats (numbers, percentages, dates, lists). The app's
 * `core/i18n.js` uses them with the interface language; the offline site uses them through
 * `createTranslator` (this module is part of its classic script, see `docs/dev/atlas.md`),
 * so both hosts read the same catalogues the same way.
 *
 *   {name}                         the argument as text
 *   {n, number} {n, number, percent} {n, number, integer}
 *   {d, date} {d, date, short|medium|long|full}   also `time` and `datetime`
 *   {items, list} {items, list, or}               Intl.ListFormat
 *   {n, plural, =0 {…} one {# item} other {# items}}   `#` is n, formatted
 *   {n, selectordinal, one {#st} other {#th}}
 *   {kind, select, build {…} other {…}}
 *
 * A quote escapes ICU syntax as in ICU: '{' is a brace, '' is a quote; any other
 * apostrophe (French « l'arbre ») is plain text.
 */

// ── parser ──────────────────────────────────────────────────────────────────

/** Parse an ICU message into a list of nodes (strings and argument objects). */
export function parseMessage(text) {
  const state = { text, i: 0 };
  const nodes = msgNodes(state, false);
  if (state.i < text.length) throw new Error(`unexpected "}" at ${state.i}`);
  return nodes;
}

function msgNodes(state, inPlural) {
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
      nodes.push(msgArgument(state));
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

function msgSpace(state) {
  while (state.i < state.text.length && /\s/.test(state.text[state.i])) state.i += 1;
}

function msgWord(state) {
  msgSpace(state);
  const m = /^[^\s{},]+/.exec(state.text.slice(state.i));
  if (!m) throw new Error(`expected a word at ${state.i}`);
  state.i += m[0].length;
  return m[0];
}

function msgExpect(state, ch) {
  msgSpace(state);
  if (state.text[state.i] !== ch) throw new Error(`expected "${ch}" at ${state.i}`);
  state.i += 1;
}

function msgArgument(state) {
  msgExpect(state, '{');
  const name = msgWord(state);
  msgSpace(state);
  if (state.text[state.i] === '}') {
    state.i += 1;
    return { type: 'arg', name };
  }
  msgExpect(state, ',');
  const kind = msgWord(state);
  msgSpace(state);
  if (kind === 'plural' || kind === 'selectordinal' || kind === 'select') {
    msgExpect(state, ',');
    let offset = 0;
    const options = {};
    msgSpace(state);
    if (state.text.startsWith('offset:', state.i)) {
      state.i += 7;
      offset = Number(msgWord(state));
    }
    for (;;) {
      msgSpace(state);
      if (state.text[state.i] === '}') {
        state.i += 1;
        break;
      }
      const key = msgWord(state);
      msgExpect(state, '{');
      options[key] = msgNodes(state, kind !== 'select');
      msgExpect(state, '}');
    }
    if (!('other' in options)) throw new Error(`{${name}, ${kind}} has no "other" case`);
    return { type: kind, name, offset, options };
  }
  let style = '';
  if (state.text[state.i] === ',') {
    state.i += 1;
    style = msgWord(state);
  }
  msgExpect(state, '}');
  if (!['number', 'date', 'time', 'datetime', 'list'].includes(kind)) {
    throw new Error(`unknown argument type "${kind}"`);
  }
  return { type: kind, name, style };
}

// ── formats ─────────────────────────────────────────────────────────────────

const msgFormatters = new Map();

function msgCached(loc, kind, options, make) {
  const key = `${loc}|${kind}|${JSON.stringify(options)}`;
  let f = msgFormatters.get(key);
  if (!f) {
    f = make(loc, options);
    msgFormatters.set(key, f);
  }
  return f;
}

/** A number in the language *loc* (grouping, decimal mark). */
export function numberIn(loc, value, options = {}) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '';
  return msgCached(loc, 'number', options, (l, o) => new Intl.NumberFormat(l, o)).format(n);
}

/** A fraction (0…1) as a percentage in the language *loc* (« 45 % », « 45% »). */
export function percentIn(loc, fraction, options = { maximumFractionDigits: 0 }) {
  const n = Number(fraction);
  if (!Number.isFinite(n)) return '';
  return msgCached(loc, 'percent', options, (l, o) =>
    new Intl.NumberFormat(l, { style: 'percent', ...o })).format(n);
}

/** A date, a time or both in the language *loc*, from a Date, a timestamp or an ISO string. */
export function dateIn(loc, value, kind = 'date', style = 'medium') {
  if (value === undefined || value === null || value === '') return '';
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return '';
  const options = kind === 'time' ? { timeStyle: style === 'full' ? 'long' : style }
    : kind === 'datetime' ? { dateStyle: style, timeStyle: 'short' } : { dateStyle: style };
  return msgCached(loc, 'date', options, (l, o) => new Intl.DateTimeFormat(l, o)).format(d);
}

/** A list of strings joined as the language *loc* joins them (« a, b et c »). */
export function listIn(loc, items, type = 'conjunction') {
  return msgCached(loc, 'list', { type }, (l, o) => new Intl.ListFormat(l, o)).format(items.map(String));
}

const MSG_DATE_STYLES = { short: 'short', medium: 'medium', long: 'long', full: 'full' };

/** The parsed message *nodes* with *params* filled in, in the language *loc*. */
export function formatParsed(nodes, params, loc, pound = null) {
  let out = '';
  for (const node of nodes) {
    if (typeof node === 'string') {
      out += node;
      continue;
    }
    const value = node.name === undefined ? undefined : params[node.name];
    switch (node.type) {
      case 'pound':
        out += pound === null ? '#' : numberIn(loc, pound);
        break;
      case 'arg':
        out += value === undefined || value === null ? '' : String(value);
        break;
      case 'number':
        out += node.style === 'percent' ? percentIn(loc, value)
          : numberIn(loc, value, node.style === 'integer' ? { maximumFractionDigits: 0 } : {});
        break;
      case 'date':
      case 'time':
      case 'datetime':
        out += dateIn(loc, value, node.type, MSG_DATE_STYLES[node.style] || 'medium');
        break;
      case 'list':
        out += listIn(loc, value || [], node.style === 'or' ? 'disjunction' : 'conjunction');
        break;
      case 'select': {
        const branch = node.options[String(value)] || node.options.other;
        out += formatParsed(branch, params, loc, pound);
        break;
      }
      case 'plural':
      case 'selectordinal': {
        const n = Number(value) || 0;
        let branch = node.options[`=${n}`];
        if (!branch) {
          const rules = msgCached(loc, 'plural', { type: node.type === 'plural' ? 'cardinal' : 'ordinal' },
            (l, o) => new Intl.PluralRules(l, o));
          branch = node.options[rules.select(n - node.offset)] || node.options.other;
        }
        out += formatParsed(branch, params, loc, n - node.offset);
        break;
      }
      default:
        break;
    }
  }
  return out;
}

/**
 * A `t(key, params)` over plain catalogues (`{key: message}`): the messages of *loc*, else
 * those of *fallback*, else the key itself. What a host without the app's i18n (the offline
 * site) gives the atlas.
 */
export function createTranslator(messages, fallback, loc) {
  const parsedMessages = new Map();
  return function translate(key, params) {
    const source = (messages && messages[key]) ?? (fallback && fallback[key]);
    if (source === undefined) return key;
    let ast = parsedMessages.get(key);
    if (!ast) {
      try {
        ast = parseMessage(source);
      } catch (err) {
        return source;
      }
      parsedMessages.set(key, ast);
    }
    return formatParsed(ast, params || {}, loc);
  };
}
