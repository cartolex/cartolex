/**
 * How well keywords fit the tree being edited: the « Borderline » tab (the
 * placed keywords nearest the border between their node and another, by the
 * cosine margin of `POST /api/themes/borderline`) and the suggested places of
 * the keywords set aside or to check (`POST /api/themes/suggestions`). Both
 * read the tree being edited, so they follow every change; they are asked
 * only when shown, a moment after the last change.
 *
 * Borderline keys: A keeps a keyword here (review state `kept`: reviewed, and
 * left out of this list; a review in a rebase's queue does not hide it), O moves it to
 * the other node, S sets it aside, J and K go to the next and the previous.
 * Suggested places: 1, 2 or 3 in the tray or the queue puts the keyword there.
 */

import { html, signal, useEffect } from '../../core/preact.js';
import { formatNumber, locale, t } from '../../core/i18n.js';
import { Button, EmptyState, ErrorCard, Select, TreeView } from '../../components/index.js';
import { lang2, levelName, nodeName } from './model.js';
import { nodePath } from './dialogs.js';
import { KeywordRow } from './rows.js';

/** How long after the last change the lists are asked again (ms). */
const SETTLE = 250;
/** Rows of the borderline list asked at a time. */
const PAGE = 100;

/**
 * The fit lists of one page visit.
 * @param {object} options
 * @param {object} options.api the page's API client
 */
export function createFit({ api }) {
  const level = signal(0); // 0: each keyword at its own node's level
  const border = signal(null); // {tree, level, items, total, negative}
  const borderError = signal(null);
  const more = signal(false);
  const suggested = signal(null); // {tree, map: {term: [{node, score}]}}
  const suggestError = signal(null);
  let borderSeq = 0;
  let suggestSeq = 0;
  let asked = { border: null, suggest: null };

  async function loadBorder(tree, { append = false } = {}) {
    const seq = ++borderSeq;
    const current = border.value;
    const offset = append && current ? current.items.length : 0;
    const limit = append ? PAGE : Math.min(500, Math.max(PAGE, current && current.level === level.value
      ? current.items.length : PAGE));
    if (append) more.value = true;
    const result = await api.post('/api/themes/borderline', {
      tree, level: level.value || null, offset, limit,
    });
    if (seq !== borderSeq) return;
    more.value = false;
    if (!result.ok) {
      borderError.value = result.error;
      return;
    }
    borderError.value = null;
    const d = result.data;
    border.value = {
      tree, level: level.value, total: d.total, negative: d.negative, empty: d.empty,
      items: append && current ? [...current.items, ...d.items] : d.items,
    };
  }

  async function loadSuggestions(tree) {
    const seq = ++suggestSeq;
    const result = await api.post('/api/themes/suggestions', { tree, top: 3 });
    if (seq !== suggestSeq) return;
    if (!result.ok) {
      suggestError.value = result.error;
      return;
    }
    suggestError.value = null;
    suggested.value = { tree, map: result.data.suggestions };
  }

  /** Ask the borderline list for *tree*, a moment after the last change; returns the cancel. */
  function wantBorder(tree) {
    if (!tree || (asked.border && asked.border.tree === tree && asked.border.level === level.value)) {
      return () => {};
    }
    const timer = setTimeout(() => {
      asked = { ...asked, border: { tree, level: level.value } };
      loadBorder(tree);
    }, border.value ? SETTLE : 0);
    return () => clearTimeout(timer);
  }

  /** Ask the suggested places for *tree* (its set-aside and « to check » keywords). */
  function wantSuggestions(tree) {
    const waiting = tree && (Object.keys(tree.set_aside || {}).length
      || Object.values(tree.review || {}).includes('to_check'));
    if (!waiting || asked.suggest === tree) return () => {};
    const timer = setTimeout(() => {
      asked = { ...asked, suggest: tree };
      loadSuggestions(tree);
    }, suggested.value ? SETTLE : 0);
    return () => clearTimeout(timer);
  }

  /** The suggested places of *term*, or null while they are asked. */
  function placesOf(term) {
    const s = suggested.value;
    return s && s.map[term] ? s.map[term] : null;
  }

  return {
    level, border, borderError, more, suggested, suggestError,
    wantBorder, wantSuggestions, placesOf,
    showMore: (tree) => loadBorder(tree, { append: true }),
    retryBorder: (tree) => loadBorder(tree),
  };
}

/** A cosine margin with its sign, two decimals. */
export function margin(value) {
  return formatNumber(value, { minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: 'exceptZero' });
}

/** A cosine as a score, two decimals. */
export function score(value) {
  return formatNumber(value, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/**
 * Put *term* at a suggested *node*: back from the tray, or moved and marked reviewed from the queue.
 * @returns {object[]} the operations
 */
export function placeOps(tree, term, node) {
  const aside = term in (tree.set_aside || {});
  const ops = [aside ? { op: 'put_back', keywords: [term], node_id: node }
    : { op: 'move_keywords', keywords: [term], node_id: node }];
  if ((tree.review || {})[term] === 'to_check') ops.push({ op: 'set_review', keywords: [term], state: 'reviewed' });
  return ops;
}

/** The keys 1, 2 and 3 on a row of the tray or the queue: put the keyword at that suggestion. */
export function suggestionKey(event, term, ui) {
  if (!term || event.ctrlKey || event.metaKey || event.altKey || !/^[1-3]$/.test(event.key)) return false;
  const places = ui.fit.placesOf(term);
  const place = places && places[Number(event.key) - 1];
  if (place) ui.placeAt(term, place.node);
  return true;
}

/** The first suggested place of *term*, as a short line for a row. */
export function firstPlace(index, ui, term) {
  const places = ui.fit.placesOf(term);
  if (!places || !places.length || !index.nodes.has(places[0].node)) return null;
  return t('themes.suggest.first', { place: nodePath(index, places[0].node), score: score(places[0].score) });
}

/** The suggested places of one keyword, as buttons (the side panel). */
export function SuggestedPlaces({ editor, ui, term }) {
  const index = editor.index.value;
  const tree = editor.tree.value;
  useEffect(() => ui.fit.wantSuggestions(tree), [tree]);
  const places = ui.fit.placesOf(term);
  const readOnly = editor.readOnly.value;
  return html`<section class="cx-themes-panel__section" aria-labelledby="cx-themes-panel-suggest">
    <h3 class="cx-themes-panel__subtitle" id="cx-themes-panel-suggest">${t('themes.suggest.title')}</h3>
    ${places === null ? html`<p class="cx-themes-empty-line" aria-busy="true">${t('common.loading')}</p>`
      : places.length ? html`<ol class="cx-themes-suggest">
        ${places.filter((p) => index.nodes.has(p.node)).map((p, i) => html`<li key=${p.node}>
          <${Button} size="s" disabled=${readOnly} aria-keyshortcuts=${String(i + 1)}
            onClick=${() => ui.placeAt(term, p.node)}>${t('themes.suggest.put', { place: nodePath(index, p.node) })}<//>
          <span class="cx-themes-row__count" title=${t('themes.suggest.score_help')}>${score(p.score)}</span>
        </li>`)}
      </ol>
      <p class="cx-themes-panel__note">${t('themes.suggest.note')}</p>`
        : html`<p class="cx-themes-empty-line">${t('themes.suggest.none')}</p>`}
  </section>`;
}

/** The borderline tab of the outline. */
export function BorderlinePane({ editor, ui }) {
  const index = editor.index.value;
  const tree = editor.tree.value;
  const fit = ui.fit;
  const lang = lang2(locale.value);
  const chosen = fit.level.value;
  useEffect(() => fit.wantBorder(tree), [tree, chosen]);
  const data = fit.border.value;
  const items = data ? data.items.filter((i) => index.tree.keywords[i.keyword] !== undefined) : [];
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
  const nextOf = (term) => {
    const at = items.findIndex((i) => i.keyword === term);
    return () => go((items[at + 1] || items[at - 1] || {}).keyword);
  };
  const act = (what, item) => {
    if (!item || readOnly) return;
    const after = nextOf(item.keyword);
    if (what === 'keep') {
      after();
      ui.runOrToast([{ op: 'set_review', keywords: [item.keyword], state: 'kept' }]);
    } else if (what === 'other') {
      after();
      ui.runOrToast([{ op: 'move_keywords', keywords: [item.keyword], node_id: item.other }]);
    } else if (what === 'aside') {
      ui.setAside([item.keyword], { after });
    } else if (what === 'show') {
      ui.openKeyword(item.keyword);
    }
  };
  const onKey = (event, row) => {
    if (!row || event.ctrlKey || event.metaKey || event.altKey) return false;
    const key = event.key.toLowerCase();
    const at = items.findIndex((i) => i.keyword === row.term);
    if (key === 'a') act('keep', row.item);
    else if (key === 'o') act('other', row.item);
    else if (key === 's') act('aside', row.item);
    else if (key === 'j' || key === 'k') go((items[at + (key === 'j' ? 1 : -1)] || {}).keyword);
    else return false;
    return true;
  };
  const menu = (keys) => {
    const row = rows.find((r) => keys.includes(r.key));
    if (!row) return [];
    return [
      { id: 'keep', label: t('themes.border.keep'), hint: t('themes.key.a'), disabled: readOnly },
      { id: 'other', label: t('themes.border.move', { place: nodePath(index, row.item.other) }), hint: t('themes.key.o'), disabled: readOnly },
      { id: 'aside', label: t('themes.action.set_aside_more'), hint: t('themes.key.s'), disabled: readOnly },
      { kind: 'separator', id: 'sep' },
      { id: 'show', label: t('themes.action.show_node') },
    ];
  };
  const onMenu = (entry, keys) => {
    const row = rows.find((r) => keys.includes(r.key));
    if (row) act(entry.id, row.item);
  };
  const levels = [{ value: '0', label: t('themes.border.level.own') },
    ...index.tree.levels.map((_, i) => ({ value: String(i + 1), label: levelName(index.tree, i + 1, lang) }))];
  return html`<div class="cx-themes-check cx-themes-border">
    <div class="cx-themes-border__head">
      <label class="cx-themes-border__level">
        <span>${t('themes.border.level')}</span>
        <${Select} value=${String(chosen)} options=${levels}
          onChange=${(e) => {
            fit.level.value = Number(e.currentTarget.value);
          }} />
      </label>
      <p class="cx-themes-check__help" id="cx-themes-border-help">${t('themes.border.keys')}</p>
      ${data ? html`<p class="cx-themes-border__count" aria-live="polite">
        ${t('themes.border.count', { total: data.total, negative: data.negative })}</p>` : null}
    </div>
    ${fit.borderError.value ? html`<${ErrorCard} error=${fit.borderError.value} compact
      onRetry=${() => fit.retryBorder(tree)} />` : null}
    ${!data && !fit.borderError.value ? html`<p class="cx-themes-empty-line" aria-busy="true">${t('common.loading')}</p>` : null}
    ${data ? html`<${TreeView} rows=${rows} label=${t('themes.border.label')} class="cx-themes-outline__tree"
      treeRef=${ui.outlineRef}
      activeKey=${ui.active.value} onActiveChange=${(key) => ui.setActive(key)}
      selection=${ui.treeSel.value} onSelectionChange=${(keys) => ui.select(keys)}
      onOpen=${(row) => ui.open(row)} onKeyCommand=${onKey}
      rowMenu=${menu} onRowMenu=${onMenu}
      renderRow=${(row) => html`<${KeywordRow} index=${index} term=${row.term}
        detail=${t('themes.border.detail', { other: nodeName(index.nodes.get(row.item.other), lang) })}
        after=${html`<span class=${`cx-themes-row__share cx-themes-margin ${row.item.margin < 0 ? 'is-negative' : ''}`}
          title=${t('themes.border.margin_help')}>${margin(row.item.margin)}</span>`} />`}
      empty=${html`<${EmptyState} icon="check" title=${t('themes.border.empty')}>${t('themes.border.empty.text')}<//>`} />` : null}
    ${data && data.total > data.items.length ? html`<div class="cx-themes-border__more">
      <${Button} size="s" variant="ghost" loading=${fit.more.value} onClick=${() => fit.showMore(tree)}>
        ${t('themes.border.more', { shown: data.items.length, total: data.total })}<//></div>` : null}
    <p class="cx-themes-panel__note cx-themes-border__note">${t('themes.border.measure')}</p>
  </div>`;
}
