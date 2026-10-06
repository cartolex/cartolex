// SPDX-License-Identifier: MIT
/**
 * The parts both AI copilot dialogs share (themes and keywords): what the
 * bundle holds and never holds with its counts and the download, the choice
 * of the result files, the results imported before, and the assistant's
 * measures, counts, caveats, standing rules and notes beside the review. The
 * bundle is a zip an assistant that runs code works from on its own; its
 * result comes back as `result.json` (for triage, one per part, or several
 * taken up in turn: they are merged).
 */
import { html, useEffect, useRef, useState } from '../../core/preact.js';
import { formatDate, formatNumber, has, t } from '../../core/i18n.js';
import { useUid } from '../../core/dom.js';
import { Button, ErrorCard, FormField, Icon, Textarea } from '../../components/index.js';

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
        <h3 id=${`${uid}-contains`} class="cx-handoff__box-title"><${Icon} name="check" />${t('copilot.contains')}</h3>
        <ul class="cx-handoff__list">${contains.map((key) => html`<li key=${key}>${t(key)}</li>`)}</ul>
      </section>
      <section class="cx-handoff__box" aria-labelledby=${`${uid}-never`}>
        <h3 id=${`${uid}-never`} class="cx-handoff__box-title"><${Icon} name="cross" />${t('copilot.never')}</h3>
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
 * Choose the result file(s) and read them (JSON documents); *onRead* gets the parsed
 * results, in the order chosen. With *multiple*, several (the parts of a bundle).
 * @param {object} props
 * @param {(results: object[]) => Promise<void>} props.onRead
 * @param {boolean} props.busy
 * @param {boolean} [props.multiple]
 * @param {object} [props.error] the server's refusal
 */
export function CopilotImport({ onRead, busy, error, multiple = false }) {
  const input = useRef(null);
  const [problem, setProblem] = useState('');
  const [names, setNames] = useState([]);
  const uid = useUid('cx-copilot-file');
  const read = async (event) => {
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    if (!files.length) return;
    setNames(files.map((f) => f.name));
    setProblem('');
    const results = [];
    for (const file of files) {
      try {
        results.push(JSON.parse(await file.text()));
      } catch (_) {
        setProblem(t('copilot.import.not_json', { name: file.name }));
        return;
      }
    }
    await onRead(results);
  };
  return html`<p class="cx-handoff__lead" id=${`${uid}-help`}>${t(multiple ? 'copilot.import.help_several' : 'copilot.import.help')}</p>
    <div class="cx-handoff__file">
      <input ref=${input} type="file" accept=".json,application/json" multiple=${multiple}
        class="cx-visually-hidden" tabindex="-1" aria-hidden="true" onChange=${read} />
      <${Button} icon="upload" variant="primary" loading=${busy} aria-describedby=${`${uid}-help`}
        onClick=${() => input.current.click()}>${t(multiple ? 'copilot.import.choose_several' : 'copilot.import.choose')}<//>
      ${names.length ? html`<span class="cx-handoff__status" role="status">${names.map((n) => html`<code key=${n}>${n}</code> `)}</span>` : null}
    </div>
    ${problem ? html`<p class="cx-copilot__problem" role="alert"><${Icon} name="warning" />${problem}</p>` : null}
    ${error ? html`<${ErrorCard} error=${error} compact />` : null}`;
}

/**
 * The curator's curation notes (teams, keywords that belong together or apart, standing
 * context), kept in the project (`decisions/curation-notes.md`, also in Settings › Project)
 * and carried by every bundle: the assistant checks its work against them.
 * @param {object} props
 * @param {object} props.api
 */
export function CurationNotes({ api }) {
  const [notes, setNotes] = useState(null);
  const [kept, setKept] = useState('');
  const [etag, setEtag] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.get('/api/settings/curation').then((r) => {
      if (!r.ok) return;
      setNotes(r.data.notes);
      setKept(r.data.notes);
      setEtag(r.etag);
    });
  }, []);
  if (notes === null) return null;
  const save = async () => {
    setBusy(true);
    setError(null);
    const r = await api.put('/api/settings/curation', { notes }, { ifMatch: etag });
    setBusy(false);
    if (r.ok) {
      setKept(r.data.notes);
      setEtag(r.etag);
    } else setError(r.error);
  };
  return html`<div class="cx-copilot__curation">
    <${FormField} label=${t('copilot.curation_notes')} help=${t('copilot.curation_notes.help')}>
      ${(field) => html`<${Textarea} ...${field} rows=${3} value=${notes} maxLength=${20000}
        onInput=${(e) => setNotes(e.currentTarget.value)} />`}
    <//>
    <div class="cx-copilot__curation-actions">
      <${Button} size="s" loading=${busy} disabled=${notes === kept} onClick=${save}>${t('copilot.curation_notes.save')}<//>
      <span class="cx-handoff__status" role="status">${notes === kept && kept ? t('copilot.curation_notes.saved') : ''}</span>
    </div>
    ${error ? html`<${ErrorCard} error=${error} compact />` : null}
  </div>`;
}

/** An id's time (`20260929T210651Z-…`) as a date. */
function stampDate(id) {
  const s = id.slice(0, 16);
  return formatDate(`${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}T${s.slice(9, 11)}:${s.slice(11, 13)}:${s.slice(13, 15)}Z`,
    'datetime', 'short');
}

/**
 * The results imported before (the copilot's, and the answers to a handoff an earlier
 * version imported), newest first: one opens again for review.
 * @param {object} props
 * @param {object} props.api
 * @param {string} props.url the list's address
 * @param {(id: string) => void} props.onOpen
 */
export function EarlierResults({ api, url, onOpen }) {
  const [items, setItems] = useState(null);
  useEffect(() => {
    api.get(url).then((r) => { if (r.ok) setItems(r.data.items); });
  }, [url]);
  if (!items || !items.length) return null;
  return html`<details class="cx-kw-earlier">
    <summary>${t('copilot.earlier', { n: items.length })}</summary>
    <ul>${items.slice(0, 20).map((p) => html`<li key=${p.id}>
      <button type="button" class="cx-link-button" onClick=${() => onOpen(p.id)}>${stampDate(p.id)}</button>
      ${' '}<span class="cx-corpus-muted">${t(p.id.includes('-copilot-') ? 'copilot.earlier.copilot' : 'copilot.earlier.handoff')}</span></li>`)}</ul>
  </details>`;
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

const COVERAGE = ['decided_by_group', 'decided_by_term', 'decided_unread', 'read_undecided', 'not_shown'];

/** What the kit counted, per band: decided by group, term by term, without reading, read
 * and undecided, never shown. */
function TriageCoverage({ coverage }) {
  const bands = Object.keys(coverage || {});
  if (!bands.length) return null;
  return html`<table class="cx-copilot__measures">
    <caption>${t('copilot.coverage.title')}</caption>
    <thead><tr><th scope="col">${t('copilot.coverage.band')}</th>
      ${COVERAGE.map((k) => html`<th scope="col" key=${k}>${t(`copilot.coverage.${k}`)}</th>`)}</tr></thead>
    <tbody>${bands.map((b) => html`<tr key=${b}>
      <th scope="row">${has(`keywords.band.${b}`) ? t(`keywords.band.${b}`) : t('copilot.coverage.all')}</th>
      ${COVERAGE.map((k) => html`<td key=${k}>${formatNumber(coverage[b][k] || 0)}</td>`)}</tr>`)}</tbody>
  </table>`;
}

/** What the result does not cover: the kit's counts, and the assistant's own words. */
function Caveats({ caveats }) {
  const c = caveats || {};
  const count = (key) => (c[key] && c[key].all) || 0;
  const lines = ['not_read', 'read_undecided', 'decided_unread'].filter((k) => count(k))
    .map((k) => t(`copilot.caveats.${k}`, { n: count(k) }));
  if (!lines.length && !c.read_lightly) return null;
  return html`<div class="cx-copilot__caveats" role="note">
    <h4 class="cx-copilot__notes-title"><${Icon} name="warning" />${t('copilot.caveats')}</h4>
    <ul>${lines.map((l) => html`<li key=${l}>${l}</li>`)}
      ${c.read_lightly ? html`<li>${t('copilot.caveats.read_lightly', { text: c.read_lightly })}</li>` : null}</ul>
  </div>`;
}

/** The assistant's notes, the standing rules it brought back and, for themes, the measures
 * before and after; for triage, what the kit counted and the caveats (shown open). */
export function CopilotOutcome({ proposal }) {
  if (!proposal) return null;
  const notes = (proposal.notes || '').trim();
  const rules = proposal.rules || [];
  return html`${proposal.bundle_known === false ? html`<p class="cx-copilot__note cx-copilot__note--warning"
      role="note"><${Icon} name="warning" /><span><strong>${t('copilot.unknown_bundle.word')}</strong>${' '}
      ${t('copilot.unknown_bundle')}</span></p>` : null}
    ${proposal.partial ? html`<p class="cx-copilot__note" role="note"><${Icon} name="info" />
      ${t('copilot.partial')}</p>` : null}
    ${proposal.task === 'triage' ? html`<${Caveats} caveats=${proposal.caveats} />` : null}
    <details class="cx-copilot__outcome" open=${proposal.task === 'triage'}>
    <summary>${t('copilot.outcome')}</summary>
    ${proposal.task === 'themes' ? html`<${ThemeMeasures} measures=${proposal.measures || {}} />`
      : html`<${TriageCoverage} coverage=${proposal.coverage} />`}
    ${rules.length ? html`<h4 class="cx-copilot__notes-title">${t('copilot.rules')}</h4>
      <ul class="cx-copilot__rules">${rules.map((r) => html`<li key=${r}>${r}</li>`)}</ul>` : null}
    ${notes ? html`<h4 class="cx-copilot__notes-title">${t('copilot.notes')}</h4>
      <p class="cx-copilot__notes">${notes}</p>` : html`<p>${t('copilot.notes.none')}</p>`}
  </details>`;
}
