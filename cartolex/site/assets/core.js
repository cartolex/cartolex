// SPDX-License-Identifier: MIT
/**
 * The offline site's core: the catalogues and formats, a small DOM helper,
 * the data indexed by id, the parts loaded on demand and the stored
 * preferences. A classic script (a page opened from `file://` cannot load ES
 * modules): it puts what the other scripts use on `window.CxSite`.
 *
 * The data comes as classic scripts too (`data/core.js`, `data/orgs.js`,
 * `data/links.js`, and the parts `data/people/<n>.js`, `data/keywords/<n>.js`,
 * `data/texts/<n>.js`), each setting `window.CX_SITE[<part>]`: a page opened from
 * `file://` can load a script, not read a JSON file.
 */
(function () {
  'use strict';

  const S = (window.CxSite = window.CxSite || {});
  const DATA = (window.CX_SITE = window.CX_SITE || {});
  S.data = DATA;
  /** The pages, by route name (each script adds its own): `(main, route) → teardown`. */
  S.pages = S.pages || {};
  S.LANGS = ['en', 'fr', 'pt-BR'];

  /** A stored preference (`undefined` value: read it); storage may be refused from `file://`. */
  S.store = function store(key, value) {
    try {
      if (value === undefined) return window.localStorage.getItem(key);
      if (value === null) window.localStorage.removeItem(key);
      else window.localStorage.setItem(key, value);
    } catch (e) {
      return null;
    }
    return null;
  };

  const saved = S.store('cx-site-lang');
  S.lang = S.LANGS.indexOf(saved) >= 0 ? saved : (DATA.core && DATA.core.language) || 'en';

  function words(lang) {
    const all = DATA.i18n || {};
    return all[lang] || all.en || {};
  }

  /** A formatted value: numbers with the language's digits and separators. */
  S.fmt = function fmt(value) {
    return typeof value === 'number' ? new Intl.NumberFormat(S.lang).format(value) : String(value);
  };

  /** The catalogue's text of *key*, with its `{name}` parameters filled. */
  S.t = function t(key, params) {
    let text = words(S.lang)[key];
    if (text === undefined) text = words('en')[key];
    if (text === undefined) return key;
    return String(text).replace(/\{(\w+)\}/g, (m, name) => (params && params[name] !== undefined
      ? S.fmt(params[name]) : m));
  };

  /** The plural form of *key* for *n* (`key.one`, `key.other`…), with `{n}` filled. */
  S.tn = function tn(key, n, params) {
    const rule = new Intl.PluralRules(S.lang).select(n);
    const own = words(S.lang);
    const k = own[`${key}.${rule}`] !== undefined ? `${key}.${rule}` : `${key}.other`;
    return S.t(k, Object.assign({ n }, params || {}));
  };

  S.percent = function percent(x) {
    return new Intl.NumberFormat(S.lang, { style: 'percent', maximumFractionDigits: 0 }).format(x);
  };

  S.date = function date(iso) {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '' : new Intl.DateTimeFormat(S.lang, { dateStyle: 'long' }).format(d);
  };

  function append(el, child) {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) child.forEach((c) => append(el, c));
    else if (typeof child === 'string' || typeof child === 'number') el.appendChild(document.createTextNode(String(child)));
    else el.appendChild(child);
  }

  /**
   * An element: `attrs` are attributes, except `class`, `text` (its text),
   * `on<event>` (listeners) and `style` (custom properties and styles set
   * through the CSSOM); children are nodes, strings or arrays of them.
   */
  S.h = function h(tag, attrs, children) {
    const svg = tag === 'svg' || tag === 'path' || tag === 'rect' || tag === 'circle';
    const el = svg ? document.createElementNS('http://www.w3.org/2000/svg', tag) : document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.setAttribute('class', v);
      else if (k === 'text') el.textContent = v;
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else if (k === 'style') Object.entries(v).forEach(([p, val]) => el.style.setProperty(p, val));
      else el.setAttribute(k, v === true ? '' : String(v));
    }
    append(el, children);
    return el;
  };

  /** A link to a route of the site (`/person/s3`). */
  S.link = function link(path, text, cls) {
    return S.h('a', { href: `#${path}`, class: cls || null }, text);
  };

  /** A kind's symbol (the map's shapes), for legends and lists. */
  S.symbol = function symbol(shape, color) {
    const fill = !color ? 'var(--cx-text-muted)' : color.startsWith('--') ? `var(${color})` : color;
    const body = {
      square: S.h('rect', { x: 2, y: 2, width: 8, height: 8 }),
      triangle: S.h('path', { d: 'M6 1.5L10.8 10H1.2z' }),
      diamond: S.h('path', { d: 'M6 1l5 5-5 5-5-5z' }),
      ring: S.h('circle', { cx: 6, cy: 6, r: 3.8, class: 'cx-symbol__ring' }),
    }[shape] || S.h('circle', { cx: 6, cy: 6, r: 4.5 });
    return S.h('svg', { class: 'cx-symbol', viewBox: '0 0 12 12', width: 12, height: 12, 'aria-hidden': 'true',
      style: { '--cx-symbol': fill } }, body);
  };

  /** Text without case or accents, for searching. */
  S.fold = function fold(text) {
    return String(text || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  };

  // ── the data, by id ──────────────────────────────────────────────────────

  /** The number in a site id (`s12` → 12). */
  function numberOf(id) {
    return Number.parseInt(String(id).slice(1), 10) || 0;
  }

  /** Integers sent as base64 bytes (`data.py`'s `_ints`): a typed array of *Type*. */
  S.ints = function ints(b64, Type) {
    const bin = window.atob(b64 || '');
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
    return new (Type || Int32Array)(bytes.buffer);
  };

  /**
   * The indexes of the core data: the tree, people, organisations, keywords, and
   * each organisation's members (directly or through an organisation below it).
   */
  S.indexData = function indexData(core) {
    const nodes = new Map(core.nodes.map((n) => [n.id, n]));
    const children = new Map([[null, []]]);
    core.nodes.forEach((n) => children.set(n.id, []));
    core.nodes.forEach((n) => children.get(n.parent && nodes.has(n.parent) ? n.parent : null).push(n.id));
    for (const list of children.values()) {
      list.sort((a, b) => (nodes.get(a).order - nodes.get(b).order) || (a < b ? -1 : 1));
    }
    const tops = children.get(null);
    const topOf = new Map();
    const visit = (id, top) => {
      topOf.set(id, top);
      (children.get(id) || []).forEach((c) => visit(c, top));
    };
    tops.forEach((top) => visit(top, top));
    const byPerson = new Map(core.people.id.map((id, i) => [id, i]));
    const byOrg = new Map(core.orgs.id.map((id, i) => [id, i]));
    const byTerm = new Map(core.keywords.term.map((term, i) => [term, i]));
    const byProjected = new Map(core.projected.id.map((id, i) => [id, i]));
    const members = core.orgs.id.map(() => []);
    core.people.orgs.forEach((list, i) => {
      const seen = new Set();
      const todo = list.slice();
      while (todo.length) {
        const o = todo.pop();
        if (seen.has(o)) continue;
        seen.add(o);
        members[o].push(i);
        todo.push(...core.orgs.parents[o]);
      }
    });
    return { core, nodes, children, tops, topOf, byPerson, byOrg, byTerm, byProjected, members };
  };

  /** A person's shares at *level* (0: the top), as `[[node id, share]]`, the largest first. */
  S.sharesOf = function sharesOf(i, level, list) {
    const flat = ((list || S.ix.core.people.shares)[i] || [])[level || 0] || [];
    const out = [];
    for (let k = 0; k + 1 < flat.length; k += 2) out.push([S.ix.core.nodes[flat[k]].id, flat[k + 1] / 1000]);
    return out;
  };

  /** An organisation's shares at *level*: the mean of its members'. */
  S.orgSharesOf = function orgSharesOf(o, level) {
    const people = S.ix.members[o] || [];
    const total = new Map();
    people.forEach((i) => S.sharesOf(i, level).forEach(([node, share]) => {
      total.set(node, (total.get(node) || 0) + share / people.length);
    }));
    return [...total].sort((a, b) => b[1] - a[1]);
  };

  /** A person's main top-level theme (or null). */
  S.topOfPerson = function topOfPerson(i) {
    const first = S.sharesOf(i, 0)[0];
    return first ? first[0] : null;
  };

  /** A theme's colour: the atlas's colour scheme when the site has the atlas, else its hue. */
  S.themeColour = function themeColour(id) {
    const top = S.ix.topOf.get(id) || id;
    const colours = S.themeColours ? S.themeColours() : null;
    if (colours && colours.get(top)) return colours.get(top);
    return `var(--cx-hue-${(Math.max(0, S.ix.tops.indexOf(top)) % 12) + 1})`;
  };

  /** A theme's name in the site's language, else in the first language it has. */
  S.nodeName = function nodeName(id) {
    const n = S.ix.nodes.get(id);
    if (!n) return '';
    const names = n.names || {};
    const two = S.lang.slice(0, 2);
    return names[S.lang] || names[two] || names.en || Object.values(names).find(Boolean) || id;
  };

  /** A person's name, or their pseudonym (« Person 12 »). */
  S.personName = function personName(i) {
    const p = S.ix.core.people;
    return p.name[i] || S.t('person.pseudonym', { n: numberOf(p.id[i]) });
  };

  S.projectedName = function projectedName(i) {
    const p = S.ix.core.projected;
    return p.name[i] || S.t('projected.pseudonym', { n: numberOf(p.id[i]) });
  };

  /** An organisation's short name (its acronym, else its name). */
  S.orgShort = function orgShort(i) {
    const o = S.ix.core.orgs;
    return o.acronym[i] || o.name[i];
  };

  S.orgLevelName = function orgLevelName(id) {
    const level = (S.ix.core.org_levels || []).find((l) => l.id === id);
    const names = (level && level.names) || {};
    return names[S.lang] || names[S.lang.slice(0, 2)] || names.en || Object.values(names).find(Boolean) || id;
  };

  // ── parts loaded on demand ───────────────────────────────────────────────

  const waiting = new Map();

  /** Load a data part (`orgs`, `links`, `people/3`…) once; *done(ok)* when it is there or missing. */
  S.need = function need(part, done) {
    if (DATA[part]) {
      done(true);
      return;
    }
    if (waiting.has(part)) {
      waiting.get(part).push(done);
      return;
    }
    waiting.set(part, [done]);
    const finish = (ok) => {
      const list = waiting.get(part) || [];
      waiting.delete(part);
      list.forEach((fn) => fn(ok && Boolean(DATA[part])));
    };
    const script = document.createElement('script');
    script.src = `data/${part}.js`;
    script.addEventListener('load', () => finish(true));
    script.addEventListener('error', () => finish(false));
    document.head.appendChild(script);
  };

  /** A data part, as a promise of whether it is there. */
  S.load = function load(part) {
    return new Promise((resolve) => { S.need(part, resolve); });
  };

  /** The part holding a person's details (`people`) or texts (`texts`): the part
   * `(number − 1) mod n` of their id `s<number>`; a keyword's users (`keywords`): the
   * part `index mod n` of its index; *n* in `core.shards`. */
  S.partOf = function partOf(kind, id) {
    const n = ((DATA.core && DATA.core.shards) || {})[kind] || 1;
    const k = typeof id === 'number' ? id : parseInt(String(id).slice(1), 10) - 1;
    return `${kind}/${k % n}`;
  };

  /** A person's details or texts, once their part is loaded (`undefined` before). */
  S.personPart = function personPart(kind, id) {
    const part = DATA[S.partOf(kind, id)];
    return part ? part[id] : undefined;
  };

  /**
   * Who writes with a person (`people`: an index of `core.people`, or after them of
   * `core.projected`) or an organisation (`orgs`: an index of `core.orgs`), from their
   * details (`co`: flat pairs of an index and the works together): `[[index, works]]`, the
   * strongest first; null before the details are loaded.
   */
  S.partners = function partners(kind, i) {
    const core = DATA.core;
    let d = null;
    if (kind === 'orgs') d = DATA.details && DATA.details.orgs[core.orgs.id[i]];
    else if (i < core.people.id.length) d = S.personPart('people', core.people.id[i]);
    else d = DATA.details && (DATA.details.projected || {})[core.projected.id[i - core.people.id.length]];
    if (d === undefined || (d === null && !DATA.details)) return null;
    const flat = (d && d.co) || [];
    const out = [];
    for (let k = 0; k + 1 < flat.length; k += 2) out.push([flat[k], flat[k + 1]]);
    return out;
  };

  /** A partner's selection (`{kind, id}`) from its index in the links of *kind*. */
  S.partnerSel = function partnerSel(kind, j) {
    const core = DATA.core;
    if (kind === 'orgs') return { kind: 'org', id: core.orgs.id[j] };
    const n = core.people.id.length;
    return j < n ? { kind: 'person', id: core.people.id[j] } : { kind: 'projected', id: core.projected.id[j - n] };
  };

  /** A selection's links and index in them (`['people' | 'orgs', i]`), or null. */
  S.selIndex = function selIndex(sel) {
    const ix = S.ix;
    if (!sel) return null;
    if (sel.kind === 'person' && ix.byPerson.has(sel.id)) return ['people', ix.byPerson.get(sel.id)];
    if (sel.kind === 'projected' && ix.byProjected.has(sel.id)) {
      return ['people', ix.core.people.id.length + ix.byProjected.get(sel.id)];
    }
    if (sel.kind === 'org' && ix.byOrg.has(sel.id)) return ['orgs', ix.byOrg.get(sel.id)];
    return null;
  };

  /** The title of a list of partners: « Co-authors (N) » or « Writes with (N) ». */
  S.coTitle = function coTitle(kind, n) {
    return S.tn(kind === 'orgs' ? 'org.coauthors' : 'person.coauthors', n);
  };

  /** A list of partners (`[[index, works]]` of the links of *kind*), each a button that
   * selects it (*onSelect*), else a link to its page, with the works together. */
  S.partnerList = function partnerList(kind, co, onSelect) {
    const ix = S.ix;
    if (!co.length) return S.h('p', { class: 'cx-muted', text: S.t('page.none') });
    return S.h('ol', { class: 'cx-list' }, co.map(([j, n]) => {
      const sel = S.partnerSel(kind, j);
      const name = sel.kind === 'person' ? S.personName(ix.byPerson.get(sel.id))
        : sel.kind === 'projected' ? S.projectedName(ix.byProjected.get(sel.id))
          : ix.core.orgs.name[ix.byOrg.get(sel.id)];
      const open = onSelect
        ? S.h('button', { type: 'button', class: 'cx-link-button', onclick: () => onSelect(sel) }, name)
        : S.link(sel.kind === 'person' ? `/person/${sel.id}` : sel.kind === 'org' ? `/org/${sel.id}`
          : `/map?sel=${encodeURIComponent(`projected:${sel.id}`)}`, name);
      return S.h('li', {}, [open, ' ', S.h('span', { class: 'cx-muted', text: S.tn('coauthors.works', n) })]);
    }));
  };

  /** The message a page shows when a part of the site's files is missing. */
  S.missingNote = function missingNote() {
    return S.h('div', { class: 'cx-note cx-note--warning', role: 'alert' }, [
      S.h('strong', { text: S.t('missing.title') }), ' ', S.t('missing.text'),
    ]);
  };
}());
