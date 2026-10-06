// SPDX-License-Identifier: MIT
/**
 * The copilot's acceptance gate on the keywords screen: once a copilot's triage
 * was accepted for the current extraction, only the keywords with an accepting
 * decision enter the vocabulary at the next build. The candidates nobody judged
 * stay out; this note counts them and offers to send them to the AI (a bundle of
 * those only) or to keep them anyway (a keep decision on each, after a question).
 */
import { html, useState } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { Button, ConfirmDialog, ErrorCard, Icon } from '../../components/index.js';

/**
 * @param {object} props
 * @param {object} props.ctx the page's context
 * @param {{mode: string, unjudged: number}} props.gate the list's `gate`
 * @param {string} props.version the keywords.csv version read with the list
 * @param {Function} props.onSend opens the copilot with the unjudged candidates
 * @param {(message: string) => void} props.onDone after the keep
 */
export function UnjudgedNote({ ctx, gate, version, onSend, onDone }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const n = gate.unjudged;
  const keep = async (yes) => {
    setAsking(false);
    if (!yes) return;
    setBusy(true);
    setError(null);
    const r = await ctx.api.post('/api/keywords/decisions/where', {
      where: { unjudged: true }, decision: 'keep', reason: t('keywords.unjudged.reason'),
    }, { ifMatch: version });
    setBusy(false);
    if (r.ok) onDone(t('keywords.done.keep', { n: r.data.decided }));
    else setError(r.error);
  };
  return html`<div class="cx-kw-warning cx-kw-gate" role="note">
    <${Icon} name="info" />
    <p><strong>${t('keywords.unjudged.title', { n })}</strong>${' '}
      ${t('keywords.unjudged.text')}</p>
    <${Button} size="s" variant="primary" onClick=${onSend}>${t('keywords.unjudged.send')}<//>
    <${Button} size="s" loading=${busy} onClick=${() => setAsking(true)}>${t('keywords.unjudged.keep')}<//>
    ${error ? html`<${ErrorCard} error=${error} compact onDismiss=${() => setError(null)} />` : null}
    <${ConfirmDialog} open=${asking} title=${t('keywords.unjudged.confirm_title', { n })}
      confirmLabel=${t('keywords.unjudged.keep')} onAnswer=${keep}>
      <p>${t('keywords.unjudged.confirm_text')}</p>
    <//>
  </div>`;
}
