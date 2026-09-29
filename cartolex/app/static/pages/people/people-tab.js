// SPDX-License-Identifier: MIT
/**
 * The People tab: every person with their role (mapped, context, projected),
 * identity, coverage state and texts; filters from the list's own columns;
 * a bulk change of role for the rows selected or for every row the filters
 * keep. The list is paged on the server (10⁵ people).
 */
import { html, useEffect, useMemo, useState } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import {
  Button, ErrorCard, EmptyState, Input, MenuButton, Select, Table,
} from '../../components/index.js';
import { CoverageState, IdentityState, personName, roleLabel, usePaged } from './common.js';

const ROLES = ['mapped', 'context', 'projected', 'excluded', 'undecided'];
const IDENTITIES = ['pending', 'confirmed', 'auto', 'none'];
const STATES = ['good', 'thin', 'failed', 'no_data'];
const SORTS = { name: 'name', role: 'role', identity: 'identity', state: 'state', texts: 'texts',
  unit: 'unit' };

function useDebounced(value, ms = 250) {
  const [out, setOut] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setOut(value), ms);
    return () => clearTimeout(timer);
  }, [value]);
  return out;
}

/** The filters bar: text, role, identity, coverage, and the list's own columns. */
function Filters({ filters, setFilters, counts, facets }) {
  const option = (value, label, n) => ({ value, label: n === undefined ? label
    : t('corpus.filter.option', { label, n }) });
  const set = (key) => (event) => setFilters({ ...filters, [key]: event.currentTarget.value || null });
  const columns = filters.columns || {};
  const shown = facets.filter((f) => f.distinct > 1 && f.distinct <= 50).slice(0, 4);
  return html`<div class="cx-corpus-filters" role="group" aria-label=${t('corpus.filters')}>
    <label class="cx-corpus-filters__search">
      <span class="cx-visually-hidden">${t('corpus.people.search')}</span>
      <${Input} type="search" placeholder=${t('corpus.people.search')} value=${filters.q || ''}
        onInput=${(e) => setFilters({ ...filters, q: e.currentTarget.value })} />
    </label>
    <label class="cx-corpus-filters__select"><span class="cx-visually-hidden">${t('corpus.col.role')}</span>
      <${Select} aria-label=${t('corpus.col.role')} value=${filters.role || ''} onChange=${set('role')} options=${[
        option('', t('corpus.filter.any_role')),
        ...ROLES.map((r) => option(r, roleLabel(r), (counts.role || {})[r] || 0)),
      ]} /></label>
    <label class="cx-corpus-filters__select"><span class="cx-visually-hidden">${t('corpus.col.identity')}</span>
      <${Select} aria-label=${t('corpus.col.identity')} value=${filters.identity || ''} onChange=${set('identity')} options=${[
        option('', t('corpus.filter.any_identity')),
        ...IDENTITIES.map((r) => option(r, t(`corpus.identity.${r}`), (counts.identity || {})[r] || 0)),
      ]} /></label>
    <label class="cx-corpus-filters__select"><span class="cx-visually-hidden">${t('corpus.col.state')}</span>
      <${Select} aria-label=${t('corpus.col.state')} value=${filters.coverage || ''} onChange=${set('coverage')} options=${[
        option('', t('corpus.filter.any_state')),
        ...STATES.map((r) => option(r, t(`corpus.state.${r}`), (counts.state || {})[r] || 0)),
      ]} /></label>
    ${shown.map((f) => html`<label class="cx-corpus-filters__select" key=${f.column}>
      <span class="cx-visually-hidden">${f.column}</span>
      <${Select} value=${columns[f.column] || ''}
        onChange=${(e) => {
          const next = { ...columns };
          if (e.currentTarget.value) next[f.column] = e.currentTarget.value;
          else delete next[f.column];
          setFilters({ ...filters, columns: next });
        }}
        options=${[option('', t('corpus.filter.any_value', { column: f.column })),
          ...f.values.map((v) => option(v.value, v.value, v.count))]} /></label>`)}
  </div>`;
}

/** The People tab. */
export function PeopleTab({ ctx, version, bump, toast, openSheet, preset }) {
  const [filters, setFilters] = useState({ q: '', columns: {} });
  const [sort, setSort] = useState({ column: 'name', direction: 'ascending' });
  const [selection, setSelection] = useState(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const q = useDebounced(filters.q);
  useEffect(() => {
    if (preset) setFilters({ q: '', columns: {}, ...preset });
  }, [preset && preset.$at]);

  const query = {
    q: q || undefined,
    role: filters.role || undefined,
    identity: filters.identity || undefined,
    coverage: filters.coverage || undefined,
    col: Object.entries(filters.columns || {}).map(([k, v]) => `${k}:${v}`),
    sort: `${sort.direction === 'descending' ? '-' : ''}${SORTS[sort.column] || 'name'}`,
    $v: version,
  };
  const list = usePaged(ctx, '/api/people', query, (row) => row.person_id);
  const data = list.data || {};
  const counts = data.counts || {};
  const facets = data.facets || [];
  const sets = data.sets || [];

  const filtered = Boolean(q || filters.role || filters.identity || filters.coverage
    || Object.keys(filters.columns || {}).length);
  const selected = [...selection].filter((k) => !k.startsWith('@'));

  async function setRole(role, { all = false } = {}) {
    setBusy(true);
    setError(null);
    const body = { role };
    if (role === 'projected') body.set = sets[0] || '';
    if (all) {
      body.where = { role: filters.role || null, identity: filters.identity || null,
        coverage: filters.coverage || null, q: q || null, columns: filters.columns || {} };
    } else body.person_ids = selected;
    const result = await ctx.api.patch('/api/people', body, { ifMatch: data.etag });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    toast({ kind: 'success', title: t('corpus.people.changed', { n: result.data.changed, role: roleLabel(role) }) });
    setSelection(new Set());
    bump();
  }

  const roleItems = ROLES.filter((r) => r !== 'projected' || sets.length)
    .map((r) => ({ id: r, label: roleLabel(r) }));
  const columns = useMemo(() => {
    const extra = facets.slice(0, 2).map((f) => ({
      id: `col:${f.column}`, label: f.column, render: (p) => (p.columns || {})[f.column] || '',
    }));
    return [
      { id: 'name', label: t('corpus.col.name'), sortable: true, width: 'minmax(12rem, 2fr)',
        render: (p) => html`<span class="cx-corpus-name">${personName(p)}</span>` },
      { id: 'role', label: t('corpus.col.role'), sortable: true, width: '8rem',
        render: (p) => html`${roleLabel(p.role)}${p.set ? html` <span class="cx-corpus-muted">${p.set}</span>` : null}` },
      { id: 'identity', label: t('corpus.col.identity'), sortable: true, width: '9rem',
        render: (p) => html`<${IdentityState} state=${p.identity} />` },
      { id: 'state', label: t('corpus.col.state'), sortable: true, width: '9rem',
        render: (p) => html`<${CoverageState} state=${p.state} />` },
      { id: 'texts', label: t('corpus.col.texts'), sortable: true, width: '7rem', align: 'end',
        render: (p) => t('corpus.people.texts', { n: (p.coverage || {}).texts || 0,
          words: (p.coverage || {}).with_abstract || 0 }) },
      { id: 'unit', label: t('corpus.col.unit'), sortable: true, width: 'minmax(8rem, 1fr)' },
      ...extra,
    ];
  }, [facets.map((f) => f.column).join('|')]);

  const rowMenu = (keys) => [
    { id: 'sheet', label: t('corpus.people.open_sheet'), disabled: keys.length !== 1 },
    { kind: 'group', id: 'roles', label: t('corpus.people.set_role'),
      items: roleItems.map((r) => ({ ...r, id: `role:${r.id}` })) },
  ];
  const onRowMenu = (item, keys) => {
    if (item.id === 'sheet') openSheet(keys[0]);
    else if (item.id.startsWith('role:')) setRole(item.id.slice(5));
  };

  const empty = data.empty;
  return html`<div class="cx-corpus-tab">
    <${Filters} filters=${filters} setFilters=${setFilters} counts=${counts} facets=${facets} />
    <div class="cx-corpus-bulk" role="region" aria-label=${t('corpus.people.bulk')}>
      <span class="cx-corpus-bulk__count" aria-live="polite">
        ${selected.length ? t('corpus.people.selected', { n: selected.length })
          : t('corpus.people.total', { n: list.total })}</span>
      ${selected.length ? html`<${MenuButton} label=${t('corpus.people.set_role')} size="s"
        items=${roleItems} onSelect=${(item) => setRole(item.id)} />` : null}
      ${filtered && !selected.length && list.total ? html`<${MenuButton}
        label=${t('corpus.people.set_role_all', { n: list.total })} size="s"
        items=${roleItems} onSelect=${(item) => setRole(item.id, { all: true })} />` : null}
      ${filtered ? html`<${Button} size="s" variant="ghost" icon="close"
        onClick=${() => setFilters({ q: '', columns: {} })}>${t('corpus.filter.clear')}<//>` : null}
      ${busy ? html`<span class="cx-spinner" aria-hidden="true"></span>` : null}
    </div>
    ${error ? html`<${ErrorCard} error=${error} compact onDismiss=${() => setError(null)} />` : null}
    <${Table} class="cx-corpus-table" size="fill" label=${t('corpus.tab.people')}
      columns=${columns} rows=${list.rows} rowKey=${list.rowKey} loading=${list.loading}
      error=${list.error} onRetry=${list.reload} sortMode="server" sort=${sort}
      onSortChange=${setSort} selection=${selection} onSelectionChange=${setSelection}
      onActivate=${(row) => !row.$pending && openSheet(row.person_id)} onRange=${list.onRange}
      rowMenu=${rowMenu} onRowMenu=${onRowMenu}
      empty=${empty ? html`<${EmptyState} title=${t(`corpus.empty.${empty.code}`)} icon="file" />`
        : null} />
  </div>`;
}
