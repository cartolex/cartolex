// SPDX-License-Identifier: MIT
/**
 * The AI copilot of the keywords: download a bundle an assistant that runs
 * code works from on its own (the Kept and To check candidates with their
 * evidence, who uses which as numbers, and the kit), optionally with a few
 * lines of text around each candidate, names masked; bring back its
 * `result.json`, review the decisions term by term, and accept all or some.
 * Nothing reaches the decisions before « Accept ».
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatNumber, locale, t } from '../../core/i18n.js';
import { useUid } from '../../core/dom.js';
import { Button, Checkbox, Dialog, ErrorCard, Stepper } from '../../components/index.js';
import { COPILOT_STEPS, CopilotExport, CopilotImport, CopilotOutcome, stepState } from '../copilot/parts.js';
import { Review } from './handoff.js';

const SCOPES = ['both', 'check'];
const itemKey = (it) => `${it.language}\u0000${it.term}`;

/**
 * @param {object} props
 * @param {object} props.ctx the page's context
 * @param {Function} props.onClose
 * @param {(message: string) => void} props.onDone after an accept
 */
export function KeywordCopilotDialog({ ctx, onClose, onDone }) {
  const [step, setStep] = useState('export');
  const [scope, setScope] = useState('both');
  const [lines, setLines] = useState(false);
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState(null);
  const [proposal, setProposal] = useState(null);
  const [chosen, setChosen] = useState(new Set());
  const [busy, setBusy] = useState(false);
  const uid = useUid('cx-kw-copilot');
  const language = (locale.value || 'en').slice(0, 2);
  const query = `scope=${scope}&usage_lines=${lines ? 'true' : 'false'}`;

  useEffect(() => {
    setSummary(null);
    setError(null);
    ctx.api.get(`/api/keywords/copilot/summary?${query}`).then((r) => {
      if (r.ok) setSummary(r.data);
      else setError(r.error);
    });
  }, [scope, lines]);

  const read = async (result) => {
    setBusy(true);
    setError(null);
    const r = await ctx.api.post('/api/keywords/copilot/import', { result });
    setBusy(false);
    if (!r.ok) {
      setError(r.error);
      return;
    }
    setProposal(r.data);
    setChosen(new Set(r.data.items.map(itemKey)));
    setStep('review');
  };
  const accept = async () => {
    setBusy(true);
    setError(null);
    const all = chosen.size === proposal.items.length;
    const terms = all ? [] : proposal.items.filter((it) => chosen.has(itemKey(it)))
      .map((it) => ({ term: it.term, language: it.language }));
    const r = await ctx.api.post(`/api/handoff/proposals/${encodeURIComponent(proposal.id)}/accept`,
      { all, terms }, { ifMatch: `"${proposal.keywords_version}"` });
    setBusy(false);
    if (r.ok) onDone(t('keywords.ai.accepted', { n: r.data.accepted }));
    else setError(r.error);
  };

  let body;
  let footer;
  if (step === 'export') {
    const c = summary && summary.counts;
    const counts = c ? t('copilot.triage.counts', { terms: formatNumber(c.terms), check: formatNumber(c.check),
      people: formatNumber(c.people) }) : null;
    const contains = ['terms', 'evidence', 'people', 'decisions', 'field', 'kit']
      .map((k) => `copilot.triage.contains.${k}`).concat(lines ? ['copilot.triage.contains.usage'] : []);
    const never = [lines ? 'copilot.never.texts_whole' : 'copilot.never.texts', 'copilot.never.people',
      'copilot.never.named', 'copilot.never.keys'];
    body = html`<${CopilotExport} lead=${t('copilot.triage.lead')} contains=${contains} never=${never}
      counts=${counts} error=${error}
      href=${summary && c.terms ? `/api/keywords/copilot/export?${query}&language=${encodeURIComponent(language)}` : null}>
      <fieldset class="cx-kw-scope"><legend>${t('keywords.ai.scope')}</legend>
        ${SCOPES.map((id) => html`<label class="cx-kw-radio" key=${id}>
          <input type="radio" name=${`${uid}-scope`} checked=${scope === id} onChange=${() => setScope(id)} />
          <span>${t(`keywords.ai.scope.${id}`)}</span></label>`)}
      </fieldset>
      <${Checkbox} checked=${lines} onChange=${() => setLines(!lines)} label=${t('copilot.usage_lines')}
        aria-describedby=${`${uid}-lines`} />
      <p class="cx-handoff__hint" id=${`${uid}-lines`}>${t('copilot.usage_lines.help')}</p>
    <//>`;
    footer = html`<${Button} variant="ghost" onClick=${onClose}>${t('common.cancel')}<//>
      <${Button} variant="primary" iconAfter="chevron-right" disabled=${!summary}
        onClick=${() => setStep('import')}>${t('copilot.next')}<//>`;
  } else if (step === 'import') {
    body = html`<${CopilotImport} onRead=${read} busy=${busy} error=${error} />`;
    footer = html`<${Button} variant="ghost" icon="chevron-left" onClick=${() => setStep('export')}>${t('common.back')}<//>`;
  } else {
    body = html`<${CopilotOutcome} proposal=${proposal} />
      ${proposal ? html`<${Review} proposal=${proposal} chosen=${chosen} setChosen=${setChosen} />` : null}
      ${error ? html`<${ErrorCard} error=${error} compact />` : null}
      <p class="cx-handoff__hint">${t('handoff.review.hint')}</p>`;
    footer = html`<${Button} variant="ghost" icon="chevron-left" onClick=${() => setStep('import')}>${t('common.back')}<//>
      <${Button} variant="primary" loading=${busy} disabled=${!chosen.size} onClick=${accept}>
        ${t('keywords.ai.accept', { n: chosen.size })}<//>`;
  }
  return html`<${Dialog} open=${true} onClose=${onClose} size="l" title=${t('copilot.triage.title')}
    description=${t('copilot.triage.description')} footer=${footer}>
    <${Stepper} label=${t('handoff.steps')} steps=${COPILOT_STEPS.map((id) => ({
      id, label: t(`copilot.step.${id}`), state: stepState(step, id) }))}
      onSelect=${(id) => { if (id !== 'review' || proposal) setStep(id); }} />
    <div class="cx-handoff" role="group" aria-label=${t(`copilot.step.${step}`)}>${body}</div>
  <//>`;
}
