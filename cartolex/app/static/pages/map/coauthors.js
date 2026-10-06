// SPDX-License-Identifier: MIT
/**
 * Who writes with the selection, in the atlas (`GET /api/atlas/coauthors`, one call per
 * selection, and one per page asked): a person's co-authors in the project (the people who
 * signed a work with them, with the works together) and, with « Second circle », the
 * co-authors of their co-authors; an organisation's partners, the organisations of its
 * level whose people sign works with its people. On the map: a line from the selection to
 * each partner drawn there, the thicker the more works together; projected co-authors
 * faint, the second circle thinner and fainter, organisations dashed. These are real links
 * (works signed together), never a similarity. Projected people are never named: their id.
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { Button, Checkbox, ErrorCard } from '../../components/index.js';
import { linkTo } from './links.js';

/** The partners a page lists. */
export const PAGE = 30;

/** What the API is asked about for a selection (`{kind, id}`), or null. */
function targetOf(sel) {
  if (!sel) return null;
  if (sel.kind === 'person' || sel.kind === 'projected') return { kind: 'person', id: sel.id };
  if (sel.kind === 'organisation') return { kind: 'organisation', id: sel.id };
  return null;
}

/** The co-authors of the selection (`{key, data}`, `{key, error}` or null), read when the
 * selection or the circle changes, with `more('first' | 'second')` reading the next page. */
export function useCoauthors(ctx, index, sel, second, enabled) {
  const [answer, setAnswer] = useState(null);
  const [busy, setBusy] = useState(false);
  const target = targetOf(sel);
  const circle = second ? 2 : 1;
  const key = target ? `${target.kind}:${target.id}:${circle}` : '';
  useEffect(() => {
    setAnswer(null);
    if (!index || !target || !enabled) return;
    ctx.api.get('/api/atlas/coauthors', { query: { ...target, circle, limit: PAGE, limit2: PAGE } })
      .then((r) => setAnswer(r.ok ? { key, data: r.data } : { key, error: r.error }));
  }, [index ? index.atlas : null, key, enabled]);
  if (!answer || answer.key !== key) return null;
  const more = (which) => {
    if (!answer.data || busy) return;
    const d = answer.data;
    const query = { ...target, circle, limit: PAGE, limit2: PAGE };
    if (which === 'first') query.offset = d.items.length;
    else query.offset2 = d.second.items.length;
    setBusy(true);
    ctx.api.get('/api/atlas/coauthors', { query }).then((r) => {
      setBusy(false);
      if (!r.ok) return;
      setAnswer((old) => {
        if (!old || old.key !== key || !old.data) return old;
        const next = { ...old.data };
        if (which === 'first') next.items = [...old.data.items, ...r.data.items];
        else next.second = { ...old.data.second, items: [...old.data.second.items, ...r.data.second.items] };
        return { key, data: next };
      });
    });
  };
  return { ...answer, more, busy };
}

/** Where *id* of *kind* (`person`, `projected`, `organisation`) is drawn, or null. */
function placeOf(index, kind, id) {
  if (kind === 'organisation') {
    const o = index.orgs[index.byOrg.get(id)];
    return o && o.x !== null && o.x !== undefined ? o : null;
  }
  if (kind === 'projected') {
    const p = index.projected.find((q) => q.person_id === id);
    return p && p.x !== null && p.x !== undefined ? p : null;
  }
  const p = index.people[index.byPerson.get(id)];
  return p && p.x !== null && p.x !== undefined ? p : null;
}

/** The kind of a partner on the map: a person, a projected person or an organisation. */
const kindOf = (target, item) => (target === 'organisation' ? 'organisation'
  : item.place === 'projected' ? 'projected' : 'person');

/** The width of a line for *n* works together. */
export function widthOf(n) {
  if (n >= 9) return 5;
  if (n >= 5) return 4;
  if (n >= 3) return 3;
  return n >= 2 ? 2 : 1.25;
}

/**
 * What the co-authors add to the map's scene: `lines` (segments grouped by width and
 * kind), the partners lit (`people`, `projected`, `organisations`: item indexes) and the
 * projected partners to draw faintly when the projected people are not shown (`faint`).
 */
export function coauthorScene(index, sel, answer) {
  const out = { lines: [], people: new Set(), projected: new Set(), organisations: new Set(), faint: new Set() };
  if (!index || !sel || !answer || !answer.data) return out;
  const d = answer.data;
  const target = d.kind;
  const from = placeOf(index, sel.kind === 'projected' ? 'projected' : target === 'organisation' ? 'organisation' : 'person', sel.id);
  const groups = new Map();
  const push = (id, a, b, width, alpha, dash) => {
    const group = `${width}:${alpha}:${dash}`;
    if (!groups.has(group)) groups.set(group, { id, xs: [], ys: [], width, alpha, dash });
    const g = groups.get(group);
    g.xs.push(a.x, b.x);
    g.ys.push(a.y, b.y);
  };
  const where = new Map();
  const locate = (id) => {
    if (!where.has(id)) {
      let at = null;
      let kind = target === 'organisation' ? 'organisation' : 'person';
      at = placeOf(index, kind, id);
      if (!at && kind === 'person') {
        kind = 'projected';
        at = placeOf(index, kind, id);
      }
      where.set(id, at ? { at, kind } : null);
    }
    return where.get(id);
  };
  // the first circle is lit; a projected partner is drawn faintly (when the projected people
  // are hidden), the second circle's only then
  const mark = (found, first) => {
    if (found.kind === 'projected') {
      const i = index.projected.indexOf(found.at);
      if (first) out.projected.add(i);
      out.faint.add(i);
    } else if (!first) {
      return;
    } else if (found.kind === 'organisation') out.organisations.add(index.byOrg.get(found.at.id));
    else out.people.add(index.byPerson.get(found.at.person_id));
  };
  const dash = target === 'organisation' ? 6 : 0;
  for (const [id, n] of d.lines || []) {
    const found = locate(id);
    if (!found) continue;
    mark(found, true);
    if (from) push(`coauthors-${id}`, from, found.at, widthOf(n), found.kind === 'projected' ? 0.3 : 0.7, dash);
  }
  if (d.second) {
    for (const [a, b] of d.second.lines || []) {
      const one = locate(a);
      const two = locate(b);
      if (!one || !two) continue;
      mark(two, false);
      push('coauthors-second', one.at, two.at, 1, 0.22, dash);
    }
  }
  for (const g of groups.values()) {
    out.lines.push({ id: `coauthors-${g.width}-${g.alpha}-${g.dash}`, x: Float32Array.from(g.xs), y: Float32Array.from(g.ys),
      color: '--cx-accent', alpha: g.alpha, width: g.width, dash: g.dash || undefined });
  }
  // the thin lines first, the strongest on top
  out.lines.sort((a, b) => a.width - b.width);
  return out;
}

/** One partner: a button that selects it on the map (else a link to its sheet), the works. */
function Partner({ item, target, onSelect, value }) {
  const kind = kindOf(target, item);
  const name = item.name || item.id;
  const label = target === 'organisation' && item.acronym ? `${name} (${item.acronym})` : name;
  const open = item.place
    ? html`<button type="button" class="cx-link-button" onClick=${() => onSelect({ kind, id: item.id })}>${label}</button>`
    : html`<a href=${target === 'organisation' ? linkTo.organisation(item.id) : linkTo.person(item.id)}>${label}</a>`;
  const role = target === 'person' && item.role && item.role !== 'mapped' ? t(`corpus.role.${item.role}`) : '';
  return html`<li>
    ${open}
    ${role ? html`<span class="cx-atlas-coauthors__role">${role}</span>` : null}
    <span class="cx-atlas-coauthors__value">${value}</span>
  </li>`;
}

/** The co-authors (an organisation's partners) in the panel, with the second circle. */
export function Coauthors({ answer, onSelect, second, onSecond }) {
  if (!answer) return html`<p class="cx-atlas-panel__muted" aria-busy="true">${t('common.loading')}</p>`;
  if (answer.error) return html`<${ErrorCard} error=${answer.error} compact />`;
  const d = answer.data;
  const target = d.kind;
  const org = target === 'organisation';
  const works = (n) => t('map.coauthors.works', { count: n });
  return html`<div class="cx-atlas-coauthors">
    ${d.count ? html`<ol class="cx-atlas-list cx-atlas-coauthors__list" aria-label=${t(org ? 'map.coauthors.orgs_title' : 'map.coauthors.title', { count: d.count })}>
      ${d.items.map((it) => html`<${Partner} key=${it.id} item=${it} target=${target} onSelect=${onSelect} value=${works(it.texts)} />`)}
    </ol>` : html`<p class="cx-atlas-panel__muted">${t(org ? 'map.coauthors.orgs_none' : 'map.coauthors.none')}</p>`}
    ${d.items.length < d.count ? html`<${Button} size="s" variant="ghost" loading=${answer.busy}
      onClick=${() => answer.more('first')}>${t('map.coauthors.more', { count: d.count - d.items.length })}<//>` : null}
    ${d.outside ? html`<p class="cx-atlas-panel__muted">${t('map.coauthors.outside', { count: d.outside })}</p>` : null}
    ${d.large ? html`<p class="cx-atlas-panel__muted">${t('map.coauthors.large', { count: d.large, max: d.max_authors })}</p>` : null}
    <${Checkbox} label=${t('map.coauthors.second')} checked=${second} onChange=${(e) => onSecond(e.target.checked)} />
    ${second && d.second ? html`<div class="cx-atlas-coauthors__second">
      <h4 class="cx-atlas-panel__subtitle">${t('map.coauthors.second_title', { count: d.second.count })}</h4>
      ${d.second.items.length ? html`<ol class="cx-atlas-list cx-atlas-coauthors__list">
        ${d.second.items.map((it) => html`<${Partner} key=${it.id} item=${it} target=${target} onSelect=${onSelect}
          value=${t('map.coauthors.paths', { count: it.paths })} />`)}
      </ol>` : html`<p class="cx-atlas-panel__muted">${t('map.coauthors.second_none')}</p>`}
      ${d.second.items.length < d.second.count ? html`<${Button} size="s" variant="ghost" loading=${answer.busy}
        onClick=${() => answer.more('second')}>${t('map.coauthors.more', { count: d.second.count - d.second.items.length })}<//>` : null}
    </div>` : null}
    <p class="cx-atlas-panel__muted">${t(org ? 'map.coauthors.orgs_help' : 'map.coauthors.help', { placed: d.placed })}</p>
  </div>`;
}
