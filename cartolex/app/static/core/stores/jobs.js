// SPDX-License-Identifier: MIT
/**
 * Jobs (builds, collections…) and the one poller of the whole app.
 *
 * The poller reads `GET /api/jobs` every second while a job is queued or
 * running, or while someone watches (the Activity drawer), and every twenty
 * seconds otherwise (to see jobs started elsewhere: the command line, another
 * tab). It pauses while the tab is hidden. When a job ends, `onFinished` is
 * called once (the shell refreshes the project state and shows a toast).
 *
 * A failed job stays in the list until the person dismisses it; dismissed ids
 * are kept with the preferences.
 */
import { computed, signal } from '../preact.js';

export const ACTIVE = new Set(['queued', 'running', 'cancelling']);
export const FAST_MS = 1000;
export const IDLE_MS = 20000;

/**
 * @param {object} options
 * @param {object} options.api the ApiClient
 * @param {{value: string[]}} options.dismissed a signal holding dismissed job ids
 * @param {(job: object) => void} [options.onFinished]
 * @param {{fast?: number, idle?: number}} [options.timing]
 * @param {boolean} [options.enabled] false when no project is open: nothing is polled
 */
export function createJobsStore({ api, dismissed, onFinished, timing = {}, enabled = true }) {
  const jobs = signal([]);
  const error = signal(null);
  const watchers = signal(0);
  const fast = timing.fast || FAST_MS;
  const idle = timing.idle || IDLE_MS;
  let timer = 0;
  let running = false;
  let inFlight = null;
  const seen = new Map();

  const active = computed(() => jobs.value.filter((j) => ACTIVE.has(j.state)));
  /** Jobs to show: active ones, and finished ones not dismissed (newest first). */
  const visible = computed(() => {
    const hidden = new Set(dismissed.value);
    return jobs.value.filter((j) => ACTIVE.has(j.state) || !hidden.has(j.id));
  });
  /** The job the header shows: the first active one, else the latest failure not dismissed. */
  const headline = computed(() => active.value[0]
    || visible.value.find((j) => j.state === 'failed' || j.state === 'interrupted') || null);

  const apply = (list) => {
    for (const job of list) {
      const before = seen.get(job.id);
      if (before && ACTIVE.has(before) && !ACTIVE.has(job.state) && onFinished) onFinished(job);
      seen.set(job.id, job.state);
    }
    jobs.value = list;
  };

  const poll = () => {
    // No project open: no job to follow (every job belongs to a project).
    if (!enabled) return Promise.resolve({ ok: false, kind: 'no-project' });
    if (inFlight) return inFlight;
    inFlight = api.get('/api/jobs').then((result) => {
      inFlight = null;
      if (result.ok) {
        apply((result.data && result.data.jobs) || []);
        error.value = null;
      } else if (result.kind !== 'aborted') {
        error.value = result.error;
      }
      return result;
    });
    return inFlight;
  };

  const schedule = () => {
    clearTimeout(timer);
    if (!running) return;
    const hidden = typeof document !== 'undefined' && document.visibilityState === 'hidden';
    if (hidden) return;
    const delay = active.value.length || watchers.value ? fast : idle;
    timer = setTimeout(() => poll().then(schedule), delay);
  };

  const onVisibility = () => {
    if (document.visibilityState === 'visible') poll().then(schedule);
    else clearTimeout(timer);
  };

  return {
    jobs,
    active,
    visible,
    headline,
    error,
    /** Start polling (once per app). */
    start() {
      if (running) return;
      running = true;
      document.addEventListener('visibilitychange', onVisibility);
      poll().then(schedule);
    },
    stop() {
      running = false;
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    },
    /** Poll now (after starting a job), then at the pace the jobs need. */
    refresh() {
      return poll().then((result) => {
        schedule();
        return result;
      });
    },
    /** Watch closely (1 s polling) until the returned function is called. */
    watch() {
      watchers.value += 1;
      schedule();
      return () => {
        watchers.value = Math.max(0, watchers.value - 1);
      };
    },
    /** Ask the server to cancel a job. */
    async cancel(id) {
      const result = await api.post(`/api/jobs/${encodeURIComponent(id)}/cancel`, {});
      await this.refresh();
      return result;
    },
    /** Hide a finished job from the list. */
    dismiss(id) {
      dismissed.value = [...dismissed.value.filter((d) => d !== id), id].slice(-100);
    },
  };
}
