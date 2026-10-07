// SPDX-License-Identifier: MIT
/**
 * Two or more people side by side, from the project's own data: names and
 * other names, ORCID and records, identity and role, organisation, texts (how
 * many, their years, the most recent titles), affiliations with their years,
 * co-authors in the project; what two or more of them share, and who; and the
 * evidence of each pair proposed among them, each line with its weight (+ for,
 * − against). Each person's column may carry the choice of the one kept (a
 * radio) and whether they are part of the decision (a check box).
 */
import { html } from '../../core/preact.js';
import { formatNumber, formatPercent, has, t } from '../../core/i18n.js';
import { Checkbox } from '../../components/index.js';
import { IdentityState, roleLabel } from './common.js';

/** A line of evidence in words: the catalogue's, else the server's English. */
export function evidenceText(e) {
  const key = `corpus.dup.evidence.${e.code}`;
  return has(key) ? t(key, e.params || {}) : e.text || e.code;
}

function years(first, last) {
  if (!first && !last) return t('corpus.years.unknown');
  if (first === last) return String(first);
  return t('corpus.years.span', { first: first || '…', last: last || '…' });
}

function span(a) {
  if (!a.start_year && !a.end_year) return t('corpus.years.unknown');
  if (!a.end_year) return t('corpus.years.since', { first: a.start_year });
  return t('corpus.years.span', { first: a.start_year || '…', last: a.end_year });
}

const dash = '—';

function Row({ label, values }) {
  return html`<tr><th scope="row">${label}</th>${values.map((v, i) => html`<td key=${i}>${v}</td>`)}</tr>`;
}

function list(items, render) {
  return items && items.length ? html`<ul class="cx-corpus-list">${items.map(render)}</ul>` : dash;
}

/** The evidence of one pair: each line with its sign and weight. */
export function Evidence({ evidence }) {
  return html`<ul class="cx-corpus-list cx-dup-evidence">${evidence.map((e, i) => html`<li key=${i}
    class=${e.points < 0 ? 'cx-dup-against' : 'cx-dup-for'}>
    <span class="cx-dup-sign" aria-hidden="true">${e.points < 0 ? '−' : '+'}</span>
    <span class="cx-visually-hidden">${e.points < 0 ? t('corpus.dup.against') : t('corpus.dup.for')}</span>
    ${evidenceText(e)} <span class="cx-corpus-muted">${t('corpus.identities.points',
      { points: formatNumber(Math.abs(e.points), { maximumFractionDigits: 1 }) })}</span></li>`)}</ul>`;
}

/**
 * The comparison of *people* (each `{person_id, name, …}`: a group's briefs, or the
 * ids alone), with *compare* once read (`GET /api/people/duplicates/group`) and the
 * *pairs* proposed among them. *keep* and *onKeep* add the choice of the one kept;
 * *chosen* (a Set) and *onToggle* the check boxes of those taken in; *numbered* shows
 * each column's key.
 */
export function GroupCompare({ people, compare, pairs, openSheet, keep, onKeep, chosen, onToggle,
  name = 'cx-dup-keep', numbered = false }) {
  const sides = compare ? compare.people : null;
  const byId = Object.fromEntries((sides || people).map((p) => [p.person_id, p]));
  const ids = people.map((p) => p.person_id);
  // What the list's brief already says, until the comparison is read.
  const known = ids.map((id) => byId[id] || people.find((p) => p.person_id === id) || {});
  const nameOf = (id) => (byId[id] && byId[id].name) || id;
  const each = (fn) => (sides ? known.map((s) => (s.person_id && s.aliases ? fn(s) : dash)) : ids.map(() => dash));
  const shared = compare && compare.shared;
  const who = (people_) => people_.map(nameOf).join(', ');
  const several = ids.length > 2;
  const orcid = (s) => s.orcid || (s.orcids || []).join(', ') || dash;
  return html`<div class="cx-dup-compare">
    <div class="cx-dup-scroll">
    <table class=${`cx-corpus-simple cx-dup-table ${several ? 'cx-dup-table--wide' : ''}`}>
      <thead><tr><td></td>${ids.map((id, i) => html`<th scope="col" key=${id}>
        ${numbered && i < 9 ? html`<kbd class="cx-corpus-kbd">${i + 1}</kbd> ` : null}
        <button type="button" class="cx-link-button cx-corpus-name"
          onClick=${() => openSheet(id)}>${nameOf(id)}</button>
        <code class="cx-corpus-muted cx-dup-id">${id}</code>
        ${onKeep ? html`<label class="cx-dup-choice">
          <input type="radio" name=${name} value=${id} checked=${keep === id}
            onChange=${() => onKeep(id)} />
          <span>${t('corpus.dup.keep_this')}</span></label>` : null}
        ${onToggle ? html`<${Checkbox} class="cx-dup-choice" checked=${chosen.has(id)}
          label=${t('corpus.dup.include')} onChange=${() => onToggle(id)} />` : null}
      </th>`)}</tr></thead>
      <tbody>
        <${Row} label=${t('corpus.sheet.aliases')} values=${each((s) => (s.aliases.length ? s.aliases.join(' · ') : dash))} />
        <${Row} label=${t('corpus.dup.orcid')} values=${known.map(orcid)} />
        <${Row} label=${t('corpus.sheet.records')} values=${each((s) => (s.records.length
            ? s.records.map((r) => html`<code key=${r}>${r}</code> `) : dash))} />
        <${Row} label=${t('corpus.col.identity')} values=${known.map((s) => html`<${IdentityState} state=${s.identity} />`)} />
        <${Row} label=${t('corpus.col.role')} values=${known.map((s) => (s.merged_into
          ? t('corpus.people.merged') : roleLabel(s.role || '')))} />
        <${Row} label=${t('corpus.col.unit')} values=${known.map((s) => s.unit || dash)} />
        <${Row} label=${t('corpus.col.texts')} values=${known.map((s) => t('corpus.dup.texts',
          { n: s.texts || 0, years: years(s.first_year, s.last_year) }))} />
        <${Row} label=${t('corpus.dup.recent')} values=${each((s) => list(s.recent, (r) => html`<li key=${r.text_id}>
            <span class="cx-corpus-muted">${r.year || dash}</span> ${r.title}</li>`))} />
        <${Row} label=${t('corpus.dup.affiliations')} values=${each((s) => list(s.affiliations.slice(0, 6), (r, i) => html`<li key=${`${r.org_id}-${i}`}>
            ${r.name} <span class="cx-corpus-muted">${span(r)}</span></li>`))} />
        <${Row} label=${t('corpus.dup.coauthors')} values=${each((s) => (s.coauthors.length ? s.coauthors.map((c) => t('corpus.dup.coauthor',
            { name: c.name, n: c.texts })).join(' · ') : dash))} />
      </tbody>
    </table>
    </div>
    ${shared ? html`<section class="cx-dup-shared">
      <h3 class="cx-corpus-h3">${t('corpus.dup.shared')}</h3>
      <ul class="cx-corpus-list">
        <li>${t('corpus.dup.shared_texts', { n: shared.texts.length })}${shared.texts.length
          ? html`: ${shared.texts.slice(0, 3).map((x) => (several ? t('corpus.dup.shared_by',
            { what: x.title, who: who(x.people) }) : x.title)).join(' · ')}` : null}</li>
        <li>${t('corpus.dup.shared_orgs', { n: shared.organisations.length })}${shared.organisations.length
          ? html`: ${shared.organisations.map((o) => (several ? t('corpus.dup.shared_by',
            { what: o.name, who: who(o.people) }) : o.name)).join(' · ')}` : null}</li>
        <li>${t('corpus.dup.shared_coauthors', { n: shared.coauthors_total })}${shared.coauthors.length
          ? html`: ${shared.coauthors.map((c) => (several ? t('corpus.dup.shared_by',
            { what: c.name, who: who(c.people) }) : c.name)).join(' · ')}` : null}</li>
      </ul></section>` : null}
    ${pairs && pairs.length ? html`<section class="cx-dup-why">
      <h3 class="cx-corpus-h3">${t('corpus.dup.why')}</h3>
      ${several ? pairs.map((p) => html`<div key=${`${p.a}|${p.b}`} class="cx-dup-pair">
        <p class="cx-dup-pair__head">${t('corpus.dup.pair_head', { a: nameOf(p.a), b: nameOf(p.b),
          score: formatPercent(p.score) })}${p.decision === 'later'
          ? html` <span class="cx-corpus-chip">${t('corpus.dup.later')}</span>` : null}</p>
        <${Evidence} evidence=${p.evidence} /></div>`)
        : html`<p class="cx-corpus-muted">${t('corpus.dup.score', { score: Math.round(pairs[0].score * 100) })}</p>
          <${Evidence} evidence=${pairs[0].evidence} />`}
    </section>` : compare ? html`<p class="cx-corpus-muted">${t('corpus.dup.not_proposed')}</p>` : null}
  </div>`;
}
