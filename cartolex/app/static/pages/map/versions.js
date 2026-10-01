// SPDX-License-Identifier: MIT
/**
 * Map versions and base maps, in one dialog (read when it opens):
 *
 * - the versions, newest first: how each was drawn (method, seed, note),
 *   the pinned one, the one the map shows; pin one (the map is redrawn with
 *   it) or discard one nobody pinned;
 * - « Try another layout »: another seed or another method (UMAP, t-SNE when
 *   installed, the theme tree) as a new version, pinned and drawn, so the map
 *   shows it; pinning the earlier version brings the earlier map back;
 * - base maps: another project's map copied into this one, on which this
 *   project's people and keywords are placed by the keywords both share;
 *   show the project on one, remove one, add one from a project's folder.
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatDate, formatNumber, t } from '../../core/i18n.js';
import {
  Button, Dialog, ErrorCard, FormField, Input, Select, StatusPill,
} from '../../components/index.js';

function methodName(method) {
  return t(`map.method.${method}`);
}

/** The dialog. */
export function VersionsDialog({ ctx, open, onClose, drawn, base, onBase, onBuild }) {
  const [versions, setVersions] = useState(null);
  const [bases, setBases] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState('');
  const [method, setMethod] = useState('');
  const [seed, setSeed] = useState('');
  const [note, setNote] = useState('');
  const [folder, setFolder] = useState('');
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!open) return;
    setError(null);
    ctx.api.get('/api/map/versions').then((r) => {
      if (!r.ok) setError(r.error);
      else {
        setVersions({ ...r.data, etag: r.etag });
        if (!method) setMethod(r.data.methods.find((m) => !(m in (r.data.unavailable || {}))));
      }
    });
    ctx.api.get('/api/map/bases').then((r) => {
      if (r.ok) setBases({ ...r.data, etag: r.etag });
    });
  }, [open, tick]);

  const act = async (label, body) => {
    setBusy(label);
    const r = await ctx.api.post('/api/map/versions', body, { ifMatch: versions.etag });
    setBusy('');
    if (!r.ok) {
      setError(r.error);
      setTick((n) => n + 1);
      return null;
    }
    setVersions({ ...r.data, etag: r.etag });
    if (r.data.job) onBuild(r.data.job);
    return r;
  };
  const tryAnother = async () => {
    const body = { action: 'try', method, note };
    if (seed.trim()) body.seed = Number.parseInt(seed, 10);
    const tried = await act('try', body);
    if (!tried) return;
    const added = tried.data.done.split(' ').pop();
    const pinned = await ctx.api.post('/api/map/versions', { action: 'pin', version: added, build: true },
      { ifMatch: tried.etag });
    if (!pinned.ok) setError(pinned.error);
    else {
      setVersions({ ...pinned.data, etag: pinned.etag });
      if (pinned.data.job) onBuild(pinned.data.job);
      setNote('');
      setSeed('');
    }
  };
  const addBase = async () => {
    setBusy('base');
    const r = await ctx.api.post('/api/map/bases', { folder }, { ifMatch: bases.etag });
    setBusy('');
    if (!r.ok) {
      setError(r.error);
      return;
    }
    setBases({ ...r.data, etag: r.etag });
    setFolder('');
  };
  const removeBase = async (id) => {
    setBusy(`remove:${id}`);
    const r = await ctx.api.delete(`/api/map/bases/${encodeURIComponent(id)}`, { ifMatch: bases.etag });
    setBusy('');
    if (!r.ok) setError(r.error);
    else {
      setBases({ ...r.data, etag: r.etag });
      if (base === id) onBase('');
    }
  };

  const list = versions ? versions.versions : [];
  return html`<${Dialog} open=${open} onClose=${onClose} size="l" title=${t('map.versions.title')}
    description=${t('map.versions.lead')}
    footer=${html`<${Button} onClick=${onClose}>${t('common.close')}<//>`}>
    ${error ? html`<${ErrorCard} error=${error} compact onDismiss=${() => setError(null)}
      onRetry=${() => setTick((n) => n + 1)} />` : null}
    ${!versions && !error ? html`<p aria-busy="true">${t('common.loading')}</p>` : null}
    ${versions ? html`<section class="cx-atlas-versions" aria-labelledby="cx-atlas-versions-h">
      <h3 id="cx-atlas-versions-h" class="cx-atlas-panel__subtitle">${t('map.versions.list')}</h3>
      <ul class="cx-atlas-versions__list">
        ${list.map((v) => html`<li key=${v.id} class="cx-atlas-versions__item">
          <div>
            <strong>${v.id}</strong> · ${methodName(v.layout.method)} · ${t('map.versions.seed', { seed: v.layout.seed })}
            ${v.pinned ? html` <${StatusPill} state="up_to_date" detail=${t('map.versions.pinned')} />` : null}
            ${v.id === drawn ? html` <span class="cx-atlas-versions__drawn">${t('map.versions.drawn')}</span>` : null}
            <p class="cx-atlas-panel__muted">${v.note || ''} ${v.created_at ? formatDate(v.created_at) : ''}</p>
          </div>
          <div class="cx-atlas-versions__actions">
            ${v.pinned ? null : html`<${Button} size="s" loading=${busy === `pin:${v.id}`}
              onClick=${() => act(`pin:${v.id}`, { action: 'pin', version: v.id, build: true })}>
              ${t('map.versions.pin')}<//>`}
            ${v.pinned ? null : html`<${Button} size="s" variant="ghost" loading=${busy === `discard:${v.id}`}
              onClick=${() => act(`discard:${v.id}`, { action: 'discard', version: v.id })}>
              ${t('map.versions.discard')}<//>`}
          </div>
        </li>`)}
      </ul>
      <h3 class="cx-atlas-panel__subtitle">${t('map.versions.try')}</h3>
      <div class="cx-atlas-versions__form">
        <${FormField} label=${t('map.versions.method')}
          help=${Object.values(versions.unavailable || {}).map((why) => t('error.layout_method_unavailable.message', why.params)).join(' ')}>
          ${(field) => html`<${Select} ...${field} value=${method}
            options=${versions.methods.map((m) => ({ value: m, label: methodName(m),
              disabled: m in (versions.unavailable || {}) }))}
            onChange=${(e) => setMethod(e.currentTarget.value)} />`}
        <//>
        <${FormField} label=${t('map.versions.seed_field')} help=${t('map.versions.seed_help')}>
          ${(field) => html`<${Input} ...${field} inputmode="numeric" value=${seed}
            onInput=${(e) => setSeed(e.currentTarget.value.replace(/[^0-9]/g, ''))} />`}
        <//>
        <${FormField} label=${t('map.versions.note')}>
          ${(field) => html`<${Input} ...${field} value=${note} maxlength="500"
            onInput=${(e) => setNote(e.currentTarget.value)} />`}
        <//>
        <${Button} variant="primary" loading=${busy === 'try'} onClick=${tryAnother}>${t('map.versions.draw')}<//>
      </div>
      <p class="cx-atlas-panel__muted">${t('map.versions.try_help')}</p>
    </section>` : null}
    ${bases ? html`<section class="cx-atlas-versions" aria-labelledby="cx-atlas-bases-h">
      <h3 id="cx-atlas-bases-h" class="cx-atlas-panel__subtitle">${t('map.bases.title')}</h3>
      <p class="cx-atlas-panel__muted">${t('map.bases.lead')}</p>
      ${bases.bases.length ? html`<ul class="cx-atlas-versions__list">
        ${bases.bases.map((b) => html`<li key=${b.id} class="cx-atlas-versions__item">
          <div>
            <strong>${b.name || b.id}</strong> · ${b.map_version}
            <p class="cx-atlas-panel__muted">${b.missing ? t('map.bases.missing')
              : t('map.bases.size', { keywords: formatNumber(b.keywords), people: formatNumber(b.people) })}</p>
          </div>
          <div class="cx-atlas-versions__actions">
            ${b.missing ? null : html`<${Button} size="s" variant=${base === b.id ? 'primary' : 'secondary'}
              aria-pressed=${String(base === b.id)} onClick=${() => onBase(base === b.id ? '' : b.id)}>
              ${base === b.id ? t('map.bases.shown') : t('map.bases.show')}<//>`}
            <${Button} size="s" variant="ghost" loading=${busy === `remove:${b.id}`}
              onClick=${() => removeBase(b.id)}>${t('map.bases.remove')}<//>
          </div>
        </li>`)}
      </ul>` : null}
      <div class="cx-atlas-versions__form">
        <${FormField} label=${t('map.bases.folder')} help=${t('map.bases.folder_help')}>
          ${(field) => html`<${Input} ...${field} value=${folder}
            onInput=${(e) => setFolder(e.currentTarget.value)} />`}
        <//>
        <${Button} loading=${busy === 'base'} disabled=${!folder.trim()} onClick=${addBase}>${t('map.bases.add')}<//>
      </div>
    </section>` : null}
  <//>`;
}
