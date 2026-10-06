// SPDX-License-Identifier: MIT
/**
 * What the space of the themes says about the selection, in the atlas's panel: the nearest
 * people (organisations of the same level) by the cosine of their vectors, with the values,
 * joined to the selection by lines on the map (`GET /api/atlas/neighbours`); and the people
 * who use a keyword, ranked by its share of their keyword use, lit on the map
 * (`GET /api/atlas/keyword-people`). Each answer is read once per selection.
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatNumber, formatPercent, t } from '../../core/i18n.js';
import { ErrorCard } from '../../components/index.js';

/** The number of nearest asked for, and of the people using a keyword listed. */
export const NEAREST = 10;
export const USERS = 30;

/** What the space answers about *sel* (`{key, kind, data}`, `{key, error}` or null), read
 * when the selection changes; nothing in the world view. */
export function useSpaceOf(ctx, index, sel, enabled) {
  const [answer, setAnswer] = useState(null);
  const key = sel ? `${sel.kind}:${sel.id}` : '';
  useEffect(() => {
    setAnswer(null);
    if (!index || !sel || !enabled) return;
    let request = null;
    let kind = '';
    if (sel.kind === 'person' || sel.kind === 'organisation' || sel.kind === 'projected') {
      kind = 'near';
      request = ctx.api.get('/api/atlas/neighbours', { query: { kind: sel.kind, id: sel.id, k: NEAREST } });
    } else if (sel.kind === 'keyword') {
      kind = 'users';
      request = ctx.api.get('/api/atlas/keyword-people', { query: { term: sel.id, limit: USERS } });
    }
    if (!request) return;
    request.then((r) => setAnswer(r.ok ? { key, kind, data: r.data } : { key, kind, error: r.error }));
  }, [index, key, enabled]);
  return answer && answer.key === key ? answer : null;
}

/** The nearest, each a button that selects it, with its similarity. */
export function Nearest({ answer, onSelect, kind }) {
  if (!answer) return html`<p class="cx-atlas-panel__muted" aria-busy="true">${t('common.loading')}</p>`;
  if (answer.error) return html`<${ErrorCard} error=${answer.error} compact />`;
  const items = answer.data.items || [];
  if (!items.length) return html`<p class="cx-atlas-panel__muted">${t('map.near.none')}</p>`;
  const target = kind === 'organisation' ? 'organisation' : 'person';
  return html`<ol class="cx-atlas-list cx-atlas-near">
    ${items.map((it) => html`<li key=${it.id}>
      <button type="button" class="cx-link-button" onClick=${() => onSelect({ kind: target, id: it.id })}>
        ${it.name || it.id}</button>
      <span class="cx-atlas-near__value" aria-label=${t('map.near.value_label', { value: it.similarity })}>
        ${formatNumber(it.similarity, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
    </li>`)}
  </ol>
  <p class="cx-atlas-panel__muted">${t('map.near.help')}</p>`;
}

/** The people who use a keyword: how many, and the first, each with its share of their use. */
export function Users({ answer, onSelect }) {
  if (!answer) return html`<p class="cx-atlas-panel__muted" aria-busy="true">${t('common.loading')}</p>`;
  if (answer.error) {
    return answer.error.code === 'atlas_item_not_found'
      ? html`<p class="cx-atlas-panel__muted">${t('map.users.unknown')}</p>`
      : html`<${ErrorCard} error=${answer.error} compact />`;
  }
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

/** What the answer lights on the map: `{people: Set, organisations: Set, lines: [[x, y, x, y]]}`. */
export function litBySpace(index, sel, answer) {
  const out = { people: new Set(), organisations: new Set(), lines: [] };
  if (!answer || answer.error || !sel) return out;
  if (answer.kind === 'users') {
    for (const k of answer.data.at || []) out.people.add(k);
    return out;
  }
  let from = null;
  if (sel.kind === 'person' && index.byPerson.has(sel.id)) from = index.people[index.byPerson.get(sel.id)];
  else if (sel.kind === 'organisation' && index.byOrg.has(sel.id)) from = index.orgs[index.byOrg.get(sel.id)];
  else if (sel.kind === 'projected') from = index.projected.find((p) => p.person_id === sel.id) || null;
  for (const it of answer.data.items || []) {
    let to = null;
    if (sel.kind === 'organisation') {
      const k = index.byOrg.get(it.id);
      if (k !== undefined) {
        out.organisations.add(k);
        to = index.orgs[k];
      }
    } else {
      const k = index.byPerson.get(it.id);
      if (k !== undefined) {
        out.people.add(k);
        to = index.people[k];
      }
    }
    if (from && to && from.x !== null && to.x !== null && from.x !== undefined && to.x !== undefined) {
      out.lines.push([from.x, from.y, to.x, to.y]);
    }
  }
  return out;
}
