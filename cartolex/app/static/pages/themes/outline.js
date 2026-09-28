/**
 * The outline: the tree at any depth, its keywords on demand, a search over
 * every keyword, and two trays: the keywords set aside and the « to check »
 * queue a rebase fills.
 */

import { html, useMemo, useState } from '../../core/preact.js';
import { formatNumber, locale, t } from '../../core/i18n.js';
import { Button, EmptyState, IconButton, Input, Tabs, TreeView } from '../../components/index.js';
import { lang2, nodeName, pathOf, search, searchIndex } from './model.js';
import { KeywordRow, NodeRow } from './rows.js';
import { ReviewQueue } from './review.js';

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
      panel=${(id) => (id === 'aside' ? aside
        : id === 'check' ? html`<${ReviewQueue} editor=${editor} ui=${ui} />` : outline)} />
  </section>`;
}
