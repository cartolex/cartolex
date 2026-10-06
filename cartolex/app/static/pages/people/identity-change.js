// SPDX-License-Identifier: MIT
/**
 * « Change identity… » from a person's sheet: the identity choice opened again
 * for one person, whatever was decided (confirmed, accepted automatically, none).
 * The candidate records every finder proposed, the record pasted (an id or an
 * ORCID), or none of them; the same routes as the identity queue
 * (`GET /api/collection/identities?state=all&person=`, then
 * `POST /api/collection/identities/{person_id}` with the version it answered).
 * Keyboard: 1–9 pick a candidate, N none, ⏎ confirm (the focus starts on Confirm).
 */
import { html, useEffect, useRef, useState } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { Button, Dialog, ErrorCard, Input } from '../../components/index.js';
import { IdentityState, personName } from './common.js';
import { Candidate, defaultPick } from './identities.js';

/** The dialog for *person* (its `person_id`, names and `decision`); *onSaved* after a save. */
export function IdentityChangeDialog({ ctx, person, onClose, onSaved, toast }) {
  const personId = person.person_id;
  const decision = person.decision || {};
  const [entry, setEntry] = useState(null);
  const [pick, setPick] = useState(-1);
  const [pasted, setPasted] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const etag = useRef(null);
  const confirmButton = useRef(null);

  const load = () => ctx.api.get('/api/collection/identities',
    { query: { state: 'all', person: personId, limit: 1 } }).then((r) => {
    if (!r.ok) {
      setError(r.error);
      setEntry((old) => old || { candidates: [] });
      return;
    }
    etag.current = r.etag;
    const found = r.data.items[0] || { candidates: [] };
    setEntry(found);
    setPick(defaultPick(found));
  });
  useEffect(() => { load(); }, [personId]);

  async function decide(body) {
    if (busy) return;
    setBusy(true);
    setError(null);
    const result = await ctx.api.post(`/api/collection/identities/${encodeURIComponent(personId)}`,
      body, { ifMatch: etag.current });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      if (result.kind === 'stale') load();
      return;
    }
    toast({ kind: 'success', title: t(`corpus.identities.saved.${body.decision}`,
      { name: personName(person) }), timeout: 2500 });
    onSaved();
  }
  const candidates = (entry && entry.candidates) || [];
  const picked = candidates[pick];
  const confirm = () => {
    if (picked && picked.record) decide({ decision: 'accept', record: picked.record });
  };

  const onKeyDown = (event) => {
    const target = event.target;
    if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) return;
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (/^[1-9]$/.test(event.key)) {
      const i = Number(event.key) - 1;
      if (candidates[i] && candidates[i].record) setPick(i);
    } else if (event.key === 'n' || event.key === 'N') {
      decide({ decision: 'none' });
    } else {
      return;
    }
    event.preventDefault();
  };

  // Shown once read, so the focus starts on Confirm (or the first control without candidates).
  if (!entry) return null;
  const actions = html`<div class="cx-corpus-panel__actions">
    <${Button} variant="ghost" onClick=${() => onClose()}>${t('common.cancel')}<//>
    <${Button} onClick=${() => decide({ decision: 'none' })} disabled=${busy}>
      ${t('corpus.identities.none')} <kbd class="cx-corpus-kbd">${t('corpus.identities.key_none')}</kbd><//>
    <${Button} variant="primary" icon="check" loading=${busy} buttonRef=${confirmButton}
      disabled=${!picked || !picked.record} onClick=${confirm}>
      ${t('corpus.identities.confirm')} <kbd class="cx-corpus-kbd">⏎</kbd><//>
  </div>`;
  return html`<${Dialog} open=${true} onClose=${() => onClose()} size="m" class="cx-corpus-idchange"
    title=${t('corpus.identity_change.title', { name: personName(person) })}
    description=${t('corpus.identity_change.intro')}
    initialFocus=${picked && picked.record ? confirmButton : undefined}>
    <div class="cx-corpus-idchange__body" onKeyDown=${onKeyDown}>
      ${error ? html`<${ErrorCard} error=${error} compact onDismiss=${() => setError(null)} />` : null}
      <dl class="cx-corpus-facts">
        <div class="cx-corpus-fact"><dt>${t('corpus.identity_change.now')}</dt>
          <dd><${IdentityState} state=${decision.identity} />
          ${(decision.records || []).map((r) => html` <code key=${r}>${r}</code>`)}</dd></div>
      </dl>
      ${candidates.length ? html`<ol class="cx-corpus-cands" aria-label=${t('corpus.identity_change.candidates')}>
        ${candidates.slice(0, 9).map((c, i) => html`<${Candidate} key=${`${c.finder}-${c.record}-${i}`}
          candidate=${c} number=${i + 1} picked=${i === pick} onPick=${() => setPick(i)} />`)}
      </ol>` : html`<p class="cx-corpus-muted">${t('corpus.identities.none_found')}</p>`}
      <form class="cx-corpus-paste" onSubmit=${(e) => {
        e.preventDefault();
        if (pasted.trim()) decide({ decision: 'id', record: pasted.trim() });
      }}>
        <label class="cx-field__label" for="cx-idchange-paste">${t('corpus.identities.paste')}</label>
        <div class="cx-corpus-paste__row">
          <${Input} id="cx-idchange-paste" value=${pasted} placeholder=${t('corpus.identities.paste_example')}
            onInput=${(e) => setPasted(e.currentTarget.value)} />
          <${Button} type="submit" disabled=${!pasted.trim() || busy}>${t('corpus.identities.use_record')}<//>
        </div>
      </form>
      ${actions}
      <p class="cx-corpus-keys" aria-hidden="true">${t('corpus.identity_change.keys')}</p>
    </div>
  <//>`;
}
