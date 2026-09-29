/**
 * The comb read on the tree being edited, in the « Borderline » tab: the placed
 * keywords whose texts support a higher node (`POST /api/themes/levels`):
 * « move up to <node> » or « too broad for any theme », each with its share.
 *
 * Keys: U applies the suggestion (moves the keyword up, or sets it aside as too
 * broad), A keeps it here (review state `kept`, which leaves both lists), J and
 * K go to the next and the previous. The list is asked only when shown, a
 * moment after the last change.
 */

import { html, signal, useEffect } from '../../core/preact.js';
import { formatNumber, t } from '../../core/i18n.js';
import { EmptyState, ErrorCard, Select, TreeView } from '../../components/index.js';
import { nodePath } from './dialogs.js';
import { KeywordRow } from './rows.js';
import { shortShare } from './labels.js';

/** How long after the last change the list is asked again (ms). */
const SETTLE = 250;

/**
 * The levels list of one page visit.
 * @param {object} options
 * @param {object} options.api the page's API client
 */
export function createLevels({ api }) {
  const shown = signal('margin'); // 'margin': the borderline list; 'levels': this one
  const data = signal(null); // {tree, items, total, theta, empty}
  const error = signal(null);
  let seq = 0;
  let asked = null;

  async function load(tree) {
    const mine = ++seq;
    const result = await api.post('/api/themes/levels', { tree, limit: 500 });
    if (mine !== seq) return;
    if (!result.ok) {
      error.value = result.error;
      return;
    }
    error.value = null;
    const d = result.data;
    data.value = { tree, items: d.items, total: d.total, theta: d.theta, tooBroad: d.too_broad, empty: d.empty };
  }

  /** Ask the list for *tree*, a moment after the last change; returns the cancel. */
  function want(tree) {
    if (!tree || asked === tree) return () => {};
    const timer = setTimeout(() => {
      asked = tree;
      load(tree);
    }, data.value ? SETTLE : 0);
    return () => clearTimeout(timer);
  }

  return { shown, data, error, want, retry: (tree) => load(tree) };
}

/** The choice between the two lists of the tab. */
export function MeasureSelect({ ui }) {
  const levels = ui.levels;
  return html`<label class="cx-themes-border__level">
    <span>${t('themes.levels.show')}</span>
    <${Select} value=${levels.shown.value}
      options=${[{ value: 'margin', label: t('themes.levels.show.margin') },
        { value: 'levels', label: t('themes.levels.show.levels') }]}
      onChange=${(e) => {
        levels.shown.value = e.currentTarget.value;
      }} />
  </label>`;
}

/** The keywords the texts would put higher, in the borderline tab. */
export function LevelsPane({ editor, ui }) {
  const index = editor.index.value;
  const tree = editor.tree.value;
  const levels = ui.levels;
  useEffect(() => levels.want(tree), [tree]);
  const data = levels.data.value;
  const items = data ? data.items.filter((i) => index.tree.keywords[i.keyword] !== undefined
    && (i.to === null || index.nodes.has(i.to))) : [];
  const rows = items.map((item, i) => ({
    key: `b:${item.keyword}`, kind: 'border', term: item.keyword, item, level: 1,
    setsize: items.length, posinset: i + 1, parent: null, text: item.keyword, multi: false,
  }));
  const readOnly = editor.readOnly.value;
  const go = (term) => {
    if (!term) return;
    ui.active.value = `b:${term}`;
    ui.treeSel.value = new Set([`b:${term}`]);
  };
  const act = (what, item) => {
    if (!item || readOnly) return;
    const at = items.findIndex((i) => i.keyword === item.keyword);
    const after = () => go((items[at + 1] || items[at - 1] || {}).keyword);
    if (what === 'apply') {
      after();
      ui.runOrToast([item.to
        ? { op: 'move_keywords', keywords: [item.keyword], node_id: item.to }
        : { op: 'set_aside', keywords: [item.keyword], reason: item.reason || '' }]);
    } else if (what === 'keep') {
      after();
      ui.runOrToast([{ op: 'set_review', keywords: [item.keyword], state: 'kept' }]);
    } else if (what === 'show') {
      ui.openKeyword(item.keyword);
    }
  };
  const onKey = (event, row) => {
    if (!row || event.ctrlKey || event.metaKey || event.altKey) return false;
    const key = event.key.toLowerCase();
    const at = items.findIndex((i) => i.keyword === row.term);
    if (key === 'u') act('apply', row.item);
    else if (key === 'a') act('keep', row.item);
    else if (key === 'j' || key === 'k') go((items[at + (key === 'j' ? 1 : -1)] || {}).keyword);
    else return false;
    return true;
  };
  const suggestion = (item) => (item.to
    ? t('themes.levels.up', { place: nodePath(index, item.to) }) : t('themes.levels.broad'));
  const menu = (keys) => {
    const row = rows.find((r) => keys.includes(r.key));
    if (!row) return [];
    return [
      { id: 'apply', label: suggestion(row.item), hint: t('themes.key.u'), disabled: readOnly },
      { id: 'keep', label: t('themes.border.keep'), hint: t('themes.key.a'), disabled: readOnly },
      { kind: 'separator', id: 'sep' },
      { id: 'show', label: t('themes.action.show_node') },
    ];
  };
  const onMenu = (entry, keys) => {
    const row = rows.find((r) => keys.includes(r.key));
    if (row) act(entry.id, row.item);
  };
  return html`<div class="cx-themes-check cx-themes-border">
    <div class="cx-themes-border__head">
      <${MeasureSelect} ui=${ui} />
      <p class="cx-themes-check__help">${t('themes.levels.keys')}</p>
      ${data && data.theta !== null ? html`<p class="cx-themes-border__count" aria-live="polite">
        ${t('themes.levels.count', { total: data.total, broad: data.tooBroad })}</p>` : null}
    </div>
    ${levels.error.value ? html`<${ErrorCard} error=${levels.error.value} compact
      onRetry=${() => levels.retry(tree)} />` : null}
    ${!data && !levels.error.value ? html`<p class="cx-themes-empty-line" aria-busy="true">${t('common.loading')}</p>` : null}
    ${data ? html`<${TreeView} rows=${rows} label=${t('themes.levels.label')} class="cx-themes-outline__tree"
      treeRef=${ui.outlineRef}
      activeKey=${ui.active.value} onActiveChange=${(key) => ui.setActive(key)}
      selection=${ui.treeSel.value} onSelectionChange=${(keys) => ui.select(keys)}
      onKeyCommand=${onKey} rowMenu=${menu} onRowMenu=${onMenu}
      renderRow=${(row) => html`<${KeywordRow} index=${index} term=${row.term}
        detail=${html`<span class=${`cx-themes-levels__mark ${row.item.to ? 'is-up' : 'is-broad'}`}
          aria-hidden="true"></span>${suggestion(row.item)}`}
        after=${html`<span class="cx-themes-row__share" title=${t('themes.levels.share_help')}>
          ${shortShare(row.item.share)}</span>`} />`}
      empty=${data.theta === null
        ? html`<${EmptyState} icon="info" title=${t('themes.levels.no_texts')}>${t('themes.levels.no_texts.text')}<//>`
        : html`<${EmptyState} icon="check" title=${t('themes.levels.empty')}>${t('themes.levels.empty.text')}<//>`} />` : null}
    <p class="cx-themes-panel__note cx-themes-border__note">${data && data.theta !== null
      ? t('themes.levels.measure', { theta: formatNumber(data.theta, { maximumFractionDigits: 3 }) })
      : null}</p>
  </div>`;
}
