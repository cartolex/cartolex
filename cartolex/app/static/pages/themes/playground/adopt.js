// SPDX-License-Identifier: MIT
/**
 * « Adopt as my draft »: the playground's settings written to `params.json` (as the Tune
 * panel's Save writes them, `If-Match`), the grouping built with them (the preview is what
 * that build proposes), then its proposal carried onto the editor's tree
 * (`POST /api/themes/carry`: the curator's names, set-asides, attributions and reviews kept
 * wherever a node continues) and put in place as one step of the undo list — the draft,
 * saved by the editor's Save as any other edit. A build that would also run earlier stages
 * goes to the pre-flight sheet instead (`/build?scope=themes.group`).
 */

import { ACTIVE } from '../../../core/stores/jobs.js';
import { SETTINGS } from './controls.js';

/** The `PUT /api/params` body: the file's values, with the playground's laid over them. */
export function paramsBody(data, values) {
  const stages = {};
  for (const stage of data.stages) {
    for (const p of stage.params) {
      if (p.name === 'seed' || p.name === 'year') continue;
      const key = `${stage.id}.${p.name}`;
      const ours = (SETTINGS[stage.id] || []).includes(p.name) && key in values;
      let keep = p.set_in_file;
      let value = p.value;
      if (ours) {
        value = values[key];
        // a value the parameter has without params.json is left to its default or rule
        keep = value !== null && JSON.stringify(value) !== JSON.stringify(p.default_value);
      }
      if (keep) stages[stage.id] = { ...(stages[stage.id] || {}), [p.name]: value };
    }
  }
  return { seed: data.global.seed.value, pinned_year: data.global.pinned_year.value, stages };
}

/** Wait for job *id* to end (the one poller); resolves to the job. */
export function jobEnd(jobs, id) {
  return new Promise((resolve) => {
    const stopWatch = jobs.watch();
    let stop = null;
    stop = jobs.jobs.subscribe((list) => {
      const job = list.find((j) => j.id === id);
      if (!job || ACTIVE.has(job.state)) return;
      if (stop) stop();
      stopWatch();
      resolve(job);
    });
    jobs.refresh();
  });
}

/**
 * Adopt the playground's *values*: `{ok, carried}`, `{ok: false, error}`, or
 * `{ok: false, navigated: true}` when the build needs the pre-flight sheet.
 * *params* is the resource of `GET /api/params` (`data`, `etag`, `set`); *onStep(step)*
 * says where it is (`saving`, `building`, `carrying`).
 */
export async function adopt({ ctx, editor, params, values, onStep, onSaved }) {
  const api = ctx.api;
  onStep('saving');
  const saved = await api.put('/api/params', paramsBody(params.data, values), { ifMatch: params.etag });
  if (!saved.ok) return { ok: false, error: saved.error, stale: saved.kind === 'stale' };
  params.set(saved.data, saved.etag);
  onSaved(saved.data, saved.etag);
  ctx.app.stores.project.refresh();
  const scope = ['themes.group'];
  const dry = await api.post('/api/build', { scope, dry_run: true });
  if (!dry.ok) return { ok: false, error: dry.error };
  const toRun = dry.data.to_run || [];
  if (toRun.some((s) => s !== 'themes.space' && s !== 'themes.group')) {
    ctx.navigate('/build?scope=themes.group');
    return { ok: false, navigated: true };
  }
  if (toRun.length) {
    onStep('building');
    const started = await api.post('/api/build', { scope, dry_run: false });
    if (!started.ok) return { ok: false, error: started.error };
    const job = await jobEnd(ctx.app.stores.jobs, started.data.job.id);
    if (job.state !== 'succeeded') return { ok: false, error: job.error || { code: 'job_failed' } };
  }
  onStep('carrying');
  const draft = await api.get('/api/themes/draft');
  if (!draft.ok) return { ok: false, error: draft.error };
  const carried = await api.post('/api/themes/carry', { tree: editor.tree.value, proposal: draft.data.tree });
  if (!carried.ok) return { ok: false, error: carried.error };
  const run = String(draft.data.run || '').split('/').pop();
  await editor.replace(carried.data.tree, { label: `adopt the grouping proposal ${run}`,
    labelKey: { key: 'playground.adopt.entry' } });
  await editor.refreshInfo();
  return { ok: true, carried: carried.data.carried, against: carried.data.against };
}
