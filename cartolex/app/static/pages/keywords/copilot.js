// SPDX-License-Identifier: MIT
/**
 * Triage with an AI copilot: download a bundle an assistant that runs code
 * works from on its own (the candidates with their evidence, sorted into
 * groups by the kit, who uses which as numbers, the curator's standing rules,
 * and the kit), optionally with a few lines of text around each candidate,
 * names masked, cut into parts one conversation each; bring back its
 * `result.json` files (the parts, or a result taken up again: merged), see
 * what the kit counted and the caveats, review the decisions term by term,
 * and accept all or some. Nothing reaches the decisions before « Accept ».
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatNumber, locale, t } from '../../core/i18n.js';
import { downloadFile, useUid } from '../../core/dom.js';
import {
  Button, Checkbox, Dialog, ErrorCard, FormField, Icon, Select, Stepper, messageOf,
} from '../../components/index.js';
import {
  COPILOT_STEPS, CopilotExport, CopilotImport, CopilotOutcome, CurationNotes, EarlierResults, stepState,
} from '../copilot/parts.js';
import { Review, itemKey } from './review.js';

const SCOPES = ['all', 'both', 'check', 'unjudged'];
const PARTS = [1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64];

/**
 * @param {object} props
 * @param {object} props.ctx the page's context
 * @param {string} [props.scope] the candidates to send first (`unjudged`: those nobody judged)
 * @param {Function} props.onClose
 * @param {(message: string, more?: object) => void} props.onDone after an accept (`more`: the
 *   toast's message and action when the build's route changed)
 * @param {string} [props.proposal] an imported result to open at its review
 */
export function KeywordCopilotDialog({ ctx, scope: initialScope = 'all', onClose, onDone, proposal: initial }) {
  const [step, setStep] = useState('export');
  const [scope, setScope] = useState(initialScope);
  const [lines, setLines] = useState(false);
  const [parts, setParts] = useState(0); // 0: as the summary suggests
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState(null);
  const [proposal, setProposal] = useState(null);
  const [chosen, setChosen] = useState(new Set());
  const [busy, setBusy] = useState(false);
  const [making, setMaking] = useState(false);
  const uid = useUid('cx-kw-copilot');
  const language = (locale.value || 'en').slice(0, 2);
  const query = `scope=${scope}&usage_lines=${lines ? 'true' : 'false'}`;
  const nParts = parts || (summary ? summary.parts : 1);

  useEffect(() => {
    setSummary(null);
    setError(null);
    ctx.api.get(`/api/keywords/copilot/summary?${query}`).then((r) => {
      if (r.ok) setSummary(r.data);
      else setError(r.error);
    });
  }, [scope, lines]);

  // Made on demand: on a large project the zip takes a while, so the button says so while it is
  // made and a refusal shows in the dialog (a plain link showed nothing until the file came).
  const download = async () => {
    setMaking(true);
    setError(null);
    const r = await ctx.api.get(`/api/keywords/copilot/export?${query}&parts=${nParts}`
      + `&language=${encodeURIComponent(language)}`);
    setMaking(false);
    if (r.ok && r.data && r.data.blob) {
      downloadFile(r.data.filename || 'cartolex-triage.zip', r.data.blob, 'application/zip');
    } else setError(r.error);
  };
  const show = (data) => {
    setProposal(data);
    setChosen(new Set(data.items.map(itemKey)));
    setStep('review');
  };
  const read = async (results) => {
    setBusy(true);
    setError(null);
    const r = await ctx.api.post('/api/keywords/copilot/import', { results });
    setBusy(false);
    if (r.ok) show(r.data);
    else setError(r.error);
  };
  const open = async (id) => {
    setError(null);
    const r = await ctx.api.get(`/api/ai/proposals/${encodeURIComponent(id)}`);
    if (r.ok) show(r.data);
    else setError(r.error);
  };
  useEffect(() => {
    if (initial && /^\d{8}T\d{6}Z-copilot-triage(-\d+)?$/.test(initial)) open(initial);
  }, []);
  const accept = async () => {
    setBusy(true);
    setError(null);
    const all = chosen.size === proposal.items.length;
    const terms = all ? [] : proposal.items.filter((it) => chosen.has(itemKey(it)))
      .map((it) => ({ term: it.term, language: it.language }));
    const r = await ctx.api.post(`/api/ai/proposals/${encodeURIComponent(proposal.id)}/accept`,
      { all, terms }, { ifMatch: `"${proposal.keywords_version}"` });
    setBusy(false);
    if (!r.ok) {
      setError(r.error);
      return;
    }
    // A copilot's triage accepted with the route at « No AI »: the build now asks the copilot.
    const note = r.data.note;
    onDone(t('keywords.ai.accepted', { n: r.data.accepted }), note ? {
      message: messageOf(note), timeout: 15000,
      action: { label: t(`overview.action.${note.code}`), onClick: () => ctx.navigate('/build') },
    } : {});
  };

  let body;
  let footer;
  if (step === 'export') {
    const c = summary && summary.counts;
    const counts = c ? t('copilot.triage.counts', { terms: formatNumber(c.terms), check: formatNumber(c.check),
      people: formatNumber(c.people) }) : null;
    const contains = ['terms', 'evidence', 'people', 'decisions', 'rules', 'field', 'kit']
      .map((k) => `copilot.triage.contains.${k}`).concat(lines ? ['copilot.triage.contains.usage'] : []);
    const never = [lines ? 'copilot.never.texts_whole' : 'copilot.never.texts', 'copilot.never.people',
      'copilot.never.named', 'copilot.never.keys'];
    body = html`<${CopilotExport} lead=${t('copilot.triage.lead')} contains=${contains} never=${never}
      counts=${counts} error=${error} onDownload=${summary && c.terms ? download : null} busy=${making}>
      <fieldset class="cx-kw-scope"><legend>${t('keywords.ai.scope')}</legend>
        ${SCOPES.map((id) => html`<label class="cx-kw-radio" key=${id}>
          <input type="radio" name=${`${uid}-scope`} checked=${scope === id} onChange=${() => setScope(id)} />
          <span>${t(`keywords.ai.scope.${id}`)}</span></label>`)}
      </fieldset>
      <${Checkbox} checked=${lines} onChange=${() => setLines(!lines)} label=${t('copilot.usage_lines')}
        aria-describedby=${`${uid}-lines`} />
      <p class="cx-handoff__hint" id=${`${uid}-lines`}>${t('copilot.usage_lines.help')}</p>
      ${summary && summary.warning ? html`<p class="cx-kw-warning" role="note"><${Icon} name="warning" />
        <span>${t('copilot.many_terms', { terms: summary.warning.params.terms,
          parts: summary.warning.params.parts })}</span></p>` : null}
      <${FormField} label=${t('copilot.parts')} help=${summary ? t('copilot.parts.help', {
        tokens: formatNumber(summary.tokens), suggested: summary.parts }) : ''}>
        ${(field) => html`<${Select} ...${field} value=${String(nParts)}
          onChange=${(e) => setParts(Number(e.currentTarget.value))}
          options=${[...new Set([...PARTS, nParts])].sort((a, b) => a - b)
            .map((n) => ({ value: String(n), label: t('copilot.parts.n', { n }) }))} />`}
      <//>
      <${CurationNotes} api=${ctx.api} />
    <//>`;
    footer = html`<${Button} variant="ghost" onClick=${onClose}>${t('common.cancel')}<//>
      <${Button} variant="primary" iconAfter="chevron-right" disabled=${!summary}
        onClick=${() => setStep('import')}>${t('copilot.next')}<//>`;
  } else if (step === 'import') {
    body = html`<${CopilotImport} onRead=${read} busy=${busy} error=${error} multiple />
      <${EarlierResults} api=${ctx.api} url="/api/ai/proposals" onOpen=${open} />`;
    footer = html`<${Button} variant="ghost" icon="chevron-left" onClick=${() => setStep('export')}>${t('common.back')}<//>`;
  } else {
    body = html`<${CopilotOutcome} proposal=${proposal} />
      ${proposal ? html`<${Review} proposal=${proposal} chosen=${chosen} setChosen=${setChosen} />` : null}
      ${error ? html`<${ErrorCard} error=${error} compact />` : null}
      <p class="cx-handoff__hint">${t('copilot.review.hint')}</p>`;
    footer = html`<${Button} variant="ghost" icon="chevron-left" onClick=${() => setStep('import')}>${t('common.back')}<//>
      <${Button} variant="primary" loading=${busy} disabled=${!chosen.size} onClick=${accept}>
        ${t('keywords.ai.accept', { n: chosen.size })}<//>`;
  }
  return html`<${Dialog} open=${true} onClose=${onClose} size="l" title=${t('copilot.triage.title')}
    description=${t('copilot.triage.description')} footer=${footer}>
    <${Stepper} label=${t('copilot.steps')} steps=${COPILOT_STEPS.map((id) => ({
      id, label: t(`copilot.step.${id}`), state: stepState(step, id) }))}
      onSelect=${(id) => { if (id !== 'review' || proposal) setStep(id); }} />
    <div class="cx-handoff" role="group" aria-label=${t(`copilot.step.${step}`)}>${body}</div>
  <//>`;
}
