// SPDX-License-Identifier: MIT
/**
 * Importing: a list of people (a CSV file or a pasted list) with its column
 * mapping proposed and editable, one field per column; a folder of documents
 * (a zip, or one document, for everyone or for one person); a corpus (a zip
 * holding its index). After a list, the possible duplicates are proposed, each
 * with « Merge » (nothing is merged unless asked).
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatNumber, has, t } from '../../core/i18n.js';
import {
  Button, Checkbox, Dialog, ErrorCard, FormField, Input, Select, Tabs, Textarea,
} from '../../components/index.js';
import { roleLabel } from './common.js';

const ROLES = ['mapped', 'context', 'projected', 'excluded', 'undecided'];

function fieldLabel(field, levels) {
  if (field.startsWith('org:')) {
    const id = field.slice(4);
    const level = (levels || []).find((l) => l.id === id);
    const name = level && Object.values(level.names || {})[0];
    return t('corpus.field.org', { level: name || id });
  }
  return has(`corpus.field.${field}`) ? t(`corpus.field.${field}`) : field;
}

/** The mapping of a proposed list: one field per column, with the first values. */
function Mapping({ proposal, mapping, setMapping }) {
  const values = (i) => proposal.preview.slice(0, 3).map((row) => row[i]).filter(Boolean).join(' · ');
  return html`<table class="cx-corpus-simple cx-corpus-mapping">
    <caption class="cx-visually-hidden">${t('corpus.import.mapping')}</caption>
    <thead><tr><th scope="col">${t('corpus.import.column')}</th><th scope="col">${t('corpus.import.values')}</th>
      <th scope="col">${t('corpus.import.field')}</th></tr></thead>
    <tbody>${proposal.columns.map((column, i) => {
      const refused = proposal.refused[column];
      return html`<tr key=${column}>
        <th scope="row">${column}</th>
        <td class="cx-corpus-muted">${values(i)}</td>
        <td>${refused ? html`<span class="cx-corpus-refused">${t(`corpus.import.${refused.code}`)}</span>`
          : html`<label><span class="cx-visually-hidden">${t('corpus.import.field_of', { column })}</span>
          <${Select} value=${mapping[column] || 'ignore'}
            onChange=${(e) => setMapping({ ...mapping, [column]: e.currentTarget.value })}
            options=${proposal.fields.map((f) => ({ value: f, label: fieldLabel(f, proposal.levels) }))} /></label>`}
        </td></tr>`;
    })}</tbody>
  </table>`;
}

/** What an import did, and its possible duplicates to merge. */
function Result({ ctx, result, onMerged, onReview }) {
  const [merged, setMerged] = useState(new Set());
  const [error, setError] = useState(null);
  async function merge(d) {
    const people = await ctx.api.get('/api/people', { query: { limit: 1 } });
    const r = await ctx.api.post('/api/people/merge', { target: d.person_id, sources: [d.other_id] },
      { ifMatch: people.etag });
    if (!r.ok) {
      setError(r.error);
      return;
    }
    setMerged(new Set([...merged, `${d.person_id}|${d.other_id}`]));
    onMerged();
  }
  return html`<div class="cx-corpus-result" role="status">
    <p><strong>${t('corpus.import.added', { n: result.added, known: result.already_known })}</strong></p>
    ${result.skipped.length ? html`<details class="cx-corpus-part"><summary>${t('corpus.import.skipped', { n: result.skipped.length })}</summary>
      <ul class="cx-corpus-list">${result.skipped.slice(0, 50).map((s, i) => html`<li key=${i}>${s}</li>`)}</ul></details>` : null}
    ${error ? html`<${ErrorCard} error=${error} compact onDismiss=${() => setError(null)} />` : null}
    ${result.duplicates.length && onReview ? html`<p class="cx-corpus-note" role="note">
      ${t('corpus.dup.after_import', { n: result.duplicates.length })}
      <${Button} size="s" variant="ghost" onClick=${onReview}>${t('corpus.dup.review')}<//></p>` : null}
    ${result.duplicates.length ? html`<h3 class="cx-corpus-h3">${t('corpus.import.duplicates', { n: result.duplicates.length })}</h3>
      <ul class="cx-corpus-dups">${result.duplicates.map((d) => {
        const key = `${d.person_id}|${d.other_id}`;
        return html`<li key=${key}>
          <span>${t('corpus.import.pair', { a: d.names[0], b: d.names[1] })}</span>
          <span class="cx-corpus-muted"> ${d.reason}</span>
          ${merged.has(key) ? html`<span class="cx-corpus-chip">${t('corpus.import.merged')}</span>`
            : html`<${Button} size="s" onClick=${() => merge(d)}>${t('corpus.import.merge')}<//>`}
        </li>`;
      })}</ul>` : null}
  </div>`;
}

/** The import dialog: a list, documents or a corpus (*mode*); *person*: documents for one person. */
export function ImportDialog({ ctx, mode: initial, person, onClose, onStarted, openTab }) {
  const [mode, setMode] = useState(initial || 'list');
  const [text, setText] = useState('');
  const [file, setFile] = useState(null);
  const [proposal, setProposal] = useState(null);
  const [mapping, setMapping] = useState({});
  const [role, setRole] = useState('mapped');
  const [people, setPeople] = useState(null); // {etag, sets}
  const [set, setSet] = useState('');
  const [create, setCreate] = useState(false);
  const [result, setResult] = useState(null);
  const [changed, setChanged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    ctx.api.get('/api/people', { query: { limit: 1 } }).then((r) => {
      if (r.ok) {
        setPeople({ etag: r.etag, sets: r.data.sets || [] });
        setSet((r.data.sets || [])[0] || '');
      }
    });
  }, [changed]);

  const close = () => {
    if (proposal && !result) ctx.api.delete(`/api/people/import/${proposal.import_id}`);
    onClose(changed);
  };

  async function propose() {
    setBusy(true);
    setError(null);
    let body;
    if (file) {
      body = new FormData();
      body.append('file', file, file.name);
    } else body = { text };
    const r = await ctx.api.post('/api/people/import', body);
    setBusy(false);
    if (!r.ok) {
      setError(r.error);
      return;
    }
    setProposal(r.data);
    setMapping(r.data.mapping);
  }

  async function confirm() {
    setBusy(true);
    setError(null);
    const r = await ctx.api.post(`/api/people/import/${proposal.import_id}/confirm`,
      { mapping, role, set: role === 'projected' ? set : '' }, { ifMatch: people && people.etag });
    setBusy(false);
    if (!r.ok) {
      setError(r.error);
      return;
    }
    setResult(r.data);
    setChanged(true);
  }

  async function sendDocuments() {
    setBusy(true);
    setError(null);
    const body = new FormData();
    body.append('file', file, file.name);
    body.append('kind', mode);
    if (person) body.append('person_id', person.person_id);
    if (create) body.append('create_people', 'true');
    const r = await ctx.api.post('/api/people/import/documents', body);
    setBusy(false);
    if (!r.ok) {
      setError(r.error);
      return;
    }
    onStarted(r.data.job);
    onClose(false);
  }

  const fileField = (accept, help) => html`<${FormField} label=${t('corpus.import.file')} help=${help}>
    ${(f) => html`<input ...${f} type="file" class="cx-input cx-corpus-file" accept=${accept}
      onChange=${(e) => setFile(e.currentTarget.files[0] || null)} />`}<//>`;

  let body;
  let footer;
  if (mode === 'list' && result) {
    body = html`<${Result} ctx=${ctx} result=${result} onMerged=${() => setChanged(true)}
      onReview=${openTab ? () => { onClose(true); openTab('duplicates'); } : null} />`;
    footer = html`<${Button} variant="primary" onClick=${close}>${t('common.close')}<//>`;
  } else if (mode === 'list' && proposal) {
    body = html`<p>${t('corpus.import.read', { rows: formatNumber(proposal.rows), columns: proposal.columns.length })}</p>
      <${Mapping} proposal=${proposal} mapping=${mapping} setMapping=${setMapping} />
      <div class="cx-corpus-two">
        <${FormField} label=${t('corpus.import.role')} help=${t('corpus.import.role_help')}>
          ${(f) => html`<${Select} ...${f} value=${role} onChange=${(e) => setRole(e.currentTarget.value)}
            options=${ROLES.map((r) => ({ value: r, label: roleLabel(r) }))} />`}<//>
        ${role === 'projected' ? html`<${FormField} label=${t('corpus.import.set')}>
          ${(f) => (people && people.sets.length ? html`<${Select} ...${f} value=${set}
            onChange=${(e) => setSet(e.currentTarget.value)}
            options=${people.sets.map((s) => ({ value: s, label: s }))} />`
            : html`<${Input} ...${f} value=${set} onInput=${(e) => setSet(e.currentTarget.value)} />`)}<//>` : null}
      </div>`;
    footer = html`<${Button} onClick=${() => setProposal(null)}>${t('corpus.collect.back')}<//>
      <${Button} variant="primary" loading=${busy} onClick=${confirm}>
        ${t('corpus.import.confirm', { n: proposal.rows })}<//>`;
  } else if (mode === 'list') {
    body = html`<p>${t('corpus.import.list_lead')}</p>
      ${fileField('.csv,.tsv,.txt', t('corpus.import.file_help'))}
      <${FormField} label=${t('corpus.import.paste')} help=${t('corpus.import.paste_help')}>
        ${(f) => html`<${Textarea} ...${f} rows=${8} value=${text} disabled=${Boolean(file)}
          onInput=${(e) => setText(e.currentTarget.value)} />`}<//>`;
    footer = html`<${Button} onClick=${close}>${t('common.cancel')}<//>
      <${Button} variant="primary" loading=${busy} disabled=${!file && !text.trim()} onClick=${propose}>
        ${t('corpus.import.read_list')}<//>`;
  } else {
    body = html`<p>${t(mode === 'corpus' ? 'corpus.import.corpus_lead' : 'corpus.import.folder_lead')}</p>
      ${person ? html`<p class="cx-corpus-note" role="note">${t('corpus.import.for_person', { name: person.name })}</p>` : null}
      ${fileField(mode === 'corpus' ? '.zip' : '.zip,.pdf,.txt,.md',
        t(mode === 'corpus' ? 'corpus.import.corpus_help' : 'corpus.import.folder_help'))}
      ${mode === 'folder' && !person ? html`<${Checkbox} checked=${create} label=${t('corpus.import.create_people')}
        onChange=${(e) => setCreate(e.currentTarget.checked)} />` : null}`;
    footer = html`<${Button} onClick=${close}>${t('common.cancel')}<//>
      <${Button} variant="primary" loading=${busy} disabled=${!file} onClick=${sendDocuments}>
        ${t('corpus.import.send')}<//>`;
  }

  const modes = ['list', 'folder', 'corpus'].map((id) => ({ id, label: t(`corpus.import.${id}`),
    disabled: Boolean(person) && id !== 'folder' }));
  return html`<${Dialog} open=${true} onClose=${close} size="l" title=${t('corpus.import.title')}
    footer=${footer}>
    ${error ? html`<${ErrorCard} error=${error} compact onDismiss=${() => setError(null)} />` : null}
    ${proposal || result ? body : html`<${Tabs} tabs=${modes} selected=${mode} label=${t('corpus.import.title')}
      onSelect=${(id) => { setMode(id); setFile(null); }} panel=${() => body} />`}
  <//>`;
}
