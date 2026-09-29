// SPDX-License-Identifier: MIT
/**
 * The Collaborators tab: the co-authors found round by round, with what
 * speaks for each (joint texts, the last joint year, the topical fit, the
 * path from a seed), the cap and whether a round was cut; decide on one or
 * many: map them, keep them as context, project them, refuse them, or later.
 */
import { html, useState } from '../../core/preact.js';
import { formatNumber, t } from '../../core/i18n.js';
import {
  Button, EmptyState, ErrorCard, MenuButton, Select, Table,
} from '../../components/index.js';
import { usePaged } from './common.js';

const DECISIONS = ['mapped', 'context', 'projected', 'no', 'later'];

/** The Collaborators tab. */
export function CollaboratorsTab({ ctx, version, bump, toast, openSheet, openCollect, canCollect }) {
  const [round, setRound] = useState('');
  const [decision, setDecision] = useState('');
  const [sort, setSort] = useState({ column: 'joint_texts', direction: 'descending' });
  const [selection, setSelection] = useState(new Set());
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const list = usePaged(ctx, '/api/collection/collaborators', {
    round: round || undefined, decision: decision || undefined,
    sort: `${sort.direction === 'descending' ? '-' : ''}${sort.column}`, $v: version,
  }, (row) => row.person_id);
  const data = list.data || {};
  const latest = data.latest || {};
  const counts = data.counts || {};
  const selected = [...selection].filter((k) => !k.startsWith('@'));

  async function decide(value) {
    setBusy(true);
    setError(null);
    const decisions = Object.fromEntries(selected.map((pid) => [pid, value]));
    const result = await ctx.api.post('/api/collection/collaborators/decide', { decisions },
      { ifMatch: data.etag });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    toast({ kind: 'success', title: t('corpus.collab.decided', { n: selected.length,
      decision: t(`corpus.decision.${value}`) }) });
    setSelection(new Set());
    bump();
  }

  const decisionItems = DECISIONS.map((d) => ({ id: d, label: t(`corpus.decision.${d}`) }));
  const columns = [
    { id: 'name', label: t('corpus.col.name'), sortable: true, width: 'minmax(10rem, 2fr)' },
    { id: 'round', label: t('corpus.col.round'), sortable: true, numeric: true, width: '5rem' },
    { id: 'joint_texts', label: t('corpus.col.joint'), sortable: true, numeric: true, width: '7rem' },
    { id: 'last_joint_year', label: t('corpus.col.last_joint'), width: '7rem', align: 'end' },
    { id: 'fit', label: t('corpus.col.fit'), sortable: true, width: '5rem', align: 'end',
      render: (r) => (r.fit === null ? '—' : r.fit.toFixed(2)) },
    { id: 'seeds', label: t('corpus.col.with'), width: 'minmax(10rem, 2fr)',
      render: (r) => r.seed_names.slice(0, 3).join(' · ') },
    { id: 'decision', label: t('corpus.col.decision'), sortable: true, width: '8rem',
      render: (r) => t(`corpus.decision.${r.decision}`) },
  ];
  const rounds = Object.keys(counts.round || {}).sort();
  return html`<div class="cx-corpus-tab">
    <div class="cx-corpus-runinfo" role="status">
      ${latest.run ? html`<p>${t('corpus.collab.latest', { rounds: (latest.rounds || []).length,
        seeds: latest.seeds, cap: formatNumber(latest.cap), max: latest.max_authors })}</p>` : null}
      ${latest.cut ? html`<p class="cx-corpus-warning" role="note"><span class="cx-corpus-warning__word">
        ${t('toast.kind.warning')}</span> ${t('corpus.collab.cut', { cap: formatNumber(latest.cap) })}</p>` : null}
      ${canCollect ? html`<${Button} size="s" icon="search"
        onClick=${() => openCollect('collaborators', { rounds: 1 })}>${t('corpus.collab.find')}<//>` : null}
    </div>
    <div class="cx-corpus-filters" role="group" aria-label=${t('corpus.filters')}>
      <label class="cx-corpus-filters__select"><span class="cx-visually-hidden">${t('corpus.col.round')}</span>
        <${Select} aria-label=${t('corpus.col.round')} value=${round} onChange=${(e) => setRound(e.currentTarget.value)} options=${[
          { value: '', label: t('corpus.filter.any_round') },
          ...rounds.map((r) => ({ value: r, label: t('corpus.collab.round', { n: r, count: counts.round[r] }) })),
        ]} /></label>
      <label class="cx-corpus-filters__select"><span class="cx-visually-hidden">${t('corpus.col.decision')}</span>
        <${Select} aria-label=${t('corpus.col.decision')} value=${decision} onChange=${(e) => setDecision(e.currentTarget.value)} options=${[
          { value: '', label: t('corpus.filter.any_decision') },
          ...DECISIONS.map((d) => ({ value: d, label: t('corpus.filter.option', { label: t(`corpus.decision.${d}`), n: (counts.decision || {})[d] || 0 }) })),
        ]} /></label>
      ${selected.length ? html`<${MenuButton} size="s" variant="primary"
        label=${t('corpus.collab.decide', { n: selected.length })} items=${decisionItems}
        onSelect=${(item) => decide(item.id)} />` : null}
      ${busy ? html`<span class="cx-spinner" aria-hidden="true"></span>` : null}
    </div>
    ${error ? html`<${ErrorCard} error=${error} compact onDismiss=${() => setError(null)} />` : null}
    <${Table} size="fill" label=${t('corpus.tab.collaborators')} columns=${columns} rows=${list.rows}
      rowKey=${list.rowKey} loading=${list.loading} error=${list.error} onRetry=${list.reload}
      sortMode="server" sort=${sort} onSortChange=${setSort} onRange=${list.onRange}
      selection=${selection} onSelectionChange=${setSelection}
      onActivate=${(row) => !row.$pending && openSheet(row.person_id)}
      empty=${html`<${EmptyState} icon="file" title=${t('corpus.collab.empty')}>
        ${t('corpus.collab.empty_text')}<//>`} />
  </div>`;
}
