// SPDX-License-Identifier: MIT
/**
 * The keyword list of one band: search, filters (language, category, route, decision),
 * a virtualised table paged on the server (every candidate: its reason, its
 * usage in people and texts, its language and forms, the route that decided
 * it), range selection, and the bulk actions: keep, exclude, merge, restore,
 * for the rows selected or for every row the filters keep. In the band rejected
 * automatically, keeping is « put back »: the term leaves this computer's
 * rejection cache too. A keyword of the vocabulary given by the address (`?q=`, a link from
 * the map) keeps its row and the candidates merged into it (`term`), whatever their band. A
 * row opens the people who use it (`people.js`) and its place on the map.
 */
import { html, useEffect, useMemo, useState } from '../../core/preact.js';
import { formatNumber, t } from '../../core/i18n.js';
import {
  Button, EmptyState, ErrorCard, Input, MenuButton, Select, Table,
} from '../../components/index.js';
import { usePaged } from '../people/common.js';
import {
  BandMark, CATEGORIES, CategoryMark, ROUTES, RouteMark, decide, keyOf, reasonText, refsOf, restore,
} from './common.js';
import { vocabularyTerm } from './people.js';
import { linkTo } from '../map/links.js';

const DECISIONS = ['none', 'keep', 'exclude', 'merge'];
const SORTS = { term: 'term', people: 'people', texts: 'texts', score: 'score', language: 'language' };

function useDebounced(value, ms = 250) {
  const [out, setOut] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setOut(value), ms);
    return () => clearTimeout(timer);
  }, [value]);
  return out;
}

/** The filters bar. */
function Filters({ filters, setFilters, data }) {
  const set = (key) => (event) => setFilters({ ...filters, [key]: event.currentTarget.value || '' });
  const option = (value, label, n) => ({ value, label: n === undefined ? label
    : t('keywords.filter.option', { label, n }) });
  const languages = (data && data.corpus_languages) || [];
  const byLang = (data && data.languages) || {};
  const routes = (data && data.routes) || {};
  const categories = (data && data.categories) || {};
  return html`<div class="cx-corpus-filters" role="group" aria-label=${t('keywords.filters')}>
    <label class="cx-corpus-filters__search"><span class="cx-visually-hidden">${t('keywords.search')}</span>
      <${Input} type="search" value=${filters.q} placeholder=${t('keywords.search')}
        onInput=${(e) => setFilters({ ...filters, q: e.currentTarget.value })} /></label>
    ${languages.length > 1 ? html`<label class="cx-corpus-filters__select">
      <span class="cx-visually-hidden">${t('keywords.col.language')}</span>
      <${Select} aria-label=${t('keywords.col.language')} value=${filters.lang} onChange=${set('lang')}
        options=${[option('', t('keywords.filter.any_language')),
          ...languages.map((l) => option(l, l, byLang[l] || 0))]} /></label>` : null}
    <label class="cx-corpus-filters__select"><span class="cx-visually-hidden">${t('keywords.col.category')}</span>
      <${Select} aria-label=${t('keywords.col.category')} value=${filters.category} onChange=${set('category')}
        options=${[option('', t('keywords.filter.any_category')),
          ...[...CATEGORIES, 'none'].map((c) => option(c, t(`keywords.category.${c}`), categories[c] || 0))]} /></label>
    <label class="cx-corpus-filters__select"><span class="cx-visually-hidden">${t('keywords.col.route')}</span>
      <${Select} aria-label=${t('keywords.col.route')} value=${filters.route} onChange=${set('route')}
        options=${[option('', t('keywords.filter.any_route')),
          ...ROUTES.map((r) => option(r, t(`keywords.route.${r}`), routes[r] || 0))]} /></label>
    <label class="cx-corpus-filters__select"><span class="cx-visually-hidden">${t('keywords.col.decision')}</span>
      <${Select} aria-label=${t('keywords.col.decision')} value=${filters.decision} onChange=${set('decision')}
        options=${[option('', t('keywords.filter.any_decision')),
          ...DECISIONS.map((d) => option(d, t(`keywords.decision.${d}`)))]} /></label>
  </div>`;
}

/**
 * @param {object} props
 * @param {object} props.ctx the page's context
 * @param {string} props.band
 * @param {number} props.version bumped after each change (reads the list again)
 * @param {(data: object) => void} props.onData the first page's answer (counts, versions)
 * @param {Function} props.onChanged after a change
 * @param {(rows: Array<object>) => void} props.onMerge opens the merge dialog
 * @param {Function} props.toast
 * @param {string} [props.term] one keyword of the vocabulary and the candidates merged into it
 * @param {Function} [props.onClearTerm] show every keyword again
 * @param {(term: string) => void} [props.onPeople] open the people who use a keyword
 */
export function KeywordList({ ctx, band, version, onData, onChanged, onMerge, toast, term = '', onClearTerm,
  onPeople }) {
  const blank = { q: '', lang: '', route: '', decision: '', category: '' };
  const [filters, setFilters] = useState(blank);
  const [sort, setSort] = useState({ column: 'score', direction: 'descending' });
  const [selection, setSelection] = useState(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const q = useDebounced(filters.q);
  useEffect(() => setSelection(new Set()), [band]);

  const query = {
    band, q: q || undefined, lang: filters.lang || undefined, route: filters.route || undefined,
    decision: filters.decision || undefined, category: filters.category || undefined,
    term: term || undefined,
    sort: `${sort.direction === 'descending' ? '-' : ''}${SORTS[sort.column] || 'score'}`,
    $v: version,
  };
  const list = usePaged(ctx, '/api/keywords', query, keyOf);
  const data = list.data;
  useEffect(() => {
    if (data) onData(data);
  }, [data]);

  const byKey = useMemo(() => {
    const map = new Map();
    list.rows.forEach((row) => { if (!row.$pending) map.set(keyOf(row), row); });
    return map;
  }, [list.rows]);
  const selected = [...selection].filter((k) => !k.startsWith('@'));
  const filtered = Boolean(q || filters.lang || filters.route || filters.decision || filters.category);
  // In the band rejected automatically, keeping a term puts it back.
  const keepLabel = band === 'rejected' ? t('keywords.action.put_back') : t('keywords.action.keep');

  const done = async (result, message) => {
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    toast({ kind: 'success', title: message(result.data) });
    setSelection(new Set());
    onChanged();
  };
  const act = async (kind, keys, { all = false } = {}) => {
    setBusy(true);
    setError(null);
    const version = data && data.etag;
    if (kind === 'restore') {
      const refs = refsOf(keys).filter((r) => {
        const row = byKey.get(`${r.language}\u0000${r.term}`);
        return row && row.decision;
      });
      const result = await restore(ctx.api, version, refs);
      done(result, (d) => t('keywords.done.restore', { n: d.restored }));
      return;
    }
    if (all) {
      const result = await ctx.api.post('/api/keywords/decisions/where', {
        where: { band, lang: filters.lang || null, route: filters.route || null,
          decision: filters.decision || null, category: filters.category || null, q: q || '' },
        decision: kind,
      }, { ifMatch: version });
      done(result, (d) => t(`keywords.done.${kind}`, { n: d.decided }));
      return;
    }
    const result = await decide(ctx.api, version, refsOf(keys).map((r) => ({ ...r, decision: kind })));
    done(result, (d) => t(`keywords.done.${kind}`, { n: d.decided }));
  };
  const merge = (keys) => onMerge(keys.map((k) => byKey.get(k)).filter(Boolean), data && data.etag);

  const columns = [
    { id: 'term', label: t('keywords.col.term'), sortable: true, width: 'minmax(14rem, 3fr)',
      render: (row) => html`<span class="cx-kw-term">${row.term}</span>${row.forms.length > 1
        ? html` <span class="cx-corpus-muted" title=${row.forms.join(' · ')}>${t('keywords.forms', { n: row.forms.length - 1 })}</span>` : null}` },
    { id: 'language', label: t('keywords.col.language'), sortable: true, width: '5rem',
      render: (row) => html`<code>${row.language}</code>` },
    { id: 'band', label: t('keywords.col.band'), width: '12.5rem',
      render: (row) => html`<${BandMark} band=${row.band} />` },
    { id: 'reason', label: t('keywords.col.reason'), width: 'minmax(12rem, 2fr)',
      render: (row) => html`<span class="cx-kw-reason">${reasonText(row)}</span>` },
    { id: 'category', label: t('keywords.col.category'), width: '7rem',
      render: (row) => html`<${CategoryMark} category=${row.category} />` },
    { id: 'people', label: t('keywords.col.people'), sortable: true, numeric: true, width: '6rem' },
    { id: 'texts', label: t('keywords.col.texts'), sortable: true, numeric: true, width: '6rem' },
    { id: 'route', label: t('keywords.col.route'), width: '9rem',
      render: (row) => html`<${RouteMark} route=${row.route} />` },
    { id: 'score', label: t('keywords.col.score'), sortable: true, width: '6rem', align: 'end',
      render: (row) => formatNumber(row.score_len, { maximumFractionDigits: 2 }) },
  ];

  const rowMenu = (keys) => [
    { id: 'keep', label: keepLabel },
    { id: 'exclude', label: t('keywords.action.exclude') },
    { id: 'merge', label: t('keywords.action.merge'), disabled: !keys.length },
    { kind: 'separator', id: 'sep' },
    { id: 'restore', label: t('keywords.action.restore'),
      disabled: !keys.some((k) => byKey.get(k) && byKey.get(k).decision) },
    { kind: 'separator', id: 'sep-links' },
    { id: 'people', label: t('keywords.action.people'), disabled: keys.length !== 1 || !byKey.get(keys[0]) },
    { id: 'map', label: t('keywords.action.show_map'), disabled: keys.length !== 1 || !byKey.get(keys[0]) },
  ];
  const onRowMenu = (item, keys) => {
    const row = byKey.get(keys[0]);
    if (item.id === 'merge') merge(keys);
    else if (item.id === 'people') { if (row && onPeople) onPeople(vocabularyTerm(row)); }
    else if (item.id === 'map') { if (row) ctx.navigate(linkTo.map('keyword', vocabularyTerm(row))); }
    else act(item.id, keys);
  };

  const empty = data && data.empty;
  const canRestore = selected.some((k) => byKey.get(k) && byKey.get(k).decision);
  return html`<div class="cx-corpus-tab">
    ${term ? html`<div class="cx-kw-term-filter" role="status">
      <p>${t('keywords.term_filter', { term })}</p>
      <${Button} size="s" variant="ghost" icon="close" onClick=${onClearTerm}>${t('keywords.term_filter.clear')}<//>
    </div>` : null}
    <${Filters} filters=${filters} setFilters=${setFilters} data=${data} />
    <div class="cx-corpus-bulk" role="region" aria-label=${t('keywords.bulk')}>
      <span class="cx-corpus-bulk__count" aria-live="polite">${selected.length
        ? t('keywords.selected', { n: selected.length }) : t('keywords.total', { n: list.total })}</span>
      ${selected.length ? html`
        <${Button} size="s" icon="check" onClick=${() => act('keep', selected)}>${keepLabel}<//>
        <${Button} size="s" icon="cross" onClick=${() => act('exclude', selected)}>${t('keywords.action.exclude')}<//>
        <${Button} size="s" onClick=${() => merge(selected)}>${t('keywords.action.merge')}<//>
        ${canRestore ? html`<${Button} size="s" variant="ghost" icon="undo"
          onClick=${() => act('restore', selected)}>${t('keywords.action.restore')}<//>` : null}` : null}
      ${filtered && !selected.length && list.total ? html`<${MenuButton} size="s"
        label=${t('keywords.action.all', { n: list.total })}
        items=${[{ id: 'keep', label: keepLabel }, { id: 'exclude', label: t('keywords.action.exclude') }]}
        onSelect=${(item) => act(item.id, [], { all: true })} />` : null}
      ${filtered ? html`<${Button} size="s" variant="ghost" icon="close"
        onClick=${() => setFilters(blank)}>${t('keywords.filter.clear')}<//>` : null}
      ${busy ? html`<span class="cx-spinner" aria-hidden="true"></span>` : null}
    </div>
    ${error ? html`<${ErrorCard} error=${error} compact onDismiss=${() => setError(null)}
      onReload=${() => { setError(null); onChanged(); }} />` : null}
    <${Table} class="cx-kw-table" size="fill" label=${t(`keywords.band.${band}`)}
      columns=${columns} rows=${list.rows} rowKey=${list.rowKey} loading=${list.loading}
      error=${list.error} onRetry=${list.reload} sortMode="server" sort=${sort}
      onSortChange=${setSort} selection=${selection} onSelectionChange=${setSelection}
      onRange=${list.onRange} rowMenu=${rowMenu} onRowMenu=${onRowMenu}
      onActivate=${(row) => { if (row && !row.$pending && onPeople) onPeople(vocabularyTerm(row)); }}
      empty=${empty && empty.code === 'empty_no_keywords'
        ? html`<${EmptyState} icon="file" title=${t('keywords.empty.empty_no_keywords')}
            action=${{ label: t('keywords.empty.build'), href: '/build?scope=keywords' }} />`
        : html`<${EmptyState} icon="search" title=${t('keywords.empty.empty_no_match')}
            action=${{ label: t('keywords.filter.clear'),
              onClick: () => setFilters(blank) }} />`} />
  </div>`;
}
