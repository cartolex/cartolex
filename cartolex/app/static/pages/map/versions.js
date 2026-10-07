// SPDX-License-Identifier: MIT
/**
 * Map versions and base maps, in one dialog (read when it opens):
 *
 * - the versions, newest first: how each was drawn (method, seed, flat or in space,
 *   note), the pinned one, the one the map shows, how many of each person's nearest
 *   people stay nearest on it (its trustworthiness, from the last build); pin one (the
 *   map is redrawn with it), discard one nobody pinned, « Build it too » (built with
 *   the pinned one at each build of the map, so the atlas and the offline site can show
 *   it; the pinned one is always built) and « Show » for a built one (`map=<id>`);
 * - « Try another layout »: another seed or another method (UMAP, t-SNE when
 *   installed, the theme tree), flat or in space (3D, when the method draws it), as a
 *   new version pinned and drawn, or built beside the pinned one;
 * - base maps: another project's map copied into this one, on which this
 *   project's people and keywords are placed by the keywords both share;
 *   show the project on one, remove one, add one from a project's folder.
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatDate, t } from '../../core/i18n.js';
import {
  Button, Dialog, ErrorCard, FormField, Input, Select, StatusPill,
} from '../../components/index.js';
import { BasesSection } from './bases.js';

function methodName(method) {
  return t(`map.method.${method}`);
}

/** The dimensions a method draws, from the versions' answer (`[2]` when it does not say). */
const dimensionsOf = (versions, method) => (versions.dimensions && versions.dimensions[method]) || [2];

/** A switch (a checkbox with the switch role), its label visible beside it. */
function Switch({ id, label, checked, disabled, describedBy, onChange }) {
  return html`<label class="cx-switch cx-atlas-versions__switch">
    <input type="checkbox" role="switch" class="cx-switch__control" id=${id} checked=${checked}
      disabled=${disabled} aria-describedby=${describedBy} onChange=${(e) => onChange(e.currentTarget.checked)} />
    <span class="cx-switch__track" aria-hidden="true"><span class="cx-switch__thumb"></span></span>
    <span class="cx-switch__label">${label}</span></label>`;
}

/** A group of radio buttons drawn as a segmented control. */
function Segmented({ name, label, value, options, onChange }) {
  return html`<fieldset class="cx-segmented cx-atlas-versions__segmented" id=${`cx-map-${name}`}>
    <legend class="cx-segmented__legend">${label}</legend>
    ${options.map((o) => html`<label class="cx-segmented__option" key=${o.value}>
      <input type="radio" name=${`cx-map-${name}`} value=${o.value} checked=${o.value === value}
        disabled=${o.disabled} onChange=${() => onChange(o.value)} />
      <span>${o.label}</span></label>`)}
  </fieldset>`;
}

/** One version's row. */
function VersionRow({ v, drawn, busy, act, onShow }) {
  const space = v.layout.dimensions === 3;
  const trust = v.measure && v.measure.trustworthiness;
  const switchId = `cx-map-built-${v.id}`;
  return html`<li class="cx-atlas-versions__item" data-version=${v.id}>
    <div>
      <strong>${v.id}</strong> · ${methodName(v.layout.method)} · ${t('map.versions.seed', { seed: v.layout.seed })}
      ${' '}<span class="cx-atlas-versions__dims">${t(space ? 'map.versions.dims.3' : 'map.versions.dims.2')}</span>
      ${v.pinned ? html` <${StatusPill} state="up_to_date" detail=${t('map.versions.pinned')} />` : null}
      ${v.id === drawn ? html` <span class="cx-atlas-versions__drawn">${t('map.versions.drawn')}</span>` : null}
      <p class="cx-atlas-panel__muted">
        ${typeof trust === 'number' ? html`<span class="cx-atlas-versions__trust">${t('map.versions.trust',
          { value: trust })}</span> ` : null}
        ${v.built && !v.ready ? html`<span>${t('map.versions.not_ready')}</span> ` : null}
        ${v.note || ''} ${v.created_at ? formatDate(v.created_at) : ''}</p>
    </div>
    <div class="cx-atlas-versions__actions">
      <${Switch} id=${switchId} label=${t('map.versions.build_too')} checked=${v.pinned || Boolean(v.built)}
        disabled=${v.pinned || busy === `build:${v.id}`} describedBy=${v.pinned ? 'cx-map-built-pinned' : undefined}
        onChange=${(on) => act(`build:${v.id}`, { action: 'build', version: v.id, built: on }, { marked: v.id })} />
      ${v.ready && v.id !== drawn ? html`<${Button} size="s" variant="ghost" onClick=${() => onShow(v)}>
        ${t('map.versions.show')}<//>` : null}
      ${v.pinned ? null : html`<${Button} size="s" loading=${busy === `pin:${v.id}`}
        onClick=${() => act(`pin:${v.id}`, { action: 'pin', version: v.id, build: true })}>
        ${t('map.versions.pin')}<//>`}
      ${v.pinned ? null : html`<${Button} size="s" variant="ghost" loading=${busy === `discard:${v.id}`}
        onClick=${() => act(`discard:${v.id}`, { action: 'discard', version: v.id })}>
        ${t('map.versions.discard')}<//>`}
    </div>
  </li>`;
}

/** The dialog. */
export function VersionsDialog({
  ctx, open, onClose, drawn, base, onBase, onBuild, onShow, pinned3d = false, refresh = 0,
}) {
  const [versions, setVersions] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState('');
  const [method, setMethod] = useState('');
  const [dims, setDims] = useState(2);
  const [after, setAfter] = useState('pin');
  const [seed, setSeed] = useState('');
  const [note, setNote] = useState('');
  const [marked, setMarked] = useState(false);
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
  }, [open, tick, refresh]); // read again when a build of the map ended
  useEffect(() => {
    if (!open) setMarked(false);
  }, [open]);

  const started = (job) => {
    setMarked(false);
    onBuild(job);
  };
  const act = async (label, body, { marked: mark = null } = {}) => {
    setBusy(label);
    const r = await ctx.api.post('/api/map/versions', body, { ifMatch: versions.etag });
    setBusy('');
    if (!r.ok) {
      setError(r.error);
      setTick((n) => n + 1);
      return null;
    }
    setVersions({ ...r.data, etag: r.etag });
    if (r.data.job) started(r.data.job);
    else if (mark) setMarked(true);
    return r;
  };
  const buildNow = async () => {
    setBusy('build-now');
    const r = await ctx.api.post('/api/build', { scope: ['map'], dry_run: false });
    setBusy('');
    if (!r.ok) setError(r.error);
    else if (r.data.job) started(r.data.job);
  };
  const chooseDims = (value) => {
    setDims(value);
    // a map in space is shown beside the pinned (flat) one by default; a flat one is pinned
    setAfter(value === 3 ? 'beside' : 'pin');
  };
  const chooseMethod = (value) => {
    setMethod(value);
    if (versions && !dimensionsOf(versions, value).includes(dims)) chooseDims(2);
  };
  const tryAnother = async () => {
    const beside = after === 'beside';
    const body = { action: 'try', method, note, dimensions: dims, built: beside, build: beside };
    if (seed.trim()) body.seed = Number.parseInt(seed, 10);
    const tried = await act('try', body);
    if (!tried) return;
    setNote('');
    setSeed('');
    if (beside) return;
    const added = tried.data.done.split(' ').pop();
    const pinned = await ctx.api.post('/api/map/versions', { action: 'pin', version: added, build: true },
      { ifMatch: tried.etag });
    if (!pinned.ok) setError(pinned.error);
    else {
      setVersions({ ...pinned.data, etag: pinned.etag });
      if (pinned.data.job) started(pinned.data.job);
    }
  };

  const list = versions ? versions.versions : [];
  const spaceOk = versions ? dimensionsOf(versions, method).includes(3) : false;
  return html`<${Dialog} open=${open} onClose=${onClose} size="l" title=${t('map.versions.title')}
    description=${t('map.versions.lead')}
    footer=${html`<${Button} onClick=${onClose}>${t('common.close')}<//>`}>
    ${error ? html`<${ErrorCard} error=${error} compact onDismiss=${() => setError(null)}
      onRetry=${() => setTick((n) => n + 1)} />` : null}
    ${!versions && !error ? html`<p aria-busy="true">${t('common.loading')}</p>` : null}
    ${versions ? html`<section class="cx-atlas-versions" aria-labelledby="cx-atlas-versions-h">
      <h3 id="cx-atlas-versions-h" class="cx-atlas-panel__subtitle">${t('map.versions.list')}</h3>
      <p class="cx-atlas-panel__muted" id="cx-map-built-pinned">${t('map.versions.built_help')}</p>
      ${marked ? html`<div class="cx-atlas-versions__marked" role="status">
        <p>${t('map.versions.marked')}</p>
        <${Button} size="s" variant="primary" loading=${busy === 'build-now'} onClick=${buildNow}>
          ${t('map.versions.build_now')}<//>
      </div>` : null}
      <ul class="cx-atlas-versions__list">
        ${list.map((v) => html`<${VersionRow} key=${v.id} v=${v} drawn=${drawn} busy=${busy} act=${act}
          onShow=${onShow} />`)}
      </ul>
      <h3 class="cx-atlas-panel__subtitle">${t('map.versions.try')}</h3>
      <div class="cx-atlas-versions__form">
        <${FormField} label=${t('map.versions.method')}
          help=${Object.values(versions.unavailable || {}).map((why) => t('error.layout_method_unavailable.message', why.params)).join(' ')}>
          ${(field) => html`<${Select} ...${field} value=${method}
            options=${versions.methods.map((m) => ({ value: m, label: methodName(m),
              disabled: m in (versions.unavailable || {}) }))}
            onChange=${(e) => chooseMethod(e.currentTarget.value)} />`}
        <//>
        <${FormField} label=${t('map.versions.seed_field')} help=${t('map.versions.seed_help')}>
          ${(field) => html`<${Input} ...${field} inputmode="numeric" value=${seed}
            onInput=${(e) => setSeed(e.currentTarget.value.replace(/[^0-9]/g, ''))} />`}
        <//>
        <${FormField} label=${t('map.versions.note')}>
          ${(field) => html`<${Input} ...${field} value=${note} maxlength="500"
            onInput=${(e) => setNote(e.currentTarget.value)} />`}
        <//>
      </div>
      <div class="cx-atlas-versions__form">
        <${Segmented} name="dims" label=${t('map.versions.dims')} value=${String(dims)}
          options=${[{ value: '2', label: t('map.versions.dims.flat') },
            { value: '3', label: t('map.versions.dims.space'), disabled: !spaceOk }]}
          onChange=${(v) => chooseDims(Number(v))} />
        <${Segmented} name="after" label=${t('map.versions.after')} value=${after}
          options=${[{ value: 'pin', label: t('map.versions.after.pin') },
            { value: 'beside', label: t('map.versions.after.beside') }]}
          onChange=${setAfter} />
        <${Button} variant="primary" loading=${busy === 'try'} onClick=${tryAnother}>${t('map.versions.draw')}<//>
      </div>
      <p class="cx-atlas-panel__muted">${spaceOk ? t('map.versions.try_help')
        : t('map.versions.space_umap', { method: methodName(method) })}</p>
    </section>` : null}
    <${BasesSection} ctx=${ctx} open=${open} base=${base} onBase=${onBase} pinned3d=${pinned3d}
      onError=${setError} />
  <//>`;
}
