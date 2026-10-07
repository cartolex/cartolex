// SPDX-License-Identifier: MIT
/**
 * A build while it runs (the tracker, its progress and an honest Stop) and
 * when it ends (one sentence, the error card of a failed stage).
 */
import { html } from '../../core/preact.js';
import { formatDuration, formatPercent, t } from '../../core/i18n.js';
import { Button, Card, ErrorCard, ProgressBar, StageTracker } from '../../components/index.js';
import { aiRow, failureError, namesOf, resultSentence } from './words.js';
import { Notes } from './preflight.js';

const ACTIVE = new Set(['queued', 'running', 'cancelling']);

/** Whether a job still runs. */
export function isActive(job) {
  return Boolean(job) && ACTIVE.has(job.state);
}

/**
 * Every stage of the project in build order, with what this build does to it:
 * done, running (with its progress), waiting, stopped, kept, skipped, not run, held
 * back for a copilot (the AI step a build waits at among them). The AI clean-up,
 * skipped, reads as the project state shows it (`tracker.ai`): done with the copilot,
 * waiting for it, its earlier decisions only.
 */
export function trackerRows(order, tracker, job) {
  const own = new Map((tracker.stages || []).map((s) => [s.stage, s]));
  const kept = new Set(tracker.kept || []);
  const skipped = new Set(tracker.skipped || []);
  const refused = new Set(tracker.refused || []);
  const pause = job && job.result && job.result.waiting;
  const held = new Set((pause && pause.held) || []);
  const progress = job && job.progress;
  const rows = [];
  for (const id of order) {
    const base = { id, name: id };
    const s = own.get(id);
    if (s) {
      const live = isActive(job) && progress && progress.stage === id;
      if (s.state === 'done') {
        rows.push({ ...base, state: 'up_to_date', stateText: t('build.stage.done'),
          note: s.seconds ? t('build.stage.took', { time: formatDuration(s.seconds) }) : '' });
      } else if (s.state === 'running' || live) {
        rows.push({ ...base, state: 'running', stateText: t('build.stage.running'),
          progress: live ? progress : null });
      } else if (s.state === 'stopped') {
        rows.push({ ...base, state: 'skipped', stateText: t('build.stage.stopped') });
      } else {
        rows.push({ ...base, state: 'never_built', stateText: t('build.stage.waiting') });
      }
    } else if (held.has(id) || (pause && pause.step === id)) {
      rows.push({ ...base, state: 'never_built', stateText: t('build.action.held') });
    } else if (refused.has(id)) {
      rows.push({ ...base, state: 'skipped', stateText: t('build.stage.refused') });
    } else if (kept.has(id)) {
      rows.push({ ...base, state: 'up_to_date', stateText: t('build.action.keep') });
    } else if (skipped.has(id) && tracker.ai && tracker.ai.stage === id) {
      rows.push(aiRow(base, tracker.ai));
    } else if (skipped.has(id)) {
      rows.push({ ...base, state: 'skipped', stateText: t('build.action.skip') });
    }
  }
  return rows;
}

/** The build while it runs. */
export function Running({ job, rows, onCancel }) {
  const p = (job && job.progress) || {};
  const stopping = job.state === 'cancelling';
  return html`<${Card} level=${2} title=${t('build.run.title')} class="cx-build-run"
    actions=${html`<${Button} variant="secondary" loading=${stopping} disabled=${stopping}
      onClick=${onCancel}>${t('job.cancel')}<//>`}>
    <div class="cx-build-run__head" aria-live="polite">
      <${ProgressBar} value=${job.state === 'queued' ? null : p.fraction} resetKey=${job.id}
        label=${t('build.run.progress')} showValue />
      <p class="cx-build-run__meta">
        ${stopping ? t('build.run.stopping') : p.phases ? html`${t('tracker.phase', { phase: p.phase, phases: p.phases })}
          ${' · '}${formatPercent(p.fraction || 0)}
          ${p.eta_s ? html`${' · '}${t('tracker.eta', { eta: formatDuration(p.eta_s) })}` : null}`
          : t('build.run.starting')}
      </p>
      <p class="cx-build-note">${t('build.run.cancel_note')}</p>
    </div>
    <${StageTracker} stages=${rows} label=${t('build.run.stages')} />
  <//>`;
}

/** How the build ended: one sentence, the failure's card, what was not run. */
export function Result({ job, rows, onOverview, onAgain }) {
  const failed = job.state === 'failed' || job.state === 'interrupted';
  const refused = Object.keys((job.result && job.result.refused) || {});
  return html`<${Card} level=${2} title=${t(`build.end.${job.state}`)} class="cx-build-result">
    <p class="cx-build-result__sentence" role="status" data-outcome=${job.state}>${resultSentence(job)}</p>
    ${refused.length ? html`<p class="cx-build-note">${t('build.result.not_run', {
      n: refused.length, stages: namesOf(refused) })}</p>` : null}
    ${failed ? html`<${ErrorCard} error=${failureError(job)} level=${3} />` : null}
    <${Notes} notes=${job.result && job.result.notes} />
    <${StageTracker} stages=${rows} label=${t('build.run.stages')} compact />
    <div class="cx-build-actions">
      <${Button} variant="primary" onClick=${onOverview}>${t('build.to_overview')}<//>
      <${Button} variant="secondary" onClick=${onAgain}>${t('build.again')}<//>
    </div>
  <//>`;
}
