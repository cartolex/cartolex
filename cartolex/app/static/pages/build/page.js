// SPDX-License-Identifier: MIT
/**
 * The build page (`/build`, `?scope=map,themes`, and `&force=themes.space` to run
 * stages again even when they are up to date, « rebuild from here »): the
 * pre-flight sheet, then the tracker of the job it starts, then its result.
 *
 * On arrival it reads the last build (`GET /api/build`): a running one is
 * followed at once; otherwise the dry run (`POST /api/build`) gives the sheet.
 * While a job runs, its progress comes from the jobs poller (one second while
 * the page watches) and the tracker's stages are read again when the stage
 * changes and when the job ends.
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatList, has, t } from '../../core/i18n.js';
import { usePage, usePageTitle } from '../../core/page.js';
import { ErrorCard } from '../../components/index.js';
import { Preflight } from './preflight.js';
import { Result, Running, isActive, trackerRows } from './run.js';
import { resultSentence } from './words.js';

function scopeOf(query, name = 'scope') {
  const raw = query && query.get(name);
  const list = raw ? raw.split(',').map((s) => s.trim()).filter((s) => /^[a-z][a-z._-]{0,63}$/.test(s)) : [];
  return list.length ? list : null;
}

function areaWord(scope) {
  const key = `area.${scope.split('.')[0]}.in_sentence`;
  return has(key) ? t(key) : scope;
}

export function BuildPage() {
  const ctx = usePage();
  const { jobs, project } = ctx.app.stores;
  usePageTitle(t('build.title'));
  const scope = scopeOf(ctx.query);
  const force = scopeOf(ctx.query, 'force') || [];
  const [plan, setPlan] = useState(null);
  const [tracker, setTracker] = useState(null);
  const [error, setError] = useState(null);
  const [watching, setWatching] = useState(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState(null);

  const loadPlan = async () => {
    setPlan(null);
    const r = await ctx.api.post('/api/build', { scope, dry_run: true, options: { force } });
    if (r.ok) setPlan(r.data);
    else setError(r.error);
  };
  const loadTracker = async () => {
    const r = await ctx.api.get('/api/build');
    if (r.ok) setTracker(r.data);
    else setError(r.error);
    return r;
  };

  useEffect(() => {
    const stop = jobs.watch();
    loadTracker().then((r) => {
      if (r.ok && isActive(r.data.job)) setWatching(r.data.job.id);
      else if (r.ok) loadPlan();
    });
    return stop;
  }, []);

  const live = (watching && jobs.jobs.value.find((j) => j.id === watching))
    || (tracker && tracker.job && tracker.job.id === watching ? tracker.job : null);
  const liveStage = live && live.progress ? live.progress.stage : null;
  const liveState = live ? live.state : null;
  useEffect(() => {
    // The tracker's stages change when a stage ends and when the job ends.
    if (watching && liveState) loadTracker();
  }, [watching, liveStage, liveState]);

  const start = async ({ consent, allowOverBudget }) => {
    setStarting(true);
    setStartError(null);
    const r = await ctx.api.post('/api/build', { scope, dry_run: false, consent,
      options: { allow_over_budget: allowOverBudget, force } });
    setStarting(false);
    if (!r.ok) {
      setStartError(r.error);
      return;
    }
    const job = r.data.job;
    setTracker({ job, stages: (plan.to_run || []).map((stage) => ({ stage, state: 'waiting' })),
      kept: plan.to_keep, skipped: plan.to_skip, refused: [] });
    setWatching(job.id);
    jobs.refresh();
  };
  const again = () => {
    setWatching(null);
    setError(null);
    loadPlan();
  };

  const order = [...project.stages.value.keys()];
  const known = project.stages.value;
  let body;
  if (error) {
    body = html`<${ErrorCard} error=${error} onRetry=${again} />`;
  } else if (watching && live && tracker) {
    const rows = trackerRows(order.length ? order : (tracker.stages || []).map((s) => s.stage), tracker, live);
    body = isActive(live)
      ? html`<${Running} job=${live} rows=${rows} onCancel=${() => jobs.cancel(live.id)} />`
      : html`<${Result} job=${{ ...live, result: live.result || (tracker.job && tracker.job.result) }}
          rows=${rows} onOverview=${() => ctx.navigate('/overview')} onAgain=${again} />`;
  } else if (plan) {
    body = html`<${Preflight} plan=${plan} stages=${known} starting=${starting} error=${startError}
      onStart=${start} onClose=${() => ctx.navigate('/overview')} />`;
  } else {
    body = html`<p class="cx-build-note" aria-busy="true">${t('common.loading')}</p>`;
  }
  const last = !watching && tracker && tracker.job && !isActive(tracker.job) ? tracker.job : null;
  return html`<div class="cx-page cx-build">
    <h1 class="cx-page__title">${t('build.title')}</h1>
    <p class="cx-page__lead">${scope ? t('build.lead.scope', { areas: formatList(scope.map(areaWord)) })
      : t('build.lead.all')}</p>
    ${last ? html`<p class="cx-build-note" data-last-build>${t('build.last', { sentence: resultSentence(last) })}</p>` : null}
    ${body}
  </div>`;
}
