// SPDX-License-Identifier: MIT
/**
 * The review of a keyword proposal (a copilot's result, or an answer to a
 * handoff an earlier version imported): each answered term with the decision
 * it proposes, its code, category and reason, chosen or not (every one by
 * default). Nothing reaches the decisions before « Accept ».
 */
import { html } from '../../core/preact.js';
import { has, t } from '../../core/i18n.js';
import { Button, Icon, Table } from '../../components/index.js';

export const itemKey = (it) => `${it.language}\u0000${it.term}`;

/** A proposal's decision in words: keep, exclude, or merge into its English form. */
function proposedText(it) {
  if (it.proposed === 'merge') return t('keywords.why.merged', { target: it.target });
  return t(`keywords.decision.${it.proposed}`);
}

/** The review of one proposal: each answered term, chosen or not (every one by default). */
export function Review({ proposal, chosen, setChosen }) {
  const read = proposal.read || {};
  const columns = [
    { id: 'term', label: t('keywords.col.term'), width: 'minmax(10rem, 2fr)' },
    { id: 'language', label: t('keywords.col.language'), width: '5rem',
      render: (it) => html`<code>${it.language}</code>` },
    { id: 'proposed', label: t('keywords.ai.proposed'), width: 'minmax(9rem, 1.5fr)',
      render: (it) => html`<span class=${`cx-kw-proposed cx-kw-proposed--${it.proposed}`}>
        <${Icon} name=${it.proposed === 'exclude' ? 'cross' : 'check'} />${proposedText(it)}</span>` },
    { id: 'code', label: t('keywords.ai.code'), width: 'minmax(9rem, 1.5fr)',
      render: (it) => (has(`keywords.ai.code.${it.code}`) ? t(`keywords.ai.code.${it.code}`) : it.code) },
    { id: 'category', label: t('keywords.col.category'), width: '7rem',
      render: (it) => (it.category ? t(`keywords.category.${it.category}`) : '—') },
    { id: 'current', label: t('keywords.ai.current'), width: '8rem',
      render: (it) => (it.current ? t(`keywords.decision.${it.current}`) : '—') },
  ];
  // A copilot's decisions each give their reason.
  if (proposal.items.some((it) => it.reason)) {
    columns.splice(5, 0, { id: 'reason', label: t('keywords.col.reason'), width: 'minmax(10rem, 2fr)' });
  }
  const all = proposal.items.map(itemKey);
  return html`<div class="cx-kw-review">
    <p class="cx-handoff__lead">${t('keywords.ai.review.lead', { n: proposal.answered,
      missing: proposal.unanswered })}</p>
    ${read.ignored || read.unmatched || read.renumbered ? html`<p class="cx-corpus-muted">${t('keywords.ai.review.read', {
      ignored: read.ignored || 0, unmatched: read.unmatched || 0, renumbered: read.renumbered || 0 })}</p>` : null}
    <div class="cx-corpus-bulk">
      <${Button} size="s" variant="ghost" onClick=${() => setChosen(new Set(all))}>${t('keywords.ai.all')}<//>
      <${Button} size="s" variant="ghost" onClick=${() => setChosen(new Set())}>${t('keywords.ai.none')}<//>
      <span class="cx-corpus-bulk__count" aria-live="polite">${t('keywords.ai.chosen', {
        n: chosen.size, total: proposal.items.length })}</span>
    </div>
    <${Table} size="m" label=${t('keywords.ai.review.table')} columns=${columns}
      rows=${proposal.items} rowKey=${itemKey} selection=${chosen} onSelectionChange=${setChosen} />
  </div>`;
}
