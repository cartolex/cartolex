// SPDX-License-Identifier: MIT
/**
 * The Organisations tab: every organisation with its level, its parents, its
 * units and its people; an organisation's drawer lists each affiliation with
 * its years. Below, the people of institutions: the latest search (choose
 * institutions, then read their people) and the latest proposal (take all, or
 * some, with a role).
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatNumber, t } from '../../core/i18n.js';
import {
  Button, Card, Checkbox, Drawer, EmptyState, ErrorCard, Input, MenuButton, Select, Table,
} from '../../components/index.js';
import { roleLabel, usePaged } from './common.js';

function years(a) {
  if (!a.start_year && !a.end_year) return t('corpus.years.unknown');
  if (!a.end_year) return t('corpus.years.since', { first: a.start_year });
  return t('corpus.years.span', { first: a.start_year || '…', last: a.end_year });
}

/** One organisation, in a drawer. */
function OrganisationDrawer({ ctx, orgId, onClose, openSheet, onOpen }) {
  const [org, setOrg] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    ctx.api.get(`/api/organisations/${encodeURIComponent(orgId)}`).then((r) => {
      if (r.ok) setOrg(r.data);
      else setError(r.error);
    });
  }, [orgId]);
  return html`<${Drawer} open=${true} onClose=${onClose} size="l"
    title=${org ? org.name : t('common.loading')}
    description=${org ? [org.acronym, org.level, org.country].filter(Boolean).join(' · ') : null}>
    ${error ? html`<${ErrorCard} error=${error} compact />` : null}
    ${org ? html`<div class="cx-corpus-sheet">
      ${org.parents.length ? html`<section><h3 class="cx-corpus-h3">${t('corpus.orgs.parents')}</h3>
        <ul class="cx-corpus-links">${org.parents.map((p) => html`<li key=${p.org_id}>
          <button type="button" class="cx-link-button" onClick=${() => onOpen(p.org_id)}>${p.name}</button></li>`)}</ul>
      </section>` : null}
      ${org.units.length ? html`<section><h3 class="cx-corpus-h3">${t('corpus.orgs.units', { n: org.units.length })}</h3>
        <ul class="cx-corpus-links">${org.units.slice(0, 100).map((u) => html`<li key=${u.org_id}>
          <button type="button" class="cx-link-button" onClick=${() => onOpen(u.org_id)}>${u.name}</button>
          <span class="cx-corpus-muted"> ${u.level}</span></li>`)}</ul>
      </section>` : null}
      <section><h3 class="cx-corpus-h3">${t('corpus.orgs.affiliations', { n: org.affiliations.length })}</h3>
        <table class="cx-corpus-simple">
          <thead><tr><th scope="col">${t('corpus.col.name')}</th><th scope="col">${t('corpus.col.years')}</th>
            <th scope="col">${t('corpus.col.source')}</th></tr></thead>
          <tbody>${org.affiliations.slice(0, 500).map((a, i) => html`<tr key=${`${a.person_id}-${i}`}>
            <td><button type="button" class="cx-link-button" onClick=${() => openSheet(a.person_id)}>${a.name}</button></td>
            <td>${years(a)}</td><td>${a.source}</td></tr>`)}</tbody>
        </table>
      </section>
    </div>` : null}
  <//>`;
}

/** The people of institutions: the latest search and the latest proposal. */
function Institutions({ ctx, version, bump, toast, openCollect, canCollect }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [name, setName] = useState('');
  const [chosen, setChosen] = useState(new Set());
  const [picked, setPicked] = useState(new Set());
  const [busy, setBusy] = useState(false);
  const load = () => ctx.api.get('/api/collection/institutions', { query: { limit: 200 } })
    .then((r) => (r.ok ? setData({ ...r.data, etag: r.etag }) : setError(r.error)));
  useEffect(() => { load(); }, [version]);

  async function take(what, role) {
    setBusy(true);
    const people = await ctx.api.get('/api/people', { query: { limit: 1 } });
    const result = await ctx.api.post('/api/collection/institutions/take', { take: what, role },
      { ifMatch: people.etag });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    toast({ kind: 'success', title: t('corpus.institutions.taken', { n: result.data.taken.length }) });
    setPicked(new Set());
    bump();
  }

  const search = data && data.search;
  const proposal = data && data.proposal;
  const roles = ['mapped', 'context', 'projected'].map((r) => ({ id: r, label: roleLabel(r) }));
  return html`<${Card} title=${t('corpus.institutions.title')} level=${2} class="cx-corpus-card">
    ${error ? html`<${ErrorCard} error=${error} compact onDismiss=${() => setError(null)} />` : null}
    ${canCollect ? html`<form class="cx-corpus-paste__row" onSubmit=${(e) => {
      e.preventDefault();
      if (name.trim()) openCollect('institutions', { search: name.trim() });
    }}>
      <label class="cx-visually-hidden" for="cx-corpus-inst">${t('corpus.institutions.search')}</label>
      <${Input} id="cx-corpus-inst" value=${name} placeholder=${t('corpus.institutions.search')}
        onInput=${(e) => setName(e.currentTarget.value)} />
      <${Button} type="submit" icon="search" disabled=${!name.trim()}>${t('corpus.institutions.find')}<//>
    </form>` : null}
    ${search ? html`<fieldset class="cx-corpus-fieldset">
      <legend>${t('corpus.institutions.found', { n: search.institutions.length, name: search.search })}</legend>
      <ul class="cx-corpus-checks">${search.institutions.map((i) => html`<li key=${i.id}>
        <${Checkbox} checked=${chosen.has(i.id)} onChange=${() => {
          const next = new Set(chosen);
          if (next.has(i.id)) next.delete(i.id);
          else next.add(i.id);
          setChosen(next);
        }} label=${t('corpus.institutions.item', { name: i.name, type: i.type || '—',
          works: formatNumber(i.works_count), country: i.country || '—' })} />
        ${i.parents.length ? html`<span class="cx-corpus-muted"> ${t('corpus.institutions.part_of', { parents: i.parents.join(', ') })}</span>` : null}
      </li>`)}</ul>
      <${Button} disabled=${!chosen.size} onClick=${() => openCollect('institutions', { institutions: [...chosen] })}>
        ${t('corpus.institutions.read', { n: chosen.size })}<//>
    </fieldset>` : null}
    ${proposal ? html`<section class="cx-corpus-proposal">
      <h3 class="cx-corpus-h3">${t('corpus.institutions.proposal', {
        roots: proposal.roots.map((r) => r.name).join(', '), n: proposal.total,
        works: formatNumber(proposal.works_read), min: proposal.min_works })}</h3>
      ${proposal.merges.length ? html`<p class="cx-corpus-muted">${t('corpus.institutions.merges', { n: proposal.merges.length })}</p>` : null}
      <div class="cx-corpus-bulk">
        <${MenuButton} label=${t('corpus.institutions.take_all', { n: proposal.total - proposal.already })}
          size="s" variant="primary" items=${roles} onSelect=${(item) => take(['all'], item.id)} />
        ${picked.size ? html`<${MenuButton} label=${t('corpus.institutions.take_some', { n: picked.size })}
          size="s" items=${roles} onSelect=${(item) => take([...picked], item.id)} />` : null}
        ${busy ? html`<span class="cx-spinner" aria-hidden="true"></span>` : null}
      </div>
      <${Table} size="m" label=${t('corpus.institutions.people')} rowKey=${(p) => p.record}
        rows=${proposal.items} selection=${picked} onSelectionChange=${setPicked}
        columns=${[
          { id: 'name', label: t('corpus.col.name'), width: 'minmax(10rem, 2fr)' },
          { id: 'works', label: t('corpus.col.works'), numeric: true, width: '6rem', sortable: true },
          { id: 'years', label: t('corpus.col.years'), width: '8rem',
            render: (p) => years({ start_year: p.first_year, end_year: p.last_year }) },
          { id: 'units', label: t('corpus.col.units'), width: 'minmax(10rem, 2fr)',
            render: (p) => p.units.slice(0, 2).map((u) => u.name).join(' · ') },
          { id: 'person_id', label: t('corpus.col.already'), width: '7rem',
            render: (p) => (p.person_id ? t('corpus.institutions.already') : '') },
        ]} />
    </section>` : !search ? html`<p class="cx-corpus-muted">${t('corpus.institutions.none')}</p>` : null}
  <//>`;
}

/** The Organisations tab. */
export function OrganisationsTab(props) {
  const { ctx, version, openSheet, openCollect, openImport, canCollect } = props;
  const [level, setLevel] = useState('');
  const [q, setQ] = useState('');
  const [sort, setSort] = useState({ column: 'people', direction: 'descending' });
  const [open, setOpen] = useState(null);
  const list = usePaged(ctx, '/api/organisations', {
    level: level || undefined, q: q || undefined,
    sort: `${sort.direction === 'descending' ? '-' : ''}${sort.column}`, $v: version,
  }, (o) => o.org_id);
  const levels = Object.entries(((list.data || {}).counts || {}).level || {});
  const columns = [
    { id: 'name', label: t('corpus.col.name'), sortable: true, width: 'minmax(12rem, 2fr)',
      render: (o) => html`${o.name}${o.acronym && o.acronym !== o.name ? html` <span class="cx-corpus-muted">${o.acronym}</span>` : null}` },
    { id: 'level', label: t('corpus.col.level'), sortable: true, width: '8rem' },
    { id: 'parents', label: t('corpus.col.parents'), width: 'minmax(8rem, 1.5fr)',
      render: (o) => o.parent_names.join(' · ') },
    { id: 'children', label: t('corpus.col.units'), sortable: true, numeric: true, width: '6rem' },
    { id: 'people', label: t('corpus.col.people_now'), sortable: true, numeric: true, width: '7rem' },
    { id: 'people_ever', label: t('corpus.col.people_ever'), sortable: true, numeric: true, width: '7rem' },
  ];
  return html`<div class="cx-corpus-tab cx-corpus-tab--scroll">
    <div class="cx-corpus-filters" role="group" aria-label=${t('corpus.filters')}>
      <label class="cx-corpus-filters__search"><span class="cx-visually-hidden">${t('corpus.orgs.search')}</span>
        <${Input} type="search" value=${q} placeholder=${t('corpus.orgs.search')}
          onInput=${(e) => setQ(e.currentTarget.value)} /></label>
      <label class="cx-corpus-filters__select"><span class="cx-visually-hidden">${t('corpus.col.level')}</span>
        <${Select} aria-label=${t('corpus.col.level')} value=${level} onChange=${(e) => setLevel(e.currentTarget.value)} options=${[
          { value: '', label: t('corpus.filter.any_level') },
          ...levels.map(([lv, n]) => ({ value: lv, label: t('corpus.filter.option', { label: lv || '—', n }) })),
        ]} /></label>
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
    <${Institutions} ...${props} />
    ${open ? html`<${OrganisationDrawer} ctx=${ctx} orgId=${open} onClose=${() => setOpen(null)}
      openSheet=${openSheet} onOpen=${setOpen} />` : null}
  </div>`;
}
