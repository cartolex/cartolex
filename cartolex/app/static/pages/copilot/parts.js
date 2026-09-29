// SPDX-License-Identifier: MIT
/**
 * The parts both AI copilot dialogs share (themes and keywords): what the
 * bundle holds and never holds with its counts and the download, the choice
 * of the result file, and the assistant's measures and notes beside the
 * review. The bundle is a zip an assistant that runs code works from on its
 * own; its result comes back as one file, `result.json`.
 */
import { html, useRef, useState } from '../../core/preact.js';
import { formatNumber, has, t } from '../../core/i18n.js';
import { useUid } from '../../core/dom.js';
import { Button, ErrorCard, Icon } from '../../components/index.js';

export const COPILOT_STEPS = ['export', 'import', 'review'];

/** The state of a step in the stepper, from the current one. */
export function stepState(step, id) {
  const at = COPILOT_STEPS.indexOf(step);
  const i = COPILOT_STEPS.indexOf(id);
  return i < at ? 'done' : i === at ? 'current' : 'todo';
}

/**
 * What the bundle holds and never holds, its counts, and the download.
 * @param {object} props
 * @param {string[]} props.contains catalogue keys
 * @param {string[]} props.never catalogue keys
 * @param {string|null} props.counts the counts in words
 * @param {string|null} [props.href] the download address (null while loading)
 * @param {Function|null} [props.onDownload] or: make the zip and offer it (a POST)
 * @param {boolean} [props.busy] while the zip is made
 * @param {object} [props.error]
 */
export function CopilotExport({ lead, contains, never, counts, href, onDownload = null, busy = false,
  error, children }) {
  const uid = useUid('cx-copilot');
  return html`<p class="cx-handoff__lead">${lead}</p>
    ${children}
    <div class="cx-handoff__columns">
      <section class="cx-handoff__box" aria-labelledby=${`${uid}-contains`}>
        <h3 id=${`${uid}-contains`} class="cx-handoff__box-title"><${Icon} name="check" />${t('handoff.export.contains')}</h3>
        <ul class="cx-handoff__list">${contains.map((key) => html`<li key=${key}>${t(key)}</li>`)}</ul>
      </section>
      <section class="cx-handoff__box" aria-labelledby=${`${uid}-never`}>
        <h3 id=${`${uid}-never`} class="cx-handoff__box-title"><${Icon} name="cross" />${t('handoff.export.never')}</h3>
        <ul class="cx-handoff__list">${never.map((key) => html`<li key=${key}>${t(key)}</li>`)}</ul>
      </section>
    </div>
    ${error ? html`<${ErrorCard} error=${error} compact />` : null}
    ${!counts && !error ? html`<p aria-busy="true">${t('common.loading')}</p>` : null}
    ${counts ? html`<p class="cx-handoff__evidence">${counts}</p>` : null}
    ${href ? html`<div class="cx-handoff__actions">
      <a class="cx-button cx-button--primary cx-button--m" href=${href} download>
        <${Icon} name="download" /><span class="cx-button__label">${t('copilot.download')}</span></a>
    </div>` : null}
    ${onDownload ? html`<div class="cx-handoff__actions">
      <${Button} variant="primary" icon="download" loading=${busy} onClick=${onDownload}>${t('copilot.download')}<//>
    </div>` : null}
    <p class="cx-handoff__hint">${t('copilot.hint')}</p>`;
}

/**
 * Choose the result file and read it (a JSON document); *onRead* gets the parsed result.
 * @param {object} props
 * @param {(result: object) => Promise<void>} props.onRead
 * @param {boolean} props.busy
 * @param {object} [props.error] the server's refusal
 */
export function CopilotImport({ onRead, busy, error }) {
  const input = useRef(null);
  const [problem, setProblem] = useState('');
  const [name, setName] = useState('');
  const uid = useUid('cx-copilot-file');
  const read = async (event) => {
    const file = event.target.files && event.target.files[0];
    event.target.value = '';
    if (!file) return;
    setName(file.name);
    setProblem('');
    let result;
    try {
      result = JSON.parse(await file.text());
    } catch (_) {
      setProblem(t('copilot.import.not_json'));
      return;
    }
    await onRead(result);
  };
  return html`<p class="cx-handoff__lead" id=${`${uid}-help`}>${t('copilot.import.help')}</p>
    <div class="cx-handoff__file">
      <input ref=${input} type="file" accept=".json,application/json" class="cx-visually-hidden"
        tabindex="-1" aria-hidden="true" onChange=${read} />
      <${Button} icon="upload" variant="primary" loading=${busy} aria-describedby=${`${uid}-help`}
        onClick=${() => input.current.click()}>${t('copilot.import.choose')}<//>
      ${name ? html`<span class="cx-handoff__status" role="status"><code>${name}</code></span>` : null}
    </div>
    ${problem ? html`<p class="cx-copilot__problem" role="alert"><${Icon} name="warning" />${problem}</p>` : null}
    ${error ? html`<${ErrorCard} error=${error} compact />` : null}`;
}

const FIT = ['coherence', 'margin', 'misplaced', 'borderline'];

/** The measures of the tree before and after the assistant's changes, level by level. */
function ThemeMeasures({ measures }) {
  const before = measures.before && measures.before.fit ? measures.before.fit.levels : [];
  const after = measures.after && measures.after.fit ? measures.after.fit.levels : [];
  if (!before.length || !after.length) return null;
  const rows = [];
  before.forEach((b, i) => {
    const a = after[i];
    if (!a) return;
    FIT.forEach((key) => {
      if (typeof b[key] === 'number' && typeof a[key] === 'number') {
        rows.push({ key: `${b.level}-${key}`, level: b.level, name: key, before: b[key], after: a[key] });
      }
    });
  });
  const fmt = (v) => formatNumber(v, { maximumFractionDigits: 3 });
  return html`<table class="cx-copilot__measures">
    <caption>${t('copilot.measures.title')}</caption>
    <thead><tr><th scope="col">${t('copilot.measures.measure')}</th><th scope="col">${t('copilot.measures.before')}</th>
      <th scope="col">${t('copilot.measures.after')}</th></tr></thead>
    <tbody>${rows.map((r) => html`<tr key=${r.key}>
      <th scope="row">${t('copilot.measures.row', { level: r.level, measure: has(`copilot.measure.${r.name}`) ? t(`copilot.measure.${r.name}`) : r.name })}</th>
      <td>${fmt(r.before)}</td><td>${fmt(r.after)}</td></tr>`)}</tbody>
  </table>`;
}

/** The assistant's notes and, for themes, the measures before and after. */
export function CopilotOutcome({ proposal }) {
  if (!proposal) return null;
  const notes = (proposal.notes || '').trim();
  return html`<details class="cx-copilot__outcome">
    <summary>${t('copilot.outcome')}</summary>
    ${proposal.task === 'themes' ? html`<${ThemeMeasures} measures=${proposal.measures || {}} />` : null}
    ${notes ? html`<h4 class="cx-copilot__notes-title">${t('copilot.notes')}</h4>
      <p class="cx-copilot__notes">${notes}</p>` : html`<p>${t('copilot.notes.none')}</p>`}
  </details>`;
}
