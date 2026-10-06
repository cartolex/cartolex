// SPDX-License-Identifier: MIT
/**
 * One organisation, in a drawer: its parents, units and people, and what people
 * decide about it (`decisions/organisations.csv`): its name, its level, its
 * parents, a merge into another organisation, the organisations merged into it
 * (each merge can be undone). Its ROR and OpenAlex ids, and « Show on the map ».
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import {
  Button, ConfirmDialog, Drawer, ErrorCard, IconButton, Input, Select,
} from '../../components/index.js';

function years(a) {
  if (!a.start_year && !a.end_year) return t('corpus.years.unknown');
  if (!a.end_year) return t('corpus.years.since', { first: a.start_year });
  return t('corpus.years.span', { first: a.start_year || '…', last: a.end_year });
}

/** Organisations found by a few letters of a name, an acronym or an identifier. */
export function OrgSearch({ ctx, label, exclude = [], onPick }) {
  const [q, setQ] = useState('');
  const [found, setFound] = useState([]);
  useEffect(() => {
    const text = q.trim();
    if (text.length < 2) {
      setFound([]);
      return undefined;
    }
    const timer = setTimeout(() => {
      ctx.api.get('/api/organisations', { query: { q: text, limit: 8, sort: '-people_ever' } })
        .then((r) => r.ok && setFound(r.data.items.filter((o) => !exclude.includes(o.org_id))));
    }, 200);
    return () => clearTimeout(timer);
  }, [q]);
  return html`<div class="cx-org-search">
    <label class="cx-field__label">${label}
      <${Input} type="search" value=${q} placeholder=${t('corpus.orgs.search')}
        onInput=${(e) => setQ(e.currentTarget.value)} /></label>
    ${found.length ? html`<ul class="cx-corpus-list cx-org-search__found">${found.map((o) => html`<li key=${o.org_id}>
      <button type="button" class="cx-link-button" onClick=${() => { setQ(''); onPick(o); }}>
        ${o.name}</button>
      <span class="cx-corpus-muted"> ${[o.acronym, o.level, o.parent_names.join(', '), o.country]
        .filter(Boolean).join(' · ')}</span></li>`)}</ul>` : null}
  </div>`;
}

/** One organisation, in a drawer; *levels*: the levels an organisation can be set at. */
export function OrganisationDrawer({ ctx, orgId, onClose, openSheet, onOpen, levels, toast, bump,
  showOnMap }) {
  const [org, setOrg] = useState(null);
  const [etag, setEtag] = useState(null);
  const [error, setError] = useState(null);
  const [name, setName] = useState('');
  const [merging, setMerging] = useState(null); // the organisation to merge into, to confirm
  const load = () => ctx.api.get(`/api/organisations/${encodeURIComponent(orgId)}`).then((r) => {
    if (r.ok) {
      setOrg(r.data);
      setEtag(r.etag);
      setName(r.data.name || '');
    } else setError(r.error);
  });
  useEffect(() => {
    setOrg(null);
    load();
  }, [orgId]);

  async function write(path, body, done, method = 'post') {
    setError(null);
    const result = await ctx.api[method](path, body, { ifMatch: etag });
    if (!result.ok) {
      setError(result.error);
      return;
    }
    if (done) toast({ kind: 'success', title: done });
    bump();
    load();
  }
  const edit = (body, done) => write(`/api/organisations/${encodeURIComponent(orgId)}`, body, done,
    'patch');

  const merged = org && org.merged_into;
  const decided = (org && org.decided) || {};
  const source = (org && org.source_values) || {};
  const levelOptions = [...new Set([...(levels || []), org && org.level].filter(Boolean))];
  return html`<${Drawer} open=${true} onClose=${onClose} size="l"
    title=${org ? org.name : t('common.loading')}
    description=${org && !merged ? [org.acronym, org.level, org.country].filter(Boolean).join(' · ') : null}>
    ${error ? html`<${ErrorCard} error=${error} compact onDismiss=${() => setError(null)} />` : null}
    ${merged ? html`<section class="cx-corpus-verdict">
      <p>${t('corpus.orgs.merged_into', { name: merged.name })}</p>
      <div class="cx-corpus-actions-row">
        <${Button} size="s" onClick=${() => onOpen(merged.org_id)}>${t('corpus.orgs.open_merged_into')}<//>
      </div></section>` : null}
    ${org && !merged ? html`<div class="cx-corpus-sheet">
      <div class="cx-corpus-actions-row">
        ${showOnMap ? html`<${Button} size="s" variant="ghost"
          onClick=${() => showOnMap('organisation', orgId)}>${t('corpus.show_on_map')}<//>` : null}
      </div>
      <dl class="cx-corpus-facts">
        ${Object.entries(org.ids || {}).map(([k, v]) => html`<div class="cx-corpus-fact" key=${k}>
          <dt>${k.toUpperCase()}</dt><dd><code>${v}</code></dd></div>`)}
        <div class="cx-corpus-fact"><dt>${t('corpus.col.source')}</dt><dd>${org.source}</dd></div>
      </dl>
      <section class="cx-org-edit" aria-label=${t('corpus.orgs.decide')}>
        <h3 class="cx-corpus-h3">${t('corpus.orgs.decide')}</h3>
        <form class="cx-corpus-paste__row" onSubmit=${(e) => {
          e.preventDefault();
          edit({ name: name.trim() === source.name ? '' : name.trim() },
            t('corpus.orgs.renamed', { name: name.trim() }));
        }}>
          <label class="cx-visually-hidden" for="cx-org-name">${t('corpus.orgs.name')}</label>
          <${Input} id="cx-org-name" value=${name} onInput=${(e) => setName(e.currentTarget.value)} />
          <${Button} type="submit" size="s" disabled=${!name.trim() || name.trim() === org.name}>
            ${t('corpus.orgs.rename')}<//>
          ${decided.name ? html`<${Button} size="s" variant="ghost" onClick=${() => edit({ name: '' })}>
            ${t('corpus.orgs.back_to_source', { value: source.name })}<//>` : null}
        </form>
        <label class="cx-field__label">${t('corpus.col.level')}
          <${Select} value=${org.level || ''} onChange=${(e) => edit({ level: e.currentTarget.value },
            t('corpus.orgs.level_set', { level: e.currentTarget.value || '—' }))}
            options=${[{ value: '', label: t('corpus.orgs.level_source', { level: source.level || '—' }) },
              ...levelOptions.map((lv) => ({ value: lv, label: lv }))]} /></label>
        <div>
          <p class="cx-field__label">${t('corpus.orgs.parents')}</p>
          <ul class="cx-corpus-list">${org.parents.map((p) => html`<li key=${p.org_id} class="cx-org-parent">
            <button type="button" class="cx-link-button" onClick=${() => onOpen(p.org_id)}>${p.name}</button>
            <${IconButton} icon="close" size="s" label=${t('corpus.orgs.parent_remove', { name: p.name })}
              onClick=${() => edit({ parents: org.parents.filter((x) => x.org_id !== p.org_id).map((x) => x.org_id) })} />
          </li>`)}</ul>
          <${OrgSearch} ctx=${ctx} label=${t('corpus.orgs.parent_add')}
            exclude=${[orgId, ...org.parents.map((p) => p.org_id)]}
            onPick=${(o) => edit({ parents: [...org.parents.map((x) => x.org_id), o.org_id] },
              t('corpus.orgs.parent_added', { name: o.name }))} />
          ${decided.parents ? html`<${Button} size="s" variant="ghost" onClick=${() => edit({ parents: [] })}>
            ${t('corpus.orgs.parents_back')}<//>` : null}
        </div>
        <${OrgSearch} ctx=${ctx} label=${t('corpus.orgs.merge_into')} exclude=${[orgId]}
          onPick=${(o) => setMerging(o)} />
      </section>
      ${org.merged_from.length ? html`<section><h3 class="cx-corpus-h3">${t('corpus.orgs.merged_here',
        { n: org.merged_from.length })}</h3>
        <ul class="cx-corpus-list">${org.merged_from.map((m) => html`<li key=${m.org_id} class="cx-org-parent">
          <span>${m.name}</span>
          <${Button} size="s" variant="ghost" onClick=${() => write('/api/organisations/unmerge',
            { org_ids: [m.org_id], remember: 'distinct' }, t('corpus.orgs.unmerged', { name: m.name }))}>
            ${t('corpus.sheet.unmerge')}<//></li>`)}</ul></section>` : null}
      ${org.units.length ? html`<section><h3 class="cx-corpus-h3">${t('corpus.orgs.units', { n: org.units.length })}</h3>
        <ul class="cx-corpus-links">${org.units.slice(0, 100).map((u) => html`<li key=${u.org_id}>
          <button type="button" class="cx-link-button" onClick=${() => onOpen(u.org_id)}>${u.name}</button>
          <span class="cx-corpus-muted"> ${u.level}</span></li>`)}</ul>
      </section>` : null}
      <section><h3 class="cx-corpus-h3">${t('corpus.orgs.affiliations', { n: org.affiliations.length })}</h3>
        <table class="cx-corpus-simple">
          <thead><tr><th scope="col">${t('corpus.col.name')}</th><th scope="col">${t('corpus.col.years')}</th>
            <th scope="col">${t('corpus.col.source')}</th></tr></thead>
          <tbody>${org.affiliations.slice(0, 500).map((a, i) => html`<tr key=${`${a.person_id}-${i}`}>
            <td><button type="button" class="cx-link-button" onClick=${() => openSheet(a.person_id)}>${a.name}</button></td>
            <td>${years(a)}</td><td>${a.source}</td></tr>`)}</tbody>
        </table>
      </section>
    </div>` : null}
    ${merging ? html`<${ConfirmDialog} open=${true} title=${t('corpus.orgs.merge_confirm_title')}
      confirmLabel=${t('corpus.orgs.merge_confirm')}
      onAnswer=${(yes) => {
        const target = merging;
        setMerging(null);
        if (yes) {
          write('/api/organisations/merge', { target: target.org_id, sources: [orgId] },
            t('corpus.orgs.merged', { name: target.name })).then(() => onOpen(target.org_id));
        }
      }}>${t('corpus.orgs.merge_confirm_text', { a: org ? org.name : '', b: merging.name })}<//>` : null}
  <//>`;
}
