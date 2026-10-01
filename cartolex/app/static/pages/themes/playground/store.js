// SPDX-License-Identifier: MIT
/**
 * The playground's preview: the grouping regrouped with the settings of the controls
 * (`POST /api/themes/playground`), in the background. A moment after the last change the
 * settings are asked for: a job of the `preview` group the first time, then the cached
 * answer. A change while a preview is computed supersedes it: the request names the running
 * job (`supersede`), the server cancels it, and the newest settings are asked for once it has
 * ended. Nothing is saved.
 *
 * `phase`: `idle`, `waiting` (a change, not asked yet), `computing`, `ready`, `failed`;
 * `preview` (the last answer shown: `tree`, `settings`, `theta`, `space_refit`, `seconds`),
 * `against` (how it differs from the editor's tree), `progress` (the job's) and `problem`.
 */

import { signal } from '../../../core/preact.js';

/** How long the settings must stay the same before their preview is asked for. */
export const WAIT_MS = 400;
const ACTIVE = new Set(['queued', 'running', 'cancelling']);

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/** The preview store; *treeOf()* gives the editor's tree to compare with. */
export function createPlayground({ api, jobs, treeOf }) {
  const phase = signal('idle');
  const preview = signal(null);
  const against = signal(null);
  const progress = signal(null);
  const problem = signal(null);
  const refitting = signal(false);
  let wanted = null; // the settings asked for last
  let job = null; // {id, settings, cancelled}
  let timer = 0;
  let asking = false; // a request is on its way: the next one waits for its answer
  let stopJobs = null;
  let unwatch = null;
  let closed = false;

  const stopFollowing = () => {
    if (stopJobs) stopJobs();
    if (unwatch) unwatch();
    stopJobs = null;
    unwatch = null;
  };
  const fail = (error) => {
    phase.value = 'failed';
    problem.value = error;
    progress.value = null;
  };
  const follow = () => {
    if (stopJobs) return;
    unwatch = jobs.watch();
    stopJobs = jobs.jobs.subscribe((list) => {
      if (!job) return;
      const live = list.find((j) => j.id === job.id);
      if (!live) return;
      if (ACTIVE.has(live.state)) {
        if (!job.cancelled) progress.value = live.progress || null;
        return;
      }
      const ended = job;
      job = null;
      stopFollowing();
      progress.value = null;
      if (!wanted) return;
      // done, or superseded: ask for the newest settings (a computed preview answers at once)
      if (live.state === 'succeeded' || !same(ended.settings, wanted)) send();
      else fail({ code: 'preview_failed' });
    });
  };

  async function send() {
    if (closed || !wanted || asking) return;
    // computing these settings already, or waiting for a superseded job to end
    if (job && (same(job.settings, wanted) || job.cancelled)) return;
    const settings = wanted;
    const supersede = job ? job.id : null;
    if (job) job.cancelled = true;
    phase.value = 'computing';
    problem.value = null;
    asking = true;
    const r = await api.post('/api/themes/playground', { settings, tree: treeOf(), supersede });
    asking = false;
    if (closed) return;
    if (r.ok && r.data.superseded) return; // the old job is being cancelled: asked again at its end
    if (supersede && job && job.id === supersede) {
      // the old job had ended meanwhile
      job = null;
      stopFollowing();
    }
    if (r.ok && r.data.job) {
      job = { id: r.data.job.id, settings, cancelled: false };
      refitting.value = Boolean(r.data.job.title_params && r.data.job.title_params.refit);
      follow();
      jobs.refresh();
      if (!same(settings, wanted)) send(); // changed while asking: supersede at once
      return;
    }
    if (!same(settings, wanted)) {
      send();
      return;
    }
    if (r.ok && r.data.preview) {
      preview.value = r.data.preview;
      against.value = r.data.against || null;
      phase.value = 'ready';
    } else if (!r.ok && r.status === 409 && r.error && r.error.code === 'busy') {
      // another preview (another tab) is being computed: ask again in a moment
      timer = setTimeout(send, 1000);
    } else if (r.kind !== 'aborted') {
      fail(r.error);
    }
  }

  return {
    phase, preview, against, progress, problem, refitting,
    /** The settings asked for last (null: none). */
    wanted: () => wanted,
    /** Preview *settings* a moment after the last change (at once with *now*). */
    ask(settings, { now = false } = {}) {
      clearTimeout(timer);
      if (wanted && same(settings, wanted) && phase.value !== 'failed') return;
      wanted = settings;
      if (phase.value !== 'computing') phase.value = 'waiting';
      timer = setTimeout(send, now ? 0 : WAIT_MS);
    },
    dispose() {
      closed = true;
      clearTimeout(timer);
      stopFollowing();
    },
  };
}
