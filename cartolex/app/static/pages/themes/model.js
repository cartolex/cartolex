/**
 * The theme tree in the browser: an index for fast reads, names, live
 * weights, search, and patches for undo and redo.
 *
 * Everything here is pure: a tree (`cartolex-themes/1`, as `GET /api/themes`
 * gives it) goes in, plain data comes out, nothing is changed. The server
 * applies the operations (`POST /api/themes/ops`); the browser only reads.
 */

import { locale, t } from '../../core/i18n.js';

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
