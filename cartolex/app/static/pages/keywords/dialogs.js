// SPDX-License-Identifier: MIT
/**
 * The keywords screen's small dialogs: merging keywords into one, and the
 * history of the decisions (each one restorable, exclusions first of all).
 */
import { html, useState } from '../../core/preact.js';
import { formatDate, t } from '../../core/i18n.js';
import {
  Button, Dialog, Drawer, EmptyState, ErrorCard, FormField, Input, Select, Table,
} from '../../components/index.js';
import { usePaged } from '../people/common.js';
import { RouteMark, decide, restore } from './common.js';

/**
 * Merge keywords into one: a target chosen among them, or typed (another
 * keyword of the list, its English form…). Every other one is merged into it.
 */
export function MergeDialog({ ctx, rows, version, onClose, onDone }) {
  const [target, setTarget] = useState(rows.length ? rows[0].term : '');
  const [other, setOther] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const into = (target === '' ? other : target).trim();
  const sources = rows.filter((r) => r.term !== into);
  const send = async () => {
    setBusy(true);
    setError(null);
    const result = await decide(ctx.api, version, sources.map((r) => ({
      term: r.term, language: r.language, decision: 'merge', target: into })));
    setBusy(false);
    if (!result.ok) setError(result.error);
    else onDone(t('keywords.done.merge', { n: result.data.decided, target: into }));
  };
  return html`<${Dialog} open=${true} onClose=${onClose} title=${t('keywords.merge.title')}
    description=${t('keywords.merge.description', { n: rows.length })}
    footer=${html`<${Button} variant="ghost" onClick=${onClose}>${t('common.cancel')}<//>
      <${Button} variant="primary" loading=${busy} disabled=${!into || !sources.length}
        onClick=${send}>${t('keywords.merge.do', { n: sources.length })}<//>`}>
    <div class="cx-kw-form">
      <${FormField} label=${t('keywords.merge.into')}>
        ${(field) => html`<${Select} ...${field} value=${target}
          onChange=${(e) => setTarget(e.currentTarget.value)}
          options=${[...rows.map((r) => ({ value: r.term, label: `${r.term} (${r.language})` })),
            { value: '', label: t('keywords.merge.other') }]} />`}
      <//>
      ${target === '' ? html`<${FormField} label=${t('keywords.merge.other_label')}
        help=${t('keywords.merge.other_help')}>
        ${(field) => html`<${Input} ...${field} value=${other}
          onInput=${(e) => setOther(e.currentTarget.value)} />`}
      <//>` : null}
      ${error ? html`<${ErrorCard} error=${error} compact />` : null}
    </div>
  <//>`;
}

const SOURCES = ['person', 'ai-handoff'];

/** The history of the decisions, the latest first; restore any of them. */
export function HistoryDrawer({ ctx, version, onClose, onChanged, toast }) {
  const [decision, setDecision] = useState('exclude');
  const [source, setSource] = useState('');
  const [selection, setSelection] = useState(new Set());
  const [error, setError] = useState(null);
  const list = usePaged(ctx, '/api/keywords/decisions', {
    decision: decision || undefined, source: source || undefined, $v: version,
  }, (row) => `${row.language}\u0000${row.term}`);
  const counts = (list.data && list.data.counts) || {};
  const selected = [...selection].filter((k) => !k.startsWith('@'));
  const undo = async (keys) => {
    const keywords = keys.map((k) => {
      const [language, term] = k.split('\u0000');
      return { term, language };
    });
    const result = await restore(ctx.api, list.data && list.data.etag, keywords);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setSelection(new Set());
    toast({ kind: 'success', title: t('keywords.done.restore', { n: result.data.restored }) });
    onChanged();
  };
  const columns = [
    { id: 'term', label: t('keywords.col.term'), width: 'minmax(10rem, 2fr)' },
    { id: 'language', label: t('keywords.col.language'), width: '5rem',
      render: (r) => html`<code>${r.language || '—'}</code>` },
    { id: 'decision', label: t('keywords.col.decision'), width: '9rem',
      render: (r) => (r.decision === 'merge' ? t('keywords.why.merged', { target: r.target })
        : t(`keywords.decision.${r.decision}`)) },
    { id: 'source', label: t('keywords.col.route'), width: '9rem',
      render: (r) => html`<${RouteMark} route=${r.source === 'ai-handoff' ? 'ai-handoff' : 'person'} />` },
    { id: 'decided_at', label: t('keywords.col.when'), width: '8rem',
      render: (r) => (r.decided_at ? formatDate(r.decided_at) : '') },
  ];
  return html`<${Drawer} open=${true} onClose=${onClose} size="l" title=${t('keywords.history.title')}
    description=${t('keywords.history.description')}>
    <div class="cx-kw-history">
      <div class="cx-corpus-filters" role="group" aria-label=${t('keywords.filters')}>
        <${Select} aria-label=${t('keywords.col.decision')} value=${decision}
          onChange=${(e) => setDecision(e.currentTarget.value)} options=${[
            { value: '', label: t('keywords.filter.any_decision') },
            ...['exclude', 'keep', 'merge'].map((d) => ({ value: d,
              label: t('keywords.filter.option', { label: t(`keywords.decision.${d}`), n: counts[d] || 0 }) })),
          ]} />
        <${Select} aria-label=${t('keywords.col.route')} value=${source}
          onChange=${(e) => setSource(e.currentTarget.value)} options=${[
            { value: '', label: t('keywords.filter.any_route') },
            ...SOURCES.map((s) => ({ value: s, label: t(`keywords.route.${s}`) })),
          ]} />
        <${Button} size="s" icon="undo" disabled=${!selected.length} onClick=${() => undo(selected)}>
          ${t('keywords.history.restore', { n: selected.length })}<//>
      </div>
      ${error ? html`<${ErrorCard} error=${error} compact onDismiss=${() => setError(null)} />` : null}
      <${Table} size="m" label=${t('keywords.history.title')} columns=${columns} rows=${list.rows}
        rowKey=${list.rowKey} loading=${list.loading} error=${list.error} onRetry=${list.reload}
        sortMode="server" selection=${selection} onSelectionChange=${setSelection}
        onRange=${list.onRange}
        rowMenu=${() => [{ id: 'restore', label: t('keywords.action.restore') }]}
        onRowMenu=${(item, keys) => undo(keys.filter((k) => !k.startsWith('@')))}
        empty=${html`<${EmptyState} icon="file" title=${t('keywords.history.empty')} />`} />
    </div>
  <//>`;
}
