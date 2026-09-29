// SPDX-License-Identifier: MIT
/**
 * Curate with AI (the theme editor's header): download a bundle an assistant
 * that runs code works from on its own (the tree being edited, unsaved edits
 * included, its levels and measures, its vectors, the curator's standing
 * rules and the kit), bring back its `result.json` (or open a result imported
 * before), review each change with its reason (accept or reject, preview on
 * the tree), then apply the accepted ones as ordinary operations and save them
 * as a version.
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatNumber, locale, t } from '../../core/i18n.js';
import { downloadFile } from '../../core/dom.js';
import { Button, Dialog, Stepper } from '../../components/index.js';
import {
  COPILOT_STEPS, CopilotExport, CopilotImport, CopilotOutcome, CurationNotes, EarlierResults, stepState,
} from '../copilot/parts.js';
import { ProposalList } from './proposal.js';
import { lang2 } from './model.js';

const CONTAINS = ['tree', 'keywords', 'people', 'texts', 'rules', 'field', 'kit']
  .map((k) => `copilot.themes.contains.${k}`);
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

  const [making, setMaking] = useState(false);
  useEffect(() => {
    if (resume) return;
    api.post('/api/themes/copilot/summary', { tree: editor.tree.value, language }).then((r) => {
      if (r.ok) setSummary(r.data);
      else setError(r.error);
    });
  }, []);

  const download = async () => {
    setMaking(true);
    setError(null);
    const r = await api.post('/api/themes/copilot/export', { tree: editor.tree.value, language });
    setMaking(false);
    if (r.ok && r.data && r.data.blob) downloadFile('copilot-themes.zip', r.data.blob, 'application/zip');
    else setError(r.error);
  };
  const show = (data) => {
    setProposal(data);
    setAccepted(applicable(data));
    setStep('review');
  };
  const read = async ([result]) => {
    setBusy(true);
    setError(null);
    const r = await api.post('/api/themes/copilot/import', { result });
    setBusy(false);
    if (r.ok) show(r.data);
    else setError(r.error);
  };
  const open = async (id) => {
    setError(null);
    const kind = id.includes('-copilot-') ? 'copilot' : 'handoff';
    const r = await api.get(`/api/themes/${kind}/proposals/${encodeURIComponent(id)}`);
    if (r.ok) show(r.data);
    else setError(r.error);
  };

  let body;
  let footer;
  if (step === 'export') {
    const c = summary && summary.counts;
    const counts = c ? t('copilot.themes.counts', { nodes: formatNumber(c.nodes), keywords: formatNumber(c.keywords),
      people: formatNumber(c.people) }) : null;
    body = html`<${CopilotExport} lead=${t('copilot.themes.lead')} contains=${CONTAINS} never=${NEVER}
      counts=${counts} error=${error} onDownload=${summary ? download : null} busy=${making}>
      <p class="cx-copilot__note" role="note">${t('copilot.themes.current')}</p>
      <${CurationNotes} api=${api} />
    <//>`;
    footer = html`<${Button} variant="ghost" onClick=${() => onClose('close')}>${t('common.cancel')}<//>
      <${Button} variant="primary" iconAfter="chevron-right" disabled=${!summary}
        onClick=${() => setStep('import')}>${t('copilot.next')}<//>`;
  } else if (step === 'import') {
    body = html`<${CopilotImport} onRead=${read} busy=${busy} error=${error} />
      <${EarlierResults} api=${api} url="/api/themes/handoff/proposals" onOpen=${open} />`;
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
    <${Stepper} label=${t('copilot.steps')} steps=${COPILOT_STEPS.map((id) => ({
      id, label: t(`copilot.step.${id}`), state: stepState(step, id) }))}
      onSelect=${(id) => {
        if (id !== 'review' || proposal) setStep(id);
      }} />
    <div class="cx-handoff" role="group" aria-label=${t(`copilot.step.${step}`)}>${body}</div>
  <//>`;
}
