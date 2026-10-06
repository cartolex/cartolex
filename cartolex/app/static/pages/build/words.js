// SPDX-License-Identifier: MIT
/**
 * The build's words, shared by the pre-flight sheet, the tracker and the
 * result: sizes, stage names, the one-sentence result and the error card of a
 * failed stage.
 */
import { formatDuration, formatList, formatNumber, t } from '../../core/i18n.js';
import { errorFromResponse } from '../../core/errors.js';
import { messageOf, stageName } from '../../components/index.js';

/** A memory size in the interface language (« 820 MB », « 3.4 GB »). */
export function formatMb(mb) {
  if (mb === null || mb === undefined) return t('build.unknown');
  return mb >= 1024
    ? t('build.gb', { n: formatNumber(mb / 1024, { maximumFractionDigits: 1 }) })
    : t('build.mb', { n: formatNumber(Math.round(mb)) });
}

/** A duration estimate (« about 2 min »), or « unknown ». */
export function formatSeconds(s) {
  return s === null || s === undefined ? t('build.unknown') : formatDuration(Math.max(1, s));
}

/** A stage's name from its id (the catalogue's, else the id). */
export function nameOf(id) {
  return stageName({ id, name: id });
}

/** Several stages' names as a list. */
export function namesOf(ids) {
  return formatList((ids || []).map(nameOf));
}

/** The message of a code from the catalogue (`message.<code>`), else the server's English. */
export function messageText(item) {
  return messageOf(item);
}

function seconds(job) {
  if (!job || !job.started_at || !job.finished_at) return null;
  const s = (Date.parse(job.finished_at) - Date.parse(job.started_at)) / 1000;
  return Number.isFinite(s) && s >= 0 ? s : null;
}

/**
 * How a build ended, in one sentence: what changed, or « nothing changed »,
 * or « finished before the cancel ».
 */
export function resultSentence(job) {
  const r = (job && job.result) || {};
  const ran = r.ran || [];
  const outcome = r.outcome || (job && job.state);
  if (outcome === 'waiting' || (job && job.state === 'waiting')) {
    return t('build.result.waiting', { n: ran.length });
  }
  if (outcome === 'cancelled') {
    return ran.length ? t('build.result.cancelled_after', { n: ran.length, stages: namesOf(ran) })
      : t('build.result.cancelled_nothing');
  }
  if (outcome === 'failed' || (job && job.state === 'interrupted')) {
    const stage = r.failed ? nameOf(r.failed.stage) : '';
    if (job.state === 'interrupted') return t('build.result.interrupted', { n: ran.length });
    return t('build.result.failed', { stage, n: ran.length, stages: namesOf(ran) });
  }
  if (!ran.length) return t('build.result.up_to_date');
  const time = seconds(job);
  return time === null ? t('build.result.built', { n: ran.length, stages: namesOf(ran) })
    : t('build.result.built_in', { n: ran.length, stages: namesOf(ran), time: formatDuration(time) });
}

/** The error card of a failed build: its cause (folded) and the next action. */
export function failureError(job) {
  const failed = job && job.result && job.result.failed;
  if (!failed) {
    return errorFromResponse({ error: { code: `job_${job ? job.state : 'failed'}` } });
  }
  const error = errorFromResponse({ error: { code: `build_${failed.code}`, message: failed.message,
    next: failed.next || { label: '', action: 'report' } } });
  error.technical = `${failed.stage}: ${failed.error || ''}`;
  return error;
}
