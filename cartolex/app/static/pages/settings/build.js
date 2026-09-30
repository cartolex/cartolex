/**
 * Sizes, machine limits and build options. The main options (theme depth and
 * group counts, the counting unit, the map's layout method) are edited here;
 * every other parameter is on the method screen (`/method`), step by step,
 * beside what each step produced.
 */

import { html, useState } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { Button, Select } from '../../components/index.js';
import { Block, State, refusal, useResource } from './common.js';
import { ParamActions, ParamTable, shown, useParamEdits } from '../method/params.js';

/** The main options: (stage, parameter). */
const MAIN = [
  ['themes.group', 'depth'], ['themes.group', 'top_groups'], ['themes.group', 'keywords_per_group'],
  ['keywords.extract', 'counting_unit'],
];

export function BuildSection({ ctx, app }) {
  const params = useResource(ctx.api, '/api/params');
  const machine = useResource(ctx.api, '/api/machine');
  const maps = useResource(ctx.api, '/api/map/versions');
  const editor = useParamEdits(ctx, params, app.toaster);
  const [problem, setProblem] = useState(null);
  const [method, setMethod] = useState(null);
  const data = params.data;
  const find = (stageId, name) => {
    const stage = data && (data.stages || []).find((s) => s.id === stageId);
    const p = stage && stage.params.find((x) => x.name === name);
    return p ? { stage, p } : null;
  };

  const tryMethod = async () => {
    const result = await ctx.api.post('/api/map/versions', { action: 'try', method, note: '' }, { ifMatch: maps.etag });
    if (result.ok) {
      maps.set(result.data, result.etag);
      app.toaster.show({ kind: 'success', title: t('settings.layout.tried', { version: result.data.done || '' }) });
    } else setProblem(refusal(result.error));
  };

  const sizes = data ? data.sizes || {} : {};
  const limits = machine.data ? machine.data.limits : null;
  const pinned = maps.data && maps.data.versions.find((v) => v.pinned);
  const methods = maps.data ? maps.data.methods : [];
  const rule = maps.data ? maps.data.default_method : null;
  return html`<div class="cx-settings__grid">
    <${Block} title=${t('settings.build.sizes')} resource=${params}>
      ${data ? html`<dl class="cx-settings__facts cx-settings__facts--grid">
        ${Object.entries(sizes).map(([k, v]) => html`<div key=${k}><dt><code>${k}</code></dt><dd>${shown(v)}</dd></div>`)}
      </dl>` : null}
    <//>
    <${Block} title=${t('settings.build.machine')} resource=${machine}>
      ${limits ? html`<dl class="cx-settings__facts">
        <div><dt>${t('settings.build.cpus')}</dt><dd>${shown(limits.cpus)}</dd></div>
        <div><dt>${t('settings.build.memory')}</dt><dd>${limits.available_memory_mb === null ? '—'
          : t('settings.build.mb', { n: limits.available_memory_mb })}</dd></div>
        <div><dt>${t('settings.build.budget')}</dt><dd>${limits.budget_mb === null ? '—'
          : t('settings.build.mb', { n: limits.budget_mb })}
          <div class="cx-settings__muted">${t(`settings.build.budget_from.${limits.budget_from === 'launch' ? 'launch' : 'memory'}`)}</div></dd></div>
      </dl>` : null}
    <//>
    <${Block} title=${t('settings.build.options')} resource=${params} class="cx-settings__wide">
      ${data ? html`<p class="cx-settings__note">${t('settings.build.options_lead')}</p>
        <${ParamTable} rows=${MAIN.map(([st, n]) => find(st, n)).filter(Boolean)} edits=${editor.edits}
          setEdit=${editor.setEdit} label=${t('settings.build.options')} />
        <${ParamActions} editor=${editor} />
        <p class="cx-settings__note">${t('settings.build.method_lead')}
          <a href="/method" onClick=${(e) => {
            if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
            e.preventDefault();
            ctx.navigate('/method');
          }}>${t('settings.build.method_link')}</a></p>` : null}
    <//>
    <${Block} title=${t('settings.layout.title')} resource=${maps}>
      ${maps.data ? html`<p class="cx-settings__note">${pinned
        ? t('settings.layout.pinned', { method: t(`settings.layout.method.${pinned.layout.method}`), version: pinned.id })
        : t('settings.layout.none')}</p>
        ${rule ? html`<p class="cx-settings__note">${rule.tsne_available
          ? t('settings.layout.rule', { n: rule.tsne_from_people }) : t('settings.layout.rule_no_tsne')}</p>` : null}
        ${pinned ? html`<div class="cx-settings__inline">
          <label class="cx-settings__inline-label">${t('settings.layout.method')}
            <${Select} value=${method || pinned.layout.method}
              options=${methods.map((m) => ({ value: m, label: t(`settings.layout.method.${m}`) }))}
              onChange=${(e) => setMethod(e.currentTarget.value)} /></label>
          <${Button} disabled=${!method || method === pinned.layout.method} onClick=${tryMethod}>${t('settings.layout.try')}<//>
        </div><p class="cx-settings__muted">${t('settings.layout.try_help')}</p>` : null}
        ${problem ? html`<p class="cx-settings__problem" role="alert"><${State} kind="warning">${problem}<//></p>` : null}` : null}
    <//>
  </div>`;
}
