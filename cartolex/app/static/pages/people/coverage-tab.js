// SPDX-License-Identifier: MIT
/**
 * The Coverage tab: what was collected for whom, said honestly. Four states
 * (good, thin, failed, no data) by shape, the first blocking causes, the states
 * by organisation, the texts by year and by language. Actions: retry the
 * collections that failed, show the people of a state or a cause, exclude
 * the people without data; documents are added from a person's sheet.
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatNumber, t } from '../../core/i18n.js';
import {
  Button, Card, ConfirmDialog, EmptyState, ErrorCard, Table,
} from '../../components/index.js';
import { Bar, CoverageState } from './common.js';

const STATES = ['good', 'thin', 'failed', 'no_data'];

/** The Coverage tab. */
export function CoverageTab({ ctx, version, bump, toast, openCollect, canCollect, showPeople, openImport }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [asking, setAsking] = useState(false);
  const load = () => ctx.api.get('/api/collection/coverage').then((r) => {
    if (r.ok) setData(r.data);
    else setError(r.error);
  });
  useEffect(() => { load(); }, [version]);

  async function excludeNoData() {
    setAsking(false);
    const people = await ctx.api.get('/api/people', { query: { limit: 1 } });
    const result = await ctx.api.patch('/api/people',
      { where: { coverage: 'no_data' }, role: 'excluded' }, { ifMatch: people.etag });
    if (!result.ok) {
      setError(result.error);
      return;
    }
    toast({ kind: 'success', title: t('corpus.coverage.excluded', { n: result.data.changed }) });
    bump();
  }

  if (error) return html`<${ErrorCard} error=${error} onRetry=${load} />`;
  if (!data) return html`<${Card} loading=${true} title=${t('corpus.tab.coverage')} />`;
  if (data.empty) {
    return html`<${EmptyState} icon="file" title=${t('corpus.empty.empty_no_people')}
      action=${{ label: t('corpus.import.list'), onClick: () => openImport('list') }} />`;
  }
  const years = Object.entries(data.by_year);
  const maxYear = Math.max(1, ...years.map(([, v]) => v.with_abstract + v.titles_only));
  const languages = Object.entries(data.by_language);
  const maxLang = Math.max(1, ...languages.map(([, n]) => n));
  const orgColumns = [
    { id: 'name', label: t('corpus.col.name'), width: 'minmax(12rem, 2fr)' },
    { id: 'level', label: t('corpus.col.level'), width: '7rem' },
    { id: 'people', label: t('corpus.col.people'), numeric: true, width: '6rem' },
    ...STATES.map((s) => ({ id: s, label: t(`corpus.state.${s}`), numeric: true, width: '6rem' })),
  ];
  return html`<div class="cx-corpus-tab cx-corpus-tab--scroll cx-corpus-coverage">
    <section class="cx-corpus-states" aria-label=${t('corpus.coverage.states')}>
      ${STATES.map((s) => html`<button type="button" key=${s} class="cx-corpus-stat"
        onClick=${() => showPeople({ coverage: s })}>
        <span class="cx-corpus-stat__n">${formatNumber(data.states[s] || 0)}</span>
        <${CoverageState} state=${s} />
      </button>`)}
    </section>
    <p class="cx-corpus-muted">${t('corpus.coverage.rule', { good: data.good, counted: data.counted })}</p>
    <div class="cx-corpus-actions-row">
      ${canCollect && data.states.failed ? html`<${Button} icon="undo"
        onClick=${() => openCollect('retry')}>${t('corpus.coverage.retry', { n: data.states.failed })}<//>` : null}
      ${data.states.no_data ? html`<${Button} variant="ghost" onClick=${() => setAsking(true)}>
        ${t('corpus.coverage.exclude', { n: data.states.no_data })}<//>` : null}
      <${Button} variant="ghost" icon="upload" onClick=${() => openImport('folder')}>
        ${t('corpus.coverage.add_documents')}<//>
    </div>
    ${Object.keys(data.causes).length ? html`<${Card} level=${2} title=${t('corpus.coverage.causes')}>
      <ul class="cx-corpus-causes">${Object.entries(data.causes).map(([cause, c]) => html`<li key=${cause}>
        <span class="cx-corpus-causes__n">${formatNumber(c.count)}</span>
        <span>${t(`corpus.cause.${cause}`)}</span></li>`)}</ul>
    <//>` : null}
    <${Card} level=${2} title=${t('corpus.coverage.by_org', { n: data.organisations })}>
      <${Table} size="m" label=${t('corpus.coverage.by_org', { n: data.organisations })}
        columns=${orgColumns} rows=${data.by_organisation} rowKey=${(o) => o.org_id}
        defaultSort=${{ column: 'people', direction: 'descending' }} />
    <//>
    <div class="cx-corpus-two">
      <${Card} level=${2} title=${t('corpus.coverage.by_year')}>
        <ul class="cx-corpus-bars">${years.map(([year, v]) => html`<li key=${year}>
          <span class="cx-corpus-bars__label">${year === 'unknown' ? t('corpus.years.unknown') : year}</span>
          <${Bar} value=${v.with_abstract + v.titles_only} max=${maxYear} label=${year} />
          <span class="cx-corpus-bars__n">${t('corpus.coverage.year_counts', { words: v.with_abstract, titles: v.titles_only })}</span>
        </li>`)}</ul>
      <//>
      <${Card} level=${2} title=${t('corpus.coverage.by_language')}>
        <ul class="cx-corpus-bars">${languages.map(([lang, n]) => html`<li key=${lang}>
          <span class="cx-corpus-bars__label"><code>${lang}</code></span>
          <${Bar} value=${n} max=${maxLang} label=${lang} />
          <span class="cx-corpus-bars__n">${formatNumber(n)}</span>
        </li>`)}</ul>
      <//>
    </div>
    <${ConfirmDialog} open=${asking} danger title=${t('corpus.coverage.exclude_title')}
      confirmLabel=${t('corpus.coverage.exclude_confirm')}
      onAnswer=${(yes) => (yes ? excludeNoData() : setAsking(false))}>
      <p>${t('corpus.coverage.exclude_text', { n: data.states.no_data })}</p>
    <//>
  </div>`;
}
