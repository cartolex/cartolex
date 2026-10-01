// SPDX-License-Identifier: MIT
/**
 * A paused job in the Activity drawer: why it paused, how far it got, what a
 * large list costs (its size, requests, time, days of a daily budget, and the
 * other routes: the snapshot, narrowing), when a spent daily budget comes back (in local
 * time, next to « Resume »; never restarted automatically) and « Resume » or « Continue ».
 *
 * A paused job (`state: paused`) kept its state: `result.pause` is
 * `{code, params, message, checkpoint, progress, cause}` (docs/dev/api.md).
 */
import { html, useState } from '../core/preact.js';
import { formatDate, formatDuration, formatNumber, has, t } from '../core/i18n.js';
import { errorFromResponse, jobError } from '../core/errors.js';
import { runtime } from '../core/runtime.js';
import { Button } from './button.js';
import { ErrorCard } from './error-card.js';
import { ProgressBar } from './progress.js';

/** The pause in words: its code's catalogue entry, else the server's English words. */
function pauseWords(pause) {
  const key = `job.pause.${pause.code}`;
  return has(key) ? t(key, pause.params || {}) : pause.message || t('job.pause.other');
}

function SizeFacts({ params }) {
  return html`<dl class="cx-job__facts">
    <dt>${t('job.size.works')}</dt><dd>${formatNumber(params.total || 0)}</dd>
    <dt>${t('job.size.requests')}</dt><dd>${formatNumber(params.requests || 0)}</dd>
    <dt>${t('job.size.time')}</dt><dd>${formatDuration(Math.max(1, params.seconds || 0))}</dd>
    ${params.days ? html`<dt>${t('job.size.budget')}</dt>
      <dd>${t(params.keyed ? 'job.size.days_keyed' : 'job.size.days', { days: params.days })}</dd>` : null}
  </dl>
  <p class="cx-job__meta">${t('job.size.rough')}</p>
  <ul class="cx-job__routes">
    <li>${t('job.size.narrow')}</li>
    <li>${t('job.size.snapshot')} <code>cartolex collect snapshot</code></li>
  </ul>`;
}

/** Without a key: a free one raises the daily budget tenfold, and where to set it. */
function KeyHint() {
  return html`<p class="cx-job__meta">${t('job.budget.key')}</p>
  <div class="cx-job__actions">
    <${Button} size="s" variant="secondary" onClick=${() => runtime.navigate('/settings?section=sources')}>
      ${t('job.budget.open_settings')}<//>
  </div>`;
}

/**
 * @param {{job: object, jobs: object}} props `jobs` is the jobs store
 */
export function PausedJob({ job, jobs }) {
  const pause = (job.result && job.result.pause) || { code: 'other', params: {} };
  const progress = pause.progress || {};
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const size = pause.code === 'collect_size_confirm';
  const budget = pause.code === 'collect_budget_paused';
  const params = pause.params || {};
  const resume = async () => {
    setBusy(true);
    setError(null);
    const result = await jobs.resume(job);
    setBusy(false);
    if (!result.ok) setError(result.error || errorFromResponse(null));
  };
  const cause = pause.cause ? jobError({ error: pause.cause, state: 'failed', finished_at: job.finished_at }) : null;
  return html`<div class="cx-job__result">
    <p class="cx-job__meta">${pauseWords(pause)}</p>
    ${progress.total ? html`<${ProgressBar} value=${(progress.works || 0) / progress.total} resetKey=${job.id}
      label=${t('job.paused_label')} showValue
      valueText=${t('job.paused_progress', { works: progress.works || 0, total: progress.total })} />
      <p class="cx-job__meta">${t('job.paused_progress', { works: progress.works || 0, total: progress.total })}</p>` : null}
    ${size ? html`<${SizeFacts} params=${params} />` : null}
    ${budget && !params.keyed ? html`<${KeyHint} />` : null}
    ${cause && !budget ? html`<${ErrorCard} compact level=${4} error=${cause} />` : null}
    ${error ? html`<${ErrorCard} compact level=${4} error=${error} live />` : null}
    <div class="cx-job__actions">
      ${jobs.resumable(job) ? html`<${Button} size="s" variant="primary" loading=${busy}
        onClick=${resume}>${t(size ? 'job.continue' : 'job.resume')}<//>` : null}
      ${budget && params.resets_at ? html`<span class="cx-job__meta cx-job__back">
        ${t('job.budget.back', { time: formatDate(params.resets_at, 'datetime') })}</span>` : null}
      <${Button} size="s" variant="ghost" onClick=${() => jobs.dismiss(job.id)}>
        ${t('common.dismiss')}<//>
    </div>
  </div>`;
}
