// SPDX-License-Identifier: MIT
/**
 * The map's layout previewed on the map itself. The « Tune » panel's layout (the method, its
 * seed and its parameters, `tune/map-settings.js`) writes a draft here; a moment after the
 * last change the draft's preview is asked for (`POST /api/method/layout/preview`: a sample
 * of at most 800 people drawn with it, beside the map on the same people), a job the first
 * time, then cached. The atlas draws the preview in its own MapFrame, with « Before » (the
 * map now, on the same people) and « After », the nearest people each keeps, « Keep » (a new
 * map version, pinned, then the build of the map) and « Discard » (back to the map).
 *
 * A change while a preview is being computed supersedes it: the running job is cancelled
 * (its answer, cached, is not shown) and the newest draft is asked for once it has ended.
 * Nothing is stored but the map version « Keep » adds.
 */

import { html, signal } from '../../core/preact.js';
import { formatPercent, t } from '../../core/i18n.js';
import { Button, ProgressBar } from '../../components/index.js';
import { refusal } from '../settings/common.js';

/** How long the draft must stay the same before its preview is asked for. */
export const WAIT_MS = 400;
const ACTIVE = new Set(['queued', 'running', 'cancelling']);
const MUTED = '--cx-text-muted';

/** A group of buttons of which one is pressed. */
export function Segmented({ label, options, value, onChange, class: cls = '' }) {
  return html`<div class=${`cx-atlas-segmented ${cls}`} role="group" aria-label=${label}>
    ${options.map((o) => html`<button key=${o.value} type="button"
      class=${`cx-atlas-segmented__item ${o.value === value ? 'is-pressed' : ''}`}
      aria-pressed=${String(o.value === value)} onClick=${() => onChange(o.value)}>${o.label}</button>`)}
  </div>`;
}

/** The body of a preview request: the parameters the method takes, unset ones left out. */
export function previewBody(view, draft) {
  const known = ((view.parameters || {})[draft.method] || []).map((s) => s.name);
  const params = {};
  for (const name of known) {
    const v = draft.params[name];
    if (v !== undefined && v !== null) params[name] = v;
  }
  return { method: draft.method, seed: draft.seed, params };
}

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/**
 * The preview's store: `phase` (`idle`, `waiting`, `computing`, `ready`, `failed`), the
 * `preview` shown, the `side` (`after`, `before`) and the `draft` (null: the pinned version).
 */
export function createLayoutPreview({ api, jobs }) {
  const phase = signal('idle');
  const preview = signal(null);
  const side = signal('after');
  const draft = signal(null);
  const problem = signal(null);
  const generation = signal(0);
  let wanted = null; // the body asked for last
  let job = null; // {id, body} of the job being computed
  let timer = 0;
  let unwatch = null;
  let stopJobs = null;
  let closed = false;

  const followJob = () => {
    if (stopJobs) return;
    unwatch = jobs.watch();
    // the one poller updates the jobs' list: look at our job's state each time it changes
    stopJobs = jobs.jobs.subscribe((list) => {
      if (!job) return;
      const live = list.find((j) => j.id === job.id);
      if (!live || ACTIVE.has(live.state)) return;
      const ended = job;
      job = null;
      stopFollowing();
      if (!wanted) return;
      // done, or superseded: ask for the newest draft (a computed one answers at once)
      if (live.state === 'succeeded' || !same(ended.body, wanted)) send();
      else fail({ code: 'preview_failed' });
    });
  };
  const stopFollowing = () => {
    if (stopJobs) stopJobs();
    if (unwatch) unwatch();
    stopJobs = null;
    unwatch = null;
  };
  const fail = (error) => {
    phase.value = 'failed';
    problem.value = error;
  };

  const send = async () => {
    if (closed || !wanted) return;
    if (job) {
      // a newer draft: the running preview is superseded (asked again once it has ended)
      if (!same(job.body, wanted) && !job.cancelled) {
        job.cancelled = true;
        jobs.cancel(job.id);
      }
      return;
    }
    const body = wanted;
    phase.value = 'computing';
    problem.value = null;
    const r = await api.post('/api/method/layout/preview', body);
    if (closed) return;
    if (!same(body, wanted)) {
      // the draft changed while asking: follow (and supersede) the job, or ask for the newest
      if (r.ok && r.data.job) {
        job = { id: r.data.job.id, body };
        followJob();
      }
      if (wanted) send();
      else phase.value = 'idle';
      return;
    }
    if (r.ok && r.data.preview) {
      preview.value = r.data.preview;
      phase.value = 'ready';
    } else if (r.ok && r.data.job) {
      job = { id: r.data.job.id, body };
      followJob();
      jobs.refresh();
    } else if (!r.ok && r.status === 409 && r.error && r.error.code === 'busy') {
      // another preview (another tab) is being computed: ask again in a moment
      timer = setTimeout(send, 1000);
    } else if (r.kind !== 'aborted') {
      fail(r.error);
    }
  };

  return {
    phase, preview, side, draft, problem, generation,
    /** Show *body*'s preview a moment after the last change (*next*: the panel's draft). */
    ask(body, next) {
      draft.value = next;
      clearTimeout(timer);
      if (wanted && same(body, wanted) && phase.value !== 'failed') return;
      wanted = body;
      side.value = 'after';
      if (phase.value !== 'computing') phase.value = 'waiting';
      timer = setTimeout(send, WAIT_MS);
    },
    /** Back to the map: the draft, the preview and any job in flight are dropped. */
    discard() {
      clearTimeout(timer);
      wanted = null;
      if (job && !job.cancelled) {
        job.cancelled = true;
        jobs.cancel(job.id);
      }
      preview.value = null;
      draft.value = null;
      problem.value = null;
      phase.value = 'idle';
      generation.value += 1;
    },
    active: () => phase.value !== 'idle',
    /** The draft as a map version, pinned; then the build of the map. */
    async keep(navigate, toaster) {
      if (!wanted) return false;
      const body = wanted;
      const versions = await api.get('/api/map/versions');
      let r = versions;
      if (r.ok) {
        r = await api.post('/api/map/versions', { action: 'try', method: body.method, seed: body.seed,
          params: body.params, note: t('method.layout.note') }, { ifMatch: versions.etag });
      }
      if (r.ok) {
        const added = r.data.versions[0];
        r = await api.post('/api/map/versions', { action: 'pin', version: added.id }, { ifMatch: r.etag });
      }
      if (!r.ok) {
        problem.value = r.error;
        return false;
      }
      wanted = null;
      draft.value = null;
      toaster.show({ kind: 'success', title: t('method.map.saved') });
      navigate('/build?scope=map');
      return true;
    },
    dispose() {
      closed = true;
      clearTimeout(timer);
      stopFollowing();
    },
  };
}

/** A MapFrame scene of preview points `[x, y, top-level theme]`, in the atlas's theme hues. */
export function previewScene(points, themes, index, colours) {
  const palette = (themes || []).slice(0, 31).map((th) => {
    const i = index.colourOf(th.id);
    return i < colours.themes.length ? colours.themes[i] : MUTED;
  });
  palette.push(MUTED);
  const muted = palette.length - 1;
  const n = points.length;
  const x = new Float32Array(n);
  const y = new Float32Array(n);
  const color = new Uint16Array(n);
  let [xmin, xmax, ymin, ymax] = [Infinity, -Infinity, Infinity, -Infinity];
  points.forEach(([px, py, h], i) => {
    x[i] = px;
    y[i] = py;
    color[i] = h >= 0 && h < muted ? h : muted;
    xmin = Math.min(xmin, px); xmax = Math.max(xmax, px);
    ymin = Math.min(ymin, py); ymax = Math.max(ymax, py);
  });
  return {
    layers: [{ id: 'preview', x, y, color, palette, shape: 'circle', radius: 3.5, alpha: 0.9 }],
    labels: [],
    bounds: n ? { xmin, xmax, ymin, ymax } : { xmin: 0, xmax: 1, ymin: 0, ymax: 1 },
  };
}

const pct = (v) => (v === null || v === undefined ? '—' : formatPercent(v, { maximumFractionDigits: 0 }));

/** The bar over the map while a preview is shown: its state, before/after, the measures, keep. */
export function PreviewBar({ store, onKeep, busy }) {
  const phase = store.phase.value;
  const p = store.preview.value;
  const method = p ? t(`settings.layout.method.${p.method}`) : '';
  const problem = store.problem.value;
  return html`<div class="cx-atlas-preview" role="region" aria-label=${t('map.preview.title')}>
    <div class="cx-atlas-preview__state" role="status" aria-live="polite">
      <strong class="cx-atlas-preview__badge">${t('map.preview.title')}</strong>
      ${phase === 'waiting' || phase === 'computing' ? html`<${ProgressBar} label=${t('map.preview.computing')} />
        <span>${t('map.preview.computing')}</span>` : null}
      ${phase === 'failed' ? html`<span class="cx-settings__problem">${problem && problem.code !== 'preview_failed'
        ? refusal(problem) : t('method.layout.failed')}</span>` : null}
      ${p && phase !== 'failed' ? html`<span class="cx-atlas-preview__measure">${t('map.preview.measure', {
        method, after: pct(p.overlap), before: pct(p.current ? p.current.overlap : null) })}</span>
        <span class="cx-atlas-preview__params" data-preview-params=${JSON.stringify(p.params || {})}>${
          [['seed', p.seed], ...Object.entries(p.params || {})].map(([k, v]) => html`<code key=${k}>${k} ${v}</code>`)}</span>
        <span class="cx-settings__muted">${t('map.preview.sample', { n: p.sample, total: p.people })}</span>` : null}
    </div>
    ${p && phase !== 'failed' && p.current ? html`<${Segmented} label=${t('map.preview.side')} value=${store.side.value}
      options=${[{ value: 'before', label: t('map.preview.before') }, { value: 'after', label: t('map.preview.after') }]}
      onChange=${(v) => { store.side.value = v; }} />` : null}
    <div class="cx-atlas-preview__actions">
      ${phase === 'ready' ? html`<${Button} size="s" variant="primary" loading=${busy} onClick=${onKeep}>
        ${t('map.preview.keep')}<//>` : null}
      <${Button} size="s" onClick=${() => store.discard()}>${t('map.preview.discard')}<//>
    </div>
  </div>`;
}
