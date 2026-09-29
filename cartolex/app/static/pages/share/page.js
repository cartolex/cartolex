// SPDX-License-Identifier: MIT
/**
 * The share screen: build the offline site, the builds made so far, figures,
 * tables and files to take away.
 *
 * Opening it reads `GET /api/share` (the builds and the exports) and
 * `GET /api/share/plan` (the privacy summary and the checks of a build with
 * the form's options, read again when they change). A build or an export is
 * a job, followed through the jobs poller; when it ends the lists are read
 * again.
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { usePage, usePageTitle } from '../../core/page.js';
import { ErrorCard } from '../../components/index.js';
import { SiteCard } from './site.js';
import { BuildsCard } from './builds.js';
import { ExportsCard } from './exports.js';

const ACTIVE = new Set(['queued', 'running', 'cancelling']);

/** The job of *id* from the poller (null once it is gone). */
function useJob(jobs, id) {
  return id ? jobs.jobs.value.find((j) => j.id === id) || null : null;
}

export function ShareScreen() {
  const ctx = usePage();
  const { jobs } = ctx.app.stores;
  usePageTitle(t('nav.share'));
  const [share, setShare] = useState(null);
  const [error, setError] = useState(null);
  const [watching, setWatching] = useState(null);

  const load = async () => {
    const r = await ctx.api.get('/api/share');
    if (r.ok) {
      setShare(r.data);
      setError(null);
    } else setError(r.error);
  };
  useEffect(() => {
    load();
    return jobs.watch();
  }, []);

  const job = useJob(jobs, watching);
  const state = job ? job.state : null;
  useEffect(() => {
    if (watching && state && !ACTIVE.has(state)) {
      load();
      ctx.app.stores.project.refresh();
    }
  }, [watching, state]);

  const started = (r) => {
    if (r.ok) {
      setWatching(r.data.job.id);
      jobs.refresh();
    }
    return r;
  };
  const running = Boolean(job && ACTIVE.has(job.state));

  return html`<div class="cx-page cx-share">
    <h1 class="cx-page__title">${t('nav.share')}</h1>
    <p class="cx-page__lead">${t('share.lead')}</p>
    ${error ? html`<${ErrorCard} error=${error} onRetry=${load} />` : null}
    <div class="cx-share__grid">
      <${SiteCard} ctx=${ctx} available=${!share || share.available} job=${job && job.kind === 'site' ? job : null}
        running=${running} onStarted=${started} />
      <div class="cx-share__side">
        <${BuildsCard} share=${share} />
        <${ExportsCard} ctx=${ctx} share=${share} job=${job && job.kind === 'export' ? job : null}
          running=${running} onStarted=${started} />
      </div>
    </div>
  </div>`;
}
