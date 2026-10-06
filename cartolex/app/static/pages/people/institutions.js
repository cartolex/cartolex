// SPDX-License-Identifier: MIT
/**
 * The people of institutions, on the Organisations tab: a search by name, or an
 * institution named by its OpenAlex id, its ROR id or a link holding one; the
 * institutions found (ROR, acronym, city and country, parents); then the latest
 * proposal of their people, paged and searched on the server, and the records
 * that may be one person, each with what tells them apart and « Take as one
 * person ». « Take everyone » asks the role and the levels, takes each clear
 * pair (the same ORCID) as one person and asks about the other pairs.
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatNumber, t } from '../../core/i18n.js';
import {
  Button, Card, Checkbox, Dialog, ErrorCard, Input, MenuButton, Select, Table,
} from '../../components/index.js';
import { roleLabel, usePaged } from './common.js';

/** An institution named by an id or a link (OpenAlex `I…`, ROR `0…`), else null. */
export function institutionRef(text) {
  const value = (text || '').trim();
  if (/ror\.org\/|^ror:/i.test(value) || /^0[0-9a-hjkmnp-tv-z]{6}\d{2}$/i.test(value)) return value;
  if (/(^|\/)I\d{2,}$/i.test(value)) return value;
  return null;
}

function years(first, last) {
  if (!first && !last) return t('corpus.years.unknown');
  return t('corpus.years.span', { first: first || '…', last: last || '…' });
}

/** One record of a pair: what tells it apart. */
function Record({ person }) {
  return html`<div class="cx-inst-record">
    <span class="cx-corpus-name">${person.name}</span>
    <span class="cx-corpus-muted"> ${[person.orcid && `ORCID ${person.orcid}`,
      t('corpus.institutions.works', { n: person.works }), years(person.first_year, person.last_year)]
      .filter(Boolean).join(' · ')}</span>
    <code class="cx-corpus-cand__record"> ${person.record}</code>
    ${person.units.length ? html`<p class="cx-corpus-muted">${person.units.slice(0, 3).map((u) =>
      t('corpus.institutions.unit', { name: u.name, n: u.works })).join(' · ')}</p>` : null}
  </div>`;
}

/** Take everyone: the role, the levels, the pairs. */
function TakeAll({ proposal, levels, onTake, onClose, busy }) {
  const [role, setRole] = useState('mapped');
  const [mapping, setMapping] = useState({ ...(proposal.levels || {}) });
  const [joined, setJoined] = useState(new Set());
  const clear = proposal.merges.filter((m) => m.clear && !m.taken.some(Boolean));
  const others = proposal.merges.filter((m) => !m.clear && !m.taken.some(Boolean));
  const options = [...new Set([...levels, ...Object.values(proposal.levels || {})])].filter(Boolean);
  const keyOf = (m) => m.records.join('+');
  const submit = () => onTake({
    role,
    levels: mapping,
    join: [...clear, ...others.filter((m) => joined.has(keyOf(m)))].map((m) => m.records),
  });
  return html`<${Dialog} open=${true} onClose=${onClose} size="l" title=${t('corpus.institutions.take_title')}
    footer=${html`<${Button} onClick=${onClose}>${t('common.cancel')}<//>
      <${Button} variant="primary" loading=${busy} onClick=${submit}>
        ${t('corpus.institutions.take_all', { n: proposal.total - proposal.already })}<//>`}>
    <div class="cx-corpus-form">
      <label class="cx-field__label">${t('corpus.col.role')}
        <${Select} value=${role} onChange=${(e) => setRole(e.currentTarget.value)}
          options=${['mapped', 'context', 'projected'].map((r) => ({ value: r, label: roleLabel(r) }))} /></label>
      ${Object.keys(mapping).length ? html`<fieldset class="cx-corpus-fieldset">
        <legend>${t('corpus.institutions.levels')}</legend>
        ${Object.keys(mapping).sort().map((type) => html`<label class="cx-field__label" key=${type}>
          ${t('corpus.institutions.level_of', { type: type || '—' })}
          <${Select} value=${mapping[type]} onChange=${(e) => setMapping({ ...mapping, [type]: e.currentTarget.value })}
            options=${options.map((lv) => ({ value: lv, label: lv }))} /></label>`)}
      </fieldset>` : null}
      ${clear.length ? html`<p>${t('corpus.institutions.clear_pairs', { n: clear.length })}</p>` : null}
      ${others.length ? html`<fieldset class="cx-corpus-fieldset">
        <legend>${t('corpus.institutions.ask_pairs', { n: others.length })}</legend>
        <ul class="cx-corpus-list">${others.map((m) => html`<li key=${keyOf(m)} class="cx-inst-pair">
          <${Checkbox} checked=${joined.has(keyOf(m))} label=${t('corpus.institutions.one_person')}
            onChange=${() => {
              const next = new Set(joined);
              if (next.has(keyOf(m))) next.delete(keyOf(m));
              else next.add(keyOf(m));
              setJoined(next);
            }} />
          <span class="cx-corpus-muted">${t('corpus.institutions.pair_reason', { reason: m.reason, n: m.works })}</span>
          ${m.people.map((p) => html`<${Record} key=${p.record} person=${p} />`)}</li>`)}</ul>
      </fieldset>` : null}
    </div>
  <//>`;
}

/** The people of institutions: the latest search and the latest proposal. */
export function Institutions({ ctx, version, bump, toast, openCollect, canCollect, levels, openTab }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [name, setName] = useState('');
  const [chosen, setChosen] = useState(new Set());
  const [picked, setPicked] = useState(new Set());
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);
  const [taking, setTaking] = useState(false);
  const [taken, setTaken] = useState(null); // after a take: how many, and the duplicates to review
  const load = () => ctx.api.get('/api/collection/institutions', { query: { limit: 1 } })
    .then((r) => (r.ok ? setData({ ...r.data, etag: r.etag }) : setError(r.error)));
  useEffect(() => { load(); }, [version]);
  const list = usePaged(ctx, '/api/collection/institutions/people', { q: q || undefined, $v: version },
    (p) => p.record);

  async function take(what, role, extra = {}) {
    setBusy(true);
    setError(null);
    const people = await ctx.api.get('/api/people', { query: { limit: 1 } });
    const result = await ctx.api.post('/api/collection/institutions/take', { take: what, role, ...extra },
      { ifMatch: people.etag });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setTaking(false);
    setPicked(new Set());
    const found = await ctx.api.get('/api/people/duplicates', { query: { limit: 1 } });
    setTaken({ n: result.data.taken.length, duplicates: found.ok ? found.data.counts.open : 0 });
    toast({ kind: 'success', title: t('corpus.institutions.taken', { n: result.data.taken.length }) });
    bump();
  }

  const search = data && data.search;
  const proposal = data && data.proposal;
  const ref = institutionRef(name);
  const roles = ['mapped', 'context', 'projected'].map((r) => ({ id: r, label: roleLabel(r) }));
  return html`<${Card} title=${t('corpus.institutions.title')} level=${2} class="cx-corpus-card">
    ${error ? html`<${ErrorCard} error=${error} compact onDismiss=${() => setError(null)} />` : null}
    ${canCollect ? html`<form class="cx-corpus-paste__row" onSubmit=${(e) => {
      e.preventDefault();
      if (ref) openCollect('institutions', { institutions: [ref] });
      else if (name.trim()) openCollect('institutions', { search: name.trim() });
    }}>
      <label class="cx-visually-hidden" for="cx-corpus-inst">${t('corpus.institutions.search')}</label>
      <${Input} id="cx-corpus-inst" value=${name} placeholder=${t('corpus.institutions.search_or_id')}
        onInput=${(e) => setName(e.currentTarget.value)} />
      <${Button} type="submit" icon="search" disabled=${!name.trim()}>
        ${ref ? t('corpus.institutions.read_one') : t('corpus.institutions.find')}<//>
    </form>` : null}
    ${taken ? html`<p class="cx-corpus-note" role="status">${t('corpus.institutions.taken', { n: taken.n })}
      ${taken.duplicates ? html` ${t('corpus.dup.after_take', { n: taken.duplicates })}
        <${Button} size="s" variant="ghost" onClick=${() => openTab('duplicates')}>${t('corpus.dup.review')}<//>` : null}</p>` : null}
    ${search ? html`<fieldset class="cx-corpus-fieldset">
      <legend>${t('corpus.institutions.found', { n: search.institutions.length, name: search.search })}</legend>
      <ul class="cx-corpus-checks">${search.institutions.map((i) => html`<li key=${i.id}>
        <${Checkbox} checked=${chosen.has(i.id)} onChange=${() => {
          const next = new Set(chosen);
          if (next.has(i.id)) next.delete(i.id);
          else next.add(i.id);
          setChosen(next);
        }} label=${t('corpus.institutions.item', { name: i.name, type: i.type || '—',
          works: formatNumber(i.works_count), country: i.country || '—' })} />
        <span class="cx-corpus-muted cx-inst-facts">${[i.acronym, [i.city, i.country].filter(Boolean).join(', '),
          i.ror && `ROR ${i.ror}`].filter(Boolean).join(' · ')}</span>
        ${i.parents.length ? html`<span class="cx-corpus-muted"> ${t('corpus.institutions.part_of', { parents: i.parents.join(', ') })}</span>` : null}
      </li>`)}</ul>
      <${Button} disabled=${!chosen.size} onClick=${() => openCollect('institutions', { institutions: [...chosen] })}>
        ${t('corpus.institutions.read', { n: chosen.size })}<//>
    </fieldset>` : null}
    ${proposal ? html`<section class="cx-corpus-proposal">
      <h3 class="cx-corpus-h3">${t('corpus.institutions.proposal', {
        roots: proposal.roots.map((r) => r.name).join(', '), n: proposal.total,
        works: formatNumber(proposal.works_read), min: proposal.min_works })}</h3>
      <div class="cx-corpus-bulk">
        <${Button} size="s" variant="primary" onClick=${() => setTaking(true)}
          disabled=${proposal.total - proposal.already <= 0}>
          ${t('corpus.institutions.take_everyone', { n: proposal.total - proposal.already })}<//>
        ${picked.size ? html`<${MenuButton} label=${t('corpus.institutions.take_some', { n: picked.size })}
          size="s" items=${roles} onSelect=${(item) => take([...picked], item.id)} />` : null}
        <label class="cx-corpus-filters__search"><span class="cx-visually-hidden">${t('corpus.institutions.people_search')}</span>
          <${Input} type="search" value=${q} placeholder=${t('corpus.institutions.people_search')}
            onInput=${(e) => setQ(e.currentTarget.value)} /></label>
        ${busy ? html`<span class="cx-spinner" aria-hidden="true"></span>` : null}
      </div>
      <${Table} size="m" label=${t('corpus.institutions.people')} rowKey=${list.rowKey}
        rows=${list.rows} selection=${picked} onSelectionChange=${setPicked} onRange=${list.onRange}
        loading=${list.loading} error=${list.error} onRetry=${list.reload} sortMode="server"
        columns=${[
          { id: 'name', label: t('corpus.col.name'), width: 'minmax(10rem, 2fr)' },
          { id: 'works', label: t('corpus.col.works'), numeric: true, width: '6rem' },
          { id: 'years', label: t('corpus.col.years'), width: '8rem',
            render: (p) => years(p.first_year, p.last_year) },
          { id: 'units', label: t('corpus.col.units'), width: 'minmax(10rem, 2fr)',
            render: (p) => p.units.slice(0, 2).map((u) => u.name).join(' · ') },
          { id: 'person_id', label: t('corpus.col.already'), width: '7rem',
            render: (p) => (p.person_id ? t('corpus.institutions.already') : '') },
        ]} />
      ${proposal.merges.length ? html`<details class="cx-corpus-part">
        <summary>${t('corpus.institutions.merges', { n: proposal.merges.length })}</summary>
        <ul class="cx-corpus-list cx-inst-pairs">${proposal.merges.map((m) => html`<li key=${m.records.join('+')} class="cx-inst-pair">
          <p>${m.clear ? html`<span class="cx-corpus-chip">${t('corpus.dup.clear')}</span> ` : null}
            <span class="cx-corpus-muted">${t('corpus.institutions.pair_reason', { reason: m.reason, n: m.works })}</span></p>
          ${m.people.map((p) => html`<${Record} key=${p.record} person=${p} />`)}
          ${m.taken.some(Boolean) ? html`<p class="cx-corpus-muted">${t('corpus.institutions.already')}</p>`
            : html`<${MenuButton} size="s" label=${t('corpus.institutions.take_one')} items=${roles}
              onSelect=${(item) => take([m.records.map((r) => r.split(':').pop()).join('+')], item.id)} />`}
        </li>`)}</ul></details>` : null}
    </section>` : !search ? html`<p class="cx-corpus-muted">${t('corpus.institutions.none')}</p>` : null}
    ${taking && proposal ? html`<${TakeAll} proposal=${proposal} levels=${levels || []} busy=${busy}
      onClose=${() => setTaking(false)}
      onTake=${({ role, levels: mapping, join }) => take(['all'], role, { join, levels: mapping })} />` : null}
  <//>`;
}
