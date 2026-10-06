// SPDX-License-Identifier: MIT
/**
 * Two people side by side, from the project's own data: names and other names,
 * ORCID and records, identity and role, organisation, texts (how many, their
 * years, the most recent titles), affiliations with their years, co-authors in
 * the project; what they share; and the evidence that proposed the pair, each
 * line with its weight (+ for, − against).
 */
import { html } from '../../core/preact.js';
import { formatNumber, has, t } from '../../core/i18n.js';
import { IdentityState, roleLabel } from './common.js';

/** A line of evidence in words: the catalogue's, else the server's English. */
export function evidenceText(e) {
  const key = `corpus.dup.evidence.${e.code}`;
  return has(key) ? t(key, e.params || {}) : e.text || e.code;
}

const ROLE_RANK = ['mapped', 'context', 'projected', 'undecided', 'excluded', ''];

/** Whether the left side is the one to keep by default: its role first, then more texts. */
export function keepFirst(pair) {
  const [a, b] = pair.people;
  const ra = ROLE_RANK.indexOf(a.role);
  const rb = ROLE_RANK.indexOf(b.role);
  if (ra !== rb) return ra < rb;
  return (a.texts || 0) >= (b.texts || 0);
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

function Row({ label, a, b, values }) {
  const [x, y] = values || [a, b];
  return html`<tr><th scope="row">${label}</th><td>${x}</td><td>${y}</td></tr>`;
}

const dash = '—';

/** The comparison of *pair* (its brief from the list until *compare* is read). */
export function PairCompare({ pair, compare, openSheet }) {
  const [pa, pb] = pair.people;
  const sides = compare ? [compare.a, compare.b] : null;
  const name = (p) => html`<button type="button" class="cx-link-button cx-corpus-name"
    onClick=${() => openSheet(p.person_id)}>${p.name}</button>
    <code class="cx-corpus-muted cx-dup-id">${p.person_id}</code>`;
  const each = (fn) => (sides ? sides.map(fn) : [dash, dash]);
  const list = (items, render) => (items && items.length
    ? html`<ul class="cx-corpus-list">${items.map(render)}</ul>` : dash);
  const shared = compare && compare.shared;
  return html`<div class="cx-dup-compare">
    <header class="cx-corpus-panel__head">
      <h2 class="cx-corpus-panel__title">${t('corpus.dup.compare_title')}</h2>
      <p class="cx-corpus-muted">${t('corpus.dup.score', { score: Math.round(pair.score * 100) })}</p>
    </header>
    <table class="cx-corpus-simple cx-dup-table">
      <thead><tr><td></td><th scope="col">${name(pa)}</th><th scope="col">${name(pb)}</th></tr></thead>
      <tbody>
        <${Row} label=${t('corpus.sheet.aliases')} values=${each((s) => (s.aliases.length ? s.aliases.join(' · ') : dash))} />
        <${Row} label=${t('corpus.dup.orcid')} a=${pa.orcids.join(', ') || dash}
          b=${pb.orcids.join(', ') || dash} />
        <${Row} label=${t('corpus.sheet.records')} values=${each((s) => (s.records.length
            ? s.records.map((r) => html`<code key=${r}>${r}</code> `) : dash))} />
        <${Row} label=${t('corpus.col.identity')} a=${html`<${IdentityState} state=${pa.identity} />`}
          b=${html`<${IdentityState} state=${pb.identity} />`} />
        <${Row} label=${t('corpus.col.role')} a=${roleLabel(pa.role)} b=${roleLabel(pb.role)} />
        <${Row} label=${t('corpus.col.unit')} a=${pa.unit || dash} b=${pb.unit || dash} />
        <${Row} label=${t('corpus.col.texts')}
          a=${t('corpus.dup.texts', { n: pa.texts, years: years(pa.first_year, pa.last_year) })}
          b=${t('corpus.dup.texts', { n: pb.texts, years: years(pb.first_year, pb.last_year) })} />
        <${Row} label=${t('corpus.dup.recent')} values=${each((s) => list(s.recent, (r) => html`<li key=${r.text_id}>
            <span class="cx-corpus-muted">${r.year || dash}</span> ${r.title}</li>`))} />
        <${Row} label=${t('corpus.dup.affiliations')} values=${each((s) => list(s.affiliations.slice(0, 6), (r, i) => html`<li key=${`${r.org_id}-${i}`}>
            ${r.name} <span class="cx-corpus-muted">${span(r)}</span></li>`))} />
        <${Row} label=${t('corpus.dup.coauthors')} values=${each((s) => (s.coauthors.length ? s.coauthors.map((c) => t('corpus.dup.coauthor',
            { name: c.name, n: c.texts })).join(' · ') : dash))} />
      </tbody>
    </table>
    ${shared ? html`<section class="cx-dup-shared">
      <h3 class="cx-corpus-h3">${t('corpus.dup.shared')}</h3>
      <ul class="cx-corpus-list">
        <li>${t('corpus.dup.shared_texts', { n: shared.texts.length })}${shared.texts.length
          ? html`: ${shared.texts.slice(0, 3).map((x) => x.title).join(' · ')}` : null}</li>
        <li>${t('corpus.dup.shared_orgs', { n: shared.organisations.length })}${shared.organisations.length
          ? html`: ${shared.organisations.map((o) => o.name).join(' · ')}` : null}</li>
        <li>${t('corpus.dup.shared_coauthors', { n: shared.coauthors_total })}${shared.coauthors.length
          ? html`: ${shared.coauthors.map((c) => c.name).join(' · ')}` : null}</li>
      </ul></section>` : null}
    <section class="cx-dup-why">
      <h3 class="cx-corpus-h3">${t('corpus.dup.why')}</h3>
      <ul class="cx-corpus-list">${pair.evidence.map((e, i) => html`<li key=${i}
        class=${e.points < 0 ? 'cx-dup-against' : 'cx-dup-for'}>
        <span class="cx-dup-sign" aria-hidden="true">${e.points < 0 ? '−' : '+'}</span>
        <span class="cx-visually-hidden">${e.points < 0 ? t('corpus.dup.against') : t('corpus.dup.for')}</span>
        ${evidenceText(e)} <span class="cx-corpus-muted">${t('corpus.identities.points',
          { points: formatNumber(Math.abs(e.points), { maximumFractionDigits: 1 }) })}</span></li>`)}</ul>
    </section>
  </div>`;
}
