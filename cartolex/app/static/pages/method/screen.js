// SPDX-License-Identifier: MIT
/**
 * The method screen (`/method?step=<id>`): the steps of the build in pipeline
 * order on the left, each with the state of its stages; on the right, one
 * step: its parameters (value, origin, limits, a mark when a value differs
 * from its default, back to default), what the step produced (its
 * diagnostic, read from the stage's outputs), and « rebuild from here »,
 * which opens the pre-flight sheet for this step and every step after it.
 *
 * Opening a step reads `GET /api/params` and `GET /api/method/<step>`; the
 * keywords step also reads a page of `GET /api/keywords`.
 */

import { html, useEffect, useRef, useState } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { usePage, usePageTitle } from '../../core/page.js';
import { Button, Card, EmptyState, ErrorCard, Input } from '../../components/index.js';
import { useResource } from '../settings/common.js';
import { ParamActions, ParamTable, paramRows, useParamEdits } from './params.js';
import { STEPS, StepDot, notBuilt, rebuildHref } from './common.js';
import { TextsDiagnostic } from './texts.js';
import { KeywordsDiagnostic } from './keywords.js';
import { SpaceDiagnostic } from './space.js';
import { GroupingDiagnostic } from './grouping.js';
import { LayoutDiagnostic } from './layout.js';

const DIAGNOSTICS = {
  texts: TextsDiagnostic,
  keywords: KeywordsDiagnostic,
  space: SpaceDiagnostic,
  grouping: GroupingDiagnostic,
  layout: LayoutDiagnostic,
};

/** The seed and the pinned year, set once for the whole build (texts step). */
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

function StepPanel({ ctx, app, step }) {
  const params = useResource(ctx.api, '/api/params');
  const view = useResource(ctx.api, `/api/method/${step.id}`);
  const editor = useParamEdits(ctx, params, app.toaster);
  const [global, setGlobal] = useState({});
  const dirty = editor.dirty || Object.keys(global).length > 0;
  const dirtyRef = useRef(false);
  dirtyRef.current = dirty;
  useEffect(() => ctx.guard({ dirty: () => dirtyRef.current }), []);
  const rows = paramRows(params.data, step.stages);
  const Diagnostic = DIAGNOSTICS[step.id];
  const save = async () => {
    if (await editor.save(step.id === 'texts' ? global : null)) setGlobal({});
  };
  const combined = { ...editor, dirty, undo: () => { editor.undo(); setGlobal({}); } };
  return html`<div class="cx-method__panels">
    <${Card} level=${3} title=${t('method.params.title')} loading=${!params.data && !params.error}
      class="cx-method-card" actions=${html`<${Button} size="s" icon="undo"
        onClick=${() => ctx.navigate(rebuildHref(step.from))}>${t('method.rebuild')}<//>`}>
      ${params.error ? html`<${ErrorCard} error=${params.error} compact onRetry=${params.reload} />` : null}
      ${params.data ? html`<p class="cx-settings__note">${t(`method.params.lead.${step.id}`)}</p>
        ${rows.length ? html`<${ParamTable} rows=${rows} edits=${editor.edits} setEdit=${editor.setEdit}
          label=${t('method.params.title')} />` : html`<p class="cx-settings__muted">${t('method.params.none')}</p>`}
        ${step.id === 'texts' ? html`<${WholeBuild} data=${params.data} global=${global} setGlobal=${setGlobal} />` : null}
        <${ParamActions} editor=${combined} onSave=${save} />
        <p class="cx-settings__muted">${t('method.rebuild_help')}</p>` : null}
    <//>
    <${Card} level=${3} title=${t('method.produced.title')} loading=${!view.data && !view.error} class="cx-method-card">
      ${view.error ? html`<${ErrorCard} error=${view.error} compact onRetry=${view.reload} />` : null}
      ${view.data && view.data.empty && step.id !== 'layout' ? html`<${EmptyState} icon="file" level=${4}
          title=${notBuilt(view.data)}
          action=${{ label: t('method.build'), onClick: () => ctx.navigate(rebuildHref(step.from)) }} />`
        : view.data ? html`<${Diagnostic} ctx=${ctx} app=${app} view=${view.data} reload=${view.reload} />` : null}
    <//>
  </div>`;
}

export function MethodScreen() {
  const ctx = usePage();
  const { app } = ctx;
  usePageTitle(t('nav.method'));
  const wanted = (ctx.query && ctx.query.get('step')) || STEPS[0].id;
  const step = STEPS.find((s) => s.id === wanted) || STEPS[0];
  const open = Boolean(app.manifest.project && app.manifest.project.open);
  const stages = app.stores.project.stages.value;
  const go = (id) => ctx.navigate(`/method?step=${id}`);
  return html`<div class="cx-page cx-settings cx-method">
    <h1 class="cx-page__title">${t('nav.method')}</h1>
    <p class="cx-page__lead">${t('method.lead')}</p>
    ${open ? html`<div class="cx-settings__body">
      <nav class="cx-settings__nav" aria-label=${t('method.steps')}>
        <ol class="cx-method__steps">
          ${STEPS.map((s, i) => html`<li key=${s.id}>
            <a href=${`/method?step=${s.id}`} class="cx-settings__link cx-method__step"
              aria-current=${s.id === step.id ? 'page' : undefined}
              onClick=${(e) => {
                if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
                e.preventDefault();
                go(s.id);
              }}><span class="cx-method__number">${i + 1}</span>
              <span class="cx-method__name">${t(`method.step.${s.id}`)}</span>
              <${StepDot} step=${s} stages=${stages} /></a></li>`)}
        </ol>
      </nav>
      <section class="cx-settings__section" aria-labelledby="cx-method-title">
        <h2 class="cx-settings__title" id="cx-method-title">${t(`method.step.${step.id}`)}</h2>
        <p class="cx-page__lead">${t(`method.lead.${step.id}`)}</p>
        <${StepPanel} key=${step.id} ctx=${ctx} app=${app} step=${step} />
      </section>
    </div>` : html`<${EmptyState} icon="file" level=${2}
      title=${t('settings.no_project.title')} action=${{ label: t('settings.no_project.action'), href: '/start' }}>
      ${t('settings.no_project.text')}<//>`}
  </div>`;
}
