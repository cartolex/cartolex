/**
 * The « To check » queue: the keywords a rebase added, each with its proposed
 * place. Its own keys: A accepts a keyword where it is, M moves it, S sets it
 * aside (each marks it reviewed), J and K go to the next and the previous.
 */

import { html } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { EmptyState, TreeView } from '../../components/index.js';
import { nodePath } from './dialogs.js';
import { KeywordRow } from './rows.js';
import { firstPlace, suggestionKey } from './fit.js';

/** The queue's keys on its active row; true when the key was one of them. */
export function reviewKey(event, row, { editor, ui }) {
  if (!row || event.ctrlKey || event.metaKey || event.altKey) return false;
  const key = event.key.toLowerCase();
  const list = editor.index.value.toCheck;
  const at = list.indexOf(row.term);
  const go = (term) => {
    if (!term) return;
    ui.active.value = `c:${term}`;
    ui.treeSel.value = new Set([`c:${term}`]);
  };
  const next = () => go(list[at + 1] || list[at - 1]);
  if (key === 'a') {
    next();
    ui.accept([row.term]);
  } else if (key === 'm') {
    ui.moveKeywords([row.term], { review: true, after: next });
  } else if (key === 's') {
    ui.setAside([row.term], { review: true, after: next });
  } else if (key === 'j' || key === 'k') {
    go(list[at + (key === 'j' ? 1 : -1)]);
  } else if (/^[1-3]$/.test(key)) {
    next();
    return suggestionKey(event, row.term, ui);
  } else {
    return false;
  }
  return true;
}

/** The queue, as the outline's third tab. */
export function ReviewQueue({ editor, ui }) {
  const index = editor.index.value;
  const rows = index.toCheck.map((term, i) => ({
    key: `c:${term}`, kind: 'check', term, level: 1, setsize: index.toCheck.length, posinset: i + 1,
    parent: null, text: term, multi: false,
  }));
  const place = (term) => {
    const node = index.tree.keywords[term];
    return node !== undefined ? nodePath(index, node) : t('themes.check.aside');
  };
  return html`<div class="cx-themes-check">
    <p class="cx-themes-check__help" id="cx-themes-check-help">${t('themes.check.keys')}</p>
    <${TreeView} rows=${rows} label=${t('themes.check.label')} class="cx-themes-outline__tree"
      treeRef=${ui.outlineRef}
      activeKey=${ui.active.value} onActiveChange=${(key) => ui.setActive(key)}
      selection=${ui.treeSel.value} onSelectionChange=${(keys) => ui.select(keys)}
      onOpen=${(row) => ui.open(row)}
      onKeyCommand=${(event, row) => reviewKey(event, row, { editor, ui })}
      rowMenu=${(keys) => ui.menuFor(keys)} onRowMenu=${(item, keys) => ui.onMenu(item, keys)}
      renderRow=${(row) => {
        const best = firstPlace(index, ui, row.term);
        const proposed = t('themes.check.place', { place: place(row.term) });
        return html`<${KeywordRow} index=${index} term=${row.term}
          detail=${best ? `${proposed} · ${best}` : proposed} />`;
      }}
      empty=${html`<${EmptyState} icon="check" title=${t('themes.check.empty')}>
        ${t('themes.check.empty.text')}<//>`} />
  </div>`;
}
