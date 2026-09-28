// SPDX-License-Identifier: MIT
/**
 * ErrorCard: what went wrong in plain words, what to do about it, the
 * technical details folded away, and « Copy a diagnostic ».
 *
 * It shows the error model of core/errors.js. The words come from the
 * catalogue when it knows the error's code (`error.<code>.title`, `.message`,
 * `.next`); otherwise from the server's message and next action; otherwise
 * generic words. The diagnostic holds no project data.
 */
import { html, useState } from '../core/preact.js';
import { formatDate, has, locale, t } from '../core/i18n.js';
import { copyText, useUid } from '../core/dom.js';
import { diagnosticText } from '../core/errors.js';
import { APP_ACTIONS, actionKind, runAction, runtime } from '../core/runtime.js';
import { Button, IconButton } from './button.js';
import { Icon } from './icons.js';

function words(error, actionable) {
  const code = error.code || 'unexpected';
  const known = (part) => (has(`error.${code}.${part}`) ? t(`error.${code}.${part}`) : '');
  const statusGroup = error.status >= 500 ? 'server' : error.status >= 400 ? 'request' : 'unexpected';
  const next = error.next && error.next.action ? error.next : null;
  const kind = next ? actionKind(next.action) : 'none';
  // The server's words are English: in another language, the catalogue's words for
  // the action come first.
  const english = locale.value.split('-')[0] === 'en';
  const generic = has(`error.action.${kind}`) ? t(`error.action.${kind}`) : '';
  const label = next ? known('next') || (english && next.label) || generic || next.label : '';
  return {
    title: known('title') || t(`error.${statusGroup}.title`),
    message: known('message') || error.message || t(`error.${statusGroup}.message`),
    next: next && label && (kind === 'report' || actionable(kind)) ? { label, action: next.action, kind } : null,
  };
}

/**
 * @param {object} props
 * @param {object} props.error the error model (core/errors.js)
 * @param {(action: string) => void} [props.onAction] runs « What to do »; default: runAction
 *   (the app's actions); with it, the page's own actions (`confirm`, `fix-input`) get a button
 * @param {() => void} [props.onRetry] what 'retry' does (default: reload the page)
 * @param {() => void} [props.onReload] what 'reload' does: read again and merge (default: onRetry)
 * @param {() => void} [props.onDismiss] shows a Dismiss button
 * @param {boolean} [props.live] announce the error when it appears
 * @param {boolean} [props.compact]
 * @param {boolean} [props.detailsOpen] unfold the technical details (the gallery shows it)
 */
export function ErrorCard({ error, onAction, onRetry, onReload, onDismiss, live = false,
  compact = false, detailsOpen = false, level = 3, class: cls = '' }) {
  const id = useUid('cx-error');
  const [copied, setCopied] = useState(null);
  const [open, setOpen] = useState(detailsOpen);
  const w = words(error, (kind) => Boolean(onAction) || APP_ACTIONS.has(kind) || kind === 'open');
  const Heading = `h${level}`;
  const act = (action) => {
    if (actionKind(action) === 'report') {
      setOpen(true);
      copy();
    } else if (onAction) onAction(action);
    else runAction(action, { onRetry, onReload });
  };
  const copy = async () => {
    const ok = await copyText(diagnosticText(error, {
      app: runtime.app.name, version: runtime.app.version, page: runtime.page(),
      locale: locale.value, userAgent: navigator.userAgent,
    }));
    setCopied(ok);
  };
  return html`<div class=${`cx-error-card ${compact ? 'cx-error-card--compact' : ''} ${cls}`}
    role=${live ? 'alert' : 'group'} aria-labelledby=${id}>
    <div class="cx-error-card__head">
      <span class="cx-error-card__icon"><${Icon} name="error" size=${20} /></span>
      <${Heading} class="cx-error-card__title" id=${id}>
        <span class="cx-error-card__kind">${t('error.word')}</span> ${w.title}<//>
      ${onDismiss ? html`<${IconButton} icon="close" label=${t('common.dismiss')} size="s"
        onClick=${onDismiss} />` : null}
    </div>
    <p class="cx-error-card__message">${w.message}</p>
    ${w.next ? html`<div class="cx-error-card__next">
      <span class="cx-error-card__next-label">${t('error.what_to_do')}</span>
      <${Button} variant="primary" size=${compact ? 's' : 'm'} onClick=${() => act(w.next.action)}>
        ${w.next.label}<//>
    </div>` : null}
    <details class="cx-error-card__details" open=${open}
      onToggle=${(e) => setOpen(e.currentTarget.open)}>
      <summary>${t('error.technical_details')}</summary>
      <dl class="cx-error-card__facts">
        <dt>${t('error.fact.code')}</dt><dd><code>${error.code}</code></dd>
        ${error.status ? html`<dt>${t('error.fact.status')}</dt><dd>${error.status}</dd>` : null}
        ${error.path ? html`<dt>${t('error.fact.request')}</dt>
          <dd><code>${error.method} ${error.path}</code></dd>` : null}
        ${error.requestId ? html`<dt>${t('error.fact.request_id')}</dt>
          <dd><code>${error.requestId}</code></dd>` : null}
        <dt>${t('error.fact.time')}</dt><dd>${formatDate(error.time, 'datetime', 'medium')}</dd>
      </dl>
      ${error.technical ? html`<pre class="cx-error-card__trace">${error.technical}</pre>` : null}
      <div class="cx-error-card__copy">
        <${Button} size="s" icon="copy" onClick=${copy}>${t('error.copy_diagnostic')}<//>
        <span class="cx-error-card__copied" role="status">
          ${copied === true ? t('error.copied') : copied === false ? t('error.copy_failed') : ''}
        </span>
      </div>
    </details>
  </div>`;
}
