// SPDX-License-Identifier: MIT
/**
 * The theme editor's « Playground » tab (beside the treemap and the map): play with the
 * grouping's settings and see the tree before adopting it or sending it to AI. The controls
 * along the top (`controls.js`) start from the project's parameters (`GET /api/params`, read
 * when the tab opens); each change regroups in the background (`store.js`), never saving
 * anything; the tree shows as columns, one per level (`icicle.js`), beside the balance across
 * levels and how it differs from the editor's tree (`balance.js`); « Adopt as my draft »
 * (`adopt.js`) saves the settings, builds the grouping and puts its proposal in place of the
 * editor's tree as one undoable step; « Send to AI » adopts, then opens the copilot.
 *
 * The values live in the editor's `ui` for the visit, so the tab keeps them when it is left
 * and opened again (the server's cache answers at once). A change saved by the « Tune » panel
 * shows here, and the other way round (`tune/params.js`'s `paramsSaved`).
 */

import { html, signal, useEffect, useMemo, useState } from '../../../core/preact.js';
import { t } from '../../../core/i18n.js';
import { EmptyState, ErrorCard } from '../../../components/index.js';
import { useResource } from '../../settings/common.js';
import { paramsSaved, useParamsFollow } from '../../tune/params.js';
import { refusal } from '../actions.js';
import { indexTree } from '../model.js';
import { Controls, StateLine, settingsOf, valuesOf } from './controls.js';
import { createPlayground } from './store.js';
import { Icicle } from './icicle.js';
import { PlaygroundSide } from './balance.js';
import { adopt } from './adopt.js';

/** The playground's state for the editor's visit: its values and its preview store. */
export function playgroundState(ui) {
  if (!ui.playground) ui.playground = { values: signal(null), invalid: signal(new Set()) };
  return ui.playground;
}

export function PlaygroundPanel({ ctx, editor, ui }) {
  const { app } = ctx;
  const state = playgroundState(ui);
  const params = useResource(ctx.api, '/api/params');
  useParamsFollow(params);
  const store = useMemo(() => createPlayground({
    api: ctx.api, jobs: app.stores.jobs, treeOf: () => editor.tree.value,
  }), []);
  useEffect(() => () => store.dispose(), []);
  const [busy, setBusy] = useState(null);
  const [step, setStep] = useState(null);
  const [error, setError] = useState(null);

  // the values start from the project's parameters, and follow a save made elsewhere
  const etag = params.etag;
  useEffect(() => {
    if (!params.data) return;
    if (!state.values.value || state.values.value.$etag !== etag) {
      state.values.value = { ...valuesOf(params.data), $etag: etag };
      state.invalid.value = new Set();
    }
  }, [etag, Boolean(params.data)]);
  const values = state.values.value;
  const invalid = state.invalid.value;
  useEffect(() => {
    if (values && !invalid.size) store.ask(settingsOf(values), { now: store.phase.value === 'idle' });
  }, [values, invalid]);

  const preview = store.preview.value;
  const index = useMemo(() => (preview ? indexTree(preview.tree, editor.usage.value) : null),
    [preview, editor.usage.value]);

  if (params.error) return html`<${ErrorCard} error=${params.error} compact onRetry=${params.reload} />`;
  if (!params.data || !values) return html`<p class="cx-settings__muted" aria-busy="true">${t('common.loading')}</p>`;

  const onChange = (key, value, bad) => {
    const next = new Set(invalid);
    if (bad) next.add(key); else next.delete(key);
    state.invalid.value = next;
    if (!bad) state.values.value = { ...values, [key]: value };
  };
  const sizes = index ? Array.from({ length: index.depth },
    (_, i) => index.order.filter((id) => index.level.get(id) === i + 1).length) : null;
  const space = (params.data.stages.find((s) => s.id === 'themes.space') || { params: [] })
    .params.find((p) => p.name === 'space_unit');
  const refit = Boolean(space && space.last_run && values['themes.space.space_unit'] !== space.last_run);

  const run = async (kind) => {
    setBusy(kind);
    setError(null);
    const outcome = await adopt({
      ctx, editor, params, values,
      onStep: setStep,
      onSaved: (data, saved) => {
        paramsSaved.value = { data, etag: saved };
      },
    });
    setBusy(null);
    setStep(null);
    if (outcome.navigated) return;
    if (!outcome.ok) {
      if (outcome.stale) params.reload();
      setError(outcome.error || { code: 'job_failed' });
      return;
    }
    const c = outcome.carried || {};
    app.toaster.show({ kind: 'success', title: t('playground.adopted'),
      message: t('playground.adopted_text', { names: c.names || 0, aside: c.set_aside || 0,
        attributions: c.attributions || 0, dropped: c.dropped || 0 }) });
    ui.centreTab.value = 'treemap';
    if (kind === 'ai' && ui.openCopilot) ui.openCopilot();
  };
  const phase = store.phase.value;
  const ready = phase === 'ready' && preview;
  return html`<div class="cx-pg">
    <${Controls} data=${params.data} values=${values} onChange=${onChange} theta=${preview ? preview.theta : null}
      sizes=${sizes} state=${html`<${StateLine} store=${store} refit=${refit} />`} />
    ${phase === 'failed' ? html`<${ErrorCard} error=${store.problem.value && store.problem.value.code !== 'preview_failed'
      ? store.problem.value : { code: 'preview_failed', params: {}, message: t('playground.failed'),
        next: { label: '', action: 'retry' } }} compact onRetry=${() => store.ask(settingsOf(values), { now: true })} />` : null}
    ${error ? html`<p class="cx-settings__problem" role="alert">${refusal(error)}</p>` : null}
    <div class=${`cx-pg__body ${phase === 'computing' || phase === 'waiting' ? 'is-stale' : ''}`}
      aria-busy=${phase === 'computing' ? 'true' : 'false'}>
      ${index ? html`<${Icicle} index=${index} />`
        : html`<${EmptyState} icon="file" level=${3} title=${t('playground.empty')}>${t('playground.empty_text')}<//>`}
      <${PlaygroundSide} index=${index} against=${store.against.value} busy=${busy} step=${step}
        disabled=${!ready || Boolean(busy) || editor.readOnly.value}
        onAdopt=${() => run('adopt')} onSendAi=${() => run('ai')} />
    </div>
  </div>`;
}
