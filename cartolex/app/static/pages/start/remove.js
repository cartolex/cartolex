// SPDX-License-Identifier: MIT
/**
 * « Remove… » on a recent project of the start screen: remove it from the list (the
 * folder stays, `POST /api/projects/forget`) or delete the project's folder. Choosing
 * the deletion reads what it would remove (`GET /api/projects/removal`: the folder, its
 * size, what cartolex did not write and keeps, whether another application holds it or
 * a job runs); a last question names the folder and its size, Cancel focused, before
 * `POST /api/projects/delete`. Deleting the open project closes it first; the interface
 * then starts again on the start screen.
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatBytes, formatDate, formatList, formatNumber, t } from '../../core/i18n.js';
import { useUid } from '../../core/dom.js';
import { Button, ConfirmDialog, Dialog } from '../../components/index.js';
import { restartAt } from '../../components/project-open.js';
import { State, refusal } from '../settings/common.js';

/** What deleting the folder would remove, or why it cannot. */
function Plan({ plan, error }) {
  if (error) {
    return html`<p class="cx-settings__problem" role="alert"><${State} kind="warning">${refusal(error)}<//></p>`;
  }
  if (!plan) return html`<p class="cx-settings__muted" aria-busy="true">${t('start.remove.reading')}</p>`;
  const held = plan.held;
  return html`<div class="cx-start__plan">
    <p><code class="cx-start__path">${plan.path}</code></p>
    <p>${t('start.remove.size', { size: formatBytes(plan.bytes), files: formatNumber(plan.files) })}</p>
    ${plan.open ? html`<p class="cx-settings__note">${t('start.remove.open')}</p>` : null}
    ${plan.kept && plan.kept.length ? html`<p class="cx-settings__note">
      ${t('start.remove.kept', { n: plan.kept.length, names: formatList(plan.kept.slice(0, 5)) })}</p>` : null}
    ${held ? html`<p class="cx-settings__problem" role="alert"><${State} kind="warning">
      ${t('start.remove.held', { app: held.app, host: held.host,
        since: held.since && held.since !== '?' ? formatDate(held.since, 'datetime') : '?' })}<//></p>` : null}
  </div>`;
}

/**
 * The removal of *item* (a recent project: `{path, name, exists}`); `onDone()` after it
 * was taken out of the list (the list is read again).
 */
export function RemoveDialog({ ctx, item, onClose, onDone }) {
  const missing = item.exists === false;
  const [choice, setChoice] = useState('forget');
  const [plan, setPlan] = useState(null);
  const [planError, setPlanError] = useState(null);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const group = useUid('cx-start-remove');
  const name = item.name || item.path;

  useEffect(() => {
    if (choice !== 'delete' || plan || planError) return;
    ctx.api.get('/api/projects/removal', { query: { path: item.path } }).then((r) => {
      if (r.ok) setPlan(r.data);
      else setPlanError(r.error);
    });
  }, [choice]);

  const forget = async () => {
    setBusy(true);
    setError(null);
    const r = await ctx.api.post('/api/projects/forget', { path: item.path });
    setBusy(false);
    if (r.ok) onDone();
    else setError(r.error);
  };
  const remove = async (yes) => {
    setAsking(false);
    if (!yes) return;
    setBusy(true);
    setError(null);
    const r = await ctx.api.post('/api/projects/delete', { path: item.path, confirm: true });
    setBusy(false);
    if (!r.ok) {
      setError(r.error);
      return;
    }
    if (r.data.closed) restartAt('/start');
    else onDone(r.data);
  };
  const blocked = choice === 'delete' && (!plan || Boolean(plan.held) || Boolean(planError));
  const options = [
    { value: 'forget', label: t('start.remove.forget'), help: t('start.remove.forget.help') },
    { value: 'delete', label: t('start.remove.delete'),
      help: missing ? t('start.remove.delete.missing') : t('start.remove.delete.help'), disabled: missing },
  ];
  return html`
    <${Dialog} open size="m" title=${t('start.remove.title', { name })} onClose=${onClose}
      footer=${html`
        <${Button} variant="secondary" onClick=${onClose}>${t('common.cancel')}<//>
        ${choice === 'forget'
          ? html`<${Button} variant="primary" loading=${busy} onClick=${forget}>${t('start.remove.forget.action')}<//>`
          : html`<${Button} variant="danger" loading=${busy} disabled=${blocked}
            onClick=${() => setAsking(true)} aria-haspopup="dialog">${t('start.remove.delete.action')}<//>`}`}>
      <div class="cx-start__choices" role="radiogroup"
        aria-label=${t('start.remove.title', { name })}>
        ${options.map((o) => html`<label class="cx-start__choice" key=${o.value}>
          <input type="radio" name=${group} value=${o.value} checked=${choice === o.value}
            disabled=${o.disabled} onChange=${() => setChoice(o.value)} />
          <span><span class="cx-start__choice-label">${o.label}</span>
            <span class="cx-settings__muted">${o.help}</span></span>
        </label>`)}
      </div>
      ${choice === 'delete' ? html`<${Plan} plan=${plan} error=${planError} />` : null}
      ${error ? html`<p class="cx-settings__problem" role="alert"><${State} kind="warning">${refusal(error)}<//></p>` : null}
    <//>
    <${ConfirmDialog} open=${asking} danger title=${t('start.remove.confirm.title', { name })}
      confirmLabel=${t('start.remove.confirm.yes')} cancelLabel=${t('common.cancel')} onAnswer=${remove}>
      ${plan ? html`<p><code class="cx-start__path">${plan.path}</code></p>
        <p>${t('start.remove.confirm.text', { size: formatBytes(plan.bytes) })}</p>` : null}
    <//>`;
}
