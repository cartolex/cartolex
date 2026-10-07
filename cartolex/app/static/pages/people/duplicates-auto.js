// SPDX-License-Identifier: MIT
/**
 * The automatic merges of the Duplicates tab: « Merge the clear pairs », or « Merge
 * above a likelihood… » (every pair whose score is at least a threshold, 50 % by
 * default). Each shows what it would do first (the groups it would make, the nearest
 * to the threshold first), then merges them in one step that one « Undo » takes back.
 * The pairs are joined into groups as the review joins them: never across a pair
 * decided, nor two different ORCIDs.
 */
import { html, useEffect, useRef, useState } from '../../core/preact.js';
import { formatPercent, t } from '../../core/i18n.js';
import { useUid } from '../../core/dom.js';
import { Button, Dialog, ErrorCard, ParamControl } from '../../components/index.js';
import { evidenceText } from './duplicates-compare.js';

/** The threshold of « Merge above a likelihood… », in percent. */
const THRESHOLD = { name: 'min_score', type: 'int', minimum: 0, maximum: 100, default_value: 50,
  widget: 'slider' };

/**
 * The preview of the automatic merge, then the merge itself: the clear pairs or, with
 * *above*, every pair whose score is at least the threshold chosen here (the preview
 * follows it, a moment after the last change).
 */
export function AutoMerge({ ctx, etag, above = false, onClose, onDone }) {
  const id = useUid('cx-dup-above');
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [percent, setPercent] = useState(THRESHOLD.default_value);
  const [valid, setValid] = useState(true);
  const [asked, setAsked] = useState(percent); // the threshold of the preview shown
  const sent = useRef(0);
  const body = () => (above ? { min_score: asked / 100 } : {});
  useEffect(() => {
    if (!above || !valid || percent === asked) return undefined;
    const timer = setTimeout(() => setAsked(percent), 250);
    return () => clearTimeout(timer);
  }, [percent, valid]);
  useEffect(() => {
    const mine = ++sent.current;
    setPreview(null);
    ctx.api.post('/api/people/duplicates/auto', body()).then((r) => {
      if (mine !== sent.current) return;
      if (r.ok) setPreview(r.data);
      else setError(r.error);
    });
  }, [asked]);
  async function apply() {
    setBusy(true);
    const result = await ctx.api.post('/api/people/duplicates/auto', { ...body(), apply: true },
      { ifMatch: etag() });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    onDone(result);
  }
  const none = preview && !preview.merged;
  const current = !above || (valid && percent === asked);
  return html`<${Dialog} open=${true} onClose=${onClose} size="l"
    title=${t(above ? 'corpus.dup.above.title' : 'corpus.dup.auto.title')}
    description=${t(above ? 'corpus.dup.above.rule' : 'corpus.dup.auto.rule')}
    footer=${html`<${Button} onClick=${onClose}>${t('common.cancel')}<//>
      <${Button} variant="primary" icon="check" loading=${busy} disabled=${!preview || none || !current}
        onClick=${apply}>${t('corpus.dup.auto.apply', { n: (preview && preview.merged) || 0 })}<//>`}>
    ${above ? html`<div class="cx-dup-above" role="group" aria-labelledby=${`${id}-label`}>
      <span id=${`${id}-label`} class="cx-dup-above__label">${t('corpus.dup.above.label')}</span>
      <${ParamControl} p=${THRESHOLD} id=${id} labelId=${`${id}-label`} label=${t('corpus.dup.above.label')}
        value=${percent} onChange=${(v, bad) => {
          setValid(!bad && v !== null);
          setPercent(v);
        }} />
    </div>` : null}
    ${error ? html`<${ErrorCard} error=${error} compact />` : null}
    ${!preview && !error ? html`<p class="cx-corpus-muted">${t('common.loading')}</p>` : null}
    ${preview ? html`<div class="cx-dup-auto">
      <p><strong>${none ? t(above ? 'corpus.dup.above.none' : 'corpus.dup.auto.none')
        : t('corpus.dup.auto.count', { n: preview.merged, groups: preview.groups })}</strong></p>
      ${preview.examples.length ? html`<h3 class="cx-corpus-h3">${t(above ? 'corpus.dup.above.examples'
        : 'corpus.dup.auto.examples')}</h3>
        <ul class="cx-corpus-list cx-dup-auto__list">${preview.examples.map((g) => html`<li key=${g.keep.person_id}>
          ${above ? html`<span class="cx-dup-auto__score">${formatPercent(g.score)}</span> ` : null}
          <span>${t('corpus.dup.auto.example', { keep: g.keep.name,
            others: g.merge.map((m) => m.name).join(', ') })}</span>
          <span class="cx-corpus-muted"> ${g.evidence.filter((e) => e.points > 0).slice(0, 3)
            .map(evidenceText).join(' · ')}</span></li>`)}</ul>` : null}
      <p class="cx-corpus-muted">${t('corpus.dup.auto.undo_note')}</p>
    </div>` : null}
  <//>`;
}
