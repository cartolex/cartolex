// SPDX-License-Identifier: MIT
/**
 * The « Tune » panel of a step's page (`TunePanel({id})`: `texts` on the
 * People page's Texts tab, `keywords`, `themes`, `map`): the parameters of
 * the stages that page shows, on the page they shape.
 *
 * Collapsed by default; its header says « defaults » or « N changed » (the
 * project state's `changed_params`, read with the state, so a closed panel
 * costs no API call). Opening it (or arriving with `?tune=1`) reads
 * `GET /api/params` and the step's diagnostics (`GET /api/method/<step>`):
 * the essential parameters, then « More » and « Advanced » folded (a fold
 * holding a changed value opens by itself), the seed and the pinned year on
 * the texts, the map's layout (a map version, pinned) on the map; save and
 * undo; « Rebuild from here » (the pre-flight sheet with the panel's first
 * stage forced); and what the step produced. Above the header, when the
 * page's outputs need an update, a note names the first stage to rebuild and
 * offers to rebuild from it.
 */

import { html, useEffect, useRef, useState } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { useUid } from '../../core/dom.js';
import { Button, EmptyState, ErrorCard, Icon, Input, StatusDot } from '../../components/index.js';
import { useResource } from '../settings/common.js';
import { ParamActions, ParamTable, paramRows, useParamEdits } from './params.js';
import {
  PANELS, changedCount, firstStale, notBuilt, panelState, rebuildHref, stageWords,
} from './common.js';
import { TextsDiagnostic } from './texts.js';
import { KeywordsDiagnostic } from './keywords.js';
import { SpaceDiagnostic } from './space.js';
import { GroupingDiagnostic } from './grouping.js';
import { LayoutDiagnostic } from './layout.js';
import { MapSettings } from './map-settings.js';

const DIAGNOSTICS = {
  texts: TextsDiagnostic,
  keywords: KeywordsDiagnostic,
  space: SpaceDiagnostic,
  grouping: GroupingDiagnostic,
  layout: LayoutDiagnostic,
};

/** The seed and the pinned year, set once for the whole build (the texts' panel). */
function WholeBuild({ data, global, setGlobal }) {
  const seed = global.seed ?? data.global.seed.value;
  const year = global.pinned_year !== undefined ? global.pinned_year : data.global.pinned_year.value;
  return html`<div class="cx-method-global" role="group" aria-label=${t('method.global.title')}>
    <label class="cx-settings__inline-label">${t('method.global.seed')}
      <${Input} type="number" class="cx-settings__number" min="0" step="1" value=${seed}
        onInput=${(e) => setGlobal({ ...global, seed: Number(e.currentTarget.value || 0) })} /></label>
    <label class="cx-settings__inline-label">${t('method.global.year')}
      <${Input} type="number" class="cx-settings__number" min="1900" max="2200" step="1"
        value=${year === null || year === undefined ? '' : year} placeholder=${t('method.global.this_year')}
        onInput=${(e) => setGlobal({ ...global,
          pinned_year: e.currentTarget.value === '' ? null : Number(e.currentTarget.value) })} /></label>
  </div>`;
}

/** What one step produced: its diagnostic, or what to do before its stage ran. */
function Produced({ ctx, app, step, view, from, titled }) {
  const Diagnostic = DIAGNOSTICS[step];
  return html`<section class="cx-tune__produced" aria-label=${t(`method.step.${step}`)}>
    ${titled ? html`<h4 class="cx-method-subtitle">${t(`method.step.${step}`)}</h4>` : null}
    ${view.error ? html`<${ErrorCard} error=${view.error} compact onRetry=${view.reload} />` : null}
    ${!view.data && !view.error ? html`<p class="cx-settings__muted" aria-busy="true">${t('common.loading')}</p>` : null}
    ${view.data && view.data.empty && step !== 'layout' ? html`<${EmptyState} icon="file" level=${4}
        title=${notBuilt(view.data)}
        action=${{ label: t('method.build'), onClick: () => ctx.navigate(rebuildHref(from)) }} />`
      : view.data ? html`<${Diagnostic} ctx=${ctx} app=${app} view=${view.data} reload=${view.reload} />` : null}
  </section>`;
}

/** The opened panel: it reads the parameters and the diagnostics when it mounts. */
function TuneBody({ ctx, app, id, panel }) {
  const params = useResource(ctx.api, '/api/params');
  // one resource per step; a panel's steps never change, so the hooks keep their order
  const views = panel.steps.map((step) => useResource(ctx.api, `/api/method/${step}`));
  const editor = useParamEdits(ctx, params, app.toaster);
  const [global, setGlobal] = useState({});
  const dirty = editor.dirty || Object.keys(global).length > 0;
  const dirtyRef = useRef(false);
  dirtyRef.current = dirty;
  useEffect(() => ctx.guard({ dirty: () => dirtyRef.current }), []);
  const rows = paramRows(params.data, panel.stages);
  const save = async () => {
    if (await editor.save(id === 'texts' ? global : null)) setGlobal({});
  };
  const combined = { ...editor, dirty, undo: () => { editor.undo(); setGlobal({}); } };
  const layout = id === 'map' ? views[0] : null;
  return html`
    ${params.error ? html`<${ErrorCard} error=${params.error} compact onRetry=${params.reload} />` : null}
    ${!params.data && !params.error ? html`<p class="cx-settings__muted" aria-busy="true">${t('common.loading')}</p>` : null}
    ${params.data ? html`<p class="cx-settings__note">${t(`tune.lead.${id}`)}</p>
      ${layout && layout.data ? html`<${MapSettings} ctx=${ctx} app=${app} view=${layout.data} />
        <h4 class="cx-method-subtitle">${t('method.map.placement')}</h4>` : null}
      ${rows.length ? html`<${ParamTable} rows=${rows} edits=${editor.edits} setEdit=${editor.setEdit}
        label=${t(`tune.title.${id}`)} tiers />` : html`<p class="cx-settings__muted">${t('method.params.none')}</p>`}
      ${id === 'texts' ? html`<${WholeBuild} data=${params.data} global=${global} setGlobal=${setGlobal} />` : null}
      <${ParamActions} editor=${combined} onSave=${save} />
      <div class="cx-tune__rebuild">
        <${Button} icon="undo" onClick=${() => ctx.navigate(rebuildHref(panel.from))}>${t('method.rebuild')}<//>
        <p class="cx-settings__muted">${t('method.rebuild_help')}</p>
      </div>` : null}
    <h3 class="cx-tune__subtitle">${t('tune.produced')}</h3>
    ${panel.steps.map((step, i) => html`<${Produced} key=${step} ctx=${ctx} app=${app} step=${step}
      view=${views[i]} from=${panel.from} titled=${panel.steps.length > 1} />`)}`;
}

/** The note of a page whose outputs need an update: the first stage to rebuild, and a button. */
function OutOfDate({ ctx, stage }) {
  const name = stageWords(stage);
  return html`<div class="cx-tune__stale" role="status">
    <${StatusDot} state="needs_update" size="s" label=${t('state.needs_update')} />
    <p>${t('tune.stale', { stage: name })}</p>
    <${Button} size="s" onClick=${() => ctx.navigate(rebuildHref(stage.id))}>${t('tune.rebuild_from', { stage: name })}<//>
  </div>`;
}

/**
 * The « Tune » panel of the page *id* (`texts`, `keywords`, `themes`, `map`); *ctx* is the
 * page's context.
 */
export function TunePanel({ ctx, id }) {
  const { app } = ctx;
  const panel = PANELS[id];
  const uid = useUid('cx-tune');
  const [open, setOpen] = useState(() => Boolean(ctx.query && ctx.query.get('tune') === '1'));
  const { project } = app.stores;
  const n = changedCount(panel, project.state.value);
  const stages = project.stages.value;
  const stale = firstStale(stages, panel.upTo);
  return html`<section class=${`cx-tune ${open ? 'is-open' : ''}`} data-tune=${id}
      aria-labelledby=${`${uid}-title`}>
    ${stale ? html`<${OutOfDate} ctx=${ctx} stage=${stale} />` : null}
    <div class="cx-tune__head">
      <h2 class="cx-tune__title" id=${`${uid}-title`}>
        <button type="button" class="cx-tune__toggle" aria-expanded=${open ? 'true' : 'false'}
          aria-controls=${`${uid}-body`} onClick=${() => setOpen(!open)}>
          <${Icon} name=${open ? 'chevron-down' : 'chevron-right'} />
          <span>${t(`tune.title.${id}`)}</span>
          <span class=${`cx-tune__count ${n ? 'is-changed' : ''}`}>${n ? t('tune.changed', { n }) : t('tune.defaults')}</span>
        </button>
      </h2>
      <${StatusDot} state=${panelState(panel, stages)} size="s" label />
    </div>
    <div class="cx-tune__body" id=${`${uid}-body`} hidden=${!open}>
      ${open ? html`<${TuneBody} ctx=${ctx} app=${app} id=${id} panel=${panel} />` : null}
    </div>
  </section>`;
}
