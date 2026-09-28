// SPDX-License-Identifier: MIT
/**
 * The themes screen: the theme editor.
 *
 * One module, so that opening the screen costs one request for its code (the
 * components it uses load with the shell). Its sections, in order: the tree
 * in the browser (index, names, search, patches), the words that depend on the
 * tree, the editor's state (undo, redo, draft), the dialogs, the outline, the
 * treemap and the map, the side panel, the versions, the AI handoff, and the
 * page that puts them together.
 */
import { batch, computed, html, signal, useEffect, useMemo, useRef, useState } from '../core/preact.js';
import { formatDate, formatList, formatNumber, locale, t } from '../core/i18n.js';
import { copyText, downloadFile, useUid } from '../core/dom.js';
import { definePage, usePage, usePageTitle } from '../core/page.js';
import { runtime } from '../core/runtime.js';
import { ACTIVE } from '../core/stores/jobs.js';
import {
  Button, Checkbox, ConfirmDialog, Dialog, Drawer, EmptyState, ErrorCard, FormField, Icon, IconButton,
  Input, MapFrame, MenuButton, ProgressBar, Stepper, Tabs, Textarea, TreeView, Treemap,
} from '../components/index.js';

// ── model ───────────────────────────────────────────────────────────────────

/**
 * The theme tree in the browser: an index for fast reads, names, live
 * weights, search, and patches for undo and redo.
 *
 * Everything here is pure: a tree (`cartolex-themes/1`, as `GET /api/themes`
 * gives it) goes in, plain data comes out, nothing is changed. The server
 * applies the operations (`POST /api/themes/ops`); the browser only reads.
 */

/** Text folded for search and type-ahead: lower case, without accents. */
const folded = new Map();

/** Text folded for search and type-ahead: lower case, without accents (kept once per text). */
export function fold(text) {
  const key = String(text || '');
  let out = folded.get(key);
  if (out === undefined) {
    out = key.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
    if (folded.size > 200_000) folded.clear();
    folded.set(key, out);
  }
  return out;
}

/** The two-letter language of an interface locale (`pt-BR` → `pt`). */
export function lang2(locale) {
  return String(locale || 'en').split('-')[0];
}

/** A node's name in *lang*, else English, else its first name, else its id. */
export function nodeName(node, lang) {
  if (!node) return '';
  const names = node.names || {};
  return names[lang] || names.en || Object.values(names).find(Boolean) || node.id;
}

/** A level's name (1 = top) in *lang*, else English, else its first name. */
export function levelName(tree, level, lang) {
  const entry = tree && tree.levels && tree.levels[level - 1];
  if (!entry) return '';
  const names = entry.names || {};
  return names[lang] || names.en || Object.values(names).find(Boolean) || '';
}

/**
 * An index of *tree* for the views: nodes by id, children in order, levels,
 * top-level ancestors, keywords on each node (the most used first), keyword
 * counts and live usage weights under each node, and the top-level hue of
 * every node (its top-level node's rank, from 0).
 *
 * *usage* is `{term: [people, weight]}` (`GET /api/themes/usage`); a node's
 * weight is the weight of the keywords counting toward it (its keywords and
 * those below it, each down to its attribution), as the apply stage counts.
 */
export function indexTree(tree, usage = {}) {
  const nodes = new Map();
  const children = new Map([[null, []]]);
  for (const n of tree.nodes) {
    nodes.set(n.id, n);
    children.set(n.id, []);
  }
  for (const n of tree.nodes) {
    const list = children.get(n.parent === undefined ? null : n.parent);
    if (list) list.push(n.id);
  }
  const byOrder = (a, b) => {
    const na = nodes.get(a);
    const nb = nodes.get(b);
    return (na.order - nb.order) || (a < b ? -1 : a > b ? 1 : 0);
  };
  for (const list of children.values()) list.sort(byOrder);
  const level = new Map();
  const topOf = new Map();
  const order = [];
  const walk = (parent, lv, top) => {
    for (const id of children.get(parent) || []) {
      level.set(id, lv);
      topOf.set(id, top || id);
      order.push(id);
      walk(id, lv + 1, top || id);
    }
  };
  walk(null, 1, null);
  // Hue families that do not move when the tree changes: a top-level node `s<k>` (as the
  // grouping names them) keeps family k; any other top-level node takes the next free one.
  const hue = new Map();
  const used = new Set();
  const tops = children.get(null) || [];
  for (const id of tops) {
    const m = /^s(\d+)$/.exec(id);
    if (m) {
      hue.set(id, Number(m[1]) % 12);
      used.add(Number(m[1]) % 12);
    }
  }
  let free = 0;
  for (const id of tops) {
    if (hue.has(id)) continue;
    while (used.has(free % 12) && used.size < 12) free += 1;
    hue.set(id, free % 12);
    used.add(free % 12);
    free += 1;
  }
  for (const id of order) hue.set(id, hue.get(topOf.get(id)));

  const people = (term) => (usage[term] ? usage[term][0] : 0);
  const weightOf = (term) => (usage[term] ? usage[term][1] : 0);
  const keywordsOn = new Map(order.map((id) => [id, []]));
  for (const [term, id] of Object.entries(tree.keywords || {})) {
    const list = keywordsOn.get(id);
    if (list) list.push(term);
  }
  const byUse = (a, b) => (weightOf(b) - weightOf(a)) || (a < b ? -1 : a > b ? 1 : 0);
  for (const list of keywordsOn.values()) list.sort(byUse);

  const under = new Map();
  const weight = new Map(order.map((id) => [id, 0]));
  let total = 0;
  for (const id of [...order].reverse()) {
    let count = keywordsOn.get(id).length;
    for (const kid of children.get(id)) count += under.get(kid);
    under.set(id, count);
  }
  const attribution = tree.attribution || {};
  for (const [term, id] of Object.entries(tree.keywords || {})) {
    const w = weightOf(term);
    total += w;
    const counts = attribution[term] === undefined ? level.get(id) : attribution[term];
    let at = id;
    while (at !== null && at !== undefined) {
      if (level.get(at) <= counts) weight.set(at, weight.get(at) + w);
      at = nodes.get(at).parent;
    }
  }
  const setAside = Object.keys(tree.set_aside || {}).sort(byUse);
  const toCheck = Object.entries(tree.review || {})
    .filter(([, state]) => state === 'to_check').map(([term]) => term).sort();
  return {
    tree, nodes, children, level, topOf, order, hue, keywordsOn, under, weight, total,
    setAside, toCheck, people, weightOf,
    depth: tree.depth,
    tops: children.get(null) || [],
  };
}

/** The path of a node from the top: `[top id, …, id]`. */
export function pathOf(index, id) {
  const out = [];
  let at = id;
  while (at !== null && at !== undefined && index.nodes.has(at)) {
    out.unshift(at);
    at = index.nodes.get(at).parent;
  }
  return out;
}

/** Where a keyword is: `{node}` when placed, `{aside: entry}` when set aside, else null. */
export function placeOf(tree, term) {
  if (tree.keywords && term in tree.keywords) return { node: tree.keywords[term] };
  if (tree.set_aside && term in tree.set_aside) return { aside: tree.set_aside[term] };
  return null;
}

// ── search ──────────────────────────────────────────────────────────────────

/** A search index: every keyword and node name, folded once. */
export function searchIndex(index, lang) {
  const keywords = [];
  for (const [term] of Object.entries(index.tree.keywords || {})) keywords.push([term, fold(term)]);
  for (const term of Object.keys(index.tree.set_aside || {})) keywords.push([term, fold(term)]);
  const nodes = index.order.map((id) => [id, fold(nodeName(index.nodes.get(id), lang))]);
  return { keywords, nodes };
}

/**
 * The keywords and nodes whose folded text holds every word of *query*
 * (keywords in the order of the index, at most *limit* of each kind).
 */
export function search(searchable, query, limit = 5000) {
  const words = fold(query).split(/\s+/).filter(Boolean);
  if (!words.length) return null;
  const hit = (text) => words.every((w) => text.includes(w));
  const keywords = [];
  for (const [term, text] of searchable.keywords) {
    if (hit(text)) {
      keywords.push(term);
      if (keywords.length >= limit) break;
    }
  }
  const nodes = [];
  for (const [id, text] of searchable.nodes) if (hit(text)) nodes.push(id);
  return { words, keywords, nodes };
}

/** Pieces of *text* with the parts matching *words* marked: `[{text, match}]`. */
export function highlight(text, words) {
  if (!words || !words.length) return [{ text, match: false }];
  const folded = fold(text);
  // Folding can change the length (a letter and its accent): map folded to original offsets.
  const map = [];
  let pos = 0;
  for (const ch of String(text)) {
    const f = fold(ch);
    for (let k = 0; k < f.length; k += 1) map.push(pos);
    pos += ch.length;
  }
  map.push(pos);
  const marks = new Array(folded.length).fill(false);
  for (const w of words) {
    let at = folded.indexOf(w);
    while (at >= 0) {
      for (let k = at; k < at + w.length; k += 1) marks[k] = true;
      at = folded.indexOf(w, at + 1);
    }
  }
  const out = [];
  let k = 0;
  while (k < folded.length) {
    const m = marks[k];
    let e = k;
    while (e < folded.length && marks[e] === m) e += 1;
    out.push({ text: String(text).slice(map[k], map[e]), match: m });
    k = e;
  }
  return out.length ? out : [{ text, match: false }];
}

// ── patches: undo and redo without keeping every tree ───────────────────────

const DICTS = ['keywords', 'attribution', 'set_aside', 'review'];
const WHOLE = ['depth', 'levels', 'nodes', 'based_on', 'saved', 'format'];
const ABSENT = { absent: true };

function same(a, b) {
  return a === b || JSON.stringify(a) === JSON.stringify(b);
}

/** What changed from tree *a* to tree *b*: a patch for `applyPatch`. */
export function diffTree(a, b) {
  const patch = { whole: {}, dicts: {} };
  for (const key of WHOLE) {
    if (!same(a[key], b[key])) patch.whole[key] = [a[key] === undefined ? ABSENT : a[key], b[key] === undefined ? ABSENT : b[key]];
  }
  for (const key of DICTS) {
    const da = a[key] || {};
    const db = b[key] || {};
    const changes = [];
    for (const k of Object.keys(da)) {
      if (!(k in db)) changes.push([k, da[k], ABSENT]);
      else if (!same(da[k], db[k])) changes.push([k, da[k], db[k]]);
    }
    for (const k of Object.keys(db)) if (!(k in da)) changes.push([k, ABSENT, db[k]]);
    if (changes.length) patch.dicts[key] = changes;
  }
  return patch;
}

/** *tree* with *patch* applied forward, or backward when *reverse*. */
export function applyPatch(tree, patch, reverse = false) {
  const out = { ...tree };
  const pick = (pair) => (reverse ? pair[0] : pair[1]);
  for (const [key, pair] of Object.entries(patch.whole)) {
    const value = pick(pair);
    if (value === ABSENT || (value && value.absent === true && Object.keys(value).length === 1)) delete out[key];
    else out[key] = value;
  }
  for (const [key, changes] of Object.entries(patch.dicts)) {
    const dict = { ...(tree[key] || {}) };
    for (const [k, before, after] of changes) {
      const value = reverse ? before : after;
      if (value === ABSENT || (value && value.absent === true && Object.keys(value).length === 1)) delete dict[k];
      else dict[k] = value;
    }
    out[key] = sortedDict(dict);
  }
  return out;
}

function sortedDict(dict) {
  const out = {};
  for (const k of Object.keys(dict).sort()) out[k] = dict[k];
  return out;
}

const texts = new WeakMap();

/** A tree as text, its save stamp aside (kept once per tree object: trees are never changed). */
export function treeText(tree) {
  let text = texts.get(tree);
  if (text === undefined) {
    text = JSON.stringify({ ...tree, saved: null });
    texts.set(tree, text);
  }
  return text;
}

/** Whether two trees hold the same content (their save stamps aside). */
export function sameTree(a, b) {
  if (!a || !b) return a === b;
  return a === b || treeText(a) === treeText(b);
}

// ── what an operation may do here ───────────────────────────────────────────

/** Nodes a set of keywords may move to: every node but the one they all sit on. */
export function keywordTargets(index, terms) {
  const from = new Set(terms.map((t) => index.tree.keywords[t]).filter(Boolean));
  return index.order.filter((id) => !(from.size === 1 && from.has(id)));
}

/** Nodes a node may move under: those on the level just above it (its parent aside). */
export function nodeParents(index, id) {
  const lv = index.level.get(id);
  const parent = index.nodes.get(id).parent;
  return index.order.filter((p) => index.level.get(p) === lv - 1 && p !== parent);
}

/** Nodes a node may merge into: the other nodes of its level. */
export function mergeTargets(index, id) {
  const lv = index.level.get(id);
  return index.order.filter((n) => n !== id && index.level.get(n) === lv);
}

/** Whether a node holds nothing (no child node, no keyword). */
export function isEmpty(index, id) {
  return !(index.children.get(id) || []).length && !(index.keywordsOn.get(id) || []).length;
}

/**
 * The operation of dropping *what* (`{kind: 'keywords', terms}` or
 * `{kind: 'node', id}`) on node *target*, or null when the drop is refused.
 */
export function dropOperation(index, what, target) {
  if (!index.nodes.has(target)) return null;
  if (what.kind === 'keywords') {
    const placed = what.terms.filter((t) => index.tree.keywords[t] !== undefined);
    const aside = what.terms.filter((t) => index.tree.set_aside && t in index.tree.set_aside);
    if (placed.length && placed.every((t) => index.tree.keywords[t] === target) && !aside.length) return null;
    const ops = [];
    if (placed.length) ops.push({ op: 'move_keywords', keywords: placed, node_id: target });
    if (aside.length) ops.push({ op: 'put_back', keywords: aside, node_id: target });
    return ops.length ? ops : null;
  }
  if (what.kind === 'node') {
    if (what.id === target) return null;
    const lv = index.level.get(what.id);
    if (index.level.get(target) !== lv - 1) return null;
    if (index.nodes.get(what.id).parent === target) return null;
    return [{ op: 'move_node', node_id: what.id, parent: target }];
  }
  return null;
}

/**
 * *tree* with *ops* applied here, for an immediate view while the server
 * applies them (its tree then replaces this one): only the operations that
 * change keywords' places and nodes' names, by the same rules (the carry
 * rule of attributions). Null when an operation is of another kind or does
 * not fit the tree: the view then waits for the server.
 */
export function optimistic(tree, ops) {
  let out = { ...tree, keywords: { ...tree.keywords }, attribution: { ...(tree.attribution || {}) },
    set_aside: { ...(tree.set_aside || {}) }, review: { ...(tree.review || {}) } };
  const nodeIds = new Set(tree.nodes.map((n) => n.id));
  const parent = new Map(tree.nodes.map((n) => [n.id, n.parent]));
  const level = (id) => {
    let lv = 1;
    for (let at = parent.get(id); at !== null && at !== undefined; at = parent.get(at)) lv += 1;
    return lv;
  };
  for (const op of ops) {
    const terms = op.keywords || [];
    if (op.op === 'rename_node') {
      if (!nodeIds.has(op.node_id)) return null;
      out = { ...out, nodes: out.nodes.map((n) => {
        if (n.id !== op.node_id) return n;
        const names = { ...n.names };
        for (const [code, value] of Object.entries(op.names)) {
          if (value === null || !String(value).trim()) delete names[code];
          else names[code] = String(value).trim();
        }
        return { ...n, names };
      }) };
    } else if (op.op === 'move_keywords') {
      if (!nodeIds.has(op.node_id) || terms.some((k) => out.keywords[k] === undefined)) return null;
      for (const k of terms) {
        if (out.keywords[k] !== op.node_id && out.attribution[k]) delete out.attribution[k];
        out.keywords[k] = op.node_id;
      }
    } else if (op.op === 'set_aside') {
      for (const k of terms) {
        if (out.keywords[k] !== undefined) {
          const entry = { from: out.keywords[k], reason: String(op.reason || '').trim() };
          if (k in out.attribution) entry.attribution = out.attribution[k];
          delete out.keywords[k];
          delete out.attribution[k];
          out.set_aside[k] = entry;
        } else if (out.set_aside[k]) {
          out.set_aside[k] = { ...out.set_aside[k], reason: String(op.reason || '').trim() };
        } else return null;
      }
    } else if (op.op === 'put_back') {
      if (op.node_id && !nodeIds.has(op.node_id)) return null;
      for (const k of terms) {
        const entry = out.set_aside[k];
        const target = op.node_id || (entry && entry.from);
        if (!entry || !target || !nodeIds.has(target)) return null;
        delete out.set_aside[k];
        out.keywords[k] = target;
        const n = entry.attribution;
        if (n === 0 || (n && target === entry.from && n < level(target))) out.attribution[k] = n;
      }
    } else if (op.op === 'set_review') {
      for (const k of terms) {
        if (out.keywords[k] === undefined && !out.set_aside[k]) return null;
        if (op.state) out.review[k] = op.state;
        else delete out.review[k];
      }
    } else if (op.op === 'set_attribution') {
      for (const k of terms) {
        if (out.keywords[k] === undefined) return null;
        if (op.levels === null || op.levels === undefined) delete out.attribution[k];
        else out.attribution[k] = op.levels;
      }
    } else {
      return null;
    }
  }
  return out;
}

/** The next *count* free node ids (`n<k>`), as the server gives them (set-aside origins too). */
export function nextIds(tree, count = 1) {
  const taken = new Set(tree.nodes.map((n) => n.id));
  for (const entry of Object.values(tree.set_aside || {})) if (entry && entry.from) taken.add(entry.from);
  let top = 0;
  for (const id of taken) {
    const m = /^n([1-9][0-9]*)$/.exec(id);
    if (m) top = Math.max(top, Number(m[1]));
  }
  return Array.from({ length: count }, (_, i) => `n${top + 1 + i}`);
}

// ── labels ──────────────────────────────────────────────────────────────────

/**
 * Words of the theme editor that depend on the tree: the operations'
 * descriptions in the interface language, with node names instead of ids,
 * and the names of languages and levels.
 *
 * The server describes each operation in short English (`move 3 keywords to
 * n7`); those descriptions name the undo list's entries and the saved
 * versions. The interface shows them in its own language, with the names the
 * nodes had when the operation ran (kept in the undo entry).
 */

const PATTERNS = [
  [/^rename level (\d+)$/, (m) => ['themes.op.rename_level', { level: Number(m[1]) }]],
  [/^rename (\S+)$/, (m, n, after) => (n(m[1]) === after(m[1]) ? ['themes.op.rename_one', { name: n(m[1]) }]
    : ['themes.op.rename', { name: n(m[1]), to: after(m[1]) }])],
  [/^move (\d+) keywords? to (\S+)$/, (m, n) => ['themes.op.move_keywords', { count: Number(m[1]), name: n(m[2]) }]],
  [/^reorder (\S+)$/, (m, n) => ['themes.op.reorder', { name: n(m[1]) }]],
  [/^move (\S+) under (\S+)$/, (m, n) => ['themes.op.move_node', { name: n(m[1]), parent: n(m[2]) }]],
  [/^move (\S+) to the top$/, (m, n) => ['themes.op.move_top', { name: n(m[1]) }]],
  [/^merge (\S+) into (\S+)$/, (m, n) => ['themes.op.merge', { source: n(m[1]), target: n(m[2]) }]],
  [/^split (\S+) into (\d+)$/, (m, n) => ['themes.op.split', { name: n(m[1]), count: Number(m[2]) }]],
  [/^create (\S+)$/, (m, n) => ['themes.op.create', { name: n(m[1]) }]],
  [/^delete (\S+)$/, (m, n) => ['themes.op.delete', { name: n(m[1]) }]],
  [/^set aside (\d+) keywords?$/, (m) => ['themes.op.set_aside', { count: Number(m[1]) }]],
  [/^put back (\d+) keywords?$/, (m) => ['themes.op.put_back', { count: Number(m[1]) }]],
  [/^mark (\d+) keywords? to check$/, (m) => ['themes.op.to_check', { count: Number(m[1]) }]],
  [/^mark (\d+) keywords? reviewed$/, (m) => ['themes.op.reviewed', { count: Number(m[1]) }]],
  [/^clear the review of (\d+) keywords?$/, (m) => ['themes.op.review_cleared', { count: Number(m[1]) }]],
  [/^count (\d+) keywords? at their node's level$/, (m) => ['themes.op.count_default', { count: Number(m[1]) }]],
  [/^count (\d+) keywords? nowhere$/, (m) => ['themes.op.count_nowhere', { count: Number(m[1]) }]],
  [/^count (\d+) keywords? down to level (\d+)$/, (m) => ['themes.op.count_level', { count: Number(m[1]), level: Number(m[2]) }]],
  [/^remove empty nodes? (.+)$/, (m, n) => ['themes.op.prune', { names: formatList(m[1].split(', ').map(n)) }]],
  [/^insert level (\d+)$/, (m) => ['themes.op.insert_level', { level: Number(m[1]) }]],
  [/^remove level (\d+)$/, (m) => ['themes.op.remove_level', { level: Number(m[1]) }]],
];

/** Names of the nodes a description may name, from the trees around an operation. */
export function namesFor(descriptions, ...trees) {
  const ids = new Set();
  for (const d of descriptions) for (const word of d.split(/[\s,]+/)) ids.add(word);
  const out = {};
  for (const tree of trees) {
    if (!tree) continue;
    for (const node of tree.nodes) if (ids.has(node.id) && !out[node.id]) out[node.id] = node.names;
  }
  return out;
}

/**
 * One description of the server in the interface language, with the nodes'
 * names: *names* as they were before the operation, *after* as they became.
 */
export function describe(description, names = {}, after = {}) {
  const lang = lang2(locale.value);
  const named = (id) => {
    const n = names[id] || after[id];
    return n ? nodeName({ id, names: n }, lang) : id;
  };
  const renamed = (id) => {
    const n = after[id] || names[id];
    return n ? nodeName({ id, names: n }, lang) : id;
  };
  for (const [pattern, make] of PATTERNS) {
    const m = pattern.exec(description);
    if (m) {
      const [key, params] = make(m, named, renamed);
      return t(key, params);
    }
  }
  return description;
}

/** The name of an undo entry in the interface language. */
export function entryLabel(entry) {
  if (!entry) return '';
  if (entry.labelKey) return t(entry.labelKey.key, entry.labelKey.params || {});
  const parts = (entry.descriptions || []).map((d) => describe(d, entry.names || {}, entry.namesAfter || {}));
  return parts.length ? formatList(parts) : entry.label || '';
}

/** A language's name in the interface language (`fr` → « French »). */
export function languageName(code) {
  try {
    const names = new Intl.DisplayNames([locale.value], { type: 'language' });
    const name = names.of(code);
    return name ? name.charAt(0).toLocaleUpperCase(locale.value) + name.slice(1) : code;
  } catch {
    return code;
  }
}

/** The languages a name may be given in: the interface's, and those a name already has. */
export function nameLanguages(...nameSets) {
  const out = ['en', 'fr', 'pt'];
  for (const names of nameSets) {
    for (const code of Object.keys(names || {})) if (!out.includes(code)) out.push(code);
  }
  const first = lang2(locale.value);
  return [first, ...out.filter((c) => c !== first)];
}

/** A share (0–1) as a short percentage (« 12 % », « < 1 % »). */
export function shortShare(fraction) {
  if (!fraction || fraction <= 0) return t('themes.share.value', { value: '0' });
  const pct = fraction * 100;
  const fmt = new Intl.NumberFormat(locale.value, { maximumFractionDigits: pct < 10 ? 1 : 0 });
  if (pct < 0.1) return t('themes.share.tiny');
  return t('themes.share.value', { value: fmt.format(pct) });
}

// ── store ───────────────────────────────────────────────────────────────────

/**
 * The theme editor's state: the tree being edited, its undo and redo lists,
 * the autosaved draft, and the calls that load, change, save and apply it.
 *
 * **The tree** is changed only by the server's operations
 * (`POST /api/themes/ops`): each change is one entry of the undo list, named
 * by the operations' descriptions, holding the operations and a patch (what
 * changed), so undo and redo need no call and cost little memory.
 *
 * **The draft** is kept in the browser's local storage, per project, on every
 * change: the tree, the version of the saved tree it started from, and the
 * undo list with its operations. It survives a reload and a crash of the tab
 * or the browser, and it is personal: it never reaches the project (shared,
 * versioned, perhaps synced) before someone saves. When the saved tree has
 * changed meanwhile, the draft's operations are applied again to the newer
 * tree (« reload and merge »), and those that no longer apply are listed.
 */

export const DRAFT_PREFIX = 'cartolex.themes-draft/1:';
/** The most bytes a draft may take in local storage (the undo patches are dropped first). */
const DRAFT_LIMIT = 4_000_000;

function storage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** The draft saved for *projectId*, or null. */
export function readDraft(projectId) {
  const store = storage();
  if (!store || !projectId) return null;
  try {
    const raw = store.getItem(DRAFT_PREFIX + projectId);
    const draft = raw ? JSON.parse(raw) : null;
    return draft && draft.v === 1 && draft.tree ? draft : null;
  } catch {
    return null;
  }
}

function writeDraft(projectId, draft) {
  const store = storage();
  if (!store || !projectId) return false;
  const key = DRAFT_PREFIX + projectId;
  try {
    if (!draft) {
      store.removeItem(key);
      return true;
    }
    let text = JSON.stringify(draft);
    if (text.length > DRAFT_LIMIT) {
      // Keep what « reload and merge » needs: the operations, not the patches.
      text = JSON.stringify({ ...draft, past: draft.past.map((e) => ({ ...e, patch: null })), future: [] });
    }
    store.setItem(key, text);
    return true;
  } catch {
    return false;
  }
}

/**
 * The editor of one page visit.
 * @param {object} options
 * @param {object} options.api the page's API client (calls dropped once the page is left)
 * @param {string|null} options.projectId the open project (the draft's key)
 * @param {(message: string) => void} options.announce a polite live-region message
 */
export function createEditor({ api, projectId, announce = () => {} }) {
  const loading = signal(true);
  const error = signal(null);
  const info = signal(null); // the last GET /api/themes, without its tree
  const base = signal(null); // {tree, version, source}
  const tree = signal(null);
  const past = signal([]);
  const future = signal([]);
  const usage = signal({});
  const busy = signal(false);
  const restored = signal(null); // {at, stale, count}
  const viewing = signal(null); // a version shown read-only: {id, tree, label}
  const preview = signal(null); // AI proposals previewed: {tree, accepted}
  const lastSaved = signal(null); // {removed, action}
  const opError = signal(null);

  /** The tree the views show: a version being read, a preview, or the one being edited. */
  const shown = computed(() => (viewing.value && viewing.value.tree)
    || (preview.value && preview.value.tree) || tree.value);
  const index = computed(() => (shown.value ? indexTree(shown.value, usage.value) : null));
  const editIndex = computed(() => {
    if (!tree.value) return null;
    return shown.value === tree.value ? index.value : indexTree(tree.value, usage.value);
  });
  const readOnly = computed(() => Boolean(viewing.value || preview.value));
  const dirty = computed(() => Boolean(tree.value && base.value && !sameTree(tree.value, base.value.tree)));
  /** The length of the undo list when the tree was last saved (or loaded). */
  const savedMark = signal(0);
  /** How many steps separate the tree being edited from the saved one. */
  const unsaved = computed(() => (dirty.value ? Math.max(1, Math.abs(past.value.length - savedMark.value)) : 0));

  let chain = Promise.resolve();
  const serial = (fn) => {
    const next = chain.then(fn, fn);
    chain = next.catch(() => {});
    return next;
  };

  // The draft is written right after the change is on screen (a few milliseconds later),
  // and at once when the page is left or hidden.
  let pending = 0;
  function persist() {
    if (pending) return;
    pending = setTimeout(flush, 0);
  }
  function flush() {
    clearTimeout(pending);
    pending = 0;
    if (!tree.value || !base.value) return;
    if (!dirty.value) {
      writeDraft(projectId, null);
      return;
    }
    writeDraft(projectId, {
      v: 1,
      base_version: base.value.version,
      source: base.value.source,
      saved_at: new Date().toISOString(),
      tree: tree.value,
      past: past.value,
      future: future.value,
      saved_mark: savedMark.value,
    });
  }

  function setInfo(data) {
    const { tree: _t, ...rest } = data;
    info.value = rest;
  }

  /** Read the tree and the keywords' usage; restore a draft when there is one. */
  async function load() {
    loading.value = true;
    error.value = null;
    // The keywords' usage sizes the treemap; the tree shows without it (by keyword counts)
    // and takes it when it comes, so a cold start of the app does not hold the page.
    api.get('/api/themes/usage').then((used) => {
      if (used.ok) usage.value = used.data.terms || {};
      return used;
    });
    const themes = await api.get('/api/themes');
    if (!themes.ok) {
      batch(() => {
        error.value = themes.error;
        loading.value = false;
      });
      return false;
    }
    const data = themes.data;
    batch(() => {
      setInfo(data);
      if (data.tree) {
        base.value = { tree: data.tree, version: themes.etag || data.version, source: data.source };
        tree.value = data.tree;
      } else {
        base.value = null;
        tree.value = null;
      }
      past.value = [];
      future.value = [];
      savedMark.value = 0;
      loading.value = false;
    });
    if (data.tree) {
      const draft = readDraft(projectId);
      if (draft && !sameTree(draft.tree, data.tree)) {
        const count = (draft.past || []).length;
        if (draft.base_version === base.value.version) {
          batch(() => {
            tree.value = draft.tree;
            past.value = (draft.past || []).filter((e) => e.patch);
            future.value = (draft.future || []).filter((e) => e.patch);
            savedMark.value = Math.min(draft.saved_mark || 0, past.value.length);
            restored.value = { at: draft.saved_at, stale: false, count };
          });
        } else {
          restored.value = { at: draft.saved_at, stale: true, count, draft };
        }
      } else if (draft) {
        writeDraft(projectId, null);
      }
    }
    return true;
  }

  /** Read the tree's state again (after an apply, a rebase elsewhere) without losing edits. */
  async function refreshInfo() {
    const themes = await api.get('/api/themes');
    if (themes.ok) setInfo(themes.data);
    return themes;
  }

  function record(before, after, ops, descriptions, label, labelKey = null) {
    const patch = diffTree(before, after);
    if (!Object.keys(patch.whole).length && !Object.keys(patch.dicts).length) return false;
    const names = namesFor(descriptions, before);
    const namesAfter = namesFor(descriptions, after);
    batch(() => {
      past.value = [...past.value, { label, labelKey, descriptions, names, namesAfter, ops, patch, at: Date.now() }];
      future.value = [];
      tree.value = after;
      opError.value = null;
    });
    persist();
    return true;
  }

  /**
   * Apply *ops* to the tree being edited; resolves to `{ok, steps}`. A refused
   * operation changes nothing and sets `opError` (shown by the page).
   */
  function run(ops, { label, labelKey = null, lenient = false } = {}) {
    return serial(async () => {
      if (!tree.value || readOnly.value) return { ok: false };
      busy.value = true;
      const before = tree.value;
      // The change shows at once where it can; the server's tree follows and is the one kept.
      const guess = lenient ? null : optimistic(before, ops);
      if (guess) tree.value = guess;
      const result = await api.post('/api/themes/ops', { tree: before, ops, lenient });
      busy.value = false;
      if (!result.ok) {
        if (guess) tree.value = before;
        opError.value = result.error;
        return { ok: false, error: result.error };
      }
      const { steps } = result.data;
      const done = steps.filter((s) => s.description);
      const descriptions = done.map((s) => s.description);
      const kept = ops.filter((_, i) => steps[i] && steps[i].description);
      const name = label || descriptions.join('; ');
      const changed = record(before, result.data.tree, kept, descriptions, name, labelKey);
      if (!changed && guess) tree.value = before;
      if (changed) announce(t('themes.announce.done', { what: entryLabel(past.value[past.value.length - 1]) }));
      return { ok: true, steps, changed };
    });
  }

  function undo() {
    return serial(async () => {
      const list = past.value;
      if (!list.length || readOnly.value) return false;
      const entry = list[list.length - 1];
      batch(() => {
        tree.value = applyPatch(tree.value, entry.patch, true);
        past.value = list.slice(0, -1);
        future.value = [...future.value, entry];
      });
      persist();
      announce(t('themes.announce.undone', { what: entryLabel(entry) }));
      return true;
    });
  }

  function redo() {
    return serial(async () => {
      const list = future.value;
      if (!list.length || readOnly.value) return false;
      const entry = list[list.length - 1];
      batch(() => {
        tree.value = applyPatch(tree.value, entry.patch);
        future.value = list.slice(0, -1);
        past.value = [...past.value, entry];
      });
      persist();
      announce(t('themes.announce.redone', { what: entryLabel(entry) }));
      return true;
    });
  }

  /** Forget the edits: back to the saved tree (or the proposal). */
  function discard() {
    batch(() => {
      if (base.value) tree.value = base.value.tree;
      past.value = [];
      future.value = [];
      savedMark.value = 0;
      restored.value = null;
      preview.value = null;
    });
    writeDraft(projectId, null);
  }

  /** The action name of a save: the undo list's descriptions since the last save. */
  function actionName() {
    const names = past.value.slice(Math.min(savedMark.value, past.value.length)).map((e) => e.label).filter(Boolean);
    if (!names.length) return base.value && base.value.source === 'draft' ? 'save the proposal' : 'save';
    const text = names.join('; ');
    return text.length <= 200 ? text : `${names.length} changes: ${text}`.slice(0, 199) + '…';
  }

  /**
   * Save the tree as a new version (`If-Match`: the version it started from).
   * Resolves to `{ok}`, `{ok: false, stale: true}` on a 412, or the error.
   */
  function save() {
    return serial(async () => {
      if (!tree.value) return { ok: false };
      busy.value = true;
      const before = tree.value;
      const result = await api.put('/api/themes', { tree: before, action: actionName() },
        { ifMatch: base.value.version });
      busy.value = false;
      if (!result.ok) {
        if (result.kind === 'stale') return { ok: false, stale: true, error: result.error };
        return { ok: false, error: result.error };
      }
      const saved = result.data;
      batch(() => {
        if (saved.removed.length) {
          // The save removed empty nodes: that is a step of its own, undone like the others.
          const patch = diffTree(before, saved.tree);
          const description = `remove empty ${saved.removed.length === 1 ? 'node' : 'nodes'} ${saved.removed.join(', ')}`;
          past.value = [...past.value, { label: description, descriptions: [description],
            names: namesFor([description], before), ops: [{ op: 'prune_empty' }], patch, at: Date.now() }];
        }
        tree.value = saved.tree;
        savedMark.value = past.value.length;
        base.value = { tree: saved.tree, version: result.etag || saved.version, source: 'saved' };
        lastSaved.value = { removed: saved.removed, action: saved.action, written: saved.written };
        restored.value = null;
      });
      persist();
      refreshInfo();
      return { ok: true, saved };
    });
  }

  /**
   * Apply the operations of *entries* (undo entries, in order) again to the
   * newest saved tree; the ones that no longer apply are returned.
   */
  async function reloadAndMerge(given = null) {
    // Only the steps since the last save: the saved version holds the others already.
    const entries = given || past.value.slice(Math.min(savedMark.value, past.value.length));
    return serial(async () => {
      const themes = await api.get('/api/themes');
      if (!themes.ok) return { ok: false, error: themes.error };
      const newest = themes.data.tree;
      const ops = [];
      const owner = [];
      entries.forEach((entry, i) => {
        for (const op of entry.ops || []) {
          ops.push(op);
          owner.push(i);
        }
      });
      let merged = newest;
      const refused = [];
      if (ops.length && newest) {
        const result = await api.post('/api/themes/ops', { tree: newest, ops, lenient: true });
        if (!result.ok) return { ok: false, error: result.error };
        merged = result.data.tree;
        result.data.steps.forEach((step, k) => {
          if (step.refused) refused.push({ entry: entries[owner[k]], reason: step.refused });
        });
      }
      batch(() => {
        setInfo(themes.data);
        base.value = newest ? { tree: newest, version: themes.etag || themes.data.version,
          source: themes.data.source } : null;
        tree.value = merged;
        const patch = merged && newest ? diffTree(newest, merged) : null;
        past.value = patch && (Object.keys(patch.whole).length || Object.keys(patch.dicts).length)
          ? [{ label: `reload and merge ${entries.length} changes`, descriptions: [],
            labelKey: { key: 'themes.merge.entry', params: { count: entries.length } },
            ops: entries.flatMap((e) => e.ops || []), patch, at: Date.now() }] : [];
        future.value = [];
        savedMark.value = 0;
        restored.value = null;
      });
      persist();
      return { ok: true, refused, applied: ops.length - refused.length, total: ops.length };
    });
  }

  /** Make the tree saved elsewhere the base, and drop the edits (after a restore or a rebase). */
  function adopt(data, etag) {
    batch(() => {
      setInfo(data);
      base.value = data.tree ? { tree: data.tree, version: etag || data.version, source: data.source || 'saved' } : null;
      tree.value = data.tree;
      past.value = [];
      future.value = [];
      savedMark.value = 0;
      restored.value = null;
      viewing.value = null;
      preview.value = null;
    });
    writeDraft(projectId, null);
  }

  return {
    loading, error, info, base, tree, past, future, usage, busy, restored, viewing, preview,
    lastSaved, opError, shown, index, editIndex, readOnly, dirty, unsaved, savedMark,
    load, refreshInfo, run, undo, redo, discard, save, reloadAndMerge, adopt, persist, flush, actionName,
  };
}

// ── dialogs ─────────────────────────────────────────────────────────────────

/**
 * The theme editor's dialogs: one per action that needs a choice or a name
 * (rename, move to, merge with, split, create, set aside, attribution, the
 * levels), and the reports (a comparison, what a merge could not re-apply).
 *
 * Every dialog is a form: Enter submits, Escape cancels, the focus starts on
 * the first field and goes back where it was when the dialog closes.
 */

/** A dialog holding a form: `onSubmit` returns a promise of `true` (done) or an error text. */
function FormDialog({ open, title, description, submitLabel, danger = false, onClose, onSubmit,
  size = 'm', disabled = false, children }) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const form = useUid('cx-themes-form');
  const submit = async (event) => {
    event.preventDefault();
    if (busy || disabled) return;
    setBusy(true);
    setProblem('');
    const outcome = await onSubmit();
    setBusy(false);
    if (outcome === true) onClose('done');
    else if (outcome) setProblem(outcome);
  };
  return html`<${Dialog} open=${open} size=${size} title=${title} description=${description}
    onClose=${onClose}
    footer=${html`<${Button} variant="ghost" onClick=${() => onClose('close')}>${t('common.cancel')}<//>
      <${Button} variant=${danger ? 'danger' : 'primary'} type="submit" form=${form} loading=${busy}
        disabled=${disabled}>${submitLabel}<//>`}>
    <form id=${form} class="cx-themes-form" onSubmit=${submit} novalidate>
      ${children}
      ${problem ? html`<p class="cx-themes-form__problem" role="alert">
        <${Icon} name="warning" /><span>${problem}</span></p>` : null}
    </form>
  <//>`;
}

/** The label of a node for a list: its path of names, from the top. */
export function nodePath(index, id) {
  const lang = lang2(locale.value);
  return pathOf(index, id).map((nid) => nodeName(index.nodes.get(nid), lang)).join(' › ');
}

/** Name fields, one per language: `names` in, `onChange(names)` out. */
function NameFields({ names, languages, onChange }) {
  return html`<div class="cx-themes-form__names">
    ${languages.map((code) => html`<${FormField} key=${code}
      label=${t('themes.name.in', { language: languageName(code) })}>
      ${(field) => html`<${Input} ...${field} lang=${code} value=${names[code] || ''}
        autocomplete="off" spellcheck="true"
        onInput=${(e) => onChange({ ...names, [code]: e.currentTarget.value })} />`}
    <//>`)}
  </div>`;
}

function cleanNames(names, before = {}) {
  const out = {};
  for (const [code, value] of Object.entries(names)) {
    const v = String(value || '').trim();
    if (v) out[code] = v;
    else if (before[code]) out[code] = null;
  }
  return out;
}

/** Rename a node, in every language it may be named in. */
export function RenameDialog({ open, node, levelLabel, onClose, onSubmit }) {
  const [names, setNames] = useState(() => ({ ...(node ? node.names : {}) }));
  const languages = useMemo(() => nameLanguages(node && node.names), [node, locale.value]);
  const empty = !Object.values(names).some((v) => String(v || '').trim());
  return html`<${FormDialog} open=${open} onClose=${onClose}
    title=${t('themes.rename.title', { name: nodeName(node, lang2(locale.value)) })}
    description=${levelLabel ? t('themes.rename.lead', { level: levelLabel }) : undefined}
    submitLabel=${t('themes.rename.submit')} disabled=${empty}
    onSubmit=${() => onSubmit(cleanNames(names, node && node.names))}>
    <${NameFields} names=${names} languages=${languages} onChange=${setNames} />
  <//>`;
}

/** Rename a level of the tree. */
export function RenameLevelDialog({ open, tree, level, onClose, onSubmit }) {
  const current = (tree.levels[level - 1] || {}).names || {};
  const [names, setNames] = useState(() => ({ ...current }));
  const languages = useMemo(() => nameLanguages(current), [level, locale.value]);
  const empty = !Object.values(names).some((v) => String(v || '').trim());
  return html`<${FormDialog} open=${open} onClose=${onClose}
    title=${t('themes.level.rename.title', { name: levelName(tree, level, lang2(locale.value)), level })}
    description=${t('themes.level.rename.lead')} submitLabel=${t('themes.rename.submit')}
    disabled=${empty} onSubmit=${() => onSubmit(cleanNames(names, current))}>
    <${NameFields} names=${names} languages=${languages} onChange=${setNames} />
  <//>`;
}

/**
 * Pick one node among *candidates*: a filter field and a list, both driven by
 * the keyboard (arrows in the field move in the list, Enter picks).
 */
export function PickNodeDialog({ open, title, description, submitLabel, index, candidates,
  onClose, onSubmit, extra = null }) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(candidates[0] || null);
  const listId = useUid('cx-themes-pick');
  const lang = lang2(locale.value);
  const shown = useMemo(() => {
    const words = fold(query).split(/\s+/).filter(Boolean);
    return candidates.filter((id) => {
      if (!words.length) return true;
      const text = fold(nodePath(index, id));
      return words.every((w) => text.includes(w));
    });
  }, [query, candidates, index]);
  const current = shown.includes(active) ? active : shown[0] || null;
  const move = (delta) => {
    if (!shown.length) return;
    const i = Math.max(0, shown.indexOf(current));
    const next = shown[Math.max(0, Math.min(shown.length - 1, i + delta))];
    setActive(next);
    const el = document.getElementById(`${listId}-${next}`);
    if (el) el.scrollIntoView({ block: 'nearest' });
  };
  return html`<${FormDialog} open=${open} onClose=${onClose} title=${title} description=${description}
    submitLabel=${submitLabel} disabled=${!current} onSubmit=${() => onSubmit(current)}>
    <${FormField} label=${t('themes.pick.filter')}>
      ${(field) => html`<${Input} ...${field} type="search" value=${query} autocomplete="off"
        role="combobox" aria-expanded="true" aria-controls=${listId}
        aria-activedescendant=${current ? `${listId}-${current}` : undefined}
        onInput=${(e) => setQuery(e.currentTarget.value)}
        onKeyDown=${(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            move(1);
          } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            move(-1);
          } else if (e.key === 'PageDown') {
            e.preventDefault();
            move(8);
          } else if (e.key === 'PageUp') {
            e.preventDefault();
            move(-8);
          }
        }} />`}
    <//>
    <ul class="cx-themes-pick" id=${listId} role="listbox" aria-label=${title} hidden=${!shown.length}>
      ${shown.slice(0, 400).map((id) => {
        const node = index.nodes.get(id);
        return html`<li key=${id} id=${`${listId}-${id}`} role="option"
          aria-selected=${String(id === current)}
          class=${`cx-themes-pick__option ${id === current ? 'is-active' : ''}`}
          onClick=${() => setActive(id)}>
          <span class="cx-themes-chip" style=${{ '--cx-chip': `var(--cx-hue-${(index.hue.get(id) % 12) + 1})` }}></span>
          <span class="cx-themes-pick__name">${nodeName(node, lang)}</span>
          <span class="cx-themes-pick__path">${pathOf(index, id).length > 1 ? nodePath(index, node.parent) : levelName(index.tree, 1, lang)}</span>
          <span class="cx-themes-pick__count">${formatNumber(index.under.get(id))}</span>
        </li>`;
      })}
    </ul>
    ${!shown.length ? html`<p class="cx-themes-form__hint">${t('themes.pick.none')}</p>` : null}
    ${extra}
  <//>`;
}

/** Split a node: tick the keywords and child nodes that go to a new node beside it. */
export function SplitDialog({ open, index, id, onClose, onSubmit }) {
  const lang = lang2(locale.value);
  const node = index.nodes.get(id);
  const kids = index.children.get(id) || [];
  const own = index.keywordsOn.get(id) || [];
  const [picked, setPicked] = useState(() => new Set());
  const [names, setNames] = useState({});
  const [filter, setFilter] = useState('');
  const languages = useMemo(() => nameLanguages(node.names), [id, locale.value]);
  const total = kids.length + own.length;
  const words = fold(filter).split(/\s+/).filter(Boolean);
  const visible = own.filter((k) => !words.length || words.every((w) => fold(k).includes(w)));
  const toggle = (member) => {
    const next = new Set(picked);
    if (next.has(member)) next.delete(member);
    else next.add(member);
    setPicked(next);
  };
  const named = Object.values(names).some((v) => String(v || '').trim());
  const problem = !picked.size ? t('themes.split.none')
    : picked.size >= total ? t('themes.split.all') : !named ? t('themes.split.unnamed') : '';
  return html`<${FormDialog} open=${open} size="l" onClose=${onClose}
    title=${t('themes.split.title', { name: nodeName(node, lang) })}
    description=${t('themes.split.lead')} submitLabel=${t('themes.split.submit', { count: picked.size })}
    disabled=${Boolean(problem)}
    onSubmit=${() => onSubmit([...picked], cleanNames(names))}>
    <${NameFields} names=${names} languages=${languages.slice(0, 1)} onChange=${setNames} />
    <p class="cx-themes-form__hint" aria-live="polite">${problem || t('themes.split.count', { count: picked.size, total })}</p>
    ${kids.length ? html`<fieldset class="cx-themes-form__set">
      <legend>${t('themes.split.nodes', { level: levelName(index.tree, index.level.get(id) + 1, lang) })}</legend>
      <div class="cx-themes-form__checks">
        ${kids.map((kid) => html`<${Checkbox} key=${kid} checked=${picked.has(kid)}
          label=${`${nodeName(index.nodes.get(kid), lang)} (${formatNumber(index.under.get(kid))})`}
          onChange=${() => toggle(kid)} />`)}
      </div>
    </fieldset>` : null}
    ${own.length ? html`<fieldset class="cx-themes-form__set">
      <legend>${t('themes.split.keywords', { count: own.length })}</legend>
      ${own.length > 12 ? html`<${FormField} label=${t('themes.split.filter')}>
        ${(field) => html`<${Input} ...${field} type="search" value=${filter}
          onInput=${(e) => setFilter(e.currentTarget.value)} />`}
      <//>` : null}
      <div class="cx-themes-form__checks cx-themes-form__checks--scroll">
        ${visible.slice(0, 500).map((k) => html`<${Checkbox} key=${k} checked=${picked.has(k)}
          label=${`${k} (${formatNumber(index.people(k))})`} onChange=${() => toggle(k)} />`)}
      </div>
    </fieldset>` : null}
  <//>`;
}

/** Name a new node (under *parent*, or on the top level). */
export function CreateDialog({ open, index, parent, onClose, onSubmit }) {
  const lang = lang2(locale.value);
  const [names, setNames] = useState({});
  const level = parent ? index.level.get(parent) + 1 : 1;
  const languages = useMemo(() => nameLanguages(), [locale.value]);
  const named = Object.values(names).some((v) => String(v || '').trim());
  return html`<${FormDialog} open=${open} onClose=${onClose}
    title=${parent ? t('themes.create.title_in', { name: nodeName(index.nodes.get(parent), lang) })
      : t('themes.create.title_top', { level: levelName(index.tree, 1, lang) })}
    description=${t('themes.create.lead', { level: levelName(index.tree, level, lang) })}
    submitLabel=${t('themes.create.submit')} disabled=${!named}
    onSubmit=${() => onSubmit(cleanNames(names))}>
    <${NameFields} names=${names} languages=${languages.slice(0, 1)} onChange=${setNames} />
  <//>`;
}

const REASONS = ['general', 'field', 'broken', 'other'];

/** Set keywords aside, with a reason (kept with them, shown in the « Set aside » tray). */
export function SetAsideDialog({ open, terms, current = '', onClose, onSubmit }) {
  const [kind, setKind] = useState(current ? 'other' : 'general');
  const [text, setText] = useState(current);
  const group = useUid('cx-themes-reason');
  const reason = kind === 'other' ? text.trim() : t(`themes.aside.reason.${kind}`);
  return html`<${FormDialog} open=${open} onClose=${onClose}
    title=${t('themes.aside.title', { count: terms.length, term: terms[0] })}
    description=${t('themes.aside.lead')} submitLabel=${t('themes.aside.submit', { count: terms.length })}
    onSubmit=${() => onSubmit(reason)}>
    <fieldset class="cx-themes-form__set">
      <legend>${t('themes.aside.reason')}</legend>
      <div class="cx-themes-form__radios" role="radiogroup">
        ${REASONS.map((r) => html`<label class="cx-themes-radio" key=${r}>
          <input type="radio" name=${group} value=${r} checked=${kind === r}
            onChange=${() => setKind(r)} />
          <span>${t(`themes.aside.reason.${r}`)}</span>
        </label>`)}
      </div>
    </fieldset>
    ${kind === 'other' ? html`<${FormField} label=${t('themes.aside.own')}>
      ${(field) => html`<${Textarea} ...${field} rows=${2} value=${text}
        onInput=${(e) => setText(e.currentTarget.value)} />`}
    <//>` : null}
  <//>`;
}

/** How many levels, from the top, keywords count toward. */
export function AttributionDialog({ open, index, terms, onClose, onSubmit }) {
  const lang = lang2(locale.value);
  const tree = index.tree;
  const levels = terms.map((k) => index.level.get(tree.keywords[k]) || 1);
  const lowest = Math.min(...levels);
  const currents = new Set(terms.map((k) => (k in (tree.attribution || {}) ? String(tree.attribution[k]) : 'default')));
  const [choice, setChoice] = useState(currents.size === 1 ? [...currents][0] : 'default');
  const group = useUid('cx-themes-attr');
  const options = [
    { value: 'default', label: t('themes.attr.default'), help: t('themes.attr.default.help') },
    ...Array.from({ length: Math.max(0, lowest - 1) }, (_, i) => ({
      value: String(i + 1),
      label: t('themes.attr.level', { level: levelName(tree, i + 1, lang), count: i + 1 }),
      help: t('themes.attr.level.help', { level: levelName(tree, i + 1, lang) }),
    })),
    { value: '0', label: t('themes.attr.none'), help: t('themes.attr.none.help') },
  ];
  return html`<${FormDialog} open=${open} onClose=${onClose}
    title=${t('themes.attr.title', { count: terms.length, term: terms[0] })}
    description=${t('themes.attr.lead')} submitLabel=${t('themes.attr.submit')}
    onSubmit=${() => onSubmit(choice === 'default' ? null : Number(choice))}>
    <div class="cx-themes-form__radios cx-themes-form__radios--stack" role="radiogroup"
      aria-label=${t('themes.attr.title', { count: terms.length, term: terms[0] })}>
      ${options.map((o) => html`<label class="cx-themes-radio cx-themes-radio--wide" key=${o.value}>
        <input type="radio" name=${group} value=${o.value} checked=${choice === o.value}
          onChange=${() => setChoice(o.value)} />
        <span><span class="cx-themes-radio__label">${o.label}</span>
          <span class="cx-themes-radio__help">${o.help}</span></span>
      </label>`)}
    </div>
  <//>`;
}

/** Insert a level: where, and for a new top level, the name of its one node. */
export function InsertLevelDialog({ open, tree, onClose, onSubmit }) {
  const lang = lang2(locale.value);
  const depth = tree.depth;
  const [at, setAt] = useState(depth + 1);
  const group = useUid('cx-themes-insert');
  const positions = Array.from({ length: depth + 1 }, (_, i) => i + 1).map((pos) => ({
    value: pos,
    label: pos === 1 ? t('themes.level.insert.top', { name: levelName(tree, 1, lang) })
      : pos === depth + 1 ? t('themes.level.insert.bottom', { name: levelName(tree, depth, lang) })
        : t('themes.level.insert.between', { above: levelName(tree, pos - 1, lang), below: levelName(tree, pos, lang) }),
  }));
  return html`<${FormDialog} open=${open} onClose=${onClose} title=${t('themes.level.insert.title')}
    description=${t('themes.level.insert.lead')} submitLabel=${t('themes.level.insert.submit')}
    disabled=${depth >= 4} onSubmit=${() => onSubmit(at)}>
    ${depth >= 4 ? html`<p class="cx-themes-form__hint">${t('themes.level.insert.full')}</p>` : html`
    <div class="cx-themes-form__radios cx-themes-form__radios--stack" role="radiogroup"
      aria-label=${t('themes.level.insert.title')}>
      ${positions.map((p) => html`<label class="cx-themes-radio cx-themes-radio--wide" key=${p.value}>
        <input type="radio" name=${group} checked=${at === p.value} onChange=${() => setAt(p.value)} />
        <span class="cx-themes-radio__label">${p.label}</span>
      </label>`)}
    </div>
    <p class="cx-themes-form__hint">${at === 1 ? t('themes.level.insert.top.help')
      : at === depth + 1 ? t('themes.level.insert.bottom.help') : t('themes.level.insert.between.help')}</p>`}
  <//>`;
}

/** Remove a level: its nodes dissolve into their parents. */
export function RemoveLevelDialog({ open, tree, onClose, onSubmit }) {
  const lang = lang2(locale.value);
  const depth = tree.depth;
  const [at, setAt] = useState(depth);
  const group = useUid('cx-themes-remove');
  return html`<${FormDialog} open=${open} danger onClose=${onClose}
    title=${t('themes.level.remove.title')} description=${t('themes.level.remove.lead')}
    submitLabel=${t('themes.level.remove.submit', { name: levelName(tree, at, lang) })}
    disabled=${depth <= 1} onSubmit=${() => onSubmit(at)}>
    ${depth <= 1 ? html`<p class="cx-themes-form__hint">${t('themes.level.remove.last')}</p>` : html`
    <div class="cx-themes-form__radios cx-themes-form__radios--stack" role="radiogroup"
      aria-label=${t('themes.level.remove.title')}>
      ${tree.levels.map((_, i) => html`<label class="cx-themes-radio cx-themes-radio--wide" key=${i}>
        <input type="radio" name=${group} checked=${at === i + 1} onChange=${() => setAt(i + 1)} />
        <span class="cx-themes-radio__label">${t('themes.level.numbered', { level: i + 1, name: levelName(tree, i + 1, lang) })}</span>
      </label>`)}
    </div>
    <p class="cx-themes-form__hint">${at === 1 ? t('themes.level.remove.top.help') : t('themes.level.remove.help')}</p>`}
  <//>`;
}

const GROUPS = [
  ['nodes', ['node_added', 'node_removed', 'node_renamed', 'node_moved', 'node_reordered', 'node_changed']],
  ['keywords', ['added', 'removed', 'moved', 'set_aside_changed', 'attribution', 'review']],
  ['levels', ['depth', 'level_renamed']],
];

/**
 * What changed between two trees (`POST /api/themes/compare`), grouped:
 * nodes, keywords, levels. *names* names the nodes of both trees.
 */
export function ChangeList({ result, names }) {
  const lang = lang2(locale.value);
  const name = (id) => {
    if (id === '(set aside)') return t('themes.compare.aside');
    const n = names.get(id);
    return n ? nodeName(n, lang) : id;
  };
  if (!result) return null;
  if (!result.total) return html`<p class="cx-themes-form__hint">${t('themes.compare.same')}</p>`;
  const counts = result.counts || {};
  return html`<div class="cx-themes-changes">
    <ul class="cx-themes-changes__counts">
      ${Object.entries(counts).map(([kind, n]) => html`<li key=${kind}>
        <span class="cx-themes-changes__n">${formatNumber(n)}</span>
        <span>${t(`themes.change.${kind}`, { count: n })}</span></li>`)}
    </ul>
    ${GROUPS.map(([group, kinds]) => {
      const items = result.changes.filter((c) => kinds.includes(c.kind));
      if (!items.length) return null;
      return html`<section key=${group} class="cx-themes-changes__group">
        <h3 class="cx-themes-changes__title">${t(`themes.compare.group.${group}`)}</h3>
        <ul class="cx-themes-changes__list">
          ${items.slice(0, 300).map((c, i) => html`<li key=${i}>${changeText(c, name, lang)}</li>`)}
        </ul>
        ${items.length > 300 ? html`<p class="cx-themes-form__hint">${t('themes.compare.more', { count: items.length - 300 })}</p>` : null}
      </section>`;
    })}
  </div>`;
}

function changeText(c, name, lang) {
  const nm = (names) => nodeName({ id: c.node, names: names || {} }, lang);
  switch (c.kind) {
    case 'added': return t('themes.change.text.added', { keyword: c.keyword, place: name(c.after) });
    case 'removed': return t('themes.change.text.removed', { keyword: c.keyword, place: name(c.before) });
    case 'moved': return t('themes.change.text.moved', { keyword: c.keyword, from: name(c.before), to: name(c.after) });
    case 'node_renamed': return t('themes.change.text.renamed', { from: nm(c.before), to: nm(c.after) });
    case 'node_added': return t('themes.change.text.node_added', { name: name(c.node) });
    case 'node_removed': return t('themes.change.text.node_removed', { name: name(c.node) });
    case 'node_moved': return t('themes.change.text.node_moved', { name: name(c.node), to: c.after ? name(c.after) : t('themes.compare.top') });
    case 'attribution': return t('themes.change.text.attribution', { keyword: c.keyword });
    case 'review': return t('themes.change.text.review', { keyword: c.keyword });
    case 'set_aside_changed': return t('themes.change.text.aside', { keyword: c.keyword });
    case 'level_renamed': return t('themes.change.text.level', { level: c.level });
    case 'depth': return t('themes.change.text.depth', { before: c.before, after: c.after });
    default: return t('themes.change.text.other', { name: name(c.node || '') });
  }
}

/** A dialog showing a comparison, with optional actions. */
export function CompareDialog({ open, title, description, result, names, onClose, footer }) {
  return html`<${Dialog} open=${open} size="l" title=${title} description=${description}
    onClose=${onClose} footer=${footer || html`<${Button} onClick=${() => onClose('close')}>
      ${t('common.close')}<//>`}>
    ${result ? html`<${ChangeList} result=${result} names=${names} />`
      : html`<p class="cx-themes-form__hint">${t('common.loading')}</p>`}
  <//>`;
}

/** What « reload and merge » could not apply again, and why. */
export function MergeReportDialog({ open, report, onClose }) {
  const refused = (report && report.refused) || [];
  return html`<${Dialog} open=${open} size="m" title=${t('themes.merge.report.title')}
    description=${t('themes.merge.report.lead', { applied: report ? report.applied : 0, total: report ? report.total : 0 })}
    onClose=${onClose} footer=${html`<${Button} variant="primary" onClick=${() => onClose('close')}>
      ${t('common.close')}<//>`}>
    ${refused.length ? html`<ul class="cx-themes-changes__list">
      ${refused.map((r, i) => html`<li key=${i}><strong>${entryLabel(r.entry)}</strong>
        <span class="cx-themes-form__hint">${r.reason}</span></li>`)}
    </ul>` : html`<p>${t('themes.merge.report.all')}</p>`}
  <//>`;
}

// ── outline ─────────────────────────────────────────────────────────────────

/**
 * The outline: the tree at any depth, its keywords on demand, a search over
 * every keyword, and two trays: the keywords set aside and the « to check »
 * queue a rebase fills.
 */

/** A text with the parts that match the search marked. */
function Marked({ text, words }) {
  if (!words) return text;
  return highlight(text, words).map((p, i) => (p.match
    ? html`<mark key=${i} class="cx-themes-mark">${p.text}</mark>` : p.text));
}

/** The rows of the outline: nodes, and the keywords of the nodes that are open. */
export function outlineRows(index, open, found, lang) {
  const rows = [];
  const visit = (parent, level) => {
    const kids = (index.children.get(parent) || []).filter((id) => !found || found.shown.has(id));
    const own = parent === null ? [] : (index.keywordsOn.get(parent) || [])
      .filter((k) => !found || found.keywords.has(k));
    const size = kids.length + own.length;
    kids.forEach((id, i) => {
      const hasContent = index.children.get(id).length + index.keywordsOn.get(id).length > 0;
      const isOpen = hasContent && open.has(id);
      rows.push({
        key: `n:${id}`, kind: 'node', id, level, expandable: hasContent, expanded: isOpen,
        parent: parent === null ? null : `n:${parent}`, setsize: size, posinset: i + 1,
        text: nodeName(index.nodes.get(id), lang), multi: false,
      });
      if (isOpen) visit(id, level + 1);
    });
    own.forEach((term, j) => rows.push({
      key: `k:${term}`, kind: 'keyword', term, level, parent: `n:${parent}`,
      setsize: size, posinset: kids.length + j + 1, text: term, multi: true,
    }));
  };
  visit(null, 1);
  return rows;
}

/** What a search finds: matching keywords and nodes, and the nodes on their paths. */
export function searchFor(index, searchable, query) {
  const result = search(searchable, query);
  if (!result) return null;
  const keywords = new Set(result.keywords.filter((k) => index.tree.keywords[k] !== undefined));
  const aside = result.keywords.filter((k) => !(index.tree.keywords[k] !== undefined));
  const shown = new Set();
  const open = new Set();
  for (const k of keywords) {
    for (const id of pathOf(index, index.tree.keywords[k])) {
      shown.add(id);
      open.add(id);
    }
  }
  for (const id of result.nodes) {
    const path = pathOf(index, id);
    path.forEach((p) => shown.add(p));
    path.slice(0, -1).forEach((p) => open.add(p));
  }
  return { words: result.words, keywords, aside, nodes: new Set(result.nodes), shown, open };
}

function NodeRow({ index, row, words }) {
  const lang = lang2(locale.value);
  const node = index.nodes.get(row.id);
  const hue = (index.hue.get(row.id) % 12) + 1;
  const share = index.total ? index.weight.get(row.id) / index.total : 0;
  return html`<span class="cx-themes-row cx-themes-row--node">
    <span class=${`cx-themes-chip cx-themes-chip--l${Math.min(row.level, 4)}`}
      style=${{ '--cx-chip': `var(--cx-hue-${hue})` }} aria-hidden="true"></span>
    <span class="cx-themes-row__name"><${Marked} text=${nodeName(node, lang)} words=${words} /></span>
    <span class="cx-themes-row__count" title=${t('themes.outline.count', { count: index.under.get(row.id) })}>
      ${formatNumber(index.under.get(row.id))}</span>
    <span class="cx-themes-row__share">${shortShare(share)}</span>
  </span>`;
}

function KeywordRow({ index, term, words, detail = null }) {
  const review = (index.tree.review || {})[term];
  const attribution = (index.tree.attribution || {})[term];
  return html`<span class="cx-themes-row cx-themes-row--keyword">
    <span class="cx-themes-row__term"><${Marked} text=${term} words=${words} /></span>
    ${detail ? html`<span class="cx-themes-row__detail">${detail}</span>` : null}
    ${review === 'to_check' ? html`<span class="cx-themes-badge cx-themes-badge--check">
      ${t('themes.badge.to_check')}</span>` : null}
    ${attribution !== undefined ? html`<span class="cx-themes-badge">
      ${attribution === 0 ? t('themes.badge.nowhere') : t('themes.badge.counts', { level: attribution })}</span>` : null}
    <span class="cx-themes-row__count">${formatNumber(index.people(term))}</span>
  </span>`;
}

/**
 * The left column.
 * @param {object} props
 * @param {object} props.editor the editor store
 * @param {object} props.ui the page's view state (signals) and actions
 */
export function OutlinePane({ editor, ui }) {
  const index = editor.index.value;
  const lang = lang2(locale.value);
  const [query, setQuery] = useState('');
  const searchable = useMemo(() => (index ? searchIndex(index, lang) : null), [index, lang]);
  const started = performance.now();
  const found = useMemo(() => (index && query.trim() ? searchFor(index, searchable, query) : null),
    [index, searchable, query]);
  // A new search opens the path to every match; what is opened or closed then lasts for that search.
  const [searchOpen, setSearchOpen] = useState({ query: '', open: null });
  const open = found ? (searchOpen.query === query && searchOpen.open ? searchOpen.open : found.open)
    : ui.expanded.value;
  const rows = useMemo(() => (index ? outlineRows(index, open, found, lang) : []),
    [index, open, found, lang]);
  if (found) ui.lastSearchMs = performance.now() - started;

  if (!index) return null;
  const toggle = (row, expand) => {
    if (row.kind !== 'node') return;
    const set = new Set(open);
    if (expand) set.add(row.id);
    else set.delete(row.id);
    if (found) setSearchOpen({ query, open: set });
    else ui.expanded.value = set;
  };
  const tab = ui.leftTab.value;
  const asideCount = index.setAside.length;
  const checkCount = index.toCheck.length;

  const outline = html`<${TreeView} rows=${rows} label=${t('themes.outline.label')}
    class="cx-themes-outline__tree" treeRef=${ui.outlineRef}
    activeKey=${ui.active.value} onActiveChange=${(key) => ui.setActive(key)}
    selection=${ui.treeSel.value} onSelectionChange=${(keys) => ui.select(keys)}
    onToggle=${toggle} onOpen=${(row) => ui.open(row)} onRename=${(row) => ui.renameRow(row)}
    onDelete=${(keys) => ui.deleteKeys(keys)}
    rowMenu=${(keys) => ui.menuFor(keys)} onRowMenu=${(item, keys) => ui.onMenu(item, keys)}
    dragData=${editor.readOnly.value ? null : (keys) => ui.dragFor(keys)}
    canDrop=${(row, data) => row.kind === 'node' && ui.canDrop(row.id, data)}
    onDrop=${(row, data) => ui.drop(row.id, data)}
    renderRow=${(row) => (row.kind === 'node'
      ? html`<${NodeRow} index=${index} row=${row} words=${found && found.words} />`
      : html`<${KeywordRow} index=${index} term=${row.term} words=${found && found.words} />`)}
    empty=${found ? html`<p class="cx-themes-empty-line">${t('themes.search.none')}</p>` : null} />`;

  const asideRows = index.setAside.map((term, i) => ({
    key: `a:${term}`, kind: 'aside', term, level: 1, setsize: asideCount, posinset: i + 1,
    parent: null, text: term, multi: true,
  }));
  const aside = html`<${TreeView} rows=${asideRows} label=${t('themes.aside.tray')}
    class="cx-themes-outline__tree" treeRef=${ui.outlineRef}
    activeKey=${ui.active.value} onActiveChange=${(key) => ui.setActive(key)}
    selection=${ui.treeSel.value} onSelectionChange=${(keys) => ui.select(keys)}
    onOpen=${(row) => ui.open(row)} onDelete=${() => {}}
    rowMenu=${(keys) => ui.menuFor(keys)} onRowMenu=${(item, keys) => ui.onMenu(item, keys)}
    dragData=${editor.readOnly.value ? null : (keys) => ui.dragFor(keys)}
    renderRow=${(row) => {
      const entry = index.tree.set_aside[row.term] || {};
      return html`<${KeywordRow} index=${index} term=${row.term}
        detail=${entry.reason || t('themes.aside.no_reason')} />`;
    }}
    empty=${html`<${EmptyState} icon="check" title=${t('themes.aside.empty')}>
      ${t('themes.aside.empty.text')}<//>`} />`;

  const checkRows = index.toCheck.map((term, i) => ({
    key: `c:${term}`, kind: 'check', term, level: 1, setsize: checkCount, posinset: i + 1,
    parent: null, text: term, multi: false,
  }));
  const place = (term) => {
    const node = index.tree.keywords[term];
    return node !== undefined ? nodePath(index, node) : t('themes.check.aside');
  };
  const check = html`<div class="cx-themes-check">
    <p class="cx-themes-check__help" id="cx-themes-check-help">${t('themes.check.keys')}</p>
    <${TreeView} rows=${checkRows} label=${t('themes.check.label')} class="cx-themes-outline__tree"
      treeRef=${ui.outlineRef}
      activeKey=${ui.active.value} onActiveChange=${(key) => ui.setActive(key)}
      selection=${ui.treeSel.value} onSelectionChange=${(keys) => ui.select(keys)}
      onOpen=${(row) => ui.open(row)}
      onKeyCommand=${(event, row) => ui.checkKey(event, row)}
      rowMenu=${(keys) => ui.menuFor(keys)} onRowMenu=${(item, keys) => ui.onMenu(item, keys)}
      renderRow=${(row) => html`<${KeywordRow} index=${index} term=${row.term}
        detail=${t('themes.check.place', { place: place(row.term) })} />`}
      empty=${html`<${EmptyState} icon="check" title=${t('themes.check.empty')}>
        ${t('themes.check.empty.text')}<//>`} />
  </div>`;

  return html`<section class="cx-themes-outline" aria-label=${t('themes.outline.region')}>
    <div class="cx-themes-search" role="search">
      <label class="cx-visually-hidden" for="cx-themes-search">${t('themes.search.label')}</label>
      <${Input} id="cx-themes-search" type="search" value=${query} autocomplete="off"
        spellcheck="false" placeholder=${t('themes.search.placeholder')}
        class="cx-themes-search__input"
        onInput=${(e) => {
          setQuery(e.currentTarget.value);
          if (tab !== 'outline') ui.leftTab.value = 'outline';
        }}
        onKeyDown=${(e) => {
          if (e.key === 'Escape' && query) {
            e.preventDefault();
            setQuery('');
          } else if ((e.key === 'Enter' || e.key === 'ArrowDown') && rows.length) {
            e.preventDefault();
            const first = rows.find((r) => r.kind === 'keyword' && found && found.keywords.has(r.term))
              || rows.find((r) => r.kind === 'node' && found && found.nodes.has(r.id)) || rows[0];
            ui.setActive(first.key);
            ui.select(new Set([first.key]));
            if (ui.outlineRef.current) ui.outlineRef.current.focus();
          }
        }} />
      <p class="cx-themes-search__status" aria-live="polite">
        ${found ? t('themes.search.found', { keywords: found.keywords.size, nodes: found.nodes.size })
          : t('themes.search.total', { keywords: Object.keys(index.tree.keywords).length, nodes: index.order.length })}
        ${found && found.aside.length ? html` · <button type="button" class="cx-link-button"
          onClick=${() => {
            ui.leftTab.value = 'aside';
            ui.select(new Set(found.aside.map((k) => `a:${k}`)));
            ui.setActive(`a:${found.aside[0]}`);
          }}>${t('themes.search.aside', { count: found.aside.length })}</button>` : null}
      </p>
    </div>
    <div class="cx-themes-outline__tools">
      <${IconButton} icon="plus" size="s" label=${t('themes.outline.expand_all')}
        onClick=${() => ui.expandAll()} />
      <${IconButton} icon="dash" size="s" label=${t('themes.outline.collapse_all')}
        onClick=${() => ui.collapseAll()} />
      ${!editor.readOnly.value ? html`<${Button} size="s" variant="ghost" icon="plus"
        onClick=${() => ui.create(null)}>${t('themes.outline.new_top')}<//>` : null}
    </div>
    <${Tabs} class="cx-themes-outline__tabs" label=${t('themes.outline.tabs')}
      selected=${tab} onSelect=${(id) => {
        ui.leftTab.value = id;
      }}
      tabs=${[
        { id: 'outline', label: t('themes.tab.outline') },
        { id: 'aside', label: t('themes.tab.aside'), count: formatNumber(asideCount) },
        { id: 'check', label: t('themes.tab.check'), count: formatNumber(checkCount) },
      ]}
      panel=${(id) => (id === 'aside' ? aside : id === 'check' ? check : outline)} />
  </section>`;
}

// ── centre ──────────────────────────────────────────────────────────────────

/**
 * The centre of the theme editor: the treemap of the tree being edited, and
 * the map of people and keywords (from `GET /api/atlas`), coloured by
 * top-level node, with the selection highlighted.
 */

const NEUTRAL = 12; // palette index of what no top-level node holds

/** The palette of the map: one token per hue family, then a neutral grey. */
export const PALETTE = [...Array.from({ length: 12 }, (_, i) => `--cx-hue-${i + 1}`), '--cx-text-muted'];

/** The people's top-level node on the map (their largest usage share at level 1). */
function topNode(shares) {
  let best = null;
  let value = 0;
  for (const [id, share] of Object.entries((shares && shares[0]) || {})) {
    if (share > value) {
      best = id;
      value = share;
    }
  }
  return best;
}

/**
 * The map's scene from the atlas, coloured by the tree being edited: each
 * keyword by its top-level node now, each person by the top-level node of
 * their largest share at the last apply.
 */
export function mapScene(atlas, index, focus) {
  if (!atlas || !atlas.available || !index) return null;
  const hueOf = (node) => (node && index.nodes.has(node) ? index.hue.get(node) % 12 : NEUTRAL);
  const kws = atlas.keywords.filter((k) => k.x !== null && k.y !== null);
  const people = atlas.people.filter((p) => p.x !== null && p.y !== null);
  const kx = new Float32Array(kws.length);
  const ky = new Float32Array(kws.length);
  const kc = new Uint16Array(kws.length);
  const kh = new Uint8Array(kws.length);
  let khn = 0;
  const under = focus && focus.kind === 'node' ? new Set([focus.id]) : null;
  if (under) {
    for (const id of index.order) {
      const parent = index.nodes.get(id).parent;
      if (parent !== null && under.has(parent)) under.add(id);
    }
  }
  const terms = focus && focus.kind === 'keywords' ? new Set(focus.terms) : null;
  kws.forEach((k, i) => {
    kx[i] = k.x;
    ky[i] = k.y;
    const node = index.tree.keywords[k.term];
    kc[i] = node !== undefined ? hueOf(index.topOf.get(node)) : NEUTRAL;
    if ((under && node !== undefined && under.has(node)) || (terms && terms.has(k.term))) {
      kh[i] = 1;
      khn += 1;
    }
  });
  const px = new Float32Array(people.length);
  const py = new Float32Array(people.length);
  const pc = new Uint16Array(people.length);
  const ph = new Uint8Array(people.length);
  let phn = 0;
  const level = focus && focus.kind === 'node' ? index.level.get(focus.id) : 0;
  people.forEach((p, i) => {
    px[i] = p.x;
    py[i] = p.y;
    pc[i] = hueOf(topNode(p.shares));
    const share = level && p.shares[level - 1] ? p.shares[level - 1][focus.id] || 0 : 0;
    if (share >= 0.2 || (focus && focus.kind === 'person' && focus.id === p.person_id)) {
      ph[i] = 1;
      phn += 1;
    }
  });
  const labels = [];
  const lang = lang2(locale.value);
  for (const top of index.tops) {
    let sx = 0;
    let sy = 0;
    let n = 0;
    kws.forEach((k, i) => {
      const node = index.tree.keywords[k.term];
      if (node !== undefined && index.topOf.get(node) === top) {
        sx += kx[i];
        sy += ky[i];
        n += 1;
      }
    });
    if (n) labels.push({ x: sx / n, y: sy / n, text: nodeName(index.nodes.get(top), lang), weight: index.weight.get(top) });
  }
  labels.sort((a, b) => b.weight - a.weight);
  return {
    layers: [
      { id: 'keywords', x: kx, y: ky, color: kc, palette: PALETTE, radius: 3, alpha: 0.8,
        highlight: kh, highlightCount: khn, items: kws },
      { id: 'people', x: px, y: py, color: pc, palette: PALETTE, radius: 4, alpha: 0.95,
        highlight: ph, highlightCount: phn, items: people },
    ],
    labels,
    bounds: atlas.bounds,
  };
}

function Breadcrumb({ index, root, onZoom }) {
  const lang = lang2(locale.value);
  const path = root ? pathOf(index, root) : [];
  return html`<nav class="cx-themes-crumbs" aria-label=${t('themes.treemap.path')}>
    <ol>
      <li><button type="button" class="cx-link-button" aria-current=${root ? undefined : 'location'}
        onClick=${() => onZoom(null)}>${t('themes.treemap.all')}</button></li>
      ${path.map((id, i) => html`<li key=${id}>
        <button type="button" class="cx-link-button" aria-current=${i === path.length - 1 ? 'location' : undefined}
          onClick=${() => onZoom(id)}>${nodeName(index.nodes.get(id), lang)}</button>
      </li>`)}
    </ol>
  </nav>`;
}

/**
 * The centre column.
 * @param {object} props
 * @param {object} props.editor the editor store
 * @param {object} props.ui the page's view state and actions
 * @param {object|null} props.atlas `GET /api/atlas`
 */
export function CentrePane({ editor, ui, atlas, atlasError, onRetryAtlas }) {
  const index = editor.index.value;
  const lang = lang2(locale.value);
  const frame = useRef(null);
  ui.mapFrame = frame;
  const focus = ui.focus.value;
  const funcs = useMemo(() => {
    if (!index) return null;
    const useWeight = index.total > 0;
    const weightOf = (id) => (useWeight ? index.weight.get(id) : index.under.get(id)) || 0;
    return {
      childrenOf: (id) => index.children.get(id) || [],
      weightOf,
      ownWeightOf: (id) => {
        const kids = index.children.get(id) || [];
        return Math.max(0, weightOf(id) - kids.reduce((s, k) => s + weightOf(k), 0));
      },
      hueOf: (id) => index.hue.get(id) || 0,
      parentOf: (id) => (index.nodes.has(id) ? index.nodes.get(id).parent : null),
      nameOf: (id) => nodeName(index.nodes.get(id), lang),
      detailOf: (id) => shortShare(useWeight ? weightOf(id) / index.total : weightOf(id) / Math.max(1, Object.keys(index.tree.keywords).length)),
    };
  }, [index, lang]);
  const onMap = ui.centreTab.value === 'map';
  const scene = useMemo(() => (onMap ? mapScene(atlas, index, focus) : null), [onMap, atlas, index, focus]);
  if (!index) return null;
  const tab = ui.centreTab.value;
  const root = ui.zoom.value && index.nodes.has(ui.zoom.value) ? ui.zoom.value : null;
  const selectedNode = focus && focus.kind === 'node' ? focus.id : null;
  const marked = focus && focus.kind === 'keywords'
    ? new Set(focus.terms.map((k) => index.tree.keywords[k]).filter(Boolean)) : null;
  const status = selectedNode ? t('themes.treemap.selected', {
    name: funcs.nameOf(selectedNode), share: funcs.detailOf(selectedNode) }) : '';

  const treemap = html`<div class="cx-themes-treemap">
    <div class="cx-themes-treemap__bar">
      <${Breadcrumb} index=${index} root=${root} onZoom=${(id) => ui.zoom.value = id} />
      <div class="cx-themes-treemap__tools">
        <${IconButton} icon="plus" size="s" label=${t('themes.treemap.zoom_in')}
          disabled=${!selectedNode || !(index.children.get(selectedNode) || []).length}
          onClick=${() => {
            ui.zoom.value = selectedNode;
          }} />
        <${IconButton} icon="dash" size="s" label=${t('themes.treemap.zoom_out')} disabled=${!root}
          onClick=${() => {
            ui.zoom.value = index.nodes.get(root).parent;
          }} />
      </div>
    </div>
    ${index.order.length ? html`<${Treemap} class="cx-themes-treemap__map" root=${root}
      ...${funcs} selected=${selectedNode} marked=${marked}
      onSelect=${(id) => ui.openNode(id, { from: 'treemap' })}
      onZoom=${(id) => {
        ui.zoom.value = id;
      }}
      label=${t('themes.treemap.label')} status=${status}
      canDrop=${(id, data) => ui.canDrop(id, data)} onDrop=${(id, data) => ui.drop(id, data)} />`
      : html`<${EmptyState} icon="file" title=${t('themes.treemap.empty')}
        action=${editor.readOnly.value ? null : { label: t('themes.outline.new_top'), onClick: () => ui.create(null) }}>
        ${t('themes.treemap.empty.text')}<//>`}
    <p class="cx-themes-treemap__hint">${t('themes.treemap.keys')}</p>
  </div>`;

  let map = null;
  if (!onMap) {
    map = null; // drawn only when its tab is shown
  } else if (atlasError) {
    map = html`<${ErrorCard} error=${atlasError} onRetry=${onRetryAtlas} compact />`;
  } else if (!atlas) {
    map = html`<p class="cx-themes-empty-line" aria-busy="true">${t('common.loading')}</p>`;
  } else if (!atlas.available) {
    map = html`<${EmptyState} icon="file" title=${t('themes.map.none')}
      action=${{ label: t('themes.map.apply'), onClick: () => ui.saveAndApply() }}>
      ${t('themes.map.none.text')}<//>`;
  } else {
    const people = atlas.people.length;
    const hover = ui.mapHover.value;
    map = html`<div class="cx-themes-map">
      <div class="cx-themes-map__bar">
        <p class="cx-themes-map__lead">${t('themes.map.lead', { people, keywords: atlas.keywords.length })}</p>
        <div class="cx-themes-treemap__tools">
          <${IconButton} icon="plus" size="s" label=${t('themes.map.zoom_in')}
            onClick=${() => frame.current && frame.current.zoomBy(1.4)} />
          <${IconButton} icon="dash" size="s" label=${t('themes.map.zoom_out')}
            onClick=${() => frame.current && frame.current.zoomBy(1 / 1.4)} />
          <${Button} size="s" variant="ghost" onClick=${() => frame.current && frame.current.fit()}>
            ${t('themes.map.fit')}<//>
        </div>
      </div>
      <${MapFrame} class="cx-themes-map__frame" scene=${scene} frameRef=${frame}
        label=${t('themes.map.label')}
        status=${focus ? t('themes.map.status', { count: (scene.layers[0].highlightCount || 0) + (scene.layers[1].highlightCount || 0) }) : ''}
        onPick=${(hit) => ui.pickOnMap(hit, scene)}
        onHover=${(hit) => {
          ui.mapHover.value = hit ? (hit.layer === 'people'
            ? { kind: 'person', text: scene.layers[1].items[hit.index].name }
            : { kind: 'keyword', text: scene.layers[0].items[hit.index].term }) : null;
        }} />
      <p class="cx-themes-map__hover" aria-hidden="true">${hover ? hover.text : t('themes.map.keys')}</p>
      <ul class="cx-themes-legend" aria-label=${t('themes.map.legend')}>
        ${index.tops.map((id) => html`<li key=${id}>
          <button type="button" class=${`cx-themes-legend__item ${selectedNode === id ? 'is-selected' : ''}`}
            aria-pressed=${String(selectedNode === id)} onClick=${() => ui.openNode(id, { from: 'map' })}>
            <span class="cx-themes-chip" style=${{ '--cx-chip': `var(--cx-hue-${(index.hue.get(id) % 12) + 1})` }}
              aria-hidden="true"></span>
            ${nodeName(index.nodes.get(id), lang)}
          </button></li>`)}
      </ul>
      ${atlas.source ? html`<p class="cx-themes-map__note">${t('themes.map.note', { people: formatNumber(people) })}</p>` : null}
    </div>`;
  }

  return html`<section class="cx-themes-centre" aria-label=${t('themes.centre.region')}>
    <${Tabs} class="cx-themes-centre__tabs" label=${t('themes.centre.tabs')} selected=${tab}
      onSelect=${(id) => {
        ui.centreTab.value = id;
      }}
      tabs=${[{ id: 'treemap', label: t('themes.tab.treemap') }, { id: 'map', label: t('themes.tab.map') }]}
      panel=${(id) => (id === 'map' ? map : treemap)} />
  </section>`;
}

// ── panel ───────────────────────────────────────────────────────────────────

/**
 * The side panel: the selected node, keywords or person — what it holds, its
 * usage, the people who weigh most on it, its names, and its actions.
 */

function Path({ index, id, ui }) {
  const lang = lang2(locale.value);
  const path = pathOf(index, id);
  if (path.length < 2) return null;
  return html`<nav class="cx-themes-panel__path" aria-label=${t('themes.panel.path')}>
    <ol>${path.slice(0, -1).map((pid) => html`<li key=${pid}>
      <button type="button" class="cx-link-button" onClick=${() => ui.openNode(pid)}>
        ${nodeName(index.nodes.get(pid), lang)}</button></li>`)}</ol>
  </nav>`;
}

function Fact({ label, children }) {
  return html`<div class="cx-themes-facts__item"><dt>${label}</dt><dd>${children}</dd></div>`;
}

/** The people with the largest share on a node, from the last apply. */
function weighers(atlas, id, level, limit = 8) {
  if (!atlas || !atlas.available) return null;
  if (!atlas.nodes.some((n) => n.id === id)) return null;
  return atlas.people
    .map((p) => ({ p, share: (p.shares[level - 1] || {})[id] || 0 }))
    .filter((e) => e.share > 0)
    .sort((a, b) => b.share - a.share)
    .slice(0, limit);
}

function NodePanel({ editor, ui, atlas, id }) {
  const index = editor.index.value;
  const lang = lang2(locale.value);
  const node = index.nodes.get(id);
  const level = index.level.get(id);
  const own = index.keywordsOn.get(id) || [];
  const kids = index.children.get(id) || [];
  const under = index.under.get(id);
  const share = index.total ? index.weight.get(id) / index.total : 0;
  const readOnly = editor.readOnly.value;
  const people = weighers(atlas, id, level);
  const top = [...own];
  if (top.length < 30) {
    // A node with few keywords of its own: its most used keywords below it.
    const below = [];
    const walk = (nid) => {
      for (const kid of index.children.get(nid) || []) {
        below.push(...(index.keywordsOn.get(kid) || []));
        walk(kid);
      }
    };
    walk(id);
    below.sort((a, b) => index.weightOf(b) - index.weightOf(a));
    top.push(...below.slice(0, 30 - top.length));
  }
  const saved = editor.base.value && editor.base.value.tree.nodes.find((n) => n.id === id);
  const mapped = atlas && atlas.available ? atlas.nodes.find((n) => n.id === id) : null;
  const names = node.names || {};
  const before = saved && nodeName(saved, lang) !== nodeName(node, lang) ? nodeName(saved, lang) : null;
  const onMap = mapped && nodeName(mapped, lang) !== nodeName(node, lang) ? nodeName(mapped, lang) : null;
  const hue = (index.hue.get(id) % 12) + 1;
  return html`<div class="cx-themes-panel__body">
    <header class="cx-themes-panel__head">
      <p class="cx-themes-panel__kind">
        <span class="cx-themes-chip" style=${{ '--cx-chip': `var(--cx-hue-${hue})` }} aria-hidden="true"></span>
        ${t('themes.panel.level', { level: levelName(index.tree, level, lang), number: level })}</p>
      <h2 class="cx-themes-panel__title" id="cx-themes-panel-title">${nodeName(node, lang)}</h2>
      <${Path} index=${index} id=${id} ui=${ui} />
    </header>
    ${readOnly ? null : html`<div class="cx-themes-panel__actions">
      <${Button} size="s" onClick=${() => ui.rename(id)}>${t('themes.action.rename')}<//>
      <${Button} size="s" onClick=${() => ui.moveNode(id)} disabled=${level === 1 && !ui.canMoveNode(id)}>
        ${t('themes.action.move')}<//>
      <${Button} size="s" onClick=${() => ui.merge(id)}>${t('themes.action.merge')}<//>
      <${MenuButton} size="s" label=${t('themes.action.more')} items=${ui.nodeMenu(id, { panel: true })}
        onSelect=${(item) => ui.onMenu(item, [`n:${id}`])} />
    </div>`}
    <dl class="cx-themes-facts">
      <${Fact} label=${t('themes.panel.keywords')}>
        ${t('themes.panel.keywords.value', { count: under, own: own.length })}<//>
      <${Fact} label=${t('themes.panel.children', { level: levelName(index.tree, level + 1, lang) || '—' })}>
        ${formatNumber(kids.length)}<//>
      <${Fact} label=${t('themes.panel.share')}>${shortShare(share)}<//>
    </dl>
    <section class="cx-themes-panel__section" aria-labelledby="cx-themes-panel-kw">
      <h3 class="cx-themes-panel__subtitle" id="cx-themes-panel-kw">${own.length === top.length
        ? t('themes.panel.top_keywords') : t('themes.panel.top_keywords_under')}</h3>
      ${top.length ? html`<ul class="cx-themes-panel__keywords">
        ${top.map((term) => html`<li key=${term}>
          <button type="button" class="cx-themes-panel__keyword" onClick=${() => ui.openKeyword(term)}>
            <span>${term}</span>
            <span class="cx-themes-row__count" aria-label=${t('themes.panel.people_count', { count: index.people(term) })}>
              ${formatNumber(index.people(term))}</span>
          </button></li>`)}
      </ul>` : html`<p class="cx-themes-empty-line">${t('themes.panel.no_keywords')}</p>`}
    </section>
    <section class="cx-themes-panel__section" aria-labelledby="cx-themes-panel-people">
      <h3 class="cx-themes-panel__subtitle" id="cx-themes-panel-people">${t('themes.panel.people')}</h3>
      ${people === null ? html`<p class="cx-themes-empty-line">${t('themes.panel.people.none')}</p>`
        : people.length ? html`<ol class="cx-themes-panel__people">
          ${people.map((e) => html`<li key=${e.p.person_id || e.p.name}>
            <span class="cx-themes-panel__person">${e.p.name}</span>
            <span class="cx-themes-bar" aria-hidden="true"><span class="cx-themes-bar__fill"
              style=${{ '--cx-bar': `${Math.round(e.share * 100)}%` }}></span></span>
            <span class="cx-themes-row__share">${shortShare(e.share)}</span>
          </li>`)}
        </ol>
        <p class="cx-themes-panel__note">${t('themes.panel.people.note')}</p>`
        : html`<p class="cx-themes-empty-line">${t('themes.panel.people.empty')}</p>`}
    </section>
    <section class="cx-themes-panel__section" aria-labelledby="cx-themes-panel-names">
      <h3 class="cx-themes-panel__subtitle" id="cx-themes-panel-names">${t('themes.panel.names')}</h3>
      <dl class="cx-themes-facts cx-themes-facts--names">
        ${Object.entries(names).map(([code, value]) => html`<${Fact} key=${code} label=${languageName(code)}>
          <span lang=${code}>${value}</span><//>`)}
        ${!Object.keys(names).length ? html`<${Fact} label=${t('themes.panel.names.none')}><code>${id}</code><//>` : null}
        ${before ? html`<${Fact} label=${t('themes.panel.names.saved')}>${before}<//>` : null}
        ${onMap ? html`<${Fact} label=${t('themes.panel.names.map')}>${onMap}<//>` : null}
        <${Fact} label=${t('themes.panel.id')}><code>${id}</code><//>
      </dl>
    </section>
  </div>`;
}

function KeywordsPanel({ editor, ui, terms }) {
  const index = editor.index.value;
  const lang = lang2(locale.value);
  const tree = index.tree;
  const readOnly = editor.readOnly.value;
  const single = terms.length === 1 ? terms[0] : null;
  const aside = terms.filter((k) => tree.set_aside && k in tree.set_aside);
  const placed = terms.filter((k) => tree.keywords[k] !== undefined);
  const checking = terms.filter((k) => (tree.review || {})[k] === 'to_check');
  const people = terms.reduce((s, k) => s + index.people(k), 0);
  const weight = terms.reduce((s, k) => s + index.weightOf(k), 0);
  const nodes = [...new Set(placed.map((k) => tree.keywords[k]))];
  const entry = single && aside.length ? tree.set_aside[single] : null;
  const attr = single && placed.length ? (tree.attribution || {})[single] : undefined;
  const level = single && placed.length ? index.level.get(tree.keywords[single]) : 0;
  return html`<div class="cx-themes-panel__body">
    <header class="cx-themes-panel__head">
      <p class="cx-themes-panel__kind">${aside.length === terms.length ? t('themes.panel.aside_kind')
        : t('themes.panel.keyword_kind', { count: terms.length })}</p>
      <h2 class="cx-themes-panel__title" id="cx-themes-panel-title">${single
        || t('themes.panel.keywords_title', { count: terms.length })}</h2>
      ${single && placed.length ? html`<${Path} index=${index} id=${tree.keywords[single]} ui=${ui} />
        <p class="cx-themes-panel__on">${t('themes.panel.on', { name: nodeName(index.nodes.get(tree.keywords[single]), lang) })}</p>` : null}
    </header>
    ${readOnly ? null : html`<div class="cx-themes-panel__actions">
      ${placed.length ? html`<${Button} size="s" onClick=${() => ui.moveKeywords(placed)}>${t('themes.action.move')}<//>` : null}
      ${aside.length ? html`<${Button} size="s" onClick=${() => ui.putBack(aside)}>${t('themes.action.put_back')}<//>
        <${Button} size="s" onClick=${() => ui.putBackInto(aside)}>${t('themes.action.put_back_into')}<//>` : null}
      ${placed.length ? html`<${Button} size="s" onClick=${() => ui.setAside(placed)}>${t('themes.action.set_aside')}<//>` : null}
      ${checking.length ? html`<${Button} size="s" onClick=${() => ui.accept(checking)}>${t('themes.action.accept')}<//>` : null}
      <${MenuButton} size="s" label=${t('themes.action.more')} items=${ui.keywordMenu(terms)}
        onSelect=${(item) => ui.onMenu(item, terms.map((k) => (aside.includes(k) ? `a:${k}` : `k:${k}`)))} />
    </div>`}
    <dl class="cx-themes-facts">
      <${Fact} label=${t('themes.panel.people_using')}>${formatNumber(people)}<//>
      <${Fact} label=${t('themes.panel.usage')}>${shortShare(index.total ? weight / index.total : 0)}<//>
      ${nodes.length > 1 ? html`<${Fact} label=${t('themes.panel.on_nodes')}>${formatNumber(nodes.length)}<//>` : null}
      ${checking.length ? html`<${Fact} label=${t('themes.panel.review')}>${t('themes.panel.review.to_check', { count: checking.length })}<//>` : null}
      ${single && placed.length ? html`<${Fact} label=${t('themes.panel.counts')}>${attr === undefined
        ? t('themes.panel.counts.default', { level: levelName(tree, level, lang) })
        : attr === 0 ? t('themes.panel.counts.none')
          : t('themes.panel.counts.level', { level: levelName(tree, attr, lang) })}<//>` : null}
      ${entry ? html`<${Fact} label=${t('themes.panel.aside.reason')}>${entry.reason || t('themes.aside.no_reason')}<//>
        <${Fact} label=${t('themes.panel.aside.from')}>${entry.from && index.nodes.has(entry.from)
          ? nodeName(index.nodes.get(entry.from), lang) : t('themes.panel.aside.from.none')}<//>` : null}
    </dl>
    ${terms.length > 1 ? html`<ul class="cx-themes-panel__keywords">
      ${terms.slice(0, 60).map((term) => html`<li key=${term}>
        <button type="button" class="cx-themes-panel__keyword" onClick=${() => ui.openKeyword(term)}>
          <span>${term}</span><span class="cx-themes-row__count">${formatNumber(index.people(term))}</span>
        </button></li>`)}
    </ul>` : null}
  </div>`;
}

function PersonPanel({ editor, atlas, id }) {
  const index = editor.index.value;
  const lang = lang2(locale.value);
  const person = atlas && atlas.people.find((p) => p.person_id === id);
  if (!person) return null;
  return html`<div class="cx-themes-panel__body">
    <header class="cx-themes-panel__head">
      <p class="cx-themes-panel__kind">${t('themes.panel.person_kind')}</p>
      <h2 class="cx-themes-panel__title" id="cx-themes-panel-title">${person.name}</h2>
      <p class="cx-themes-panel__on">${person.unit}</p>
    </header>
    ${person.shares.map((shares, i) => {
      const list = Object.entries(shares).sort((a, b) => b[1] - a[1]).slice(0, 5);
      if (!list.length) return null;
      return html`<section class="cx-themes-panel__section" key=${i}>
        <h3 class="cx-themes-panel__subtitle">${levelName(index.tree, i + 1, lang) || t('themes.level.numbered', { level: i + 1, name: '' })}</h3>
        <ol class="cx-themes-panel__people">
          ${list.map(([nid, share]) => html`<li key=${nid}>
            <span class="cx-themes-panel__person">${index.nodes.has(nid) ? nodeName(index.nodes.get(nid), lang)
              : nodeName((atlas.nodes.find((n) => n.id === nid) || { id: nid }), lang)}</span>
            <span class="cx-themes-bar" aria-hidden="true"><span class="cx-themes-bar__fill"
              style=${{ '--cx-bar': `${Math.round(share * 100)}%` }}></span></span>
            <span class="cx-themes-row__share">${shortShare(share)}</span></li>`)}
        </ol>
      </section>`;
    })}
    <p class="cx-themes-panel__note">${t('themes.panel.people.note')}</p>
  </div>`;
}

function Overview({ editor }) {
  const index = editor.index.value;
  const lang = lang2(locale.value);
  const perLevel = index.tree.levels.map((_, i) => index.order.filter((id) => index.level.get(id) === i + 1).length);
  return html`<div class="cx-themes-panel__body">
    <header class="cx-themes-panel__head">
      <p class="cx-themes-panel__kind">${t('themes.panel.tree_kind')}</p>
      <h2 class="cx-themes-panel__title" id="cx-themes-panel-title">${t('themes.panel.tree_title', { depth: index.depth })}</h2>
    </header>
    <dl class="cx-themes-facts">
      ${index.tree.levels.map((_, i) => html`<${Fact} key=${i} label=${levelName(index.tree, i + 1, lang)}>
        ${formatNumber(perLevel[i])}<//>`)}
      <${Fact} label=${t('themes.panel.placed')}>${formatNumber(Object.keys(index.tree.keywords).length)}<//>
      <${Fact} label=${t('themes.tab.aside')}>${formatNumber(index.setAside.length)}<//>
      <${Fact} label=${t('themes.tab.check')}>${formatNumber(index.toCheck.length)}<//>
    </dl>
    <p class="cx-themes-panel__note">${t('themes.panel.hint')}</p>
  </div>`;
}

/** The right column. */
export function SidePanel({ editor, ui, atlas }) {
  const index = editor.index.value;
  if (!index) return null;
  const focus = ui.focus.value;
  let body;
  if (focus && focus.kind === 'node' && index.nodes.has(focus.id)) {
    body = html`<${NodePanel} editor=${editor} ui=${ui} atlas=${atlas} id=${focus.id} />`;
  } else if (focus && focus.kind === 'keywords' && focus.terms.length) {
    body = html`<${KeywordsPanel} editor=${editor} ui=${ui} terms=${focus.terms} />`;
  } else if (focus && focus.kind === 'person') {
    body = html`<${PersonPanel} editor=${editor} atlas=${atlas} id=${focus.id} />`;
  } else {
    body = html`<${Overview} editor=${editor} />`;
  }
  // Escape gives the focus back to the outline (Enter there brought it here).
  const onKeyDown = (event) => {
    if (event.key === 'Escape' && !event.defaultPrevented && ui.outlineRef.current
      && !document.querySelector('[role=menu]')) {
      event.preventDefault();
      ui.outlineRef.current.focus();
    }
  };
  return html`<aside class="cx-themes-panel" aria-labelledby="cx-themes-panel-title" tabindex="-1"
    ref=${ui.panelRef} onKeyDown=${onKeyDown}>${body}</aside>`;
}

// ── versions ────────────────────────────────────────────────────────────────

/**
 * The versions of the tree: every saved version, the current one first, each
 * with when it was saved and by which action; open one read-only, compare it
 * with the current tree, restore it (a restore is a new version).
 */

/** A saved action's name in the interface language (its parts, joined by « ; »). */
export function actionText(action, names = {}) {
  if (!action) return '';
  return action.split('; ').map((part) => describe(part, names)).join(' · ');
}

/**
 * @param {object} props
 * @param {boolean} props.open
 * @param {object} props.api the page's API client
 * @param {object} props.editor
 * @param {(version: object) => void} props.onOpenVersion read-only view
 * @param {(version: object) => void} props.onCompare
 * @param {(version: object) => void} props.onRestore
 */
export function VersionsDrawer({ open, api, editor, onClose, onOpenVersion, onCompare, onRestore }) {
  const [state, setState] = useState({ loading: true, items: [], error: null });
  const load = async () => {
    setState({ loading: true, items: [], error: null });
    const result = await api.get('/api/themes/versions');
    if (result.ok) setState({ loading: false, items: result.data.items, error: null });
    else setState({ loading: false, items: [], error: result.error });
  };
  useEffect(() => {
    if (open) load();
  }, [open]);
  const names = {};
  const tree = editor.tree.value;
  if (tree) for (const n of tree.nodes) names[n.id] = n.names;
  const viewing = editor.viewing.value;
  let body;
  if (state.error) body = html`<${ErrorCard} error=${state.error} onRetry=${load} compact />`;
  else if (state.loading) body = html`<p class="cx-themes-empty-line" aria-busy="true">${t('common.loading')}</p>`;
  else if (!state.items.length) {
    body = html`<${EmptyState} icon="file" title=${t('themes.versions.empty')}>${t('themes.versions.empty.text')}<//>`;
  } else {
    body = html`<ol class="cx-themes-versions">
      ${state.items.map((v, i) => html`<li key=${v.id} class=${`cx-themes-versions__item ${viewing && viewing.id === v.id ? 'is-open' : ''}`}>
        <p class="cx-themes-versions__when">
          ${v.made_at ? formatDate(v.made_at, 'datetime') : t('themes.versions.unknown')}
          ${i === 0 ? html` <span class="cx-themes-badge">${t('themes.versions.current')}</span>` : null}
        </p>
        <p class="cx-themes-versions__what">${v.made_by ? actionText(v.made_by, names) : t('themes.versions.first')}</p>
        <div class="cx-themes-versions__actions">
          <${Button} size="s" variant="ghost" onClick=${() => onOpenVersion(v)}>${t('themes.versions.open')}<//>
          ${i > 0 ? html`<${Button} size="s" variant="ghost" onClick=${() => onCompare(v)}>
            ${t('themes.versions.compare')}<//>
            <${Button} size="s" variant="ghost" onClick=${() => onRestore(v)}>${t('themes.versions.restore')}<//>` : null}
        </div>
      </li>`)}
    </ol>`;
  }
  return html`<${Drawer} open=${open} onClose=${onClose} title=${t('themes.versions.title')}
    description=${t('themes.versions.lead')}>
    ${body}
  <//>`;
}

// ── handoff ─────────────────────────────────────────────────────────────────

/**
 * AI curation of the theme tree through a handoff: export the tree for an
 * assistant the person already uses, import its answer, review each proposed
 * change (accept or reject, preview on the tree), then apply the accepted ones
 * as ordinary operations, undone like any other.
 */

const STEPS = ['export', 'import', 'review'];

/** One proposed operation in words, with the names the nodes have now. */
export function operationText(op, index) {
  const lang = lang2(locale.value);
  const name = (id) => (index && index.nodes.has(id) ? nodeName(index.nodes.get(id), lang) : id);
  switch (op.op) {
    case 'rename_node':
      return t('themes.ai.op.rename', { name: name(op.node_id), to: Object.values(op.names)[0] || '' });
    case 'move_keywords':
      return t('themes.ai.op.move', { keyword: op.keywords.join(', '), to: name(op.node_id) });
    case 'put_back':
      return t('themes.ai.op.put_back', { keyword: op.keywords.join(', '), to: name(op.node_id) });
    case 'merge_nodes':
      return t('themes.ai.op.merge', { source: name(op.source), target: name(op.target) });
    case 'split_node':
      return t('themes.ai.op.split', { name: name(op.node_id), to: Object.values(op.parts[0].names)[0] || '',
        count: op.parts[0].members.length });
    case 'set_aside':
      return t('themes.ai.op.set_aside', { keyword: op.keywords.join(', ') });
    case 'set_attribution':
      return op.levels === 0 ? t('themes.ai.op.count_nowhere', { keyword: op.keywords.join(', ') })
        : t('themes.ai.op.count_level', { keyword: op.keywords.join(', '), level: op.levels === null ? '—' : op.levels });
    default:
      return op.op;
  }
}

/**
 * The review list of a proposal: each operation with a checkbox, its reason,
 * and why it cannot apply when it cannot.
 */
export function ProposalList({ proposal, index, accepted, onChange }) {
  const toggle = (i) => {
    const next = new Set(accepted);
    if (next.has(i)) next.delete(i);
    else next.add(i);
    onChange(next);
  };
  return html`<div class="cx-themes-ai">
    <div class="cx-themes-ai__bulk">
      <${Button} size="s" variant="ghost" onClick=${() => onChange(new Set(proposal.items
        .map((it, i) => (it.refused ? null : i)).filter((i) => i !== null)))}>${t('themes.ai.all')}<//>
      <${Button} size="s" variant="ghost" onClick=${() => onChange(new Set())}>${t('themes.ai.none')}<//>
      <span class="cx-themes-ai__count" aria-live="polite">${t('themes.ai.chosen', {
        count: accepted.size, total: proposal.items.length })}</span>
    </div>
    <ol class="cx-themes-ai__list">
      ${proposal.items.map((item, i) => html`<li key=${i} class=${`cx-themes-ai__item ${item.refused ? 'is-refused' : ''}`}>
        <${Checkbox} checked=${accepted.has(i)} disabled=${Boolean(item.refused)}
          label=${html`<span class="cx-themes-ai__verb">${t(`themes.ai.verb.${item.verb.replace(' ', '_')}`)}</span>
            <span class="cx-themes-ai__what">${operationText(item.op, index)}</span>`}
          onChange=${() => toggle(i)} />
        ${item.reason ? html`<p class="cx-themes-ai__reason">${t('themes.ai.reason', { reason: item.reason })}</p>` : null}
        ${item.refused ? html`<p class="cx-themes-ai__refused"><${Icon} name="warning" />
          <span>${t('themes.ai.refused', { reason: item.refused })}</span></p>` : null}
      </li>`)}
    </ol>
    ${proposal.unreadable.length ? html`<details class="cx-themes-ai__unreadable">
      <summary>${t('themes.ai.unreadable', { count: proposal.unreadable.length })}</summary>
      <ul>${proposal.unreadable.map((u, i) => html`<li key=${i}>
        <span class="cx-themes-ai__line">${t('themes.ai.line', { line: u.line })}</span>
        <code class="cx-themes-ai__text">${u.text}</code>
        <span class="cx-themes-form__hint">${t(`themes.ai.problem.${u.problem}`)}</span></li>`)}</ul>
    </details>` : null}
  </div>`;
}

/**
 * @param {object} props
 * @param {boolean} props.open
 * @param {object} props.api the page's API client
 * @param {object} props.editor
 * @param {(proposal: object, accepted: Set<number>) => void} props.onPreview
 * @param {(proposal: object, accepted: Set<number>) => Promise<any>} props.onApply
 * @param {{proposal: object, accepted: Set<number>}|null} [props.resume] come back to a review
 */
export function ThemeHandoffDialog({ open, api, editor, onClose, onPreview, onApply, resume = null }) {
  const [step, setStep] = useState(resume ? 'review' : 'export');
  const [exported, setExported] = useState(null);
  const [error, setError] = useState(null);
  const [part, setPart] = useState(0);
  const [answer, setAnswer] = useState('');
  const [proposal, setProposal] = useState(resume ? resume.proposal : null);
  const [accepted, setAccepted] = useState(resume ? resume.accepted : new Set());
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(null);
  const fileInput = useRef(null);
  const uid = useUid('cx-themes-ai');
  const index = editor.editIndex.value;

  useEffect(() => {
    if (!open || exported || resume) return;
    (async () => {
      const result = await api.post('/api/themes/handoff/export', { tree: editor.tree.value });
      if (result.ok) setExported(result.data);
      else setError(result.error);
    })();
  }, [open]);

  const read = async () => {
    setBusy(true);
    setError(null);
    const result = await api.post('/api/themes/handoff/import',
      { bundle: exported.parts[part].bundle, answer });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setProposal(result.data);
    setAccepted(new Set(result.data.items.map((it, i) => (it.refused ? null : i)).filter((i) => i !== null)));
    setStep('review');
  };
  const readFile = async (event) => {
    const chosen = event.target.files && event.target.files[0];
    if (chosen) setAnswer(await chosen.text());
  };
  const state = (id) => {
    const at = STEPS.indexOf(step);
    const i = STEPS.indexOf(id);
    return i < at ? 'done' : i === at ? 'current' : 'todo';
  };

  let body;
  let footer;
  if (step === 'export') {
    const parts = exported ? exported.parts : [];
    body = html`<p class="cx-handoff__lead">${t('themes.ai.lead')}</p>
      <div class="cx-handoff__columns">
        <section class="cx-handoff__box" aria-labelledby=${`${uid}-contains`}>
          <h3 id=${`${uid}-contains`} class="cx-handoff__box-title"><${Icon} name="check" />${t('handoff.export.contains')}</h3>
          <ul class="cx-handoff__list">
            <li>${t('themes.ai.contains.tree')}</li>
            <li>${t('themes.ai.contains.keywords')}</li>
            <li>${t('themes.ai.contains.aside')}</li>
            <li>${t('themes.ai.contains.field')}</li>
          </ul>
        </section>
        <section class="cx-handoff__box" aria-labelledby=${`${uid}-never`}>
          <h3 id=${`${uid}-never`} class="cx-handoff__box-title"><${Icon} name="cross" />${t('handoff.export.never')}</h3>
          <ul class="cx-handoff__list">
            <li>${t('handoff.export.never.texts')}</li>
            <li>${t('handoff.export.never.people')}</li>
            <li>${t('handoff.export.never.keys')}</li>
          </ul>
        </section>
      </div>
      ${error ? html`<${ErrorCard} error=${error} compact />` : null}
      ${!exported && !error ? html`<p class="cx-themes-empty-line" aria-busy="true">${t('common.loading')}</p>` : null}
      ${parts.map((p) => html`<section class="cx-themes-ai__part" key=${p.name} aria-label=${t('themes.ai.part', { part: p.part, parts: p.parts })}>
        <p class="cx-themes-ai__part-title">${p.parts > 1 ? t('themes.ai.part', { part: p.part, parts: p.parts }) : t('themes.ai.one_part')}
          <span class="cx-themes-form__hint">${t('themes.ai.part.size', { nodes: p.nodes, keywords: p.keywords, tokens: formatNumber(p.tokens) })}</span></p>
        <div class="cx-handoff__actions">
          <${Button} icon="copy" onClick=${async () => setCopied(await copyText(p.files['prompt.txt']))}>
            ${t('themes.ai.copy_prompt')}<//>
          <${Button} icon="download" onClick=${() => downloadFile(`${p.name}-tree.txt`, p.files['tree.txt'], 'text/plain')}>
            ${t('themes.ai.download_tree')}<//>
          <${Button} icon="download" variant="ghost" onClick=${() => downloadFile(`${p.name}-bundle.json`,
            JSON.stringify(p.bundle, null, 1), 'application/json')}>${t('themes.ai.download_bundle')}<//>
        </div>
      </section>`)}
      <p class="cx-handoff__status" role="status">${copied === true ? t('handoff.export.copied')
        : copied === false ? t('error.copy_failed') : ''}</p>
      <p class="cx-handoff__hint">${t('themes.ai.hint')}</p>`;
    footer = html`<${Button} variant="ghost" onClick=${() => onClose('close')}>${t('common.cancel')}<//>
      <${Button} variant="primary" iconAfter="chevron-right" disabled=${!exported}
        onClick=${() => setStep('import')}>${t('handoff.export.next')}<//>`;
  } else if (step === 'import') {
    const parts = exported ? exported.parts : [];
    body = html`${parts.length > 1 ? html`<fieldset class="cx-themes-form__set">
        <legend>${t('themes.ai.which_part')}</legend>
        <div class="cx-themes-form__radios" role="radiogroup">
          ${parts.map((p, i) => html`<label class="cx-themes-radio" key=${p.name}>
            <input type="radio" name=${`${uid}-part`} checked=${part === i} onChange=${() => setPart(i)} />
            <span>${t('themes.ai.part', { part: p.part, parts: p.parts })}</span></label>`)}
        </div></fieldset>` : null}
      <${FormField} label=${t('themes.ai.answer')} help=${t('themes.ai.answer.help')} required>
        ${(field) => html`<${Textarea} ...${field} rows=${10} value=${answer} spellcheck="false"
          class="cx-handoff__answer" onInput=${(e) => setAnswer(e.currentTarget.value)} />`}
      <//>
      <div class="cx-handoff__file">
        <input ref=${fileInput} type="file" accept=".txt,text/plain" class="cx-visually-hidden"
          tabindex="-1" aria-hidden="true" onChange=${readFile} />
        <${Button} size="s" icon="upload" onClick=${() => fileInput.current.click()}>${t('handoff.import.file')}<//>
      </div>
      ${error ? html`<${ErrorCard} error=${error} compact />` : null}`;
    footer = html`<${Button} variant="ghost" icon="chevron-left" onClick=${() => setStep('export')}>${t('common.back')}<//>
      <${Button} variant="primary" loading=${busy} disabled=${!answer.trim()} onClick=${read}>
        ${t('themes.ai.read')}<//>`;
  } else {
    body = proposal ? html`<p class="cx-handoff__lead">${t('themes.ai.review.lead', {
        count: proposal.items.length, applicable: proposal.applicable })}</p>
      ${proposal.items.length ? html`<${ProposalList} proposal=${proposal} index=${index}
        accepted=${accepted} onChange=${setAccepted} />` : html`<p>${t('themes.ai.review.nothing')}</p>`}` : null;
    footer = html`<${Button} variant="ghost" icon="chevron-left" onClick=${() => setStep('import')}
        disabled=${!exported}>${t('common.back')}<//>
      <${Button} disabled=${!accepted.size} onClick=${() => onPreview(proposal, accepted)}>${t('themes.ai.preview')}<//>
      <${Button} variant="primary" loading=${busy} disabled=${!accepted.size} onClick=${async () => {
        setBusy(true);
        await onApply(proposal, accepted);
        setBusy(false);
      }}>${t('themes.ai.apply', { count: accepted.size })}<//>`;
  }

  return html`<${Dialog} open=${open} onClose=${onClose} size="l" title=${t('themes.ai.title')}
    description=${t('themes.ai.description')} footer=${footer}>
    <${Stepper} label=${t('handoff.steps')} steps=${STEPS.map((id) => ({
      id, label: t(`handoff.step.${id}`), state: state(id) }))}
      onSelect=${(id) => {
        if (id !== 'review' || proposal) setStep(id);
      }} />
    <div class="cx-handoff" role="group" aria-label=${t(`handoff.step.${step}`)}>${body}</div>
  <//>`;
}

// ── editor ──────────────────────────────────────────────────────────────────

/**
 * The theme editor: three views of one tree (outline, treemap, map) kept in
 * sync, a side panel, and every action from a menu and the keyboard (drag and
 * drop is a shortcut). Nothing is ever lost: undo and redo, an autosaved
 * draft, a guard before leaving, « reload and merge » when the saved tree
 * changed, versions to compare and restore.
 */

function refusal(error) {
  if (!error) return t('themes.refused.unknown');
  const detail = error.params && error.params.detail;
  return detail ? t('themes.refused', { detail }) : (error.message || t('themes.refused.unknown'));
}

function Banner({ tone = 'info', icon, children, actions }) {
  return html`<div class=${`cx-themes-banner cx-themes-banner--${tone}`} role="status">
    <span class="cx-themes-banner__icon" aria-hidden="true"><${Icon} name=${icon || (tone === 'warning' ? 'warning' : 'info')} /></span>
    <div class="cx-themes-banner__text">
      ${tone === 'warning' ? html`<span class="cx-themes-banner__word">${t('toast.kind.warning')}</span> ` : null}
      ${children}
    </div>
    ${actions ? html`<div class="cx-themes-banner__actions">${actions}</div>` : null}
  </div>`;
}

/** The page's view state: what is open, selected, shown, and the actions on it. */
function createUi(editor) {
  const expanded = signal(new Set());
  const treeSel = signal(new Set());
  const active = signal(null);
  const leftTab = signal('outline');
  const centreTab = signal('treemap');
  const zoom = signal(null);
  const mapHover = signal(null);
  const person = signal(null);
  const focus = computed(() => {
    if (person.value) return { kind: 'person', id: person.value };
    const keys = [...treeSel.value];
    const nodes = keys.filter((k) => k.startsWith('n:'));
    const terms = keys.filter((k) => /^[kac]:/.test(k)).map((k) => k.slice(2));
    if (nodes.length === 1 && !terms.length) return { kind: 'node', id: nodes[0].slice(2) };
    if (terms.length) return { kind: 'keywords', terms };
    const a = active.value;
    if (a && a.startsWith('n:')) return { kind: 'node', id: a.slice(2) };
    return null;
  });
  return {
    expanded, treeSel, active, leftTab, centreTab, zoom, mapHover, person, focus,
    outlineRef: { current: null }, panelRef: { current: null }, mapFrame: null, lastSearchMs: 0,
  };
}

/** The theme editor page's content. */
export function ThemesEditor() {
  const ctx = usePage();
  const { app } = ctx;
  usePageTitle(t('nav.themes'));
  const projectId = app.manifest.project && app.manifest.project.open ? app.manifest.project.id : null;
  const [announcement, setAnnouncement] = useState('');
  const editor = useMemo(() => createEditor({
    api: ctx.api, projectId, announce: (text) => setAnnouncement(text),
  }), []);
  const ui = useMemo(() => createUi(editor), []);
  const [atlas, setAtlas] = useState(null);
  const [atlasError, setAtlasError] = useState(null);
  const [dialog, setDialog] = useState(null);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [handoff, setHandoff] = useState(null); // {resume}
  const [leave, setLeave] = useState(null);
  const [applyJob, setApplyJob] = useState(null);
  const [applyError, setApplyError] = useState(null);
  const [report, setReport] = useState(null);
  const lang = lang2(locale.value);
  const toast = (item) => app.toaster.show(item);

  const loadAtlas = async () => {
    setAtlasError(null);
    const result = await ctx.api.get('/api/atlas');
    if (result.ok) setAtlas(result.data);
    else setAtlasError(result.error);
  };

  // Ready when the tree is on screen: deferred now, during the first render (an effect runs
  // after the router has already marked the page ready).
  const done = useMemo(() => ctx.deferReady(), []);
  useEffect(() => {
    editor.load().then(() => {
      const index = editor.editIndex.value;
      if (index && index.depth > 1) ui.expanded.value = new Set(index.tops);
      done(); // the tree is rendered (signals render synchronously)
      // The map's data comes next: after the tree, not competing with it.
      setTimeout(loadAtlas, 0);
    });
    ctx.guard({
      dirty: () => editor.dirty.value,
      confirm: () => new Promise((resolve) => setLeave({ resolve })),
    });
    const onKey = (event) => {
      if (document.querySelector('dialog[open]')) return;
      const target = event.target;
      const typing = target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable);
      const mod = event.ctrlKey || event.metaKey;
      if (mod && !event.altKey && (event.key === 'z' || event.key === 'Z') && !typing) {
        if (event.shiftKey) editor.redo();
        else editor.undo();
      } else if (mod && (event.key === 'y' || event.key === 'Y') && !typing) {
        editor.redo();
      } else if (mod && (event.key === 's' || event.key === 'S')) {
        save();
      } else if (event.key === '/' && !typing && !mod) {
        focusSearch();
      } else {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
    };
    // In the capture phase: the page's keys come before the widgets' own (a tree's type-ahead).
    document.addEventListener('keydown', onKey, true);
    // The draft is written when the tab is hidden or closed, whatever is pending.
    const onHide = () => editor.flush();
    window.addEventListener('pagehide', onHide);
    document.addEventListener('visibilitychange', onHide);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      window.removeEventListener('pagehide', onHide);
      document.removeEventListener('visibilitychange', onHide);
      editor.flush();
    };
  }, []);

  // The apply job: follow it with the jobs store; refresh the map when it ends.
  const jobs = app.stores.jobs.jobs.value;
  useEffect(() => {
    if (!applyJob) return;
    const job = jobs.find((j) => j.id === applyJob.id);
    if (!job || ACTIVE.has(job.state)) return;
    setApplyJob(null);
    if (job.state === 'succeeded') {
      loadAtlas();
      editor.refreshInfo().then((themes) => {
        // The apply rebases the saved tree first when the vocabulary changed: follow it.
        if (themes.ok && themes.etag && editor.base.value && themes.etag !== editor.base.value.version
          && !editor.dirty.value) editor.adopt(themes.data, themes.etag);
      });
      toast({ kind: 'success', title: t('themes.apply.done') });
    } else {
      const failed = job.result && job.result.failed;
      setApplyError({
        code: failed ? failed.code : 'job_failed', message: failed ? failed.message : (job.error || ''),
        next: { label: '', action: 'report' }, status: null, method: null, path: null, requestId: null,
        time: job.finished_at || new Date().toISOString(), technical: job.error || null,
      });
    }
  }, [jobs, applyJob]);

  // ── selection ──
  const reveal = (id) => {
    const index = editor.index.value;
    if (!index || !index.nodes.has(id)) return;
    const path = pathOf(index, id).slice(0, -1);
    if (path.some((p) => !ui.expanded.value.has(p))) {
      ui.expanded.value = new Set([...ui.expanded.value, ...path]);
    }
  };
  ui.setActive = (key) => {
    ui.active.value = key;
  };
  ui.select = (keys) => {
    batch(() => {
      ui.person.value = null;
      ui.treeSel.value = keys;
    });
  };
  ui.openNode = (id, { from } = {}) => {
    reveal(id);
    batch(() => {
      ui.leftTab.value = 'outline';
      ui.person.value = null;
      ui.active.value = `n:${id}`;
      ui.treeSel.value = new Set([`n:${id}`]);
    });
    if (!from && ui.outlineRef.current) ui.outlineRef.current.focus({ preventScroll: true });
  };
  ui.openKeyword = (term) => {
    const tree = editor.index.value.tree;
    if (tree.keywords[term] !== undefined) {
      reveal(tree.keywords[term]);
      ui.expanded.value = new Set([...ui.expanded.value, tree.keywords[term]]);
      batch(() => {
        ui.leftTab.value = 'outline';
        ui.person.value = null;
        ui.active.value = `k:${term}`;
        ui.treeSel.value = new Set([`k:${term}`]);
      });
    } else {
      batch(() => {
        ui.leftTab.value = 'aside';
        ui.person.value = null;
        ui.active.value = `a:${term}`;
        ui.treeSel.value = new Set([`a:${term}`]);
      });
    }
  };
  ui.open = () => {
    if (ui.panelRef.current) ui.panelRef.current.focus();
  };
  ui.pickOnMap = (hit, scene) => {
    if (!hit) return;
    if (hit.layer === 'keywords') ui.openKeyword(scene.layers[0].items[hit.index].term);
    else {
      const p = scene.layers[1].items[hit.index];
      batch(() => {
        ui.treeSel.value = new Set();
        ui.person.value = p.person_id;
      });
    }
  };
  ui.expandAll = () => {
    const index = editor.index.value;
    ui.expanded.value = new Set(index.order.filter((id) => (index.children.get(id) || []).length));
  };
  ui.collapseAll = () => {
    ui.expanded.value = new Set();
  };
  const focusSearch = () => {
    const el = document.getElementById('cx-themes-search');
    if (el) el.focus();
  };

  // ── operations ──
  const run = async (ops, options) => {
    const result = await editor.run(ops, options);
    if (!result.ok && result.error) return refusal(result.error);
    return result.ok;
  };
  const runOrToast = async (ops, options) => {
    const outcome = await run(ops, options);
    if (outcome !== true) toast({ kind: 'warning', title: t('themes.refused.title'), message: outcome || '' });
    return outcome;
  };
  const keysToTerms = (keys) => keys.filter((k) => /^[kac]:/.test(k)).map((k) => k.slice(2));
  const idle = () => !editor.readOnly.value;

  ui.rename = (id) => idle() && setDialog({ kind: 'rename', id });
  ui.renameRow = (row) => {
    if (row.kind === 'node') ui.rename(row.id);
  };
  ui.moveKeywords = (terms) => idle() && setDialog({ kind: 'move-keywords', terms });
  ui.canMoveNode = (id) => nodeParents(editor.index.value, id).length > 0;
  ui.moveNode = (id) => idle() && setDialog({ kind: 'move-node', id });
  ui.merge = (id) => idle() && setDialog({ kind: 'merge', id });
  ui.split = (id) => idle() && setDialog({ kind: 'split', id });
  ui.create = (parent) => idle() && setDialog({ kind: 'create', parent });
  ui.setAside = (terms) => idle() && setDialog({ kind: 'aside', terms });
  ui.attribution = (terms) => idle() && setDialog({ kind: 'attribution', terms });
  ui.putBack = (terms) => {
    const tree = editor.index.value.tree;
    const lost = terms.filter((k) => !tree.set_aside[k].from || !editor.index.value.nodes.has(tree.set_aside[k].from));
    if (lost.length) {
      setDialog({ kind: 'put-back-into', terms });
      return;
    }
    runOrToast([{ op: 'put_back', keywords: terms }]);
  };
  ui.putBackInto = (terms) => idle() && setDialog({ kind: 'put-back-into', terms });
  ui.accept = (terms) => runOrToast([{ op: 'set_review', keywords: terms, state: 'reviewed' }]);
  ui.deleteNode = (id) => {
    if (!isEmpty(editor.index.value, id)) {
      toast({ kind: 'warning', title: t('themes.delete.not_empty') });
      return;
    }
    runOrToast([{ op: 'delete_node', node_id: id }]);
  };
  ui.deleteKeys = (keys) => {
    if (!idle()) return;
    const nodes = keys.filter((k) => k.startsWith('n:'));
    if (nodes.length === 1) ui.deleteNode(nodes[0].slice(2));
    else {
      const terms = keys.filter((k) => k.startsWith('k:')).map((k) => k.slice(2));
      if (terms.length) ui.setAside(terms);
    }
  };
  ui.dragFor = (keys) => {
    const nodes = keys.filter((k) => k.startsWith('n:'));
    const terms = keysToTerms(keys);
    if (terms.length) return { kind: 'keywords', terms };
    if (nodes.length === 1) return { kind: 'node', id: nodes[0].slice(2) };
    return null;
  };
  ui.canDrop = (target, data) => !editor.readOnly.value && Boolean(dropOperation(editor.index.value, data, target));
  ui.drop = (target, data) => {
    const ops = dropOperation(editor.index.value, data, target);
    if (ops) runOrToast(ops);
  };
  // The « to check » queue's own keys: A accept here, M move to…, S set aside…, J/K next/previous.
  ui.checkKey = (event, row) => {
    if (!row || event.ctrlKey || event.metaKey || event.altKey) return false;
    const key = event.key.toLowerCase();
    const list = editor.index.value.toCheck;
    const at = list.indexOf(row.term);
    const next = () => {
      const following = list[at + 1] || list[at - 1];
      if (following) {
        ui.active.value = `c:${following}`;
        ui.treeSel.value = new Set([`c:${following}`]);
      }
    };
    if (key === 'a') {
      next();
      ui.accept([row.term]);
      return true;
    }
    if (key === 'm') {
      setDialog({ kind: 'move-keywords', terms: [row.term], review: true, after: next });
      return true;
    }
    if (key === 's') {
      setDialog({ kind: 'aside', terms: [row.term], review: true, after: next });
      return true;
    }
    if (key === 'j' || key === 'k') {
      const other = list[at + (key === 'j' ? 1 : -1)];
      if (other) {
        ui.active.value = `c:${other}`;
        ui.treeSel.value = new Set([`c:${other}`]);
      }
      return true;
    }
    return false;
  };

  // ── menus ──
  ui.nodeMenu = (id, { panel = false } = {}) => {
    const index = editor.index.value;
    const level = index.level.get(id);
    const empty = isEmpty(index, id);
    const ro = editor.readOnly.value;
    const items = [];
    if (!panel) items.push({ id: 'open', label: t('themes.action.open'), hint: t('themes.key.enter') });
    items.push(
      { id: 'rename', label: t('themes.action.rename_more'), hint: t('themes.key.f2'), disabled: ro },
      { id: 'move-node', label: t('themes.action.move_more'), disabled: ro || !nodeParents(index, id).length },
      { id: 'merge', label: t('themes.action.merge_more'), disabled: ro || !mergeTargets(index, id).length },
      { id: 'split', label: t('themes.action.split_more'), disabled: ro || (index.children.get(id).length + index.keywordsOn.get(id).length) < 2 },
      { kind: 'separator', id: 'sep1' },
      { id: 'create-in', label: t('themes.action.create_in', { level: levelName(index.tree, level + 1, lang) }),
        disabled: ro || level >= index.depth },
      { id: 'create-beside', label: t('themes.action.create_beside'), disabled: ro },
      { id: 'zoom', label: t('themes.action.zoom'), disabled: !(index.children.get(id) || []).length },
      { kind: 'separator', id: 'sep2' },
      { id: 'delete', label: empty ? t('themes.action.delete') : t('themes.action.delete_not_empty'),
        danger: true, disabled: ro || !empty, hint: empty ? t('themes.key.delete') : undefined },
    );
    return items;
  };
  ui.keywordMenu = (terms) => {
    const tree = editor.index.value.tree;
    const ro = editor.readOnly.value;
    const aside = terms.filter((k) => tree.set_aside && k in tree.set_aside);
    const placed = terms.filter((k) => tree.keywords[k] !== undefined);
    const checking = terms.filter((k) => (tree.review || {})[k] === 'to_check');
    const items = [];
    if (placed.length) {
      items.push(
        { id: 'move-keywords', label: t('themes.action.move_more'), disabled: ro },
        { id: 'aside', label: t('themes.action.set_aside_more'), hint: t('themes.key.delete'), disabled: ro },
        { id: 'attribution', label: t('themes.action.attribution_more'), disabled: ro },
      );
    }
    if (aside.length) {
      items.push(
        { id: 'put-back', label: t('themes.action.put_back'), disabled: ro },
        { id: 'put-back-into', label: t('themes.action.put_back_into'), disabled: ro },
        { id: 'aside', label: t('themes.action.reason_more'), disabled: ro },
      );
    }
    if (checking.length) items.push({ id: 'accept', label: t('themes.action.accept'), disabled: ro });
    else if (terms.some((k) => (tree.review || {})[k] === 'reviewed')) {
      items.push({ id: 'uncheck', label: t('themes.action.to_check'), disabled: ro });
    }
    if (placed.length === 1 && terms.length === 1) {
      items.push({ kind: 'separator', id: 'sep' }, { id: 'show-node', label: t('themes.action.show_node') });
    }
    return items;
  };
  ui.menuFor = (keys) => {
    const nodes = keys.filter((k) => k.startsWith('n:'));
    const terms = keysToTerms(keys);
    if (terms.length) return ui.keywordMenu(terms);
    if (nodes.length) return ui.nodeMenu(nodes[0].slice(2));
    return [];
  };
  ui.onMenu = (item, keys) => {
    const index = editor.index.value;
    const nodes = keys.filter((k) => k.startsWith('n:')).map((k) => k.slice(2));
    const terms = keysToTerms(keys);
    const id = nodes[0];
    switch (item.id) {
      case 'open': ui.open(); break;
      case 'rename': ui.rename(id); break;
      case 'move-node': ui.moveNode(id); break;
      case 'merge': ui.merge(id); break;
      case 'split': ui.split(id); break;
      case 'create-in': ui.create(id); break;
      case 'create-beside': ui.create(index.nodes.get(id).parent); break;
      case 'zoom':
        ui.zoom.value = id;
        ui.centreTab.value = 'treemap';
        break;
      case 'delete': ui.deleteNode(id); break;
      case 'move-keywords': ui.moveKeywords(terms.filter((k) => index.tree.keywords[k] !== undefined)); break;
      case 'aside': ui.setAside(terms); break;
      case 'attribution': ui.attribution(terms.filter((k) => index.tree.keywords[k] !== undefined)); break;
      case 'put-back': ui.putBack(terms.filter((k) => k in (index.tree.set_aside || {}))); break;
      case 'put-back-into': ui.putBackInto(terms.filter((k) => k in (index.tree.set_aside || {}))); break;
      case 'accept': ui.accept(terms.filter((k) => (index.tree.review || {})[k] === 'to_check')); break;
      case 'uncheck': runOrToast([{ op: 'set_review', keywords: terms, state: 'to_check' }]); break;
      case 'show-node': ui.openNode(index.tree.keywords[terms[0]]); break;
      default: break;
    }
  };

  // ── saving, applying, versions ──
  async function save({ quiet = false } = {}) {
    if (!editor.tree.value) return false;
    const result = await editor.save();
    if (result.stale) {
      setDialog({ kind: 'stale' });
      return false;
    }
    if (!result.ok) {
      if (result.error) toast({ kind: 'error', title: t('themes.save.failed'), message: refusal(result.error) });
      return false;
    }
    const { saved } = result;
    if (!quiet || saved.removed.length) {
      toast({
        kind: 'success',
        title: saved.written ? t('themes.save.done') : t('themes.save.same'),
        message: saved.removed.length ? t('themes.save.removed', { count: saved.removed.length,
          names: saved.removed.join(', ') }) : undefined,
      });
    }
    return true;
  }
  ui.saveAndApply = async () => {
    if (editor.dirty.value || (editor.base.value && editor.base.value.source === 'draft')) {
      if (!(await save({ quiet: true }))) return;
    }
    setApplyError(null);
    const result = await ctx.api.post('/api/themes/apply', {});
    if (!result.ok) {
      setApplyError(result.error);
      return;
    }
    setApplyJob(result.data.job);
    app.stores.jobs.refresh();
  };
  const merge = async () => {
    setDialog(null);
    const outcome = await editor.reloadAndMerge();
    if (!outcome.ok) {
      toast({ kind: 'error', title: t('themes.merge.failed'), message: refusal(outcome.error) });
      return;
    }
    if (outcome.refused.length) setReport(outcome);
    else toast({ kind: 'success', title: t('themes.merge.done', { count: outcome.applied }) });
  };
  const needClean = () => {
    if (!editor.dirty.value) return true;
    toast({ kind: 'warning', title: t('themes.clean.first') });
    return false;
  };
  const openVersion = async (v) => {
    const result = await ctx.api.get(`/api/themes/versions/${encodeURIComponent(v.id)}`);
    if (!result.ok) {
      toast({ kind: 'error', title: t('themes.versions.failed'), message: refusal(result.error) });
      return;
    }
    setVersionsOpen(false);
    batch(() => {
      editor.preview.value = null;
      editor.viewing.value = { id: v.id, tree: result.data.tree, at: v.made_at, current: v.id === 'current' };
    });
  };
  const compareVersion = async (v, tree = null) => {
    const version = tree || (await ctx.api.get(`/api/themes/versions/${encodeURIComponent(v.id)}`));
    const before = tree || (version.ok ? version.data.tree : null);
    if (!before) return;
    setDialog({ kind: 'compare', title: t('themes.compare.version_title', { when: v.made_at ? formatDate(v.made_at, 'datetime') : v.id }),
      description: t('themes.compare.version_lead'), before, after: editor.base.value.tree, version: v });
    const result = await ctx.api.post('/api/themes/compare', { before, after: editor.base.value.tree });
    setDialog((d) => (d && d.kind === 'compare' ? { ...d, result: result.ok ? result.data : null } : d));
  };
  const restoreVersion = async (v) => {
    if (!needClean()) return;
    const result = await ctx.api.post(`/api/themes/versions/${encodeURIComponent(v.id)}/restore`, {},
      { ifMatch: editor.base.value.version });
    if (!result.ok) {
      toast({ kind: 'error', title: t('themes.versions.restore_failed'), message: refusal(result.error) });
      return;
    }
    setDialog(null);
    setVersionsOpen(false);
    const themes = await ctx.api.get('/api/themes');
    if (themes.ok) editor.adopt(themes.data, themes.etag);
    toast({ kind: 'success', title: t('themes.versions.restored', { when: v.made_at ? formatDate(v.made_at, 'datetime') : v.id }) });
  };
  const rebase = async () => {
    if (!needClean()) return;
    const result = await ctx.api.post('/api/themes/rebase', {}, { ifMatch: editor.base.value.version });
    if (!result.ok) {
      toast({ kind: 'error', title: t('themes.rebase.failed'), message: refusal(result.error) });
      return;
    }
    const themes = await ctx.api.get('/api/themes');
    if (themes.ok) editor.adopt(themes.data, themes.etag);
    toast({ kind: 'success', title: t('themes.rebase.done', { count: result.data.to_check }) });
    if (result.data.to_check) ui.leftTab.value = 'check';
  };
  const compareProposal = async () => {
    const draft = await ctx.api.get('/api/themes/draft');
    if (!draft.ok) {
      toast({ kind: 'error', title: t('themes.proposal.failed'), message: refusal(draft.error) });
      return;
    }
    setDialog({ kind: 'proposal', run: draft.data.run, before: editor.base.value.tree, after: draft.data.tree });
    const result = await ctx.api.post('/api/themes/compare', { before: editor.base.value.tree, after: draft.data.tree });
    setDialog((d) => (d && d.kind === 'proposal' ? { ...d, result: result.ok ? result.data : null } : d));
  };
  const decideProposal = async (decision, run) => {
    if (!needClean()) return;
    const result = await ctx.api.post('/api/themes/proposal', { decision, run }, { ifMatch: editor.base.value.version });
    if (!result.ok) {
      toast({ kind: 'error', title: t('themes.proposal.failed'), message: refusal(result.error) });
      return;
    }
    setDialog(null);
    const themes = await ctx.api.get('/api/themes');
    if (themes.ok) editor.adopt(themes.data, themes.etag);
    toast({ kind: 'success', title: decision === 'adopt' ? t('themes.proposal.adopted') : t('themes.proposal.kept') });
  };

  // ── AI proposals ──
  const acceptedOps = (proposal, accepted) => proposal.items.filter((_, i) => accepted.has(i)).map((it) => it.op);
  const previewProposal = async (proposal, accepted) => {
    const result = await ctx.api.post('/api/themes/ops', { tree: editor.tree.value, ops: acceptedOps(proposal, accepted), lenient: true });
    if (!result.ok) {
      toast({ kind: 'error', title: t('themes.ai.failed'), message: refusal(result.error) });
      return;
    }
    const refused = result.data.steps.filter((s) => s.refused).length;
    setHandoff(null);
    editor.preview.value = { tree: result.data.tree, proposal, accepted, refused };
  };
  const applyProposal = async (proposal, accepted) => {
    const ops = acceptedOps(proposal, accepted);
    editor.preview.value = null;
    const result = await editor.run(ops, { lenient: true, labelKey: { key: 'themes.ai.entry', params: { count: ops.length } },
      label: `apply ${ops.length} AI proposals` });
    setHandoff(null);
    if (result.ok) {
      const refused = result.steps.filter((s) => s.refused).length;
      toast({ kind: 'success', title: t('themes.ai.applied', { count: ops.length - refused }),
        message: refused ? t('themes.ai.applied_refused', { count: refused }) : undefined });
    } else toast({ kind: 'error', title: t('themes.ai.failed'), message: refusal(result.error) });
  };

  // ── rendering ──
  if (editor.loading.value) {
    return html`<div class="cx-page cx-themes">
      <h1 class="cx-page__title">${t('nav.themes')}</h1>
      <p class="cx-themes-empty-line" aria-busy="true">${t('common.loading')}</p>
    </div>`;
  }
  if (editor.error.value) {
    return html`<div class="cx-page">
      <h1 class="cx-page__title">${t('nav.themes')}</h1>
      <${ErrorCard} error=${editor.error.value} onRetry=${() => editor.load()} live />
    </div>`;
  }
  const info = editor.info.value || {};
  if (!editor.tree.value) {
    const next = info.empty && info.empty.next;
    return html`<div class="cx-page">
      <h1 class="cx-page__title">${t('nav.themes')}</h1>
      <${EmptyState} icon="file" level=${2} title=${t('themes.none.title')}
        action=${next ? { label: t('themes.none.action'), href: '/' } : null}>${t('themes.none.text')}<//>
    </div>`;
  }

  const index = editor.index.value;
  const tree = editor.tree.value;
  const base = editor.base.value;
  const past = editor.past.value;
  const future = editor.future.value;
  const dirty = editor.dirty.value;
  const viewing = editor.viewing.value;
  const preview = editor.preview.value;
  const readOnly = editor.readOnly.value;
  const restored = editor.restored.value;
  const running = applyJob ? jobs.find((j) => j.id === applyJob.id) : null;
  const undoLabel = past.length ? t('themes.undo', { what: entryLabel(past[past.length - 1]) }) : t('themes.undo.none');
  const redoLabel = future.length ? t('themes.redo', { what: entryLabel(future[future.length - 1]) }) : t('themes.redo.none');
  const levelsMenu = [
    ...tree.levels.map((_, i) => ({ id: `rename-level:${i + 1}`, label: t('themes.levels.rename', { name: levelName(tree, i + 1, lang), level: i + 1 }) })),
    { kind: 'separator', id: 'sep' },
    { id: 'insert-level', label: t('themes.levels.insert'), disabled: tree.depth >= 4 },
    { id: 'remove-level', label: t('themes.levels.remove'), disabled: tree.depth <= 1, danger: true },
  ];
  const moreMenu = [
    { id: 'versions', label: t('themes.more.versions') },
    { id: 'ai', label: t('themes.more.ai') },
    { id: 'new-top', label: t('themes.more.new_top', { level: levelName(tree, 1, lang) }) },
    { kind: 'separator', id: 'sep' },
    { id: 'discard', label: t('themes.more.discard'), danger: true, disabled: !dirty },
  ];
  const onToolbarMenu = (item) => {
    if (item.id.startsWith('rename-level:')) setDialog({ kind: 'rename-level', level: Number(item.id.split(':')[1]) });
    else if (item.id === 'insert-level') setDialog({ kind: 'insert-level' });
    else if (item.id === 'remove-level') setDialog({ kind: 'remove-level' });
    else if (item.id === 'versions') setVersionsOpen(true);
    else if (item.id === 'ai') setHandoff({});
    else if (item.id === 'new-top') ui.create(null);
    else if (item.id === 'discard') setDialog({ kind: 'discard' });
  };

  const banners = [];
  if (viewing) {
    banners.push(html`<${Banner} key="viewing" icon="file" actions=${html`
      ${viewing.current ? null : html`<${Button} size="s" onClick=${() => compareVersion({ id: viewing.id, made_at: viewing.at }, viewing.tree)}>
        ${t('themes.versions.compare')}<//>
      <${Button} size="s" onClick=${() => restoreVersion({ id: viewing.id, made_at: viewing.at })}>${t('themes.versions.restore')}<//>`}
      <${Button} size="s" variant="primary" onClick=${() => {
        editor.viewing.value = null;
      }}>${t('themes.viewing.back')}<//>`}>
      ${t('themes.viewing.text', { when: viewing.at ? formatDate(viewing.at, 'datetime') : viewing.id })}<//>`);
  }
  if (preview) {
    banners.push(html`<${Banner} key="preview" icon="info" actions=${html`
      <${Button} size="s" variant="primary" onClick=${() => applyProposal(preview.proposal, preview.accepted)}>
        ${t('themes.ai.apply', { count: preview.accepted.size })}<//>
      <${Button} size="s" onClick=${() => {
        setHandoff({ resume: { proposal: preview.proposal, accepted: preview.accepted } });
        editor.preview.value = null;
      }}>${t('themes.preview.back')}<//>
      <${Button} size="s" variant="ghost" onClick=${() => {
        editor.preview.value = null;
      }}>${t('themes.preview.stop')}<//>`}>
      ${t('themes.preview.text', { count: preview.accepted.size, refused: preview.refused })}<//>`);
  }
  if (restored && !restored.stale) {
    banners.push(html`<${Banner} key="restored" icon="check" actions=${html`<${Button} size="s"
      onClick=${() => editor.discard()}>${t('themes.restored.discard')}<//>`}>
      ${t('themes.restored.text', { count: restored.count, when: formatDate(restored.at, 'datetime') })}<//>`);
  }
  if (restored && restored.stale) {
    banners.push(html`<${Banner} key="stale-draft" tone="warning" actions=${html`
      <${Button} size="s" variant="primary" onClick=${async () => {
        const entries = (restored.draft.past || []).slice(restored.draft.saved_mark || 0);
        const outcome = await editor.reloadAndMerge(entries);
        if (outcome.ok && outcome.refused.length) setReport(outcome);
        else if (outcome.ok) toast({ kind: 'success', title: t('themes.merge.done', { count: outcome.applied }) });
      }}>${t('themes.stale.merge')}<//>
      <${Button} size="s" onClick=${() => editor.discard()}>${t('themes.restored.discard')}<//>`}>
      ${t('themes.restored.stale', { count: restored.count, when: formatDate(restored.at, 'datetime') })}<//>`);
  }
  if (base && base.source === 'draft') {
    banners.push(html`<${Banner} key="draft">${t('themes.source.draft')}<//>`);
  }
  if (info.based_on_current === false) {
    banners.push(html`<${Banner} key="vocabulary" tone="warning" actions=${base && base.source === 'saved'
      ? html`<${Button} size="s" onClick=${rebase} softDisabled=${dirty}>${t('themes.vocabulary.rebase')}<//>` : null}>
      ${t('themes.vocabulary.text', { missing: info.missing_count || 0, extra: info.extra_count || 0 })}
      ${dirty ? html` <span class="cx-themes-banner__note">${t('themes.clean.first')}</span>` : null}<//>`);
  }
  if (info.proposal && info.proposal.pending) {
    banners.push(html`<${Banner} key="proposal" actions=${html`<${Button} size="s" onClick=${compareProposal}>
      ${t('themes.proposal.compare')}<//>`}>${t('themes.proposal.text')}<//>`);
  }
  if (index && index.toCheck.length && ui.leftTab.value !== 'check') {
    banners.push(html`<${Banner} key="check" actions=${html`<${Button} size="s" onClick=${() => {
      ui.leftTab.value = 'check';
      const first = index.toCheck[0];
      ui.active.value = `c:${first}`;
      ui.treeSel.value = new Set([`c:${first}`]);
    }}>${t('themes.check.review')}<//>`}>${t('themes.check.banner', { count: index.toCheck.length })}<//>`);
  }
  if (running) {
    const pct = running.progress && typeof running.progress.fraction === 'number' ? running.progress.fraction : null;
    banners.push(html`<${Banner} key="apply" icon="activity" actions=${html`<${Button} size="s" variant="ghost"
      onClick=${() => runtime.openActivity()}>${t('themes.apply.activity')}<//>`}>
      ${t('themes.apply.running', { stage: running.progress && running.progress.name ? running.progress.name : '' })}
      <${ProgressBar} value=${pct === null ? undefined : pct} label=${t('themes.apply.progress')} /><//>`);
  }

  const d = dialog;
  const close = () => setDialog(null);
  let modal = null;
  if (d && index) {
    const eindex = editor.editIndex.value;
    if (d.kind === 'rename') {
      modal = html`<${RenameDialog} open node=${eindex.nodes.get(d.id)} onClose=${close}
        levelLabel=${levelName(tree, eindex.level.get(d.id), lang)}
        onSubmit=${(names) => run([{ op: 'rename_node', node_id: d.id, names }])} />`;
    } else if (d.kind === 'rename-level') {
      modal = html`<${RenameLevelDialog} open tree=${tree} level=${d.level} onClose=${close}
        onSubmit=${(names) => run([{ op: 'rename_level', level: d.level, names }])} />`;
    } else if (d.kind === 'move-keywords') {
      const review = d.review ? [{ op: 'set_review', keywords: d.terms, state: 'reviewed' }] : [];
      const aside = d.terms.filter((k) => k in (tree.set_aside || {}));
      modal = html`<${PickNodeDialog} open index=${eindex} candidates=${keywordTargets(eindex, d.terms)}
        title=${t('themes.move.title', { count: d.terms.length, term: d.terms[0] })}
        description=${t('themes.move.lead')} submitLabel=${t('themes.move.submit')} onClose=${close}
        onSubmit=${async (target) => {
          const ops = aside.length ? [{ op: 'put_back', keywords: d.terms, node_id: target }]
            : [{ op: 'move_keywords', keywords: d.terms, node_id: target }];
          const outcome = await run([...ops, ...review]);
          if (outcome === true && d.after) d.after();
          return outcome;
        }} />`;
    } else if (d.kind === 'put-back-into') {
      modal = html`<${PickNodeDialog} open index=${eindex} candidates=${eindex.order}
        title=${t('themes.putback.title', { count: d.terms.length, term: d.terms[0] })}
        description=${t('themes.putback.lead')} submitLabel=${t('themes.putback.submit')} onClose=${close}
        onSubmit=${(target) => run([{ op: 'put_back', keywords: d.terms, node_id: target }])} />`;
    } else if (d.kind === 'move-node') {
      const level = eindex.level.get(d.id);
      modal = html`<${PickNodeDialog} open index=${eindex} candidates=${nodeParents(eindex, d.id)}
        title=${t('themes.movenode.title', { name: nodeName(eindex.nodes.get(d.id), lang) })}
        description=${t('themes.movenode.lead', { level: levelName(tree, level - 1, lang) })}
        submitLabel=${t('themes.move.submit')} onClose=${close}
        onSubmit=${(target) => run([{ op: 'move_node', node_id: d.id, parent: target }])} />`;
    } else if (d.kind === 'merge') {
      modal = html`<${PickNodeDialog} open index=${eindex} candidates=${mergeTargets(eindex, d.id)}
        title=${t('themes.merge_node.title', { name: nodeName(eindex.nodes.get(d.id), lang) })}
        description=${t('themes.merge_node.lead', { name: nodeName(eindex.nodes.get(d.id), lang) })}
        submitLabel=${t('themes.merge_node.submit')} onClose=${close}
        onSubmit=${(target) => run([{ op: 'merge_nodes', source: d.id, target }])} />`;
    } else if (d.kind === 'split') {
      modal = html`<${SplitDialog} open index=${eindex} id=${d.id} onClose=${close}
        onSubmit=${(members, names) => {
          const [newId] = nextIds(tree, 1);
          return run([{ op: 'split_node', node_id: d.id, parts: [{ members, names }], ids: [newId] }]);
        }} />`;
    } else if (d.kind === 'create') {
      modal = html`<${CreateDialog} open index=${eindex} parent=${d.parent} onClose=${close}
        onSubmit=${async (names) => {
          const [newId] = nextIds(tree, 1);
          const outcome = await run([{ op: 'create_node', parent: d.parent, names, node_id: newId }]);
          if (outcome === true) setTimeout(() => ui.openNode(newId), 0);
          return outcome;
        }} />`;
    } else if (d.kind === 'aside') {
      const current = d.terms.length === 1 && tree.set_aside && tree.set_aside[d.terms[0]] ? tree.set_aside[d.terms[0]].reason : '';
      modal = html`<${SetAsideDialog} open terms=${d.terms} current=${current} onClose=${close}
        onSubmit=${async (reason) => {
          const review = d.review ? [{ op: 'set_review', keywords: d.terms, state: 'reviewed' }] : [];
          const outcome = await run([{ op: 'set_aside', keywords: d.terms, reason }, ...review]);
          if (outcome === true && d.after) d.after();
          return outcome;
        }} />`;
    } else if (d.kind === 'attribution') {
      modal = html`<${AttributionDialog} open index=${eindex} terms=${d.terms} onClose=${close}
        onSubmit=${(levels) => run([{ op: 'set_attribution', keywords: d.terms, levels }])} />`;
    } else if (d.kind === 'insert-level') {
      modal = html`<${InsertLevelDialog} open tree=${tree} onClose=${close}
        onSubmit=${(at) => run([{ op: 'insert_level', at }])} />`;
    } else if (d.kind === 'remove-level') {
      modal = html`<${RemoveLevelDialog} open tree=${tree} onClose=${close}
        onSubmit=${(at) => run([{ op: 'remove_level', at }])} />`;
    } else if (d.kind === 'compare') {
      const names = new Map([...d.before.nodes, ...d.after.nodes].map((n) => [n.id, n]));
      modal = html`<${CompareDialog} open title=${d.title} description=${d.description} result=${d.result}
        names=${names} onClose=${close} footer=${html`<${Button} onClick=${close}>${t('common.close')}<//>
          <${Button} variant="primary" onClick=${() => restoreVersion(d.version)}>${t('themes.versions.restore')}<//>`} />`;
    } else if (d.kind === 'proposal') {
      const names = new Map([...d.before.nodes, ...d.after.nodes].map((n) => [n.id, n]));
      modal = html`<${CompareDialog} open title=${t('themes.proposal.title')} description=${t('themes.proposal.lead')}
        result=${d.result} names=${names} onClose=${close} footer=${html`
          <${Button} variant="ghost" onClick=${close}>${t('common.cancel')}<//>
          <${Button} onClick=${() => decideProposal('keep', d.run)}>${t('themes.proposal.keep')}<//>
          <${Button} variant="primary" onClick=${() => decideProposal('adopt', d.run)}>${t('themes.proposal.adopt')}<//>`} />`;
    } else if (d.kind === 'stale') {
      modal = html`<${ConfirmDialog} open title=${t('themes.stale.title')} confirmLabel=${t('themes.stale.merge')}
        cancelLabel=${t('common.cancel')} onAnswer=${(yes) => (yes ? merge() : close())}>
        <p>${t('themes.stale.text', { count: editor.unsaved.value })}</p><//>`;
    } else if (d.kind === 'discard') {
      modal = html`<${ConfirmDialog} open danger title=${t('themes.discard.title')} confirmLabel=${t('themes.discard.confirm')}
        onAnswer=${(yes) => {
          if (yes) editor.discard();
          close();
        }}><p>${t('themes.discard.text', { count: editor.unsaved.value })}</p><//>`;
    }
  }

  const status = viewing ? t('themes.status.viewing') : preview ? t('themes.status.preview')
    : dirty ? t('themes.status.unsaved', { count: editor.unsaved.value })
      : base.source === 'draft' ? t('themes.status.proposal') : t('themes.status.saved');

  return html`<div class=${`cx-page cx-themes ${readOnly ? 'is-read-only' : ''}`}>
    <header class="cx-themes__head">
      <div class="cx-themes__titles">
        <h1 class="cx-page__title">${t('nav.themes')}</h1>
        <p class="cx-themes__status">
          <span class=${`cx-themes-state ${dirty ? 'is-dirty' : ''}`} aria-hidden="true"></span>
          <span>${status}</span>
          ${editor.busy.value ? html`<span class="cx-spinner cx-themes__busy" aria-hidden="true"></span>` : null}
          ${tree.saved && !dirty ? html`<span class="cx-themes__saved">${t('themes.status.saved_at', { when: formatDate(tree.saved.at, 'datetime') })}</span>` : null}
          <span class="cx-themes__levels">${tree.levels.map((_, i) => levelName(tree, i + 1, lang)).join(' › ')}</span>
        </p>
      </div>
      <div class="cx-themes__toolbar" role="toolbar" aria-label=${t('themes.toolbar')}>
        <${IconButton} icon="undo" label=${undoLabel} disabled=${!past.length || readOnly}
          onClick=${() => editor.undo()} class="cx-themes__undo" aria-keyshortcuts="Control+Z" />
        <${IconButton} icon="redo" label=${redoLabel} disabled=${!future.length || readOnly}
          onClick=${() => editor.redo()} class="cx-themes__redo" aria-keyshortcuts="Control+Shift+Z" />
        <${MenuButton} label=${t('themes.levels')} items=${levelsMenu} onSelect=${onToolbarMenu} variant="ghost" />
        <${MenuButton} label=${t('themes.more')} items=${moreMenu} onSelect=${onToolbarMenu} variant="ghost" />
        <${Button} variant=${dirty ? 'primary' : 'secondary'}
          disabled=${readOnly || (!dirty && base.source !== 'draft')} onClick=${() => save()}
          aria-keyshortcuts="Control+S">${base.source === 'draft' && !dirty ? t('themes.save.proposal') : t('common.save')}<//>
        <${Button} disabled=${readOnly || Boolean(running)} loading=${Boolean(running)}
          onClick=${() => ui.saveAndApply()}>${t('themes.save_apply')}<//>
      </div>
    </header>
    ${banners.length || applyError ? html`<div class="cx-themes__banners">
      ${banners}
      ${applyError ? html`<${ErrorCard} error=${applyError} compact onDismiss=${() => setApplyError(null)}
        onRetry=${() => ui.saveAndApply()} />` : null}
    </div>` : null}
    <div class="cx-themes__body">
      <${OutlinePane} editor=${editor} ui=${ui} />
      <${CentrePane} editor=${editor} ui=${ui} atlas=${atlas} atlasError=${atlasError} onRetryAtlas=${loadAtlas} />
      <${SidePanel} editor=${editor} ui=${ui} atlas=${atlas} />
    </div>
    <p class="cx-visually-hidden" aria-live="polite" aria-atomic="true">${announcement}</p>
    ${modal}
    <${MergeReportDialog} open=${Boolean(report)} report=${report} onClose=${() => setReport(null)} />
    <${VersionsDrawer} open=${versionsOpen} api=${ctx.api} editor=${editor} onClose=${() => setVersionsOpen(false)}
      onOpenVersion=${openVersion} onCompare=${(v) => compareVersion(v)} onRestore=${restoreVersion} />
    ${handoff ? html`<${ThemeHandoffDialog} open api=${ctx.api} editor=${editor} resume=${handoff.resume || null}
      onClose=${() => setHandoff(null)} onPreview=${previewProposal} onApply=${applyProposal} />` : null}
    <${ConfirmDialog} open=${Boolean(leave)} title=${t('themes.leave.title')} confirmLabel=${t('themes.leave.confirm')}
      cancelLabel=${t('leave.stay')} onAnswer=${(yes) => {
        const answer = leave;
        setLeave(null);
        if (answer) answer.resolve(yes);
      }}>
      <p>${t('themes.leave.text', { count: editor.unsaved.value })}</p>
    <//>
  </div>`;
}

export const page = definePage(() => html`<${ThemesEditor} />`, { styles: ['/static/css/themes.css'] });
