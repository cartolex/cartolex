// SPDX-License-Identifier: MIT
/**
 * The review of a theme proposal (a copilot's result, or an answer to a
 * handoff an earlier version imported): each change in words, with its reason
 * and, when it cannot apply, why; accepted or not, previewed on the tree,
 * applied as ordinary operations.
 */

import { html } from '../../core/preact.js';
import { locale, t } from '../../core/i18n.js';
import { Button, Checkbox, Icon } from '../../components/index.js';
import { lang2, nodeName } from './model.js';

/** One proposed operation in words, with the names the nodes have now. */
export function operationText(op, index) {
  const lang = lang2(locale.value);
  const name = (id) => (index && index.nodes.has(id) ? nodeName(index.nodes.get(id), lang) : id);
  switch (op.op) {
    case 'rename_node':
      return t('themes.ai.op.rename', { name: name(op.node_id), to: Object.values(op.names)[0] || '' });
    case 'move_keywords':
      return t('themes.ai.op.move', { keyword: op.keywords.join(', '), to: name(op.node_id) });
    case 'put_back':
      return t('themes.ai.op.put_back', { keyword: op.keywords.join(', '), to: name(op.node_id) });
    case 'merge_nodes':
      return t('themes.ai.op.merge', { source: name(op.source), target: name(op.target) });
    case 'split_node':
      return t('themes.ai.op.split', { name: name(op.node_id), to: Object.values(op.parts[0].names)[0] || '',
        count: op.parts[0].members.length });
    case 'set_aside':
      return t('themes.ai.op.set_aside', { keyword: op.keywords.join(', ') });
    case 'set_attribution':
      return op.levels === 0 ? t('themes.ai.op.count_nowhere', { keyword: op.keywords.join(', ') })
        : t('themes.ai.op.count_level', { keyword: op.keywords.join(', '), level: op.levels === null ? '—' : op.levels });
    case 'create_node':
      return t('themes.ai.op.create', { name: Object.values(op.names || {})[0] || op.node_id || '' });
    case 'delete_node':
      return t('themes.ai.op.delete', { name: name(op.node_id) });
    case 'move_node':
      return t('themes.ai.op.move_node', { name: name(op.node_id), to: op.parent ? name(op.parent) : '—' });
    default:
      return op.op;
  }
}

/** A proposed change in words: its operation, or a count of its operations when it has several. */
export function itemText(item, index) {
  const ops = item.ops || [item.op];
  if (ops.length === 1) return operationText(ops[0], index);
  const count = (kind) => ops.filter((op) => op.op === kind).length;
  const moved = ops.filter((op) => op.op === 'move_keywords' || op.op === 'put_back')
    .reduce((n, op) => n + op.keywords.length, 0);
  return t('themes.ai.op.many', { created: count('create_node'), moved, removed: count('delete_node'),
    total: ops.length });
}

/** Sets of changes to choose at once: each kind, and each reason several changes share. */
function choiceSets(proposal) {
  const byVerb = new Map();
  const byReason = new Map();
  proposal.items.forEach((it, i) => {
    if (it.refused) return;
    byVerb.set(it.verb, [...(byVerb.get(it.verb) || []), i]);
    if (it.reason) byReason.set(it.reason, [...(byReason.get(it.reason) || []), i]);
  });
  const verbs = [...byVerb].filter(([, list]) => list.length > 1)
    .map(([verb, list]) => ({ id: `v:${verb}`, label: t('themes.ai.choose_kind', {
      kind: t(`themes.ai.verb.${verb.replace(' ', '_')}`), count: list.length }), list }));
  const reasons = [...byReason].filter(([, list]) => list.length > 1)
    .sort((a, b) => b[1].length - a[1].length).slice(0, 5)
    .map(([reason, list]) => ({ id: `r:${reason}`, label: t('themes.ai.choose_reason', {
      reason: reason.length > 40 ? `${reason.slice(0, 40)}…` : reason, count: list.length }), list }));
  return verbs.length + reasons.length > 1 || proposal.items.length > 8 ? [...verbs, ...reasons] : [];
}

/**
 * The review list of a proposal: each operation with a checkbox, its reason,
 * and why it cannot apply when it cannot; whole kinds of changes, or the changes
 * sharing a reason, are chosen at once.
 */
export function ProposalList({ proposal, index, accepted, onChange }) {
  const toggle = (i) => {
    const next = new Set(accepted);
    if (next.has(i)) next.delete(i);
    else next.add(i);
    onChange(next);
  };
  const toggleSet = (list) => {
    const next = new Set(accepted);
    const all = list.every((i) => next.has(i));
    list.forEach((i) => (all ? next.delete(i) : next.add(i)));
    onChange(next);
  };
  const sets = choiceSets(proposal);
  return html`<div class="cx-themes-ai">
    <div class="cx-themes-ai__bulk">
      <${Button} size="s" variant="ghost" onClick=${() => onChange(new Set(proposal.items
        .map((it, i) => (it.refused ? null : i)).filter((i) => i !== null)))}>${t('themes.ai.all')}<//>
      <${Button} size="s" variant="ghost" onClick=${() => onChange(new Set())}>${t('themes.ai.none')}<//>
      <span class="cx-themes-ai__count" aria-live="polite">${t('themes.ai.chosen', {
        count: accepted.size, total: proposal.items.length })}</span>
    </div>
    ${sets.length ? html`<div class="cx-themes-ai__sets" role="group" aria-label=${t('themes.ai.choose_sets')}>
      ${sets.map((set) => html`<${Button} key=${set.id} size="s" variant="ghost"
        aria-pressed=${set.list.every((i) => accepted.has(i)) ? 'true' : 'false'}
        onClick=${() => toggleSet(set.list)}>${set.label}<//>`)}
    </div>` : null}
    <ol class="cx-themes-ai__list">
      ${proposal.items.map((item, i) => html`<li key=${i} class=${`cx-themes-ai__item ${item.refused ? 'is-refused' : ''}`}>
        <${Checkbox} checked=${accepted.has(i)} disabled=${Boolean(item.refused)}
          label=${html`<span class="cx-themes-ai__verb">${t(`themes.ai.verb.${item.verb.replace(' ', '_')}`)}</span>
            <span class="cx-themes-ai__what">${itemText(item, index)}</span>`}
          onChange=${() => toggle(i)} />
        ${item.reason ? html`<p class="cx-themes-ai__reason">${t('themes.ai.reason', { reason: item.reason })}</p>` : null}
        ${item.refused ? html`<p class="cx-themes-ai__refused"><${Icon} name="warning" />
          <span>${t('themes.ai.refused', { reason: item.refused })}</span></p>` : null}
      </li>`)}
    </ol>
    ${proposal.unreadable.length ? html`<details class="cx-themes-ai__unreadable">
      <summary>${t('themes.ai.unreadable', { count: proposal.unreadable.length })}</summary>
      <ul>${proposal.unreadable.map((u, i) => html`<li key=${i}>
        <span class="cx-themes-ai__line">${t('themes.ai.line', { line: u.line })}</span>
        <code class="cx-themes-ai__text">${u.text}</code>
        <span class="cx-themes-form__hint">${t(`themes.ai.problem.${u.problem}`)}</span></li>`)}</ul>
    </details>` : null}
  </div>`;
}
