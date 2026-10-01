// SPDX-License-Identifier: MIT
/**
 * The keywords' thresholds previewed in their « Tune » panel: as `min_people`, `min_texts`,
 * `max_share` or `max_keywords` moves (edited or saved and not built yet), how many of the last
 * build's candidates and kept keywords they keep (the vocabulary as the step's header counts it:
 * exact, or a range when people's lists would take their next term, which only a rebuild names),
 * and the strongest that would leave or could enter (`GET /api/method/keywords/preview`, read
 * from the stored candidates; nothing is saved). A value only a new extraction can show (a looser window) is said so instead.
 * Asked again a moment after the last change; a late answer to an older value is dropped.
 */

import { html, useEffect, useRef, useState } from '../../core/preact.js';
import { formatNumber, t } from '../../core/i18n.js';
import { Icon } from '../../components/index.js';
import { BandMark } from '../keywords/common.js';
import { paramLabel } from './params.js';

/** The thresholds the preview applies, by stage. */
export const THRESHOLDS = [
  ['keywords.extract', 'min_people'],
  ['keywords.extract', 'min_texts'],
  ['keywords.extract', 'max_share'],
  ['keywords.build', 'max_keywords'],
];
const BANDS = ['kept', 'check', 'aside', 'rejected'];
const WAIT_MS = 150;

/** The values the thresholds would have: the edit, else the saved value. */
function wanted(data, edits) {
  const out = {};
  for (const [stageId, name] of THRESHOLDS) {
    const stage = (data.stages || []).find((s) => s.id === stageId);
    const p = stage && stage.params.find((q) => q.name === name);
    if (!p) continue;
    const edit = edits[`${stageId}.${name}`];
    if (edit && edit.invalid) continue;
    const value = edit ? (edit.reset ? p.default_value : edit.value) : p.value;
    if (value !== null && value !== undefined) out[name] = value;
  }
  return out;
}

const stageOf = (name) => THRESHOLDS.find(([, n]) => n === name)[0];

/** The kept keywords after: exact, a range, or only a ceiling. */
function Range({ before, low, high }) {
  const after = low === null || low === undefined
    ? t('tune.thresholds.at_most', { high })
    : t('tune.thresholds.range', { low, high });
  return html`<span class="cx-thresholds__delta">${formatNumber(before)} → <strong>${after}</strong></span>`;
}

/** A count before and after. */
function Delta({ before, after }) {
  const d = after - before;
  return html`<span class="cx-thresholds__delta">${formatNumber(before)} → <strong>${formatNumber(after)}</strong>
    ${d ? html` <span class="cx-thresholds__change">(${d > 0 ? '+' : '−'}${formatNumber(Math.abs(d))})</span>` : null}</span>`;
}

function Names({ title, items, icon, cause = false }) {
  if (!items.length) return null;
  return html`<div class="cx-thresholds__list">
    <h5 class="cx-thresholds__list-title"><${Icon} name=${icon} /> ${title}</h5>
    <ul>${items.map((r) => html`<li key=${`${r.term}/${r.language}`}><span class="cx-thresholds__term">${r.term}</span>
      ${cause && r.cause ? html` <span class="cx-settings__muted">${t('tune.thresholds.cause', {
        param: paramLabel(stageOf(r.cause), r.cause), people: r.people, texts: r.texts })}</span>` : null}</li>`)}</ul>
  </div>`;
}

export function ThresholdsPreview({ ctx, data, edits }) {
  const values = wanted(data, edits);
  const key = JSON.stringify(values);
  const [view, setView] = useState(null);
  const [busy, setBusy] = useState(false);
  const asked = useRef(0);
  useEffect(() => {
    const n = asked.current + 1;
    asked.current = n;
    setBusy(true);
    const timer = setTimeout(() => {
      ctx.api.get('/api/method/keywords/preview', { query: values }).then((r) => {
        if (asked.current !== n) return; // a newer value was asked for
        setBusy(false);
        setView(r.ok ? r.data : null);
      });
    }, WAIT_MS);
    return () => clearTimeout(timer);
  }, [key]);

  if (!view || view.empty) return null;
  const same = Object.keys(view.built).every((k) => view.used[k] === view.built[k]);
  const c = view.candidates;
  const v = view.vocabulary;
  return html`<section class="cx-thresholds" aria-labelledby="cx-thresholds-title" aria-busy=${busy ? 'true' : 'false'}>
    <h4 class="cx-method-subtitle" id="cx-thresholds-title">${t('tune.thresholds.title')}</h4>
    ${(view.needs || []).map((m) => html`<p key=${m.params.param} class="cx-method-mark cx-method-mark--changed" role="note">
      <${Icon} name="warning" /><span>${t('tune.thresholds.needs', {
        param: paramLabel(stageOf(m.params.param), m.params.param), value: m.params.value, built: m.params.built })}</span></p>`)}
    ${same ? html`<p class="cx-settings__muted">${t('tune.thresholds.same')}</p>` : html`
      <div aria-live="polite">
        <dl class="cx-settings__facts cx-settings__facts--grid">
          <div><dt>${t('method.keywords.candidates')}</dt><dd><${Delta} before=${c.before} after=${c.after} /></dd></div>
          ${BANDS.filter((b) => view.bands[b] && view.bands[b].before).map((b) => html`<div key=${b}>
            <dt><${BandMark} band=${b} /></dt><dd><${Delta} before=${view.bands[b].before} after=${view.bands[b].after} /></dd></div>`)}
          ${v.available ? html`<div><dt>${t('method.keywords.vocabulary')}</dt>
            <dd>${v.after === null ? html`<${Range} before=${v.before} low=${v.after_low} high=${v.after_high} />`
              : html`<${Delta} before=${v.before} after=${v.after} />`}</dd></div>` : null}
        </dl>
      </div>
      <div class="cx-thresholds__lists">
        <${Names} title=${t('tune.thresholds.leaving', { n: c.leaving })} items=${view.leaving} icon="dash" cause />
        <${Names} title=${t('tune.thresholds.out_of_vocabulary', { n: v.leaving })} items=${view.vocabulary_leaving} icon="dash" />
        <${Names} title=${t('tune.thresholds.entering', { n: v.could_enter })} items=${view.vocabulary_entering} icon="plus" />
      </div>
      ${v.available && v.after === null ? html`<p class="cx-settings__muted">${t('tune.thresholds.range_note')}</p>` : null}`}
    ${same ? null : html`<p class="cx-settings__muted">${t('tune.thresholds.note')}</p>`}
  </section>`;
}
