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

import { formatList, locale, t } from '../../core/i18n.js';
import { lang2, nodeName } from './model.js';

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
  [/^mark (\d+) keywords? kept$/, (m) => ['themes.op.kept', { count: Number(m[1]) }]],
  [/^clear the review of (\d+) keywords?$/, (m) => ['themes.op.review_cleared', { count: Number(m[1]) }]],
  [/^count (\d+) keywords? at their node's level$/, (m) => ['themes.op.count_default', { count: Number(m[1]) }]],
  [/^count (\d+) keywords? nowhere$/, (m) => ['themes.op.count_nowhere', { count: Number(m[1]) }]],
  [/^count (\d+) keywords? down to level (\d+)$/, (m) => ['themes.op.count_level', { count: Number(m[1]), level: Number(m[2]) }]],
  [/^remove empty nodes? (.+)$/, (m, n) => ['themes.op.prune', { names: formatList(m[1].split(', ').map(n)) }]],
  [/^insert level (\d+)$/, (m) => ['themes.op.insert_level', { level: Number(m[1]) }]],
  [/^keep the curated tree over the proposal \S+ at an apply$/, () => ['themes.saved.kept_on_apply', {}]],
  [/^keep the curated tree over the proposal \S+$/, () => ['themes.saved.kept', {}]],
  [/^adopt the grouping proposal \S+$/, () => ['themes.saved.adopted', {}]],
  [/^rebase onto the new vocabulary$/, () => ['themes.saved.rebased', {}]],
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
