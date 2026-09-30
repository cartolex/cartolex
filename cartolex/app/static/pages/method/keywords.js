// SPDX-License-Identifier: MIT
/**
 * The keywords step's diagnostic: the candidates by band and by reason, the
 * spread of their scores (a histogram by band, on a log scale), the
 * vocabulary against its cap, and the ranked list of candidates (score, people, texts, band, reason,
 * language) with its filters, paged on the server (`GET /api/keywords`).
 */

import { html, useEffect, useState } from '../../core/preact.js';
import { formatNumber, t } from '../../core/i18n.js';
import { Icon, Input, Select, Table } from '../../components/index.js';
import { usePaged } from '../people/common.js';
import { BandMark, extractionReason, keyOf } from '../keywords/common.js';
import { BarChart } from './charts.js';
import { BAND_SERIES, Facts, Lead } from './common.js';

const SORTS = { term: 'term', people: 'people', texts: 'texts', score: 'score', language: 'language' };

function useDebounced(value, ms = 250) {
  const [out, setOut] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setOut(value), ms);
    return () => clearTimeout(timer);
  }, [value]);
  return out;
}

/** The ranked candidates, with the band, language and text filters. */
function Ranked({ ctx, languages }) {
  const [filters, setFilters] = useState({ band: '', lang: '', q: '' });
  const [sort, setSort] = useState({ column: 'score', direction: 'descending' });
  const q = useDebounced(filters.q);
  const list = usePaged(ctx, '/api/keywords', {
    band: filters.band || undefined, lang: filters.lang || undefined, q: q || undefined,
    sort: `${sort.direction === 'descending' ? '-' : ''}${SORTS[sort.column] || 'score'}`,
  }, keyOf);
  const set = (key) => (e) => setFilters({ ...filters, [key]: e.currentTarget.value || '' });
  const columns = [
    { id: 'term', label: t('keywords.col.term'), sortable: true, width: 'minmax(12rem, 3fr)' },
    { id: 'score', label: t('keywords.col.score'), sortable: true, width: '6rem', align: 'end',
      render: (row) => formatNumber(row.score_len, { maximumFractionDigits: 2 }) },
    { id: 'people', label: t('keywords.col.people'), sortable: true, numeric: true, width: '5.5rem' },
    { id: 'texts', label: t('keywords.col.texts'), sortable: true, numeric: true, width: '5.5rem' },
    { id: 'band', label: t('keywords.col.band'), width: '11rem', render: (row) => html`<${BandMark} band=${row.band} />` },
    { id: 'reason', label: t('keywords.col.reason'), width: 'minmax(10rem, 2fr)',
      render: (row) => html`<span class="cx-kw-reason">${extractionReason(row.reason)}</span>` },
    { id: 'language', label: t('keywords.col.language'), sortable: true, width: '5rem',
      render: (row) => html`<code>${row.language}</code>` },
  ];
  return html`<div class="cx-method-ranked">
    <div class="cx-corpus-filters" role="group" aria-label=${t('keywords.filters')}>
      <label class="cx-corpus-filters__search"><span class="cx-visually-hidden">${t('keywords.search')}</span>
        <${Input} type="search" value=${filters.q} placeholder=${t('keywords.search')}
          onInput=${(e) => setFilters({ ...filters, q: e.currentTarget.value })} /></label>
      <label class="cx-corpus-filters__select"><span class="cx-visually-hidden">${t('keywords.col.band')}</span>
        <${Select} aria-label=${t('keywords.col.band')} value=${filters.band} onChange=${set('band')}
          options=${[{ value: '', label: t('method.keywords.any_band') },
            ...BAND_SERIES.map((b) => ({ value: b.id, label: b.label }))]} /></label>
      ${languages.length > 1 ? html`<label class="cx-corpus-filters__select">
        <span class="cx-visually-hidden">${t('keywords.col.language')}</span>
        <${Select} aria-label=${t('keywords.col.language')} value=${filters.lang} onChange=${set('lang')}
          options=${[{ value: '', label: t('keywords.filter.any_language') },
            ...languages.map((l) => ({ value: l, label: l }))]} /></label>` : null}
      <span class="cx-corpus-bulk__count" aria-live="polite">${t('keywords.total', { n: list.total })}</span>
    </div>
    <div class="cx-method-ranked__table">
      <${Table} size="fill" label=${t('method.keywords.ranked')} columns=${columns} rows=${list.rows}
        rowKey=${list.rowKey} loading=${list.loading} error=${list.error} onRetry=${list.reload}
        sortMode="server" sort=${sort} onSortChange=${setSort} onRange=${list.onRange} />
    </div>
  </div>`;
}

/** The words of a reason code; a code that names a word (« part-of: … ») gets « … ». */
const WITH_WORD = new Set(['part-of', 'common-modifier', 'name', 'stop-word-edge']);
function reasonWords(code) {
  if (code === 'none') return '—';
  return extractionReason(WITH_WORD.has(code) ? `${code}: …` : code);
}

export function KeywordsDiagnostic({ ctx, view }) {
  const bands = view.bands || {};
  const vocab = view.vocabulary || {};
  const capped = vocab.max_keywords && vocab.kept_keywords >= vocab.max_keywords;
  const hist = view.histogram || { edges: [], counts: {} };
  const bars = hist.edges.slice(0, -1).map((edge, i) => ({
    label: formatNumber(edge, { maximumSignificantDigits: 2 }),
    values: Object.fromEntries(BAND_SERIES.map((b) => [b.id, (hist.counts[b.id] || [])[i] || 0])),
  }));
  const series = BAND_SERIES.filter((b) => (hist.counts[b.id] || []).some(Boolean));
  const languages = Object.keys(view.languages || {});
  return html`<${Lead}>${t('method.keywords.lead')}<//>
    <${Facts} items=${[
      [t('method.keywords.candidates'), formatNumber(view.candidates || 0)],
      ...BAND_SERIES.map((b) => [html`<${BandMark} band=${b.id} />`, formatNumber(bands[b.id] || 0)]),
      [t('method.keywords.vocabulary'), vocab.kept_keywords === null || vocab.kept_keywords === undefined ? '—'
        : t('method.keywords.of_cap', { n: vocab.kept_keywords, cap: vocab.max_keywords || 0 })],
      [t('method.keywords.unit'), view.counting_unit || '—'],
    ]} />
    ${capped ? html`<p class="cx-method-mark cx-method-mark--changed"><${Icon} name="warning" />
      <span>${t('method.keywords.capped')}</span></p>` : null}
    ${bars.length ? html`<${BarChart} bars=${bars} series=${series} every=${4}
      label=${t('method.keywords.histogram')} caption=${t('method.keywords.histogram_caption')}
      xLabel=${t('method.keywords.score')} yLabel=${t('method.keywords.count')} />` : null}
    <table class="cx-settings__table" aria-label=${t('method.keywords.reasons')}>
      <caption class="cx-method-caption">${t('method.keywords.reasons')}</caption>
      <thead><tr><th scope="col">${t('keywords.col.band')}</th><th scope="col">${t('keywords.col.reason')}</th>
        <th scope="col" class="cx-num">${t('method.keywords.count')}</th></tr></thead>
      <tbody>${BAND_SERIES.flatMap((b) => Object.entries((view.reasons || {})[b.id] || {}).map(([code, n]) => html`
        <tr key=${`${b.id}-${code}`}><td><${BandMark} band=${b.id} /></td><td>${reasonWords(code)}</td>
          <td class="cx-num">${formatNumber(n)}</td></tr>`))}</tbody>
    </table>
    <h4 class="cx-method-subtitle">${t('method.keywords.ranked')}</h4>
    <${Ranked} ctx=${ctx} languages=${languages} />`;
}
