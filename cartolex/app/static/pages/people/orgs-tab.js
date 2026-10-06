// SPDX-License-Identifier: MIT
/**
 * The Organisations tab: every organisation as people decided it (its level,
 * its parents, its units and its people), searched by a name, an acronym, a ROR
 * or an OpenAlex id. An organisation's drawer lists each affiliation with its
 * years and holds what people decide about it (`org-drawer.js`). A note says
 * when some organisations may be one, and opens their review (`org-review.js`).
 * Below, the people of institutions (`institutions.js`).
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { Button, EmptyState, Input, Select, Table } from '../../components/index.js';
import { usePaged } from './common.js';
import { OrganisationDrawer } from './org-drawer.js';
import { OrgReview } from './org-review.js';
import { Institutions } from './institutions.js';

/** The Organisations tab; *focus*: an organisation to open at once (from the address). */
export function OrganisationsTab(props) {
  const { ctx, version, openSheet, openCollect, openImport, canCollect, focus, onFocusClosed, toast, bump, showOnMap } = props;
  const [level, setLevel] = useState('');
  const [q, setQ] = useState('');
  const [sort, setSort] = useState({ column: 'people', direction: 'descending' });
  const [open, setOpen] = useState(focus || null);
  const [reviewing, setReviewing] = useState(false);
  const [pairs, setPairs] = useState(null);
  const list = usePaged(ctx, '/api/organisations', {
    level: level || undefined, q: q || undefined,
    sort: `${sort.direction === 'descending' ? '-' : ''}${sort.column}`, $v: version,
  }, (o) => o.org_id);
  useEffect(() => {
    ctx.api.get('/api/organisations/pairs', { query: { limit: 1 } })
      .then((r) => r.ok && setPairs(r.data.counts));
  }, [version]);
  const levels = Object.entries(((list.data || {}).counts || {}).level || {});
  const levelIds = levels.map(([lv]) => lv).filter(Boolean);
  const columns = [
    { id: 'name', label: t('corpus.col.name'), sortable: true, width: 'minmax(12rem, 2fr)',
      render: (o) => html`${o.name}${o.acronym && o.acronym !== o.name ? html` <span class="cx-corpus-muted">${o.acronym}</span>` : null}${
        o.merged_from && o.merged_from.length ? html` <span class="cx-corpus-chip">${t('corpus.orgs.merged_count', { n: o.merged_from.length })}</span>` : null}` },
    { id: 'level', label: t('corpus.col.level'), sortable: true, width: '8rem' },
    { id: 'parents', label: t('corpus.col.parents'), width: 'minmax(8rem, 1.5fr)',
      render: (o) => o.parent_names.join(' · ') },
    { id: 'ror', label: t('corpus.orgs.ror'), width: '7rem',
      render: (o) => ((o.ids || {}).ror ? html`<code>${o.ids.ror}</code>` : '') },
    { id: 'children', label: t('corpus.col.units'), sortable: true, numeric: true, width: '6rem' },
    { id: 'people', label: t('corpus.col.people_now'), sortable: true, numeric: true, width: '7rem' },
    { id: 'people_ever', label: t('corpus.col.people_ever'), sortable: true, numeric: true, width: '7rem' },
  ];
  return html`<div class="cx-corpus-tab cx-corpus-tab--scroll">
    ${pairs && pairs.open ? html`<p class="cx-corpus-note" role="note">
      ${t('corpus.orgs.pairs_note', { n: pairs.open, clear: pairs.clear || 0 })}
      <${Button} size="s" variant="ghost" onClick=${() => setReviewing(true)}>${t('corpus.dup.review')}<//></p>`
      : null}
    <div class="cx-corpus-filters" role="group" aria-label=${t('corpus.filters')}>
      <label class="cx-corpus-filters__search"><span class="cx-visually-hidden">${t('corpus.orgs.search')}</span>
        <${Input} type="search" value=${q} placeholder=${t('corpus.orgs.search_ids')}
          onInput=${(e) => setQ(e.currentTarget.value)} /></label>
      <label class="cx-corpus-filters__select"><span class="cx-visually-hidden">${t('corpus.col.level')}</span>
        <${Select} aria-label=${t('corpus.col.level')} value=${level} onChange=${(e) => setLevel(e.currentTarget.value)} options=${[
          { value: '', label: t('corpus.filter.any_level') },
          ...levels.map(([lv, n]) => ({ value: lv, label: t('corpus.filter.option', { label: lv || '—', n }) })),
        ]} /></label>
      <${Button} size="s" variant="ghost" onClick=${() => setReviewing(true)}>${t('corpus.orgs.review')}<//>
    </div>
    <${Table} size="l" label=${t('corpus.tab.organisations')} columns=${columns} rows=${list.rows}
      rowKey=${list.rowKey} loading=${list.loading} error=${list.error} onRetry=${list.reload}
      sortMode="server" sort=${sort} onSortChange=${setSort} onRange=${list.onRange}
      onActivate=${(o) => !o.$pending && setOpen(o.org_id)}
      empty=${q || level
        ? html`<${EmptyState} icon="file" title=${t('corpus.orgs.empty_match')}
            action=${{ label: t('corpus.filter.clear'), onClick: () => { setQ(''); setLevel(''); } }} />`
        : html`<${EmptyState} icon="file" title=${t('corpus.orgs.empty')}
            action=${canCollect && openCollect
              ? { label: t('corpus.orgs.empty.collect'), onClick: () => openCollect('harvest') }
              : openImport ? { label: t('corpus.orgs.empty.import'), onClick: () => openImport('list') }
                : null}>${t(canCollect ? 'corpus.orgs.empty.text' : 'corpus.orgs.empty.text_import')}<//>`} />
    <${Institutions} ...${props} levels=${levelIds} />
    ${open ? html`<${OrganisationDrawer} ctx=${ctx} orgId=${open} levels=${levelIds} toast=${toast}
      bump=${bump} showOnMap=${showOnMap}
      onClose=${() => {
        setOpen(null);
        if (onFocusClosed) onFocusClosed();
      }} openSheet=${openSheet} onOpen=${setOpen} />` : null}
    ${reviewing ? html`<${OrgReview} ctx=${ctx} toast=${toast} bump=${bump}
      onClose=${() => setReviewing(false)} />` : null}
  </div>`;
}
