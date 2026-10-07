// SPDX-License-Identifier: MIT
/**
 * « Same person… »: people someone knows to be one person, merged by hand. From the
 * People list (the rows selected) or a person's sheet (« Same person as… », the
 * others found by a search). The people side by side (`GET
 * /api/people/duplicates/group`), the one kept chosen, then one merge (`POST
 * /api/people/duplicates/group`), undone in one step by the caller's « Undo ». Two
 * different ORCIDs among them are merged only after a question, as in the review.
 */
import { html, useEffect, useRef, useState } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { Button, ConfirmDialog, Dialog, ErrorCard, IconButton, Input } from '../../components/index.js';
import { personName } from './common.js';
import { GroupCompare } from './duplicates-compare.js';

/** How many people one « Same person… » takes at most (the API's). */
export const MAX_SAME = 50;

/** The people found by a search, to add to the ones merged. */
function Search({ ctx, ids, onAdd }) {
  const [q, setQ] = useState('');
  const [found, setFound] = useState(null);
  const asked = useRef(0);
  useEffect(() => {
    const text = q.trim();
    if (text.length < 2) {
      setFound(null);
      return undefined;
    }
    const timer = setTimeout(() => {
      const mine = ++asked.current;
      ctx.api.get('/api/people', { query: { q: text, limit: 8 } }).then((r) => {
        if (mine === asked.current && r.ok) setFound(r.data.items);
      });
    }, 250);
    return () => clearTimeout(timer);
  }, [q]);
  const shown = (found || []).filter((p) => !ids.includes(p.person_id) && !p.merged_into);
  return html`<div class="cx-same-search">
    <label class="cx-field__label">${t('corpus.same.search')}
      <${Input} type="search" value=${q} placeholder=${t('corpus.people.search')}
        onInput=${(e) => setQ(e.currentTarget.value)} /></label>
    ${found ? html`<ul class="cx-corpus-list cx-same-found" aria-live="polite">
      ${shown.length ? shown.map((p) => html`<li key=${p.person_id}>
        <${Button} size="s" variant="ghost" icon="plus" onClick=${() => { onAdd(p.person_id); setQ(''); }}>
          ${t('corpus.same.add', { name: personName(p) })}<//>
        <span class="cx-corpus-muted"> ${[p.unit, p.orcid && `ORCID ${p.orcid}`].filter(Boolean).join(' · ')}</span>
        <code class="cx-corpus-muted cx-dup-id">${p.person_id}</code></li>`)
        : html`<li class="cx-corpus-muted">${t('corpus.same.none_found')}</li>`}</ul>` : null}
  </div>`;
}

/**
 * The dialog: *ids* the people to start from (two or more from the list, one from a
 * sheet, with *search*), *onDone(result)* after the merge (`merged`, `keep`).
 */
export function SamePersonDialog({ ctx, ids: start, search = false, openSheet, onClose, onDone }) {
  const [ids, setIds] = useState(start);
  const [compare, setCompare] = useState(null);
  const [keep, setKeep] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  useEffect(() => {
    setCompare(null);
    if (ids.length < 2) return;
    ctx.api.get('/api/people/duplicates/group', { query: { ids: ids.join(',') } }).then((r) => {
      if (!r.ok) {
        setError(r.error);
        return;
      }
      setCompare(r.data);
      setKeep((k) => (k && ids.includes(k) ? k : r.data.keep));
    });
  }, [ids.join(',')]);

  async function merge(override = false) {
    if (compare.conflict && !override) {
      setAsking(true);
      return;
    }
    setBusy(true);
    setError(null);
    const people = await ctx.api.get('/api/people', { query: { limit: 1 } });
    const result = await ctx.api.post('/api/people/duplicates/group',
      { ids, decision: 'merge', keep, override }, { ifMatch: people.etag });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    onDone({ ...result.data, keep });
  }

  const sides = compare ? compare.people : ids.map((id) => ({ person_id: id, name: id }));
  const merged = sides.filter((s) => s.merged_into);
  const keptName = compare ? (sides.find((s) => s.person_id === keep) || {}).name : '';
  const ready = compare && keep && ids.length >= 2 && !merged.some((s) => s.person_id === keep);
  return html`<${Dialog} open=${true} onClose=${onClose} size="l"
    title=${search ? t('corpus.same.title_as') : t('corpus.same.title')}
    description=${t('corpus.same.rule')}
    footer=${html`<${Button} onClick=${onClose}>${t('common.cancel')}<//>
      <${Button} variant="primary" icon="check" loading=${busy} disabled=${!ready}
        onClick=${() => merge()}>${t('corpus.same.merge', { n: ids.length, name: keptName || '' })}<//>`}>
    <div class="cx-same">
      ${search ? html`<${Search} ctx=${ctx} ids=${ids}
        onAdd=${(id) => ids.length < MAX_SAME && setIds([...ids, id])} />` : null}
      <ul class="cx-same-chips" aria-label=${t('corpus.same.chosen')}>
        ${sides.map((s, i) => html`<li key=${s.person_id} class="cx-corpus-chip">
          ${s.name || s.person_id}
          ${(search ? i > 0 : ids.length > 2) ? html`<${IconButton} size="s" icon="close"
            label=${t('corpus.same.remove', { name: s.name || s.person_id })}
            onClick=${() => setIds(ids.filter((x) => x !== s.person_id))} />` : null}</li>`)}
      </ul>
      ${error ? html`<${ErrorCard} error=${error} compact onDismiss=${() => setError(null)} />` : null}
      ${ids.length < 2 ? html`<p class="cx-corpus-muted">${t('corpus.same.pick')}</p>`
        : !compare ? html`<p class="cx-corpus-muted">${t('common.loading')}</p>` : html`
        ${compare.conflict ? html`<p class="cx-corpus-note cx-same-warning" role="note">
          <span class="cx-corpus-chip cx-dup-chip--conflict">${t('corpus.dup.conflict')}</span>
          ${t('corpus.same.conflict')}</p>` : null}
        ${merged.length ? html`<p class="cx-corpus-note" role="note">${t('corpus.same.merged_rows',
          { names: merged.map((s) => s.name), n: merged.length })}</p>` : null}
        <${GroupCompare} people=${sides} compare=${compare} pairs=${compare.pairs}
          openSheet=${openSheet} keep=${keep} onKeep=${setKeep} name="cx-same-keep" />
        <p class="cx-corpus-muted">${t('corpus.same.preview', { n: ids.length - 1, name: keptName })}</p>`}
    </div>
    ${asking ? html`<${ConfirmDialog} open=${true} danger title=${t('corpus.dup.conflict_title')}
      confirmLabel=${t('corpus.dup.conflict_confirm')}
      onAnswer=${(yes) => {
        setAsking(false);
        if (yes) merge(true);
      }}>${t('corpus.dup.conflict_text')}<//>` : null}
  <//>`;
}
