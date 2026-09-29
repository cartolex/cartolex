// SPDX-License-Identifier: MIT
/**
 * The AI copilot of the theme tree: download a bundle an assistant that runs
 * code works from on its own (the saved tree, its vectors and the kit),
 * bring back its `result.json`, review each change with its reason (accept or
 * reject, preview on the tree), then apply the accepted ones as ordinary
 * operations and save them as a version.
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatNumber, locale, t } from '../../core/i18n.js';
import { Button, Dialog, Stepper } from '../../components/index.js';
import { COPILOT_STEPS, CopilotExport, CopilotImport, CopilotOutcome, stepState } from '../copilot/parts.js';
import { ProposalList } from './handoff.js';
import { lang2 } from './model.js';

const CONTAINS = ['tree', 'keywords', 'people', 'field', 'kit'].map((k) => `copilot.themes.contains.${k}`);
const NEVER = ['texts', 'people', 'named', 'keys'].map((k) => `copilot.never.${k}`);

/** The changes that apply, all chosen at first. */
const applicable = (proposal) => new Set(proposal.items.map((it, i) => (it.refused ? null : i)).filter((i) => i !== null));

/**
 * @param {object} props
 * @param {object} props.api the page's API client
 * @param {object} props.editor
 * @param {(proposal: object, accepted: Set<number>) => void} props.onPreview
 * @param {(proposal: object, accepted: Set<number>) => Promise<any>} props.onApply applies and saves
 * @param {{proposal: object, accepted: Set<number>}|null} [props.resume] come back to a review
 */
export function ThemeCopilotDialog({ api, editor, onClose, onPreview, onApply, resume = null }) {
  const [step, setStep] = useState(resume ? 'review' : 'export');
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState(null);
  const [proposal, setProposal] = useState(resume ? resume.proposal : null);
  const [accepted, setAccepted] = useState(resume ? resume.accepted : new Set());
  const [busy, setBusy] = useState(false);
  const index = editor.editIndex.value;
  const language = lang2(locale.value);

  useEffect(() => {
    if (resume) return;
    api.get('/api/themes/copilot/summary').then((r) => {
      if (r.ok) setSummary(r.data);
      else setError(r.error);
    });
  }, []);

  const read = async (result) => {
    setBusy(true);
    setError(null);
    const r = await api.post('/api/themes/copilot/import', { result });
    setBusy(false);
    if (!r.ok) {
      setError(r.error);
      return;
    }
    setProposal(r.data);
    setAccepted(applicable(r.data));
    setStep('review');
  };

  let body;
  let footer;
  if (step === 'export') {
    const c = summary && summary.counts;
    const counts = c ? t('copilot.themes.counts', { nodes: formatNumber(c.nodes), keywords: formatNumber(c.keywords),
      people: formatNumber(c.people) }) : null;
    body = html`<${CopilotExport} lead=${t('copilot.themes.lead')} contains=${CONTAINS} never=${NEVER}
      counts=${counts} error=${error}
      href=${summary ? `/api/themes/copilot/export?language=${encodeURIComponent(language)}` : null}>
      ${editor.dirty.value ? html`<p class="cx-copilot__note" role="note">${t('copilot.themes.unsaved')}</p>`
        : summary && summary.source === 'draft' ? html`<p class="cx-copilot__note" role="note">${t('copilot.themes.draft')}</p>` : null}
    <//>`;
    footer = html`<${Button} variant="ghost" onClick=${() => onClose('close')}>${t('common.cancel')}<//>
      <${Button} variant="primary" iconAfter="chevron-right" disabled=${!summary}
        onClick=${() => setStep('import')}>${t('copilot.next')}<//>`;
  } else if (step === 'import') {
    body = html`<${CopilotImport} onRead=${read} busy=${busy} error=${error} />`;
    footer = html`<${Button} variant="ghost" icon="chevron-left" onClick=${() => setStep('export')}>${t('common.back')}<//>`;
  } else {
    body = proposal ? html`<p class="cx-handoff__lead">${t('themes.ai.review.lead', {
        count: proposal.items.length, applicable: proposal.applicable })}</p>
      <${CopilotOutcome} proposal=${proposal} />
      ${proposal.items.length ? html`<${ProposalList} proposal=${proposal} index=${index}
        accepted=${accepted} onChange=${setAccepted} />` : html`<p>${t('themes.ai.review.nothing')}</p>`}` : null;
    footer = html`<${Button} variant="ghost" icon="chevron-left" onClick=${() => setStep('import')}>${t('common.back')}<//>
      <${Button} disabled=${!accepted.size} onClick=${() => onPreview(proposal, accepted)}>${t('themes.ai.preview')}<//>
      <${Button} variant="primary" loading=${busy} disabled=${!accepted.size} onClick=${async () => {
        setBusy(true);
        await onApply(proposal, accepted);
        setBusy(false);
      }}>${t('copilot.apply_save', { count: accepted.size })}<//>`;
  }

  return html`<${Dialog} open=${true} onClose=${onClose} size="l" title=${t('copilot.themes.title')}
    description=${t('copilot.themes.description')} footer=${footer}>
    <${Stepper} label=${t('handoff.steps')} steps=${COPILOT_STEPS.map((id) => ({
      id, label: t(`copilot.step.${id}`), state: stepState(step, id) }))}
      onSelect=${(id) => {
        if (id !== 'review' || proposal) setStep(id);
      }} />
    <div class="cx-handoff" role="group" aria-label=${t(`copilot.step.${step}`)}>${body}</div>
  <//>`;
}
