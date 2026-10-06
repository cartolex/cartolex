// SPDX-License-Identifier: MIT
/**
 * What the space of the themes says about a selected keyword, in the atlas's panel: the
 * people who use it, ranked by its share of their keyword use, lit on the map
 * (`GET /api/atlas/keyword-people`), read once per selection. A person's or an
 * organisation's similarity to others is in « Compare with… » and the distances' exports,
 * not in the panel: the panel shows who they write with (`coauthors.js`).
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatPercent, t } from '../../core/i18n.js';
import { ErrorCard } from '../../components/index.js';

/** The number of people using a keyword listed. */
export const USERS = 30;

/** What the space answers about *sel* (`{key, kind, data}`, `{key, error}` or null), read
 * when the selection changes; nothing in the world view. */
export function useSpaceOf(ctx, index, sel, enabled) {
  const [answer, setAnswer] = useState(null);
  const key = sel ? `${sel.kind}:${sel.id}` : '';
  useEffect(() => {
    setAnswer(null);
    if (!index || !sel || !enabled || sel.kind !== 'keyword') return;
    const kind = 'users';
    ctx.api.get('/api/atlas/keyword-people', { query: { term: sel.id, limit: USERS } })
      .then((r) => setAnswer(r.ok ? { key, kind, data: r.data } : { key, kind, error: r.error }));
  }, [index ? index.atlas : null, key, enabled]); // the bundle, not the windows read beside it
  return answer && answer.key === key ? answer : null;
}

/** The people who use a keyword: how many, and the first, each with its share of their use. */
export function Users({ answer, onSelect }) {
  if (!answer) return html`<p class="cx-atlas-panel__muted" aria-busy="true">${t('common.loading')}</p>`;
  if (answer.error) return html`<${ErrorCard} error=${answer.error} compact />`;
  if (!answer.data.known) return html`<p class="cx-atlas-panel__muted">${t('map.users.unknown')}</p>`;
  const { count, items } = answer.data;
  return html`<p class="cx-atlas-count">${t('map.users.count', { count })}</p>
    ${items.length ? html`<ol class="cx-atlas-list">
      ${items.map((it) => html`<li key=${it.id}>
        <button type="button" class="cx-link-button" onClick=${() => onSelect({ kind: 'person', id: it.id })}>
          ${it.name || it.id}</button>
        <span class="cx-atlas-panel__muted">${formatPercent(it.share)}</span></li>`)}
    </ol>
    ${count > items.length ? html`<p class="cx-atlas-panel__muted">${t('map.users.more', { count: count - items.length })}</p>` : null}
    <p class="cx-atlas-panel__muted">${t('map.users.help')}</p>` : null}`;
}

/** What the answer lights on the map: the people who use a keyword (`{people: Set}`). */
export function litBySpace(index, sel, answer) {
  const out = { people: new Set(), organisations: new Set() };
  if (!answer || answer.error || !sel || answer.kind !== 'users') return out;
  for (const k of answer.data.at || []) out.people.add(k);
  return out;
}
