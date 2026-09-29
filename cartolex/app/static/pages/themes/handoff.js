/**
 * AI curation of the theme tree through a handoff: export the tree for an
 * assistant the person already uses, import its answer, review each proposed
 * change (accept or reject, preview on the tree), then apply the accepted ones
 * as ordinary operations, undone like any other.
 */

import { html, useEffect, useRef, useState } from '../../core/preact.js';
import { formatNumber, locale, t } from '../../core/i18n.js';
import { copyText, downloadFile, useUid } from '../../core/dom.js';
import { Button, Checkbox, Dialog, ErrorCard, FormField, Icon, Stepper, Textarea } from '../../components/index.js';
import { lang2, nodeName } from './model.js';

const STEPS = ['export', 'import', 'review'];

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

/**
 * The review list of a proposal: each operation with a checkbox, its reason,
 * and why it cannot apply when it cannot.
 */
export function ProposalList({ proposal, index, accepted, onChange }) {
  const toggle = (i) => {
    const next = new Set(accepted);
    if (next.has(i)) next.delete(i);
    else next.add(i);
    onChange(next);
  };
  return html`<div class="cx-themes-ai">
    <div class="cx-themes-ai__bulk">
      <${Button} size="s" variant="ghost" onClick=${() => onChange(new Set(proposal.items
        .map((it, i) => (it.refused ? null : i)).filter((i) => i !== null)))}>${t('themes.ai.all')}<//>
      <${Button} size="s" variant="ghost" onClick=${() => onChange(new Set())}>${t('themes.ai.none')}<//>
      <span class="cx-themes-ai__count" aria-live="polite">${t('themes.ai.chosen', {
        count: accepted.size, total: proposal.items.length })}</span>
    </div>
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

/**
 * @param {object} props
 * @param {boolean} props.open
 * @param {object} props.api the page's API client
 * @param {object} props.editor
 * @param {(proposal: object, accepted: Set<number>) => void} props.onPreview
 * @param {(proposal: object, accepted: Set<number>) => Promise<any>} props.onApply
 * @param {{proposal: object, accepted: Set<number>}|null} [props.resume] come back to a review
 */
export function ThemeHandoffDialog({ open, api, editor, onClose, onPreview, onApply, resume = null }) {
  const [step, setStep] = useState(resume ? 'review' : 'export');
  const [exported, setExported] = useState(null);
  const [error, setError] = useState(null);
  const [part, setPart] = useState(0);
  const [answer, setAnswer] = useState('');
  const [proposal, setProposal] = useState(resume ? resume.proposal : null);
  const [accepted, setAccepted] = useState(resume ? resume.accepted : new Set());
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(null);
  const fileInput = useRef(null);
  const uid = useUid('cx-themes-ai');
  const index = editor.editIndex.value;

  useEffect(() => {
    if (!open || exported || resume) return;
    (async () => {
      const result = await api.post('/api/themes/handoff/export', { tree: editor.tree.value });
      if (result.ok) setExported(result.data);
      else setError(result.error);
    })();
  }, [open]);

  const read = async () => {
    setBusy(true);
    setError(null);
    const result = await api.post('/api/themes/handoff/import',
      { bundle: exported.parts[part].bundle, answer });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setProposal(result.data);
    setAccepted(new Set(result.data.items.map((it, i) => (it.refused ? null : i)).filter((i) => i !== null)));
    setStep('review');
  };
  const readFile = async (event) => {
    const chosen = event.target.files && event.target.files[0];
    if (chosen) setAnswer(await chosen.text());
  };
  const state = (id) => {
    const at = STEPS.indexOf(step);
    const i = STEPS.indexOf(id);
    return i < at ? 'done' : i === at ? 'current' : 'todo';
  };

  let body;
  let footer;
  if (step === 'export') {
    const parts = exported ? exported.parts : [];
    body = html`<p class="cx-handoff__lead">${t('themes.ai.lead')}</p>
      <div class="cx-handoff__columns">
        <section class="cx-handoff__box" aria-labelledby=${`${uid}-contains`}>
          <h3 id=${`${uid}-contains`} class="cx-handoff__box-title"><${Icon} name="check" />${t('handoff.export.contains')}</h3>
          <ul class="cx-handoff__list">
            <li>${t('themes.ai.contains.tree')}</li>
            <li>${t('themes.ai.contains.keywords')}</li>
            <li>${t('themes.ai.contains.aside')}</li>
            <li>${t('themes.ai.contains.field')}</li>
          </ul>
        </section>
        <section class="cx-handoff__box" aria-labelledby=${`${uid}-never`}>
          <h3 id=${`${uid}-never`} class="cx-handoff__box-title"><${Icon} name="cross" />${t('handoff.export.never')}</h3>
          <ul class="cx-handoff__list">
            <li>${t('handoff.export.never.texts')}</li>
            <li>${t('handoff.export.never.people')}</li>
            <li>${t('handoff.export.never.keys')}</li>
          </ul>
        </section>
      </div>
      ${error ? html`<${ErrorCard} error=${error} compact />` : null}
      ${!exported && !error ? html`<p class="cx-themes-empty-line" aria-busy="true">${t('common.loading')}</p>` : null}
      ${parts.map((p) => html`<section class="cx-themes-ai__part" key=${p.name} aria-label=${t('themes.ai.part', { part: p.part, parts: p.parts })}>
        <p class="cx-themes-ai__part-title">${p.parts > 1 ? t('themes.ai.part', { part: p.part, parts: p.parts }) : t('themes.ai.one_part')}
          <span class="cx-themes-form__hint">${t('themes.ai.part.size', { nodes: p.nodes, keywords: p.keywords, tokens: formatNumber(p.tokens) })}</span></p>
        <div class="cx-handoff__actions">
          <${Button} icon="copy" onClick=${async () => setCopied(await copyText(p.files['prompt.txt']))}>
            ${t('themes.ai.copy_prompt')}<//>
          <${Button} icon="download" onClick=${() => downloadFile(`${p.name}-tree.txt`, p.files['tree.txt'], 'text/plain')}>
            ${t('themes.ai.download_tree')}<//>
          <${Button} icon="download" variant="ghost" onClick=${() => downloadFile(`${p.name}-bundle.json`,
            JSON.stringify(p.bundle, null, 1), 'application/json')}>${t('themes.ai.download_bundle')}<//>
        </div>
      </section>`)}
      <p class="cx-handoff__status" role="status">${copied === true ? t('handoff.export.copied')
        : copied === false ? t('error.copy_failed') : ''}</p>
      <p class="cx-handoff__hint">${t('themes.ai.hint')}</p>`;
    footer = html`<${Button} variant="ghost" onClick=${() => onClose('close')}>${t('common.cancel')}<//>
      <${Button} variant="primary" iconAfter="chevron-right" disabled=${!exported}
        onClick=${() => setStep('import')}>${t('handoff.export.next')}<//>`;
  } else if (step === 'import') {
    const parts = exported ? exported.parts : [];
    body = html`${parts.length > 1 ? html`<fieldset class="cx-themes-form__set">
        <legend>${t('themes.ai.which_part')}</legend>
        <div class="cx-themes-form__radios" role="radiogroup">
          ${parts.map((p, i) => html`<label class="cx-themes-radio" key=${p.name}>
            <input type="radio" name=${`${uid}-part`} checked=${part === i} onChange=${() => setPart(i)} />
            <span>${t('themes.ai.part', { part: p.part, parts: p.parts })}</span></label>`)}
        </div></fieldset>` : null}
      <${FormField} label=${t('themes.ai.answer')} help=${t('themes.ai.answer.help')} required>
        ${(field) => html`<${Textarea} ...${field} rows=${10} value=${answer} spellcheck="false"
          class="cx-handoff__answer" onInput=${(e) => setAnswer(e.currentTarget.value)} />`}
      <//>
      <div class="cx-handoff__file">
        <input ref=${fileInput} type="file" accept=".txt,text/plain" class="cx-visually-hidden"
          tabindex="-1" aria-hidden="true" onChange=${readFile} />
        <${Button} size="s" icon="upload" onClick=${() => fileInput.current.click()}>${t('handoff.import.file')}<//>
      </div>
      ${error ? html`<${ErrorCard} error=${error} compact />` : null}`;
    footer = html`<${Button} variant="ghost" icon="chevron-left" onClick=${() => setStep('export')}>${t('common.back')}<//>
      <${Button} variant="primary" loading=${busy} disabled=${!answer.trim()} onClick=${read}>
        ${t('themes.ai.read')}<//>`;
  } else {
    body = proposal ? html`<p class="cx-handoff__lead">${t('themes.ai.review.lead', {
        count: proposal.items.length, applicable: proposal.applicable })}</p>
      ${proposal.items.length ? html`<${ProposalList} proposal=${proposal} index=${index}
        accepted=${accepted} onChange=${setAccepted} />` : html`<p>${t('themes.ai.review.nothing')}</p>`}` : null;
    footer = html`<${Button} variant="ghost" icon="chevron-left" onClick=${() => setStep('import')}
        disabled=${!exported}>${t('common.back')}<//>
      <${Button} disabled=${!accepted.size} onClick=${() => onPreview(proposal, accepted)}>${t('themes.ai.preview')}<//>
      <${Button} variant="primary" loading=${busy} disabled=${!accepted.size} onClick=${async () => {
        setBusy(true);
        await onApply(proposal, accepted);
        setBusy(false);
      }}>${t('themes.ai.apply', { count: accepted.size })}<//>`;
  }

  return html`<${Dialog} open=${open} onClose=${onClose} size="l" title=${t('themes.ai.title')}
    description=${t('themes.ai.description')} footer=${footer}>
    <${Stepper} label=${t('handoff.steps')} steps=${STEPS.map((id) => ({
      id, label: t(`handoff.step.${id}`), state: state(id) }))}
      onSelect=${(id) => {
        if (id !== 'review' || proposal) setStep(id);
      }} />
    <div class="cx-handoff" role="group" aria-label=${t(`handoff.step.${step}`)}>${body}</div>
  <//>`;
}
