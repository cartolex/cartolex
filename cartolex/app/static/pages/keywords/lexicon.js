// SPDX-License-Identifier: MIT
/**
 * The Lexicon tab of the keywords screen: what the last vocabulary build made of the
 * candidates, the whole lexicon the themes use. A word cloud of its most important
 * keywords (by score or by people, coloured by theme or by category, drawn on the
 * server for the page's light or dark theme and the colour scheme, `cloud.js`), the list paged on the server (a term
 * per display language, twins merged; rank, people, texts, category, theme, forms;
 * search, sort, filters), its CSV, and the keyword decisions on the candidates a row
 * gathers (keep, exclude, merge), applied by the next build: a note says so.
 */
import { html, useEffect, useMemo, useState } from '../../core/preact.js';
import { locale, t } from '../../core/i18n.js';
import {
  Button, Card, EmptyState, ErrorCard, Icon, Input, Select, Table,
} from '../../components/index.js';
import { usePaged } from '../people/common.js';
import { cloudSrc } from './cloud.js';
import { CategoryMark, decide } from './common.js';

const CATEGORIES = ['concept', 'method', 'object', 'place', 'field', 'none'];
const SORTS = { rank: 'rank', people: 'people', texts: 'texts', category: 'category', theme: 'theme' };
const lang2 = (code) => String(code || 'en').slice(0, 2);
const keyOf = (row) => row.concept;

function useDebounced(value, ms = 250) {
  const [out, setOut] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setOut(value), ms);
    return () => clearTimeout(timer);
  }, [value]);
  return out;
}

/** A choice of two or three, as a segmented control. */
function Segmented({ label, value, options, onChange }) {
  return html`<fieldset class="cx-segmented cx-kw-lexicon__choice">
    <legend class="cx-kw-lexicon__legend">${label}</legend>
    ${options.map((o) => html`<label class="cx-segmented__option" key=${o.value}>
      <input type="radio" name=${label} value=${o.value} checked=${value === o.value}
        onChange=${() => onChange(o.value)} />
      <span>${o.label}</span></label>`)}
  </fieldset>`;
}

/** The word cloud, an image the server draws (and caches) for these options. */
function WordCloud({ app, run, language }) {
  const [by, setBy] = useState('score');
  const [colour, setColour] = useState('theme');
  const src = cloudSrc(app.stores.prefs, { run, language, by, colour });
  return html`<${Card} title=${t('keywords.lexicon.cloud')} class="cx-kw-lexicon__cloud"
    actions=${html`<${Segmented} label=${t('keywords.lexicon.size_by')} value=${by} onChange=${setBy}
        options=${[{ value: 'score', label: t('keywords.lexicon.by.score') },
          { value: 'people', label: t('keywords.lexicon.by.people') }]} />
      <${Segmented} label=${t('keywords.lexicon.colour_by')} value=${colour} onChange=${setColour}
        options=${[{ value: 'theme', label: t('keywords.lexicon.colour.theme') },
          { value: 'category', label: t('keywords.lexicon.colour.category') }]} />`}>
    <img class="cx-kw-lexicon__image" src=${src} width="1400" height="760" loading="lazy"
      alt=${t(by === 'people' ? 'keywords.lexicon.cloud_alt.people' : 'keywords.lexicon.cloud_alt.score')} />
  <//>`;
}

/**
 * @param {object} props
 * @param {object} props.ctx the page's context
 * @param {number} props.version bumped after each change (reads the list again)
 * @param {Function} props.onChanged after a decision
 * @param {(rows: Array<object>, version: string) => void} props.onMerge the merge dialog
 * @param {Function} props.toast
 */
export function LexiconPanel({ ctx, version, onChanged, onMerge, toast }) {
  const { app } = ctx;
  const [q, setQ] = useState('');
  const [category, setCategory] = useState('');
  const [theme, setTheme] = useState('');
  const [sort, setSort] = useState({ column: 'rank', direction: 'ascending' });
  const [selection, setSelection] = useState(new Set());
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const language = lang2(locale.value);
  const search = useDebounced(q);
  const list = usePaged(ctx, '/api/keywords/lexicon', {
    language, q: search || undefined, category: category || undefined, theme: theme || undefined,
    sort: `${sort.direction === 'descending' ? '-' : ''}${SORTS[sort.column] || 'rank'}`,
    $v: version,
  }, keyOf);
  const data = list.data;
  const languages = (data && data.languages) || [];
  const shown = languages.includes(language) ? language : languages[0];
  const byKey = useMemo(() => {
    const map = new Map();
    list.rows.forEach((row) => { if (!row.$pending) map.set(keyOf(row), row); });
    return map;
  }, [list.rows]);
  const selected = [...selection].filter((k) => !k.startsWith('@')).map((k) => byKey.get(k)).filter(Boolean);
  const stage = app.stores.project.stages.value.get('keywords.build');
  const pending = stage && stage.state === 'needs_update';

  const act = async (kind, rows) => {
    const candidates = rows.flatMap((r) => r.candidates);
    if (!candidates.length) return;
    setBusy(true);
    setError(null);
    // The version of keywords.csv the decisions are made on.
    const read = await ctx.api.get('/api/keywords/decisions?limit=1');
    if (read.ok && kind === 'merge') {
      setBusy(false);
      onMerge(candidates, read.etag);
      return;
    }
    const result = read.ok ? await decide(ctx.api, read.etag,
      candidates.map((c) => ({ term: c.term, language: c.language, decision: kind }))) : read;
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    toast({ kind: 'success', title: t(`keywords.done.${kind}`, { n: result.data.decided }) });
    setSelection(new Set());
    app.stores.project.refresh();
    onChanged();
  };

  const termColumns = languages.slice(0, 3).map((l) => ({
    id: `term_${l}`, label: t('keywords.lexicon.col.term', { language: l }), width: 'minmax(12rem, 2fr)',
    render: (row) => html`<span class="cx-kw-term">${row.terms[l]}</span>`,
  }));
  const columns = [
    { id: 'rank', label: t('keywords.lexicon.col.rank'), sortable: true, numeric: true, width: '5rem' },
    ...termColumns,
    { id: 'people', label: t('keywords.col.people'), sortable: true, numeric: true, width: '6rem' },
    { id: 'texts', label: t('keywords.col.texts'), sortable: true, numeric: true, width: '6rem' },
    { id: 'category', label: t('keywords.col.category'), sortable: true, width: '7rem',
      render: (row) => html`<${CategoryMark} category=${row.category} />` },
    { id: 'theme', label: t('keywords.lexicon.col.theme'), sortable: true, width: 'minmax(10rem, 2fr)',
      render: (row) => (row.theme ? html`<span class="cx-kw-lexicon__theme">
        <span class="cx-kw-lexicon__chip" style=${{ '--cx-chip': `var(--cx-hue-${(row.hue ?? 0) + 1})` }}
          aria-hidden="true"></span>${row.theme}</span>` : html`<span class="cx-corpus-muted">—</span>`) },
    { id: 'forms', label: t('keywords.lexicon.col.forms'), width: 'minmax(10rem, 2fr)',
      render: (row) => html`<span class="cx-corpus-muted" title=${row.forms.join(' · ')}>
        ${row.forms.slice(0, 3).join(' · ')}</span>` },
  ];
  const rowMenu = () => [
    { id: 'keep', label: t('keywords.action.keep') },
    { id: 'exclude', label: t('keywords.action.exclude') },
    { id: 'merge', label: t('keywords.action.merge') },
  ];
  const onRowMenu = (item, keys) => act(item.id, keys.map((k) => byKey.get(k)).filter(Boolean));
  const themes = (data && data.themes) || [];
  const empty = data && data.empty;
  const exportHref = `/api/keywords/lexicon/export?language=${encodeURIComponent(language)}`;

  return html`<div class="cx-corpus-tab cx-kw-lexicon">
    <div class="cx-kw-lexicon__head">
      <p class="cx-corpus__summary">${data && data.run ? t('keywords.lexicon.summary', {
        n: data.keywords, languages: languages.join(', ') }) : ''}</p>
      <a class="cx-button cx-button--secondary cx-button--s" href=${exportHref} download>
        <${Icon} name="download" /><span class="cx-button__label">${t('keywords.lexicon.export')}</span></a>
    </div>
    ${pending ? html`<div class="cx-kw-warning" role="note"><${Icon} name="info" />
      <p>${t('keywords.lexicon.rebuild')}</p>
      <a class="cx-button cx-button--secondary cx-button--s" href="/build?scope=keywords">
        <span class="cx-button__label">${t('keywords.lexicon.rebuild_action')}</span></a></div>` : null}
    ${data && data.run ? html`<${WordCloud} app=${app} run=${data.run} language=${shown} />` : null}
    <div class="cx-corpus-filters" role="group" aria-label=${t('keywords.filters')}>
      <label class="cx-corpus-filters__search"><span class="cx-visually-hidden">${t('keywords.search')}</span>
        <${Input} type="search" value=${q} placeholder=${t('keywords.search')}
          onInput=${(e) => setQ(e.currentTarget.value)} /></label>
      <label class="cx-corpus-filters__select"><span class="cx-visually-hidden">${t('keywords.col.category')}</span>
        <${Select} aria-label=${t('keywords.col.category')} value=${category}
          onChange=${(e) => setCategory(e.currentTarget.value)}
          options=${[{ value: '', label: t('keywords.filter.any_category') },
            ...CATEGORIES.map((c) => ({ value: c, label: t(`keywords.category.${c}`) }))]} /></label>
      ${themes.length ? html`<label class="cx-corpus-filters__select">
        <span class="cx-visually-hidden">${t('keywords.lexicon.col.theme')}</span>
        <${Select} aria-label=${t('keywords.lexicon.col.theme')} value=${theme}
          onChange=${(e) => setTheme(e.currentTarget.value)}
          options=${[{ value: '', label: t('keywords.lexicon.any_theme') },
            ...themes.map((th) => ({ value: th.id, label: th.label }))]} /></label>` : null}
    </div>
    <div class="cx-corpus-bulk" role="region" aria-label=${t('keywords.bulk')}>
      <span class="cx-corpus-bulk__count" aria-live="polite">${selected.length
        ? t('keywords.selected', { n: selected.length }) : t('keywords.total', { n: list.total })}</span>
      ${selected.length ? html`
        <${Button} size="s" icon="check" onClick=${() => act('keep', selected)}>${t('keywords.action.keep')}<//>
        <${Button} size="s" icon="cross" onClick=${() => act('exclude', selected)}>${t('keywords.action.exclude')}<//>
        <${Button} size="s" onClick=${() => act('merge', selected)}>${t('keywords.action.merge')}<//>` : null}
      ${busy ? html`<span class="cx-spinner" aria-hidden="true"></span>` : null}
    </div>
    ${error ? html`<${ErrorCard} error=${error} compact onDismiss=${() => setError(null)} />` : null}
    <${Table} class="cx-kw-table" size="fill" label=${t('keywords.lexicon.tab')}
      columns=${columns} rows=${list.rows} rowKey=${list.rowKey} loading=${list.loading}
      error=${list.error} onRetry=${list.reload} sortMode="server" sort=${sort}
      onSortChange=${setSort} selection=${selection} onSelectionChange=${setSelection}
      onRange=${list.onRange} rowMenu=${rowMenu} onRowMenu=${onRowMenu}
      empty=${empty && empty.code === 'empty_no_keywords'
        ? html`<${EmptyState} icon="file" title=${t('keywords.empty.empty_no_keywords')}
            action=${{ label: t('keywords.empty.build'), href: '/build?scope=keywords' }} />`
        : html`<${EmptyState} icon="search" title=${t('keywords.empty.empty_no_match')}
            action=${{ label: t('keywords.filter.clear'), onClick: () => { setQ(''); setCategory(''); setTheme(''); } }} />`} />
  </div>`;
}
