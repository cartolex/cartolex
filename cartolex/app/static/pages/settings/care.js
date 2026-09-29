/**
 * Backup and restore, diagnostics, reset. A backup holds what people decided
 * (project.json and decisions/, with their history); a restore puts its
 * decision files back, keeping each current one in its history; a reset
 * removes the built results only. The diagnostic holds no project data: it
 * can be pasted into a report as it is.
 */

import { html, useRef, useState } from '../../core/preact.js';
import { formatDate, t } from '../../core/i18n.js';
import { Button, ConfirmDialog } from '../../components/index.js';
import { Block, State, refusal } from './common.js';

export function CareSection({ ctx, app, open }) {
  const file = useRef(null);
  const [restoring, setRestoring] = useState(false);
  const [restored, setRestored] = useState(null);
  const [problem, setProblem] = useState(null);
  const [diagnostic, setDiagnostic] = useState(null);
  const [reset, setReset] = useState(false);
  const toast = (item) => app.toaster.show(item);

  const restore = async (chosen) => {
    if (!chosen) return;
    setRestoring(true);
    setProblem(null);
    const body = new FormData();
    body.append('file', chosen);
    const result = await ctx.api.post('/api/settings/restore', body);
    setRestoring(false);
    if (file.current) file.current.value = '';
    if (result.ok) {
      setRestored(result.data);
      app.stores.project.refresh();
    } else setProblem(refusal(result.error));
  };
  const readDiagnostic = async () => {
    const result = await ctx.api.get('/api/diagnostic');
    setDiagnostic(result.ok ? JSON.stringify(result.data, null, 2) : refusal(result.error));
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(diagnostic);
      toast({ kind: 'success', title: t('settings.care.copied') });
    } catch {
      toast({ kind: 'warning', title: t('settings.care.copy_failed') });
    }
  };
  const doReset = async () => {
    const result = await ctx.api.post('/api/settings/reset', { what: 'built' });
    if (result.ok) {
      toast({ kind: 'success', title: t('settings.care.reset_done') });
      app.stores.project.refresh();
    } else toast({ kind: 'error', title: t('settings.care.reset_failed'), message: refusal(result.error) });
  };

  return html`<div class="cx-settings__grid">
    ${open ? html`<${Block} title=${t('settings.care.backup')}>
      <p class="cx-settings__note">${t('settings.care.backup_text')}</p>
      <div class="cx-settings__actions">
        <a class="cx-button cx-button--primary" href="/api/settings/backup" download>${t('settings.care.backup_action')}</a>
      </div>
      <p class="cx-settings__note">${t('settings.care.restore_text')}</p>
      <div class="cx-settings__actions">
        <input ref=${file} id="cx-settings-restore" type="file" accept=".zip,application/zip" class="cx-visually-hidden"
          onChange=${(e) => restore(e.currentTarget.files && e.currentTarget.files[0])} />
        <${Button} loading=${restoring} onClick=${() => file.current && file.current.click()}>${t('settings.care.restore_action')}<//>
      </div>
      ${restored ? html`<p class="cx-settings__note" role="status"><${State} kind="ok">${t('settings.care.restored', {
        count: restored.count, when: restored.made_at ? formatDate(restored.made_at, 'datetime') : '' })}<//></p>` : null}
      ${problem ? html`<p class="cx-settings__problem" role="alert"><${State} kind="warning">${problem}<//></p>` : null}
    <//>` : null}
    <${Block} title=${t('settings.care.diagnostic')}>
      <p class="cx-settings__note">${t('settings.care.diagnostic_text')}</p>
      <div class="cx-settings__actions">
        <${Button} onClick=${readDiagnostic}>${t('settings.care.diagnostic_show')}<//>
        ${diagnostic ? html`<${Button} variant="ghost" icon="copy" onClick=${copy}>${t('settings.care.copy')}<//>` : null}
      </div>
      ${diagnostic ? html`<pre class="cx-settings__pre" tabindex="0" aria-label=${t('settings.care.diagnostic')}><code>${diagnostic}</code></pre>` : null}
    <//>
    ${open ? html`<${Block} title=${t('settings.care.reset')}>
      <p class="cx-settings__note">${t('settings.care.reset_text')}</p>
      <div class="cx-settings__actions">
        <${Button} variant="danger" onClick=${() => setReset(true)}>${t('settings.care.reset_action')}<//>
      </div>
    <//>` : null}
    <${ConfirmDialog} open=${reset} danger title=${t('settings.care.reset_title')} confirmLabel=${t('settings.care.reset_action')}
      cancelLabel=${t('common.cancel')} onAnswer=${(yes) => {
        setReset(false);
        if (yes) doReset();
      }}><p>${t('settings.care.reset_confirm')}</p><//>
  </div>`;
}
