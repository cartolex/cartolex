// SPDX-License-Identifier: MIT
/**
 * The Texts tab: every text with the richest part it has (title only, an
 * abstract, a full text), the providers of its parts, its people. A text's
 * drawer shows each part by provider (the versions of its words), the records
 * merged into it, its preprints and the conflicts between finders.
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatNumber, has, t } from '../../core/i18n.js';
import { Drawer, EmptyState, ErrorCard, Input, Select, Table } from '../../components/index.js';
import { usePaged } from './common.js';

const CONTENT = ['title', 'abstract', 'full'];

function providerLabel(p) {
  return has(`corpus.provider.${p}`) ? t(`corpus.provider.${p}`) : p;
}

/** The richest content, by shape: a dash (title), a half dot (abstract), a full dot (full). */
function Content({ value }) {
  return html`<span class=${`cx-corpus-content cx-corpus-content--${value}`}>
    <span class="cx-corpus-content__shape" aria-hidden="true"></span>
    ${t(`corpus.content.${value}`)}</span>`;
}

function TextDrawer({ ctx, textId, onClose, openSheet, onOpen }) {
  const [text, setText] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    setText(null);
    ctx.api.get(`/api/texts/${encodeURIComponent(textId)}`).then((r) => {
      if (r.ok) setText(r.data);
      else setError(r.error);
    });
  }, [textId]);
  const byProvider = {};
  if (text) text.parts.forEach((p) => { (byProvider[p.provider] = byProvider[p.provider] || []).push(p); });
  return html`<${Drawer} open=${true} onClose=${onClose} size="l"
    title=${text ? text.title : t('common.loading')}
    description=${text ? [text.year, text.doc_type, text.doi].filter(Boolean).join(' · ') : null}>
    ${error ? html`<${ErrorCard} error=${error} compact />` : null}
    ${text ? html`<div class="cx-corpus-sheet">
      <section><h3 class="cx-corpus-h3">${t('corpus.texts.people', { n: text.people.length, all: text.n_authors || text.people.length })}</h3>
        <ul class="cx-corpus-links">${text.people.map((p) => html`<li key=${p.person_id}>
          <button type="button" class="cx-link-button" onClick=${() => openSheet(p.person_id)}>${p.name}</button>
          ${p.position ? html`<span class="cx-corpus-muted"> ${t('corpus.texts.rank', { n: p.position })}</span>` : null}</li>`)}</ul>
      </section>
      ${Object.entries(byProvider).map(([provider, parts]) => html`<section key=${provider}>
        <h3 class="cx-corpus-h3">${t('corpus.texts.from', { provider: providerLabel(provider) })}</h3>
        ${parts.map((p, i) => html`<details class="cx-corpus-part" key=${i} open=${p.part === 'abstract'}>
          <summary>${t('corpus.texts.part', { part: t(`corpus.part.${p.part}`), language: p.language,
            chars: formatNumber(p.chars) })}</summary>
          <p class="cx-corpus-part__text">${p.preview}${p.chars > p.preview.length ? '…' : ''}</p>
        </details>`)}
      </section>`)}
      ${text.merges.length ? html`<section><h3 class="cx-corpus-h3">${t('corpus.texts.merged', { n: text.merges.length })}</h3>
        <ul class="cx-corpus-list">${text.merges.map((m, i) => html`<li key=${i}>
          ${t('corpus.texts.merge', { rule: m.rule, n: (m.merged || []).length })}
          <span class="cx-corpus-muted"> ${m.evidence}</span></li>`)}</ul></section>` : null}
      ${text.versions.length || text.version_of ? html`<section><h3 class="cx-corpus-h3">${t('corpus.texts.versions')}</h3>
        <ul class="cx-corpus-links">
          ${text.version_of ? html`<li><button type="button" class="cx-link-button"
            onClick=${() => onOpen(text.version_of)}>${t('corpus.texts.published')}</button></li>` : null}
          ${text.versions.map((v) => html`<li key=${v.text_id}><button type="button" class="cx-link-button"
            onClick=${() => onOpen(v.text_id)}>${t('corpus.texts.preprint', { title: v.title, year: v.year || '—' })}</button></li>`)}
        </ul></section>` : null}
      ${text.conflicts.length ? html`<section><h3 class="cx-corpus-h3">${t('corpus.texts.conflicts', { n: text.conflicts.length })}</h3>
        <ul class="cx-corpus-list">${text.conflicts.map((c, i) => html`<li key=${i}>
          ${t('corpus.texts.conflict', { field: c.field, kept: String(c.kept), from: c.kept_from,
            other: String(c.other), other_from: c.other_from })}</li>`)}</ul></section>` : null}
    </div>` : null}
  <//>`;
}

/** The Texts tab. */
export function TextsTab({ ctx, version, openSheet }) {
  const [content, setContent] = useState('');
  const [provider, setProvider] = useState('');
  const [q, setQ] = useState('');
  const [sort, setSort] = useState({ column: 'year', direction: 'descending' });
  const [open, setOpen] = useState(null);
  const list = usePaged(ctx, '/api/texts', {
    content: content || undefined, provider: provider || undefined, q: q || undefined,
    sort: `${sort.direction === 'descending' ? '-' : ''}${sort.column}`, $v: version,
  }, (row) => row.text_id);
  const counts = (list.data || {}).counts || {};
  const columns = [
    { id: 'title', label: t('corpus.col.title'), sortable: true, width: 'minmax(16rem, 3fr)' },
    { id: 'year', label: t('corpus.col.year'), sortable: true, width: '5rem', align: 'end' },
    { id: 'doc_type', label: t('corpus.col.type'), width: '8rem' },
    { id: 'content', label: t('corpus.col.content'), sortable: true, width: '9rem',
      render: (row) => html`<${Content} value=${row.content} />` },
    { id: 'providers', label: t('corpus.col.providers'), width: 'minmax(8rem, 1fr)',
      render: (row) => row.providers.map(providerLabel).join(' · ') },
    { id: 'people', label: t('corpus.col.people'), sortable: true, numeric: true, width: '6rem' },
  ];
  return html`<div class="cx-corpus-tab">
    <div class="cx-corpus-filters" role="group" aria-label=${t('corpus.filters')}>
      <label class="cx-corpus-filters__search"><span class="cx-visually-hidden">${t('corpus.texts.search')}</span>
        <${Input} type="search" value=${q} placeholder=${t('corpus.texts.search')}
          onInput=${(e) => setQ(e.currentTarget.value)} /></label>
      <label class="cx-corpus-filters__select"><span class="cx-visually-hidden">${t('corpus.col.content')}</span>
        <${Select} aria-label=${t('corpus.col.content')} value=${content} onChange=${(e) => setContent(e.currentTarget.value)} options=${[
          { value: '', label: t('corpus.filter.any_content') },
          ...CONTENT.map((c) => ({ value: c, label: t('corpus.filter.option', { label: t(`corpus.content.${c}`), n: counts.content ? counts.content[c] || 0 : 0 }) })),
        ]} /></label>
      <label class="cx-corpus-filters__select"><span class="cx-visually-hidden">${t('corpus.col.providers')}</span>
        <${Select} aria-label=${t('corpus.col.providers')} value=${provider} onChange=${(e) => setProvider(e.currentTarget.value)} options=${[
          { value: '', label: t('corpus.filter.any_provider') },
          ...Object.entries(counts.provider || {}).map(([p, n]) => ({ value: p, label: t('corpus.filter.option', { label: providerLabel(p), n }) })),
        ]} /></label>
    </div>
    <${Table} size="fill" label=${t('corpus.tab.texts')} columns=${columns} rows=${list.rows}
      rowKey=${list.rowKey} loading=${list.loading} error=${list.error} onRetry=${list.reload}
      sortMode="server" sort=${sort} onSortChange=${setSort} onRange=${list.onRange}
      onActivate=${(row) => !row.$pending && setOpen(row.text_id)}
      empty=${html`<${EmptyState} icon="file" title=${t('corpus.texts.empty')} />`} />
    ${open ? html`<${TextDrawer} ctx=${ctx} textId=${open} onClose=${() => setOpen(null)}
      openSheet=${openSheet} onOpen=${setOpen} />` : null}
  </div>`;
}
