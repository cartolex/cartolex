// SPDX-License-Identifier: MIT
/**
 * Activity: the header indicator (« Building · keywords 45 % ») and the
 * drawer listing jobs with their progress, a cancel button, their results,
 * and their errors (which stay until dismissed).
 */
import { html, useEffect } from '../core/preact.js';
import { formatDate, formatDuration, formatPercent, has, t } from '../core/i18n.js';
import { errorFromResponse } from '../core/errors.js';
import { runtime } from '../core/runtime.js';
import { Button } from './button.js';
import { Drawer } from './dialog.js';
import { EmptyState } from './empty-state.js';
import { ErrorCard } from './error-card.js';
import { ProgressBar } from './progress.js';
import { StatusDot, stageName } from './status.js';

const DOT = {
  queued: 'running', running: 'running', cancelling: 'running', succeeded: 'up_to_date',
  failed: 'failed', interrupted: 'failed', cancelled: 'skipped',
};

function kindWord(job, form) {
  const key = `job.${form}.${job.kind}`;
  return has(key) ? t(key) : t(`job.${form}.other`);
}

function areaOf(job) {
  const stage = job.progress && job.progress.stage;
  if (!stage) return '';
  const area = stage.split('.')[0];
  const key = `area.${area}.in_sentence`;
  return has(key) ? t(key) : area;
}

/** A job's title: its code's words (`job.title.<code>`), else its words, else its kind. */
export function jobTitle(job) {
  const key = job.title_code ? `job.title.${job.title_code}` : '';
  if (key && has(key)) return t(key, job.title_params || {});
  return job.title || kindWord(job, 'noun');
}

/** A job's result in one line: its code's words (`job.summary.<code>`), else its words. */
export function jobResultSummary(result) {
  if (!result) return '';
  const key = result.summary_code ? `job.summary.${result.summary_code}` : '';
  return key && has(key) ? t(key, result.summary_params || {}) : result.summary || '';
}

/** The indicator's words for a job: « Building · keywords 45 % », « Build failed ». */
export function jobHeadline(job) {
  if (job.state === 'failed' || job.state === 'interrupted') {
    return t('activity.failed', { kind: kindWord(job, 'noun') });
  }
  if (job.state === 'queued') return t('activity.queued', { verb: kindWord(job, 'verb') });
  const p = job.progress || {};
  const fraction = typeof p.stage_fraction === 'number' ? p.stage_fraction : p.fraction;
  const area = areaOf(job);
  return area && typeof fraction === 'number'
    ? t('activity.running', { verb: kindWord(job, 'verb'), area, percent: formatPercent(fraction) })
    : t('activity.running_short', { verb: kindWord(job, 'verb') });
}

/**
 * The header button: the running job's progress, the last failure, or « Activity ».
 * @param {{jobs: object, onOpen: Function}} props `jobs` is the jobs store
 */
export function ActivityIndicator({ jobs, onOpen, buttonRef }) {
  const job = jobs.headline.value;
  const state = job ? DOT[job.state] || 'running' : null;
  return html`<button ref=${buttonRef} type="button"
    class=${`cx-activity-indicator ${job ? `is-${state}` : 'is-idle'}`}
    aria-haspopup="dialog" onClick=${onOpen}>
    ${job ? html`<${StatusDot} state=${state} label=${jobHeadline(job)} size="s" />`
      : html`<${StatusDot} state="never_built" size="s" label=${t('activity.title')} />`}
  </button>`;
}

function JobItem({ job, jobs }) {
  const p = job.progress || {};
  const running = job.state === 'running' || job.state === 'queued' || job.state === 'cancelling';
  const title = jobTitle(job);
  // The API gives a job's error as the error shape, or as a sentence.
  const error = !job.error ? null : typeof job.error === 'string'
    ? errorFromResponse({ error: { code: `job_${job.state}`, message: job.error } })
    : errorFromResponse({ error: job.error }, { status: job.error.status });
  return html`<li class=${`cx-job cx-job--${job.state}`} data-job=${job.id}>
    <div class="cx-job__head">
      <${StatusDot} state=${DOT[job.state] || 'never_built'} />
      <h3 class="cx-job__title">${title}</h3>
      <span class="cx-job__state">${t(`job.state.${job.state}`)}</span>
    </div>
    ${running ? html`<div class="cx-job__progress">
      <${ProgressBar} value=${job.state === 'queued' ? null : p.fraction} resetKey=${job.id}
        label=${t('job.progress_label', { title })} showValue />
      ${p.stage ? html`<p class="cx-job__meta">
        ${t('tracker.phase', { phase: p.phase, phases: p.phases })}${' · '}
        ${stageName({ id: p.stage, name: p.name })}${' · '}${formatPercent(p.stage_fraction || 0)}
        ${p.code === 'improve_texts' ? html`${' · '}${t('tracker.improve', p.params)}` : null}
        ${p.eta_s ? html`${' · '}${t('tracker.eta', { eta: formatDuration(p.eta_s) })}` : null}
      </p>` : null}
      ${job.cancellable !== false ? html`<${Button} size="s" variant="secondary"
        loading=${job.state === 'cancelling'} onClick=${() => jobs.cancel(job.id)}>
        ${t('job.cancel')}<//>` : null}
    </div>` : null}
    ${job.state === 'succeeded' ? html`<div class="cx-job__result">
      <p class="cx-job__meta">${t('job.finished_at', { time: formatDate(job.finished_at, 'time', 'short') })}
        ${jobResultSummary(job.result) ? html`${' · '}${jobResultSummary(job.result)}` : null}</p>
      <div class="cx-job__actions">
        ${job.result && job.result.link ? html`<a class="cx-link" href=${job.result.link}>
          ${t('job.open_result')}</a>` : null}
        <${Button} size="s" variant="ghost" onClick=${() => jobs.dismiss(job.id)}>
          ${t('common.dismiss')}<//>
      </div>
    </div>` : null}
    ${job.state === 'cancelled' ? html`<div class="cx-job__result">
      <p class="cx-job__meta">${t(job.result && job.result.ran && job.result.ran.length
        ? 'job.cancelled_after' : 'job.cancelled_nothing')}</p>
      <div class="cx-job__actions"><${Button} size="s" variant="ghost"
        onClick=${() => jobs.dismiss(job.id)}>${t('common.dismiss')}<//></div>
    </div>` : null}
    ${(job.state === 'failed' || job.state === 'interrupted') ? html`<${ErrorCard} compact level=${4}
      error=${error || errorFromResponse({ error: { code: `job_${job.state}` } })}
      onDismiss=${() => jobs.dismiss(job.id)} />` : null}
  </li>`;
}

/**
 * The Activity drawer: every job not dismissed, the running ones first.
 * @param {{open: boolean, onClose: Function, jobs: object}} props
 */
export function ActivityDrawer({ open, onClose, jobs }) {
  useEffect(() => (open ? jobs.watch() : undefined), [open]);
  const list = jobs.visible.value;
  const ordered = [...list.filter((j) => DOT[j.state] === 'running'),
    ...list.filter((j) => DOT[j.state] !== 'running')];
  return html`<${Drawer} open=${open} onClose=${onClose} title=${t('activity.title')}
    description=${t('activity.description')}>
    ${ordered.length ? html`<ul class="cx-job-list">
      ${ordered.map((job) => html`<${JobItem} key=${job.id} job=${job} jobs=${jobs} />`)}
    </ul>` : html`<${EmptyState} icon="activity" title=${t('activity.empty.title')}
      action=${{ label: t('activity.empty.action'), onClick: () => {
        onClose('action');
        runtime.navigate('/');
      } }}>
      ${t('activity.empty.text')}<//>`}
  <//>`;
}
