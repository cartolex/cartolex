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
import { runAction, runtime } from '../core/runtime.js';
import { Button, IconButton } from './button.js';
import { Icon } from './icons.js';

function words(error) {
  const code = error.code || 'unexpected';
  const known = (part) => (has(`error.${code}.${part}`) ? t(`error.${code}.${part}`) : '');
  const statusGroup = error.status >= 500 ? 'server' : error.status >= 400 ? 'request' : 'unexpected';
  return {
    title: known('title') || t(`error.${statusGroup}.title`),
    message: known('message') || error.message || t(`error.${statusGroup}.message`),
    next: error.next && error.next.action
      ? { label: known('next') || error.next.label || t(`error.action.${actionKind(error.next.action)}`),
        action: error.next.action }
      : null,
  };
}

function actionKind(action) {
  if (action === 'retry' || action === 'reload') return action;
  return 'open';
}

/**
 * @param {object} props
 * @param {object} props.error the error model (core/errors.js)
 * @param {(action: string) => void} [props.onAction] runs « What to do »; default: runAction
 * @param {() => void} [props.onRetry] what 'retry' does (default: reload the page)
 * @param {() => void} [props.onDismiss] shows a Dismiss button
 * @param {boolean} [props.live] announce the error when it appears
 * @param {boolean} [props.compact]
 * @param {boolean} [props.detailsOpen] unfold the technical details (the gallery shows it)
 */
export function ErrorCard({ error, onAction, onRetry, onDismiss, live = false, compact = false,
  detailsOpen = false, level = 3, class: cls = '' }) {
  const id = useUid('cx-error');
  const [copied, setCopied] = useState(null);
  const w = words(error);
  const Heading = `h${level}`;
  const act = (action) => (onAction ? onAction(action) : runAction(action, { onRetry }));
  const copy = async () => {
    const ok = await copyText(diagnosticText(error, {
      app: runtime.app.name, version: runtime.app.version, page: runtime.page(),
      locale: locale.value, userAgent: navigator.userAgent,
    }));
    setCopied(ok);
  };
  return html`<section class=${`cx-error-card ${compact ? 'cx-error-card--compact' : ''} ${cls}`}
    role=${live ? 'alert' : undefined} aria-labelledby=${id}>
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
    <details class="cx-error-card__details" open=${detailsOpen}>
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
  </section>`;
}
