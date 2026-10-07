// SPDX-License-Identifier: MIT
/**
 * Deleting what sharing keeps, one question each (Cancel focused): a site build and its
 * zip (`DELETE /api/share/builds/<id>`), a build's zip only (`…/zip`), every build but
 * the latest with what unfinished builds left (`POST /api/share/builds/prune`), an
 * exported file and its description (`DELETE /api/share/exports/<name>`). Each question
 * names what goes and its size; a deletion cannot be undone. The lists are read again
 * after it, and the project state (the share area's dot).
 */
import { html, useState } from '../../core/preact.js';
import { formatBytes, formatDate, t } from '../../core/i18n.js';
import { ConfirmDialog, ErrorCard } from '../../components/index.js';

/** The question and the call of each deletion. */
function request(kind, target) {
  const size = formatBytes(target.bytes || 0);
  if (kind === 'build') {
    const when = target.built_at ? formatDate(target.built_at, 'datetime') : target.id;
    return { title: t('share.delete.build.title'), text: t('share.delete.build.text', { when, size }),
      call: (api) => api.delete(`/api/share/builds/${encodeURIComponent(target.id)}`) };
  }
  if (kind === 'zip') {
    return { title: t('share.delete.zip.title'), text: t('share.delete.zip.text', { size }),
      call: (api) => api.delete(`/api/share/builds/${encodeURIComponent(target.id)}/zip`) };
  }
  if (kind === 'older') {
    return { title: t('share.delete.older.title'), text: t('share.delete.older.text', { n: target.count, size }),
      call: (api) => api.post('/api/share/builds/prune', {}) };
  }
  return { title: t('share.delete.export.title'), text: t('share.delete.export.text', { name: target.name, size }),
    call: (api) => api.delete(`/api/share/exports/${encodeURIComponent(target.name)}`) };
}

/**
 * `ask(kind, target)` puts a deletion's question; `dialog` renders it. *onDone* runs after
 * a deletion (to read the lists again).
 */
export function useDeletion(ctx, onDone) {
  const [pending, setPending] = useState(null);
  const [error, setError] = useState(null);
  const ask = (kind, target) => {
    setError(null);
    setPending({ kind, target, ...request(kind, target) });
  };
  const answer = async (yes) => {
    const asked = pending;
    setPending(null);
    if (!yes || !asked) return;
    const r = await asked.call(ctx.api);
    if (!r.ok) {
      setError(r.error);
      return;
    }
    ctx.app.toaster.show({ kind: 'success', title: t('share.delete.done', { size: formatBytes(r.data.bytes || 0) }) });
    onDone();
  };
  const dialog = html`
    <${ConfirmDialog} open=${Boolean(pending)} danger title=${pending ? pending.title : ''}
      confirmLabel=${t('share.delete.yes')} cancelLabel=${t('common.cancel')} onAnswer=${answer}>
      ${pending ? html`<p>${pending.text}</p><p>${t('share.delete.final')}</p>` : null}
    <//>
    ${error ? html`<${ErrorCard} error=${error} compact />` : null}`;
  return { ask, dialog };
}
