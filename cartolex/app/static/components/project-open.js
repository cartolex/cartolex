// SPDX-License-Identifier: MIT
/**
 * Opening a project (locally), shared by the start screen and the header's
 * project menu: `POST /api/projects/open`, then the interface starts again on
 * the project's first page (every screen starts from the new manifest). A
 * project another cartolex holds can be opened anyway after a warning
 * (`force`); while a job runs on the project open now, nothing else opens: the
 * person is told so and sent to the Activity drawer.
 */
import { html, useState } from '../core/preact.js';
import { formatDate, has, t } from '../core/i18n.js';
import { runtime } from '../core/runtime.js';
import { Button } from './button.js';
import { ConfirmDialog } from './dialog.js';
import { Icon } from './icons.js';

/** The errors of a project held by another cartolex: it may be opened anyway. */
export const HELD = new Set(['locked', 'locked_here']);

/** Go to *path* with the interface started again (the open project changed). */
export function restartAt(path) {
  window.location.assign(path || '/');
}

/** The words of a refused opening. */
export function openRefusal(error) {
  if (!error) return t('projects.open_failed');
  if (error.code === 'busy') return t('projects.busy');
  const key = `error.${error.code}.message`;
  if (error.code && has(key)) return t(key, error.params || {});
  return error.message || t('projects.open_failed');
}

/** The warning before overriding the lock of *error* (`locked` or `locked_here`). */
export function OverrideWarning({ error }) {
  const params = error.params || {};
  const since = params.since ? formatDate(params.since, 'datetime') : '?';
  return html`
    <p>${error.code === 'locked_here'
      ? t('start.override.here', { since, pid: params.pid })
      : t('start.override.elsewhere', { since, host: params.host })}</p>
    <p>${t('start.override.after')}</p>`;
}

/**
 * Open projects through *api*: `open(folder)` restarts the interface on success, else
 * keeps the refusal in `problem` (`{folder, error, held, busy}`); `override()` asks
 * before opening a held project anyway.
 */
export function useProjectOpener(api) {
  const [problem, setProblem] = useState(null);
  const [asking, setAsking] = useState(false);
  const [opening, setOpening] = useState(null);
  const open = async (folder, force = false) => {
    setProblem(null);
    setOpening(folder);
    const result = await api.post('/api/projects/open', { path: folder, force });
    setOpening(null);
    if (result.ok) {
      restartAt('/');
      return true;
    }
    const error = result.error || null;
    setProblem({
      folder,
      error,
      held: Boolean(!force && error && HELD.has(error.code)),
      busy: Boolean(error && error.code === 'busy'),
    });
    return false;
  };
  return {
    open,
    problem,
    opening,
    asking,
    clear: () => setProblem(null),
    override: () => setAsking(true),
    answer: (yes) => {
      setAsking(false);
      if (yes && problem && problem.held) open(problem.folder, true);
    },
  };
}

/** The warning before opening a held project anyway; yes opens it. */
export function OverrideDialog({ opener }) {
  const { problem } = opener;
  return html`<${ConfirmDialog} open=${opener.asking && Boolean(problem && problem.held)} danger
    title=${t('start.override.title')} confirmLabel=${t('start.override.yes')}
    cancelLabel=${t('common.cancel')} onAnswer=${opener.answer}>
    ${problem && problem.held ? html`<${OverrideWarning} error=${problem.error} />` : null}
  <//>`;
}

/** Show the Activity drawer (the job that keeps another project from opening). */
export function seeActivity() {
  if (runtime.openActivity) runtime.openActivity();
}

/**
 * What went wrong when opening, and what can be done: open anyway (a held project,
 * after the warning), or see the job that runs (busy).
 * @param {{opener: object}} props `opener` from useProjectOpener
 */
export function OpenProblem({ opener }) {
  const { problem } = opener;
  return html`
    ${problem ? html`<div class="cx-open-problem" role="alert">
      <p class="cx-open-problem__text"><${Icon} name="warning" /><span>${openRefusal(problem.error)}</span></p>
      ${problem.held || problem.busy ? html`<div class="cx-open-problem__actions">
        ${problem.held ? html`<${Button} variant="secondary" onClick=${opener.override}>
          ${t('start.override')}<//>` : null}
        ${problem.busy ? html`<${Button} variant="secondary" icon="activity" onClick=${seeActivity}>
          ${t('projects.see_activity')}<//>` : null}
      </div>` : null}
    </div>` : null}
    <${OverrideDialog} opener=${opener} />`;
}
