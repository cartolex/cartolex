// SPDX-License-Identifier: MIT
/**
 * AI filtering in a browser (a handoff): export the keywords an AI judges
 * (kept, to check and set aside by default; never those rejected
 * automatically, nor those an AI already answered) in parts for a chat assistant the person already uses (the terms
 * the same people use, a term and its translation, sit in the same part),
 * paste its answer back, review the proposal term by term, and accept all
 * of it or some. Nothing reaches the decisions before « Accept ».
 */
import { html, useEffect, useRef, useState } from '../../core/preact.js';
import { formatDate, formatNumber, has, t } from '../../core/i18n.js';
import { copyText, downloadFile, useUid } from '../../core/dom.js';
import {
  Button, Dialog, ErrorCard, FormField, Icon, Select, Stepper, Table, Textarea,
} from '../../components/index.js';

const STEPS = ['export', 'import', 'review'];
const SCOPES = { all: ['kept', 'check', 'aside'], both: ['kept', 'check'], check: ['check'] };
const itemKey = (it) => `${it.language}\u0000${it.term}`;

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

/**
 * @param {object} props
 * @param {object} props.ctx the page's context
 * @param {string} props.version the keywords' version (If-Match of the accept)
 * @param {Function} props.onClose
 * @param {(message: string) => void} props.onDone after an accept
 */
export function KeywordHandoffDialog({ ctx, onClose, onDone }) {
  const [step, setStep] = useState('export');
  const [scope, setScope] = useState('all');
  const [exported, setExported] = useState(null);
  const [error, setError] = useState(null);
  const [part, setPart] = useState(0);
  const [answer, setAnswer] = useState('');
  const [earlier, setEarlier] = useState(null);
  const [proposal, setProposal] = useState(null);
  const [chosen, setChosen] = useState(new Set());
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(null);
  const fileInput = useRef(null);
  const uid = useUid('cx-kw-ai');

  useEffect(() => {
    setExported(null);
    setError(null);
    ctx.api.post('/api/handoff/export', { bands: SCOPES[scope], limit: 20000 }).then((r) => {
      if (r.ok) setExported(r.data);
      else setError(r.error);
    });
  }, [scope]);
  useEffect(() => {
    if (step !== 'import' || earlier) return;
    ctx.api.get('/api/handoff/proposals').then((r) => { if (r.ok) setEarlier(r.data.items); });
  }, [step]);

  const show = (data) => {
    setProposal(data);
    setChosen(new Set(data.items.map(itemKey)));
    setStep('review');
  };
  const read = async () => {
    setBusy(true);
    setError(null);
    const r = await ctx.api.post('/api/handoff/import', { bundle: exported.parts[part].bundle, answer });
    setBusy(false);
    if (r.ok) show(r.data);
    else setError(r.error);
  };
  const open = async (id) => {
    setError(null);
    const r = await ctx.api.get(`/api/handoff/proposals/${encodeURIComponent(id)}`);
    if (r.ok) show(r.data);
    else setError(r.error);
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
  const readFile = async (event) => {
    const file = event.target.files && event.target.files[0];
    if (file) setAnswer(await file.text());
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
    body = html`<p class="cx-handoff__lead">${t('keywords.ai.lead')}</p>
      <fieldset class="cx-kw-scope"><legend>${t('keywords.ai.scope')}</legend>
        ${Object.keys(SCOPES).map((id) => html`<label class="cx-kw-radio" key=${id}>
          <input type="radio" name=${`${uid}-scope`} checked=${scope === id} onChange=${() => setScope(id)} />
          <span>${t(`keywords.ai.scope.${id}`)}</span></label>`)}
      </fieldset>
      <div class="cx-handoff__columns">
        <section class="cx-handoff__box" aria-labelledby=${`${uid}-contains`}>
          <h3 id=${`${uid}-contains`} class="cx-handoff__box-title"><${Icon} name="check" />${t('handoff.export.contains')}</h3>
          <ul class="cx-handoff__list">
            <li>${t('handoff.export.contains.terms', { count: exported ? exported.terms : 0 })}</li>
            <li>${t('handoff.export.contains.evidence')}</li>
            <li>${t('handoff.export.contains.field')}</li>
            <li>${t('handoff.export.contains.instructions')}</li>
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
      ${!exported && !error ? html`<p aria-busy="true">${t('common.loading')}</p>` : null}
      ${exported && exported.grouped ? html`<p class="cx-corpus-muted">${t('keywords.ai.grouped')}</p>` : null}
      ${parts.map((p) => html`<section class="cx-kw-part" key=${p.name}
        aria-label=${t('keywords.ai.part', { part: p.part, parts: p.parts })}>
        <p class="cx-kw-part__title">${t('keywords.ai.part', { part: p.part, parts: p.parts })}
          <span class="cx-corpus-muted"> ${t('keywords.ai.part.size', { n: p.terms, tokens: formatNumber(p.tokens) })}</span></p>
        <div class="cx-handoff__actions">
          <${Button} size="s" icon="copy" onClick=${async () => setCopied(await copyText(p.files['prompt.txt']))}>
            ${t('keywords.ai.copy_prompt')}<//>
          <${Button} size="s" icon="download" onClick=${() => downloadFile(`${p.name}-terms.txt`, p.files['terms.txt'], 'text/plain')}>
            ${t('keywords.ai.download_terms')}<//>
          <${Button} size="s" variant="ghost" icon="download" onClick=${() => downloadFile(`${p.name}-bundle.json`,
            JSON.stringify(p.bundle, null, 1), 'application/json')}>${t('keywords.ai.download_bundle')}<//>
        </div>
      </section>`)}
      <p class="cx-handoff__status" role="status">${copied === true ? t('handoff.export.copied')
        : copied === false ? t('error.copy_failed') : ''}</p>
      <p class="cx-handoff__hint">${t('keywords.ai.hint')}</p>`;
    footer = html`<${Button} variant="ghost" onClick=${onClose}>${t('common.cancel')}<//>
      <${Button} variant="primary" iconAfter="chevron-right" disabled=${!exported || !parts.length}
        onClick=${() => setStep('import')}>${t('handoff.export.next')}<//>`;
  } else if (step === 'import') {
    const parts = exported ? exported.parts : [];
    body = html`${parts.length > 1 ? html`<${FormField} label=${t('keywords.ai.which_part')}>
        ${(field) => html`<${Select} ...${field} value=${String(part)}
          onChange=${(e) => setPart(Number(e.currentTarget.value))}
          options=${parts.map((p, i) => ({ value: String(i), label: t('keywords.ai.part', { part: p.part, parts: p.parts }) }))} />`}
      <//>` : null}
      <${FormField} label=${t('handoff.import.label')} help=${t('keywords.ai.answer_help')} required>
        ${(field) => html`<${Textarea} ...${field} rows=${9} value=${answer} spellcheck="false"
          class="cx-handoff__answer" onInput=${(e) => setAnswer(e.currentTarget.value)} />`}
      <//>
      <div class="cx-handoff__file">
        <input ref=${fileInput} type="file" accept=".txt,text/plain" class="cx-visually-hidden"
          tabindex="-1" aria-hidden="true" onChange=${readFile} />
        <${Button} size="s" icon="upload" onClick=${() => fileInput.current.click()}>${t('handoff.import.file')}<//>
      </div>
      ${earlier && earlier.length ? html`<details class="cx-kw-earlier">
        <summary>${t('keywords.ai.earlier', { n: earlier.length })}</summary>
        <ul>${earlier.slice(0, 20).map((p) => html`<li key=${p.id}>
          <button type="button" class="cx-link-button" onClick=${() => open(p.id)}>
            ${formatDate(`${p.at.slice(0, 4)}-${p.at.slice(4, 6)}-${p.at.slice(6, 8)}T${p.at.slice(9, 11)}:${p.at.slice(11, 13)}:${p.at.slice(13, 15)}Z`, 'datetime', 'short')}</button></li>`)}</ul>
      </details>` : null}
      ${error ? html`<${ErrorCard} error=${error} compact />` : null}`;
    footer = html`<${Button} variant="ghost" icon="chevron-left" onClick=${() => setStep('export')}>${t('common.back')}<//>
      <${Button} variant="primary" loading=${busy} disabled=${!answer.trim()} onClick=${read}>
        ${t('handoff.import.check')}<//>`;
  } else {
    body = html`${proposal ? html`<${Review} proposal=${proposal} chosen=${chosen} setChosen=${setChosen} />` : null}
      ${error ? html`<${ErrorCard} error=${error} compact />` : null}
      <p class="cx-handoff__hint">${t('handoff.review.hint')}</p>`;
    footer = html`<${Button} variant="ghost" icon="chevron-left" onClick=${() => setStep('import')}>${t('common.back')}<//>
      <${Button} variant="primary" loading=${busy} disabled=${!chosen.size} onClick=${accept}>
        ${t('keywords.ai.accept', { n: chosen.size })}<//>`;
  }
  return html`<${Dialog} open=${true} onClose=${onClose} size="l" title=${t('keywords.ai.handoff_title')}
    description=${t('keywords.ai.handoff_description')} footer=${footer}>
    <${Stepper} label=${t('handoff.steps')} steps=${STEPS.map((id) => ({
      id, label: t(`handoff.step.${id}`), state: state(id) }))}
      onSelect=${(id) => { if (id !== 'review' || proposal) setStep(id); }} />
    <div class="cx-handoff" role="group" aria-label=${t(`handoff.step.${step}`)}>${body}</div>
  <//>`;
}
