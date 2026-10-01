// SPDX-License-Identifier: MIT
/**
 * The layout step's diagnostic: the pinned map version (method, seed, layout
 * parameters), how many of each person's nearest people of the space the map
 * keeps, and a preview: a sample of the people drawn with a method and its
 * parameters (`POST /api/method/layout/preview`, a job the first time, then
 * cached), beside the map on the same people, with both scores. A preview can
 * become a map version, pinned and drawn from the pre-flight sheet.
 */

import { html, useEffect, useMemo, useState } from '../../core/preact.js';
import { formatNumber, formatPercent, t } from '../../core/i18n.js';
import { Button, ErrorCard, Input, MapFrame, ProgressBar, Select } from '../../components/index.js';
import { refusal } from '../settings/common.js';
import { Facts, Lead, nameOf } from './common.js';
import { shown } from './params.js';

const PALETTE = [...Array.from({ length: 12 }, (_, i) => `--cx-hue-${i + 1}`), '--cx-text-muted'];
const pct = (v) => (v === null || v === undefined ? '—' : formatPercent(v, { maximumFractionDigits: 0 }));

/** A MapFrame scene of preview points `[x, y, top-level theme]`. */
function scene(points) {
  const n = points.length;
  const x = new Float32Array(n);
  const y = new Float32Array(n);
  const color = new Uint16Array(n);
  let [xmin, xmax, ymin, ymax] = [Infinity, -Infinity, Infinity, -Infinity];
  points.forEach(([px, py, hue], i) => {
    x[i] = px;
    y[i] = py;
    color[i] = hue >= 0 ? hue % 12 : 12;
    xmin = Math.min(xmin, px); xmax = Math.max(xmax, px);
    ymin = Math.min(ymin, py); ymax = Math.max(ymax, py);
  });
  return { layers: [{ id: 'people', x, y, color, palette: PALETTE, radius: 3, alpha: 0.85 }], labels: [],
    bounds: n ? { xmin, xmax, ymin, ymax } : { xmin: 0, xmax: 1, ymin: 0, ymax: 1 } };
}

function paramsText(params) {
  const entries = Object.entries(params || {});
  return entries.length ? entries.map(([k, v]) => `${k} ${shown(v)}`).join(', ') : t('method.layout.defaults');
}

/** A layout parameter's default in words (none: the method chooses). */
const shownDefault = (s) => (s.default === null ? t('method.layout.automatic') : shown(s.default));

/** The form of a preview: method, its parameters (defaults shown), seed. */
function Form({ view, form, setForm, busy, onPreview }) {
  const specs = (view.parameters || {})[form.method] || [];
  const set = (s, raw) => setForm({ ...form, params: { ...form.params,
    [s.name]: raw === '' ? undefined : s.choices ? raw : Number(raw) } });
  return html`<div class="cx-method-form" role="group" aria-label=${t('method.layout.form')}>
    <label class="cx-settings__inline-label">${t('settings.layout.method')}
      <${Select} value=${form.method} options=${view.methods.map((m) => ({ value: m, label: t(`settings.layout.method.${m}`),
          disabled: m in (view.unavailable || {}) }))}
        onChange=${(e) => setForm({ ...form, method: e.currentTarget.value, params: {} })} /></label>
    ${Object.entries(view.unavailable || {}).map(([m, why]) => html`<p key=${m} class="cx-settings__muted cx-method-form__reason">
      ${refusal(why)}</p>`)}
    ${specs.map((s) => {
      const v = form.params[s.name];
      const changed = v !== undefined && v !== s.default;
      return html`<label key=${s.name} class=${`cx-settings__inline-label ${changed ? 'is-changed' : ''}`}>
        <code>${s.name}</code>
        ${s.choices ? html`<${Select} value=${v === undefined ? s.default : v}
            options=${s.choices.map((c) => ({ value: c, label: c }))}
            onChange=${(e) => set(s, e.currentTarget.value === s.default ? '' : e.currentTarget.value)} />`
          : html`<${Input} type="number" class="cx-settings__number" min=${s.minimum} max=${s.maximum}
          step=${s.type === 'int' ? 1 : 'any'} value=${v === undefined ? '' : v}
          placeholder=${s.default === null ? t('method.layout.automatic') : shown(s.default)}
          onInput=${(e) => set(s, e.currentTarget.value)} />`}
        <span class="cx-settings__muted">${changed ? t('method.param.changed', { value: shownDefault(s) })
          : t('method.layout.default_value', { value: shownDefault(s) })}</span></label>`;
    })}
    <label class="cx-settings__inline-label">${t('method.layout.seed')}
      <${Input} type="number" class="cx-settings__number" min="0" step="1" value=${form.seed}
        onInput=${(e) => setForm({ ...form, seed: Number(e.currentTarget.value || 0) })} /></label>
    <${Button} variant="primary" loading=${busy} onClick=${onPreview}>${t('method.layout.preview')}<//>
  </div>`;
}

/** The preview beside the map on the same people. */
function Compare({ preview }) {
  const mine = useMemo(() => scene(preview.points), [preview]);
  const now = useMemo(() => (preview.current ? scene(preview.current.points) : null), [preview]);
  const method = t(`settings.layout.method.${preview.method}`);
  return html`<div class="cx-method-compare">
    <figure class="cx-method-compare__map"><${MapFrame} class="cx-method-compare__frame" scene=${mine}
        label=${t('method.layout.preview_label', { method, n: preview.sample })} />
      <figcaption>${t('method.layout.preview_caption', { method, score: pct(preview.overlap) })}</figcaption></figure>
    ${now ? html`<figure class="cx-method-compare__map"><${MapFrame} class="cx-method-compare__frame" scene=${now}
        label=${t('method.layout.current_label', { n: preview.sample })} />
      <figcaption>${t('method.layout.current_caption', { score: pct(preview.current.overlap) })}</figcaption></figure>` : null}
    ${preview.themes && preview.themes.length ? html`<ul class="cx-chart__legend cx-method-compare__legend">
      ${preview.themes.slice(0, 12).map((th, i) => html`<li key=${th.id}><span class=${`cx-chart__swatch cx-chart__hue-${i + 1}`}
        aria-hidden="true"></span>${nameOf(th.names, th.id)}</li>`)}</ul>` : null}
    <p class="cx-settings__muted">${t('method.layout.sample_note', { n: preview.sample, total: preview.people })}</p>
  </div>`;
}

export function LayoutDiagnostic({ ctx, app, view, reload }) {
  const { jobs } = app.stores;
  const pinned = view.pinned;
  const usable = view.methods.filter((m) => !(m in (view.unavailable || {})));
  const [form, setForm] = useState({ method: pinned && usable.includes(pinned.method) ? pinned.method : usable[0], seed: 0,
    params: { ...((pinned && pinned.params) || {}) } });
  const [preview, setPreview] = useState(null);
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const [sent, setSent] = useState(null);

  const body = (f) => ({ method: f.method, seed: f.seed,
    params: Object.fromEntries(Object.entries(f.params).filter(([k, v]) => v !== undefined
      && ((view.parameters || {})[f.method] || []).some((s) => s.name === k))) });
  const ask = async (b) => {
    setError(null);
    setSent(b);
    const r = await ctx.api.post('/api/method/layout/preview', b);
    if (!r.ok) {
      setError(r.error);
      setJob(null);
      return;
    }
    if (r.data.preview) {
      setPreview(r.data.preview);
      setJob(null);
      reload();
    } else {
      setJob(r.data.job.id);
      jobs.refresh();
    }
  };
  useEffect(() => (job ? jobs.watch() : undefined), [job]);
  const live = job ? jobs.jobs.value.find((j) => j.id === job) : null;
  const state = live ? live.state : null;
  useEffect(() => {
    if (!job || !state) return;
    if (state === 'succeeded') ask(sent);
    else if (state === 'failed' || state === 'cancelled' || state === 'interrupted') {
      setJob(null);
      setError({ code: 'preview_failed' });
    }
  }, [job, state]);

  const useIt = async (pin) => {
    setError(null);
    const versions = await ctx.api.get('/api/map/versions');
    if (!versions.ok) return setError(versions.error);
    const b = body(form);
    const r = await ctx.api.post('/api/map/versions', { action: 'try', method: b.method, seed: b.seed,
      params: b.params, note: t('method.layout.note') }, { ifMatch: versions.etag });
    if (!r.ok) return setError(r.error);
    const added = r.data.versions[0];
    if (!pin) {
      app.toaster.show({ kind: 'success', title: t('settings.layout.tried', { version: added.id }) });
      return reload();
    }
    const p = await ctx.api.post('/api/map/versions', { action: 'pin', version: added.id }, { ifMatch: r.etag });
    if (!p.ok) return setError(p.error);
    return ctx.navigate('/build?scope=map');
  };

  const previews = view.previews || [];
  return html`<${Lead}>${t('method.layout.lead')}<//>
    <${Facts} items=${[
      [t('method.layout.version'), pinned ? pinned.id : '—'],
      [t('settings.layout.method'), pinned ? t(`settings.layout.method.${pinned.method}`) : '—'],
      [t('method.layout.params'), pinned ? paramsText(pinned.params) : '—'],
      [t('method.layout.seed'), pinned ? formatNumber(pinned.seed) : '—'],
      [t('method.layout.kept'), pct(view.overlap)],
      view.measures && view.measures.trustworthiness !== undefined
        ? [t('method.layout.trust'), pct(view.measures.trustworthiness)] : null,
    ]} />
    ${view.empty ? html`<p class="cx-settings__muted">${t('method.empty.empty_no_map')}</p>` : null}
    <h4 class="cx-method-subtitle">${t('method.layout.try_title')}</h4>
    <p class="cx-settings__note">${t('method.layout.try_lead')}</p>
    <${Form} view=${view} form=${form} setForm=${setForm} busy=${Boolean(job)} onPreview=${() => ask(body(form))} />
    ${job ? html`<div class="cx-method-waiting" aria-live="polite"><${ProgressBar} label=${t('method.layout.computing')} />
      <span>${t('method.layout.computing')}</span></div>` : null}
    ${error ? html`<p class="cx-settings__problem" role="alert">${error.code === 'preview_failed' ? t('method.layout.failed') : refusal(error)}</p>` : null}
    ${preview ? html`<${Compare} preview=${preview} />
      <div class="cx-settings__actions">
        <${Button} onClick=${() => useIt(false)}>${t('method.layout.add_version')}<//>
        <${Button} variant="primary" onClick=${() => useIt(true)}>${t('method.layout.use')}<//>
      </div>` : null}
    ${previews.length ? html`<table class="cx-settings__table" aria-label=${t('method.layout.scores')}>
      <caption class="cx-method-caption">${t('method.layout.scores')}</caption>
      <thead><tr><th scope="col">${t('settings.layout.method')}</th><th scope="col">${t('method.layout.params')}</th>
        <th scope="col" class="cx-num">${t('method.layout.seed')}</th><th scope="col" class="cx-num">${t('method.layout.kept')}</th></tr></thead>
      <tbody>
        ${view.overlap !== undefined && view.overlap !== null ? html`<tr><th scope="row">${t('method.layout.the_map')}</th>
          <td>${pinned ? paramsText(pinned.params) : '—'}</td><td class="cx-num">${pinned ? formatNumber(pinned.seed) : '—'}</td>
          <td class="cx-num">${pct(view.overlap)}</td></tr>` : null}
        ${previews.map((p, i) => html`<tr key=${i}><th scope="row">${t(`settings.layout.method.${p.method}`)}</th>
          <td>${paramsText(p.params)}</td><td class="cx-num">${formatNumber(p.seed)}</td>
          <td class="cx-num">${pct(p.overlap)}</td></tr>`)}
      </tbody></table>
      <p class="cx-settings__muted">${t('method.layout.scores_note')}</p>` : null}`;
}
