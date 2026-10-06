// SPDX-License-Identifier: MIT
/**
 * Organisations that may be one, in a dialog: the pairs (the same ROR or
 * OpenAlex id: clear; the same name and parents: proposed), each compared side
 * by side (names, acronym, identifiers, level, parents, units, people and the
 * people they share), decided one after another (one organisation, keeping
 * either; two organisations; later), and the clear ones merged in one step after
 * a preview.
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatNumber, has, t } from '../../core/i18n.js';
import { Button, Dialog, EmptyState, ErrorCard, Select } from '../../components/index.js';

function evidence(e) {
  const key = `corpus.orgs.evidence.${e.code}`;
  return has(key) ? t(key, e.params || {}) : e.code;
}

function Side({ org }) {
  return html`<div class="cx-org-side">
    <p class="cx-corpus-name">${org.name}</p>
    <dl class="cx-corpus-facts">
      <div class="cx-corpus-fact"><dt>${t('corpus.orgs.acronym')}</dt><dd>${org.acronym || '—'}</dd></div>
      ${Object.entries(org.ids || {}).map(([k, v]) => html`<div class="cx-corpus-fact" key=${k}>
        <dt>${k.toUpperCase()}</dt><dd><code>${v}</code></dd></div>`)}
      <div class="cx-corpus-fact"><dt>${t('corpus.col.level')}</dt><dd>${org.level || '—'}</dd></div>
      <div class="cx-corpus-fact"><dt>${t('corpus.orgs.parents')}</dt>
        <dd>${(org.parents || []).map((p) => p.name).join(' · ') || '—'}</dd></div>
      <div class="cx-corpus-fact"><dt>${t('corpus.orgs.country')}</dt><dd>${org.country || '—'}</dd></div>
      <div class="cx-corpus-fact"><dt>${t('corpus.col.units')}</dt><dd>${formatNumber((org.units || []).length)}</dd></div>
      <div class="cx-corpus-fact"><dt>${t('corpus.orgs.people')}</dt><dd>${formatNumber(org.people || 0)}</dd></div>
      <div class="cx-corpus-fact"><dt>${t('corpus.col.source')}</dt><dd>${org.source}</dd></div>
    </dl>
    ${(org.names || []).length ? html`<p class="cx-corpus-muted">${t('corpus.orgs.people_names',
      { names: org.names.join(' · ') })}</p>` : null}
  </div>`;
}

/** The review of the pairs of organisations that may be one. */
export function OrgReview({ ctx, onClose, toast, bump }) {
  const [show, setShow] = useState('open');
  const [data, setData] = useState(null);
  const [etag, setEtag] = useState(null);
  const [index, setIndex] = useState(0);
  const [compare, setCompare] = useState(null);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = () => ctx.api.get('/api/organisations/pairs', { query: { show, limit: 500 } })
    .then((r) => {
      if (r.ok) {
        setData(r.data);
        setEtag(r.etag);
      } else setError(r.error);
    });
  useEffect(() => {
    setIndex(0);
    load();
  }, [show]);
  const items = (data && data.items) || [];
  const pair = items[Math.min(index, items.length - 1)] || null;
  useEffect(() => {
    setCompare(null);
    if (!pair) return;
    ctx.api.get('/api/organisations/compare', { query: { a: pair.a, b: pair.b } })
      .then((r) => (r.ok ? setCompare(r.data) : setError(r.error)));
  }, [pair && pair.key]);

  async function decide(decision, keep = null) {
    if (!pair) return;
    setBusy(true);
    setError(null);
    const result = await ctx.api.post('/api/organisations/decide',
      { a: pair.a, b: pair.b, decision, keep }, { ifMatch: etag });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    toast({ kind: 'success', timeout: 2500, title: t(`corpus.orgs.saved.${decision}`) });
    bump();
    load();
  }
  async function auto(apply) {
    setBusy(true);
    setError(null);
    const result = await ctx.api.post('/api/organisations/auto', { apply }, apply ? { ifMatch: etag } : {});
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    if (!apply) {
      setPreview(result.data);
      return;
    }
    setPreview(null);
    toast({ kind: 'success', title: t('corpus.orgs.auto_done', { n: result.data.merged }) });
    bump();
    load();
  }
  const onKeyDown = (event) => {
    if (['INPUT', 'SELECT', 'TEXTAREA'].includes(event.target.tagName) || !pair || busy) return;
    const key = event.key.toLowerCase();
    if (key === 'arrowdown' || key === 'j') setIndex(Math.min(index + 1, items.length - 1));
    else if (key === 'arrowup' || key === 'k') setIndex(Math.max(index - 1, 0));
    else if (key === '1') decide('merge', pair.a);
    else if (key === '2') decide('merge', pair.b);
    else if (key === t('corpus.dup.key_distinct').toLowerCase()) decide('distinct');
    else if (key === t('corpus.dup.key_later').toLowerCase()) decide('later');
    else return;
    event.preventDefault();
  };
  const counts = (data && data.counts) || {};
  return html`<${Dialog} open=${true} onClose=${onClose} size="l" title=${t('corpus.orgs.review_title')}
    description=${t('corpus.orgs.review_text')}>
    <div class="cx-org-review" onKeyDown=${onKeyDown} tabindex="-1">
      <div class="cx-corpus-filters">
        <label class="cx-corpus-filters__select"><span class="cx-visually-hidden">${t('corpus.dup.show')}</span>
          <${Select} aria-label=${t('corpus.dup.show')} value=${show} onChange=${(e) => setShow(e.currentTarget.value)}
            options=${['open', 'clear', 'later'].map((s) => ({ value: s,
              label: t(`corpus.dup.show_${s}`, { n: counts[s] || 0 }) }))} /></label>
        <${Button} size="s" variant="primary" icon="check" disabled=${!counts.clear || busy}
          onClick=${() => auto(false)}>${t('corpus.orgs.auto_button', { n: counts.clear || 0 })}<//>
        <p class="cx-corpus-keys" aria-hidden="true">${t('corpus.orgs.review_keys')}</p>
      </div>
      ${error ? html`<${ErrorCard} error=${error} compact onDismiss=${() => setError(null)} />` : null}
      ${preview ? html`<section class="cx-corpus-note" role="status">
        <p>${t('corpus.orgs.auto_preview', { n: preview.merged, groups: preview.groups })}</p>
        <ul class="cx-corpus-list">${preview.examples.map((g) => html`<li key=${g.keep}>
          ${t('corpus.dup.auto.example', { keep: g.names[g.keep], others: g.merge.map((m) => g.names[m]).join(', ') })}</li>`)}</ul>
        <div class="cx-corpus-actions-row">
          <${Button} size="s" variant="primary" loading=${busy} onClick=${() => auto(true)}>
            ${t('corpus.orgs.auto_apply', { n: preview.merged })}<//>
          <${Button} size="s" onClick=${() => setPreview(null)}>${t('common.cancel')}<//>
        </div></section>` : null}
      ${data && !items.length ? html`<${EmptyState} icon="check" title=${t('corpus.orgs.review_empty')} />` : null}
      ${pair ? html`<section class="cx-org-pair" aria-live="polite">
        <p class="cx-corpus-muted">${t('corpus.orgs.pair_of', { i: index + 1, n: items.length })}
          · ${pair.evidence.map(evidence).join(' · ')}
          ${pair.clear ? html` <span class="cx-corpus-chip">${t('corpus.dup.clear')}</span>` : null}</p>
        ${compare ? html`<div class="cx-org-sides">
          <${Side} org=${compare.a} />
          <${Side} org=${compare.b} /></div>
          <p class="cx-corpus-muted">${t('corpus.orgs.shared_people', { n: compare.shared.people_total })}${
            compare.shared.people.length ? html`: ${compare.shared.people.join(' · ')}` : null}</p>`
          : html`<p class="cx-corpus-muted">${t('common.loading')}</p>`}
        <div class="cx-corpus-panel__actions">
          <${Button} variant="primary" disabled=${busy} onClick=${() => decide('merge', pair.a)}>
            ${t('corpus.dup.keep', { name: pair.orgs[0].name })} <kbd class="cx-corpus-kbd">1</kbd><//>
          <${Button} disabled=${busy} onClick=${() => decide('merge', pair.b)}>
            ${t('corpus.dup.keep', { name: pair.orgs[1].name })} <kbd class="cx-corpus-kbd">2</kbd><//>
          <${Button} disabled=${busy} onClick=${() => decide('distinct')}>${t('corpus.orgs.distinct')}
            <kbd class="cx-corpus-kbd">${t('corpus.dup.key_distinct')}</kbd><//>
          <${Button} variant="ghost" disabled=${busy} onClick=${() => decide('later')}>
            ${t('corpus.dup.later_action')} <kbd class="cx-corpus-kbd">${t('corpus.dup.key_later')}</kbd><//>
        </div>
      </section>` : null}
    </div>
  <//>`;
}
