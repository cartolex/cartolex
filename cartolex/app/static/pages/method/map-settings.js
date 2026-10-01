// SPDX-License-Identifier: MIT
/**
 * The map's layout on the layout step, beside the step's own parameters: the
 * method of the pinned map version (UMAP, t-SNE, the tree; one this computer
 * cannot draw is listed, switched off, with its reason and its fix), its
 * parameters by tier (the essential ones, then « More » and « Advanced »
 * folded) and its seed. They live in `maps.json`, not in `params.json`: saving
 * adds a map version with them, pins it and opens the pre-flight sheet of the
 * map (`POST /api/map/versions`, `try` then `pin`, with `If-Match`).
 *
 * It reads nothing when it opens: the layout step's diagnostic
 * (`GET /api/method/layout`) carries the methods, their parameters and the
 * pinned version.
 */

import { html, useEffect, useRef, useState } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { Button, Input, ParamControl, ParamField } from '../../components/index.js';
import { refusal } from '../settings/common.js';

const TIERS = ['essential', 'intermediate', 'advanced'];

/** The first method this computer can draw. */
function firstAvailable(view) {
  const off = view.unavailable || {};
  return view.methods.find((m) => !(m in off)) || view.methods[0];
}

export function MapSettings({ ctx, app, view }) {
  const pinned = view.pinned;
  const off = view.unavailable || {};
  const start = pinned && !(pinned.method in off) ? pinned.method : firstAvailable(view);
  const [method, setMethod] = useState(start);
  const [edits, setEdits] = useState({});
  const [seed, setSeed] = useState(pinned ? pinned.seed : 0);
  const [problem, setProblem] = useState(null);
  const [busy, setBusy] = useState(false);
  const own = pinned && pinned.method === method ? pinned.params || {} : {};
  const dirty = Boolean(pinned) && (method !== pinned.method || seed !== pinned.seed || Object.keys(edits).length > 0);
  const dirtyRef = useRef(false);
  dirtyRef.current = dirty;
  useEffect(() => ctx.guard({ dirty: () => dirtyRef.current }), []);

  const specs = ((view.parameters || {})[method] || []).map((s) => {
    const set = s.name in own && own[s.name] !== null;
    const value = set ? own[s.name] : s.default;
    return { ...s, value, default_value: s.default, from: set ? 'version' : 'default', set_in_file: set,
      differs: JSON.stringify(value) !== JSON.stringify(s.default) };
  });
  const choice = { name: 'layout_method', type: 'str', widget: 'choice', choices: view.methods,
    unavailable: Object.fromEntries(Object.entries(off).map(([m, why]) => [m, refusal(why)])) };

  const field = (p) => html`<${ParamField} key=${`${method}.${p.name}`} p=${p} id=${`cx-map-${p.name}`}
    dataKey=${`map.${p.name}`} edit=${edits[p.name]}
    help=${t(`method.map.param.${p.name}`)}
    onEdit=${(e) => setEdits({ ...edits, [p.name]: e })} />`;
  const tier = (name) => specs.filter((p) => (p.tier || 'advanced') === name);

  const save = async () => {
    setProblem(null);
    if (Object.values(edits).some((e) => e.invalid)) return setProblem(t('param.field.invalid'));
    setBusy(true);
    const params = {};
    for (const p of specs) {
      const e = edits[p.name];
      if (e && e.reset) params[p.name] = null;
      else if (e) params[p.name] = e.value;
    }
    const versions = await ctx.api.get('/api/map/versions');
    let r = versions;
    if (r.ok) {
      r = await ctx.api.post('/api/map/versions', { action: 'try', method, seed, params, note: t('method.layout.note') },
        { ifMatch: versions.etag });
    }
    if (r.ok) {
      const added = r.data.versions[0];
      r = await ctx.api.post('/api/map/versions', { action: 'pin', version: added.id }, { ifMatch: r.etag });
    }
    setBusy(false);
    if (!r.ok) return setProblem(refusal(r.error));
    dirtyRef.current = false;
    app.toaster.show({ kind: 'success', title: t('method.map.saved') });
    return ctx.navigate('/build?scope=map');
  };

  if (!pinned) {
    return html`<section class="cx-method-map" aria-labelledby="cx-method-map-title">
      <h4 class="cx-method-subtitle" id="cx-method-map-title">${t('method.map.title')}</h4>
      <p class="cx-settings__note">${t('method.map.none')}</p></section>`;
  }
  return html`<section class="cx-method-map" aria-labelledby="cx-method-map-title">
    <h4 class="cx-method-subtitle" id="cx-method-map-title">${t('method.map.title')}</h4>
    <p class="cx-settings__note">${t('method.map.lead')}</p>
    <div class="cx-param">
      <div class="cx-param__head"><span class="cx-param__name" id="cx-map-method-label">${t('settings.layout.method')}</span>
        <p class="cx-param__help">${t('method.map.method_help')}</p></div>
      <div class="cx-param__control"><${ParamControl} p=${choice} id="cx-map-method" labelId="cx-map-method-label"
        value=${method} onChange=${(m) => { setMethod(m); setEdits({}); }} /></div>
    </div>
    ${tier('essential').map(field)}
    <div class="cx-param">
      <div class="cx-param__head"><label class="cx-param__name" for="cx-map-seed">${t('method.layout.seed')}</label>
        <p class="cx-param__help">${t('method.map.seed_help')}</p></div>
      <div class="cx-param__control"><${Input} id="cx-map-seed" type="number" class="cx-param__number" min="0" step="1"
        value=${seed} onInput=${(e) => setSeed(Number(e.currentTarget.value || 0))} /></div>
    </div>
    ${TIERS.slice(1).map((name) => (tier(name).length ? html`<details class="cx-method-tier" key=${name}>
      <summary class="cx-method-tier__summary">${t(`method.tier.${name}`, { n: tier(name).length })}</summary>
      ${tier(name).map(field)}</details>` : null))}
    ${problem ? html`<p class="cx-settings__problem" role="alert">${problem}</p>` : null}
    <div class="cx-settings__actions">
      <${Button} variant="primary" disabled=${!dirty} loading=${busy} onClick=${save}>${t('method.map.save')}<//>
      ${dirty ? html`<${Button} variant="ghost" onClick=${() => {
        setMethod(start); setEdits({}); setSeed(pinned ? pinned.seed : 0);
      }}>${t('settings.build.undo')}<//>` : null}
    </div>
  </section>`;
}
