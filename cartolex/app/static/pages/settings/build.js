/**
 * Sizes, machine limits and build options. The main options (theme depth and
 * group counts, the counting unit, the map's layout method) come first; the
 * advanced view lists every parameter of every stage with its value, where
 * the value comes from (a default, a rule and its reason, `params.json`) and
 * its limits, each one editable and each one back to its default in one step.
 */

import { html, useState } from '../../core/preact.js';
import { formatNumber, t } from '../../core/i18n.js';
import { Button, Checkbox, Input, Select } from '../../components/index.js';
import { Block, State, refusal, useResource } from './common.js';

/** The main options: (stage, parameter). */
const MAIN = [
  ['themes.group', 'depth'], ['themes.group', 'top_groups'], ['themes.group', 'keywords_per_group'],
  ['keywords.extract', 'counting_unit'],
];

/** A value in a few characters. */
export function shown(value) {
  if (value === null || value === undefined) return '—';
  if (Array.isArray(value)) return value.map(shown).join(', ');
  if (typeof value === 'object') return JSON.stringify(value);
  if (typeof value === 'number') return formatNumber(value);
  return String(value);
}

/** Where a value comes from, in words. */
function origin(p) {
  if (p.from === 'rule') return t('settings.origin.rule_named', { rule: p.rule_description || p.rule || '' });
  return t(`settings.origin.${p.from}`);
}

/** The editor of one parameter: its kind's input, the edit kept in *edits*. */
function ParamInput({ p, id, edit, onEdit }) {
  const value = edit && 'value' in edit ? edit.value : p.value;
  const label = `${p.name}`;
  if (p.choices && p.choices.length) {
    return html`<${Select} id=${id} aria-label=${label} value=${String(value)}
      options=${p.choices.map((c) => ({ value: String(c), label: String(c) }))}
      onChange=${(e) => onEdit({ value: e.currentTarget.value })} />`;
  }
  if (p.type === 'bool') {
    return html`<${Checkbox} label=${t('settings.build.on')} checked=${Boolean(value)}
      onChange=${(e) => onEdit({ value: e.currentTarget.checked })} />`;
  }
  if (p.type === 'int' || p.type === 'float') {
    return html`<${Input} id=${id} aria-label=${label} type="number" class="cx-settings__number"
      value=${value === null || value === undefined ? '' : value} min=${p.minimum ?? undefined} max=${p.maximum ?? undefined}
      step=${p.type === 'int' ? 1 : 'any'}
      onInput=${(e) => onEdit({ value: e.currentTarget.value === '' ? null : Number(e.currentTarget.value) })} />`;
  }
  return html`<${Input} id=${id} aria-label=${label} spellcheck="false" class="cx-settings__json"
    value=${edit && 'text' in edit ? edit.text : JSON.stringify(value)}
    onInput=${(e) => {
      const text = e.currentTarget.value;
      try {
        onEdit({ value: JSON.parse(text), text });
      } catch {
        onEdit({ text, invalid: true });
      }
    }} />`;
}

function ParamRow({ stage, p, edits, setEdit }) {
  const key = `${stage.id}.${p.name}`;
  const edit = edits[key];
  const id = `cx-param-${key.replace(/[^a-z0-9]/gi, '-')}`;
  return html`<tr>
    <th scope="row"><label for=${id}><code>${p.name}</code></label>
      <div class="cx-settings__muted">${p.description || ''}</div></th>
    <td><${ParamInput} p=${p} id=${id} edit=${edit} onEdit=${(e) => setEdit(key, e)} />
      ${edit && edit.invalid ? html`<div><${State} kind="warning">${t('settings.build.invalid')}<//></div>` : null}</td>
    <td>${edit && edit.reset ? t('settings.origin.default') : origin(p)}
      ${p.waits_for ? html`<div class="cx-settings__muted">${t('settings.build.waits', { sizes: p.waits_for.join(', ') })}</div>` : null}
      ${p.minimum !== null && p.minimum !== undefined ? html`<div class="cx-settings__muted">
        ${t('settings.build.limits', { min: shown(p.minimum), max: shown(p.maximum) })}</div>` : null}</td>
    <td>${p.set_in_file || (edit && !edit.reset) ? html`<${Button} size="s" variant="ghost"
      onClick=${() => setEdit(key, { reset: true })}>${t('settings.build.default')}<//>` : null}</td>
  </tr>`;
}

export function BuildSection({ ctx, app }) {
  const params = useResource(ctx.api, '/api/params');
  const machine = useResource(ctx.api, '/api/machine');
  const maps = useResource(ctx.api, '/api/map/versions');
  const [edits, setEdits] = useState({});
  const [advanced, setAdvanced] = useState(false);
  const [problem, setProblem] = useState(null);
  const [method, setMethod] = useState(null);
  const data = params.data;
  const setEdit = (key, edit) => setEdits({ ...edits, [key]: edit });
  const find = (stageId, name) => {
    const stage = data && (data.stages || []).find((s) => s.id === stageId);
    const p = stage && stage.params.find((x) => x.name === name);
    return p ? { stage, p } : null;
  };

  const save = async () => {
    setProblem(null);
    if (Object.values(edits).some((e) => e.invalid)) {
      setProblem(t('settings.build.invalid'));
      return;
    }
    const stages = {};
    for (const stage of data.stages) {
      for (const p of stage.params) {
        if (p.name === 'seed' || p.name === 'year') continue;
        const edit = edits[`${stage.id}.${p.name}`];
        const keep = edit ? !edit.reset : p.set_in_file;
        if (!keep) continue;
        stages[stage.id] = { ...(stages[stage.id] || {}), [p.name]: edit ? edit.value : p.value };
      }
    }
    const body = { seed: data.global.seed.value, pinned_year: data.global.pinned_year.value, stages };
    const result = await ctx.api.put('/api/params', body, { ifMatch: params.etag });
    if (result.ok) {
      params.set(result.data, result.etag);
      setEdits({});
      app.toaster.show({ kind: 'success', title: t('settings.build.saved') });
    } else if (result.kind === 'stale') {
      setProblem(t('settings.stale'));
      params.reload();
    } else {
      const problems = result.error && result.error.message;
      setProblem(problems || refusal(result.error));
    }
  };

  const tryMethod = async () => {
    const result = await ctx.api.post('/api/map/versions', { action: 'try', method, note: '' }, { ifMatch: maps.etag });
    if (result.ok) {
      maps.set(result.data, result.etag);
      app.toaster.show({ kind: 'success', title: t('settings.layout.tried', { version: result.data.done || '' }) });
    } else setProblem(refusal(result.error));
  };

  const dirty = Object.keys(edits).length > 0;
  const sizes = data ? data.sizes || {} : {};
  const limits = machine.data ? machine.data.limits : null;
  const pinned = maps.data && maps.data.versions.find((v) => v.pinned);
  const methods = maps.data ? maps.data.methods : [];
  const rule = maps.data ? maps.data.default_method : null;
  const actions = html`<div class="cx-settings__actions">
    <${Button} variant="primary" disabled=${!dirty} onClick=${save}>${t('common.save')}<//>
    ${dirty ? html`<${Button} variant="ghost" onClick=${() => setEdits({})}>${t('settings.build.undo')}<//>` : null}
  </div>`;

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
        <table class="cx-settings__table cx-settings__params">
          <thead><tr><th scope="col">${t('settings.build.param')}</th><th scope="col">${t('settings.build.value')}</th>
            <th scope="col">${t('settings.build.origin')}</th><th scope="col"><span class="cx-visually-hidden">${t('settings.build.default')}</span></th></tr></thead>
          <tbody>${MAIN.map(([s, n]) => find(s, n)).filter(Boolean).map(({ stage, p }) => html`<${ParamRow}
            key=${`${stage.id}.${p.name}`} stage=${stage} p=${p} edits=${edits} setEdit=${setEdit} />`)}</tbody>
        </table>
        ${problem ? html`<p class="cx-settings__problem" role="alert"><${State} kind="warning">${problem}<//></p>` : null}
        ${actions}
        <${Button} variant="ghost" aria-expanded=${advanced ? 'true' : 'false'} onClick=${() => setAdvanced(!advanced)}>
          ${advanced ? t('settings.build.advanced_hide') : t('settings.build.advanced_show')}<//>` : null}
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
        </div><p class="cx-settings__muted">${t('settings.layout.try_help')}</p>` : null}` : null}
    <//>
    ${advanced && data ? html`<${Block} title=${t('settings.build.every')} class="cx-settings__wide">
      ${data.problems && data.problems.length ? html`<ul class="cx-settings__list">
        ${data.problems.map((pr, i) => html`<li key=${i}><${State} kind="warning">${typeof pr === 'string' ? pr : JSON.stringify(pr)}<//></li>`)}
      </ul>` : null}
      ${data.stages.map((stage) => html`<section key=${stage.id} class="cx-settings__stage" aria-labelledby=${`cx-stage-${stage.id}`}>
        <h4 id=${`cx-stage-${stage.id}`}>${stage.name} <code>${stage.id}</code></h4>
        <table class="cx-settings__table cx-settings__params">
          <thead><tr><th scope="col">${t('settings.build.param')}</th><th scope="col">${t('settings.build.value')}</th>
            <th scope="col">${t('settings.build.origin')}</th><th scope="col"><span class="cx-visually-hidden">${t('settings.build.default')}</span></th></tr></thead>
          <tbody>${stage.params.filter((p) => p.name !== 'seed' && p.name !== 'year').map((p) => html`<${ParamRow}
            key=${p.name} stage=${stage} p=${p} edits=${edits} setEdit=${setEdit} />`)}</tbody>
        </table>
      </section>`)}
      ${actions}
    <//>` : null}
  </div>`;
}
