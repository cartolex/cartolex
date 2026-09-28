// SPDX-License-Identifier: MIT
/**
 * The AI handoff dialog: export a bundle of terms with its instructions for
 * an AI assistant the person already uses, then import the answer.
 *
 * Three steps. **Export** says what the bundle contains and what it never
 * contains, and offers the file and the text to paste. **Import** takes the
 * pasted answer or a file. **Review** shows what the answer would change;
 * nothing changes before « Import ». The checking and the import are the
 * caller's (`onCheck`, `onImport`: the server's handoff routes in the app).
 */
import { html, useEffect, useRef, useState } from '../core/preact.js';
import { formatNumber, t } from '../core/i18n.js';
import { copyText, downloadFile, useUid } from '../core/dom.js';
import { Button } from './button.js';
import { Dialog } from './dialog.js';
import { FormField, Textarea } from './form-field.js';
import { Icon } from './icons.js';
import { Stepper } from './stepper.js';

const STEPS = ['export', 'import', 'review'];

/**
 * @param {object} props
 * @param {boolean} props.open
 * @param {Function} props.onClose
 * @param {{domain: string, items: Array<object>}} props.bundle what is exported
 * @param {{name: string, content: string}} props.file the bundle as a file
 * @param {string} props.prompt the instructions and terms, as text to paste
 * @param {(text: string) => Promise<{ok: boolean, summary?: object}>} props.onCheck
 * @param {(checked: object) => Promise<any>} props.onImport
 * @param {'export'|'import'|'review'} [props.initialStep] (the gallery shows each step)
 * @param {string} [props.initialAnswer]
 */
export function AiHandoffDialog({ open, onClose, bundle, file, prompt, onCheck, onImport,
  initialStep = 'export', initialAnswer = '', initialChecked = null }) {
  const [step, setStep] = useState(initialStep);
  const [answer, setAnswer] = useState(initialAnswer);
  const [checked, setChecked] = useState(initialChecked);
  const [problem, setProblem] = useState('');
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(null);
  const fileInput = useRef(null);
  const content = useRef(null);
  const firstStep = useRef(true);
  const uid = useUid('cx-handoff');
  // A new step replaces the buttons that had the focus: give it to the step itself.
  useEffect(() => {
    if (firstStep.current) {
      firstStep.current = false;
      return;
    }
    const field = content.current && content.current.querySelector('textarea');
    (field || content.current)?.focus();
  }, [step]);
  const count = bundle.items.length;

  const stepState = (id) => {
    const at = STEPS.indexOf(step);
    const i = STEPS.indexOf(id);
    return i < at ? 'done' : i === at ? 'current' : 'todo';
  };
  const check = async () => {
    setBusy(true);
    setProblem('');
    const result = await onCheck(answer);
    setBusy(false);
    if (result && result.ok) {
      setChecked(result);
      setStep('review');
    } else {
      setProblem(t('handoff.import.nothing_understood'));
    }
  };
  const finish = async () => {
    setBusy(true);
    await onImport(checked);
    setBusy(false);
    onClose('done');
  };
  const readFile = async (event) => {
    const chosen = event.target.files && event.target.files[0];
    if (chosen) setAnswer(await chosen.text());
  };

  let body;
  let footer;
  if (step === 'export') {
    body = html`<p class="cx-handoff__lead">${t('handoff.export.lead', { count, domain: bundle.domain })}</p>
      <div class="cx-handoff__columns">
        <section class="cx-handoff__box" aria-labelledby=${`${uid}-contains`}>
          <h3 id=${`${uid}-contains`} class="cx-handoff__box-title">
            <${Icon} name="check" />${t('handoff.export.contains')}</h3>
          <ul class="cx-handoff__list">
            <li>${t('handoff.export.contains.terms', { count })}</li>
            <li>${t('handoff.export.contains.evidence')}</li>
            <li>${t('handoff.export.contains.field')}</li>
            <li>${t('handoff.export.contains.instructions')}</li>
          </ul>
        </section>
        <section class="cx-handoff__box" aria-labelledby=${`${uid}-never`}>
          <h3 id=${`${uid}-never`} class="cx-handoff__box-title">
            <${Icon} name="cross" />${t('handoff.export.never')}</h3>
          <ul class="cx-handoff__list">
            <li>${t('handoff.export.never.texts')}</li>
            <li>${t('handoff.export.never.people')}</li>
            <li>${t('handoff.export.never.keys')}</li>
          </ul>
        </section>
      </div>
      <details class="cx-handoff__preview">
        <summary>${t('handoff.export.preview', { count: Math.min(5, count) })}</summary>
        <ol class="cx-handoff__terms">
          ${bundle.items.slice(0, 5).map((item) => html`<li key=${item.term}>
            <span class="cx-handoff__term">${item.term}</span>
            <span class="cx-handoff__lang">${item.lang}</span>
            <span class="cx-handoff__evidence">${t('handoff.export.evidence', {
              people: item.people, texts: item.texts })}</span>
          </li>`)}
        </ol>
      </details>
      <div class="cx-handoff__actions">
        <${Button} icon="download" onClick=${() => downloadFile(file.name, file.content)}>
          ${t('handoff.export.download')}<//>
        <${Button} icon="copy" onClick=${async () => setCopied(await copyText(prompt))}>
          ${t('handoff.export.copy')}<//>
        <span class="cx-handoff__status" role="status">
          ${copied === true ? t('handoff.export.copied') : copied === false ? t('error.copy_failed') : ''}
        </span>
      </div>
      <p class="cx-handoff__hint">${t('handoff.export.hint')}</p>`;
    footer = html`<${Button} variant="ghost" onClick=${() => onClose('close')}>${t('common.cancel')}<//>
      <${Button} variant="primary" iconAfter="chevron-right" onClick=${() => setStep('import')}>
        ${t('handoff.export.next')}<//>`;
  } else if (step === 'import') {
    body = html`<${FormField} label=${t('handoff.import.label')} help=${t('handoff.import.help')}
        error=${problem} required>
        ${(field) => html`<${Textarea} ...${field} rows=${9} value=${answer} spellcheck="false"
          class="cx-handoff__answer" onInput=${(e) => setAnswer(e.currentTarget.value)} />`}
      <//>
      <div class="cx-handoff__file">
        <input ref=${fileInput} type="file" accept=".txt,.json,text/plain,application/json"
          class="cx-visually-hidden" tabindex="-1" aria-hidden="true" onChange=${readFile} />
        <${Button} size="s" icon="upload" onClick=${() => fileInput.current.click()}>
          ${t('handoff.import.file')}<//>
      </div>`;
    footer = html`<${Button} variant="ghost" icon="chevron-left" onClick=${() => setStep('export')}>
        ${t('common.back')}<//>
      <${Button} variant="primary" loading=${busy} disabled=${!answer.trim()} onClick=${check}>
        ${t('handoff.import.check')}<//>`;
  } else {
    const s = (checked && checked.summary) || {};
    body = html`<p class="cx-handoff__lead">${t('handoff.review.lead', { count: s.answered || 0, total: count })}</p>
      <dl class="cx-handoff__summary">
        <div><dt>${t('handoff.review.kept')}</dt><dd>${formatNumber(s.kept || 0)}</dd></div>
        <div><dt>${t('handoff.review.set_aside')}</dt><dd>${formatNumber(s.setAside || 0)}</dd></div>
        <div><dt>${t('handoff.review.unanswered')}</dt><dd>${formatNumber(s.unanswered || 0)}</dd></div>
        <div><dt>${t('handoff.review.not_understood')}</dt><dd>${formatNumber(s.notUnderstood || 0)}</dd></div>
      </dl>
      <p class="cx-handoff__hint">${t('handoff.review.hint')}</p>`;
    footer = html`<${Button} variant="ghost" icon="chevron-left" onClick=${() => setStep('import')}>
        ${t('common.back')}<//>
      <${Button} variant="primary" loading=${busy} onClick=${finish}>${t('handoff.review.import')}<//>`;
  }

  return html`<${Dialog} open=${open} onClose=${onClose} size="l" title=${t('handoff.title')}
    description=${t('handoff.description')} footer=${footer}>
    <${Stepper} label=${t('handoff.steps')} steps=${STEPS.map((id) => ({
      id, label: t(`handoff.step.${id}`), state: stepState(id) }))}
      onSelect=${(id) => setStep(id)} />
    <div class="cx-handoff" ref=${content} tabindex="-1" role="group"
      aria-label=${t(`handoff.step.${step}`)}>${body}</div>
  <//>`;
}
