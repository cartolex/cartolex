// SPDX-License-Identifier: MIT
/**
 * Editing build parameters (`GET/PUT /api/params`), shared by the method
 * screen and the settings' build options: each parameter's value in the input
 * of its kind, where the value comes from (a default, a rule and its reason,
 * `params.json`), its limits, a mark when it differs from its default and when
 * the last build used another value, and « back to default ». The edits are
 * kept until saved (with the version read, `If-Match`) or undone.
 */

import { html, useState } from '../../core/preact.js';
import { formatNumber, has, t } from '../../core/i18n.js';
import { Button, Checkbox, Icon, Input, Select } from '../../components/index.js';
import { State, refusal } from '../settings/common.js';

/** Parameters the stages record but people set elsewhere (the global seed, the year). */
const GLOBAL = new Set(['seed', 'year']);

/** A value in a few characters. */
export function shown(value) {
  if (value === null || value === undefined) return '—';
  if (Array.isArray(value)) return value.map(shown).join(', ');
  if (typeof value === 'object') return JSON.stringify(value);
  if (typeof value === 'number') return formatNumber(value);
  if (typeof value === 'boolean') return value ? t('settings.build.on') : t('method.off');
  return String(value);
}

/** Where a value comes from, in words. */
export function origin(p) {
  if (p.from === 'rule') return t('settings.origin.rule_named', { rule: p.rule_description || p.rule || '' });
  return t(`settings.origin.${p.from}`);
}

/** A parameter's one-line explanation, in the interface language when the catalogue has it. */
export function explanation(stageId, p) {
  const key = `param.${stageId}.${p.name}`;
  return has(key) ? t(key) : p.description || '';
}

/** The editor of one parameter: its kind's input, the edit kept in *edit*. */
function ParamInput({ p, id, edit, onEdit }) {
  const value = edit && 'value' in edit ? edit.value : p.value;
  const label = `${p.name}`;
  if (p.choices && p.choices.length && p.type !== 'list') {
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

/** The marks of a parameter: changed from its default, not built with this value yet. */
function Marks({ p, edit }) {
  const pending = edit && !edit.reset;
  const differs = pending ? JSON.stringify(edit.value) !== JSON.stringify(p.default_value) : p.differs && !(edit && edit.reset);
  return html`${differs ? html`<div class="cx-method-mark cx-method-mark--changed">
      <${Icon} name="dot" /><span>${t('method.param.changed', { value: shown(p.default_value) })}</span></div>` : null}
    ${!pending && p.changed_since_last_run ? html`<div class="cx-method-mark">
      <${Icon} name="info" /><span>${t('method.param.not_built', { value: shown(p.last_run) })}</span></div>` : null}
    ${pending ? html`<div class="cx-method-mark"><${Icon} name="info" /><span>${t('method.param.unsaved')}</span></div>` : null}`;
}

/** One parameter's row. */
export function ParamRow({ stage, p, edits, setEdit }) {
  const key = `${stage.id}.${p.name}`;
  const edit = edits[key];
  const id = `cx-param-${key.replace(/[^a-z0-9]/gi, '-')}`;
  const differs = edit ? !edit.reset && JSON.stringify(edit.value) !== JSON.stringify(p.default_value) : p.differs;
  return html`<tr class=${differs ? 'is-changed' : ''} data-param=${key}>
    <th scope="row"><label for=${id}><code>${p.name}</code></label>
      <div class="cx-settings__muted">${explanation(stage.id, p)}</div></th>
    <td><${ParamInput} p=${p} id=${id} edit=${edit} onEdit=${(e) => setEdit(key, e)} />
      ${edit && edit.invalid ? html`<div><${State} kind="warning">${t('settings.build.invalid')}<//></div>` : null}</td>
    <td>${edit && edit.reset ? t('settings.origin.default') : origin(p)}
      ${p.waits_for ? html`<div class="cx-settings__muted">${t('settings.build.waits', { sizes: p.waits_for.join(', ') })}</div>` : null}
      ${p.minimum !== null && p.minimum !== undefined ? html`<div class="cx-settings__muted">
        ${t('settings.build.limits', { min: shown(p.minimum), max: shown(p.maximum) })}</div>` : null}
      <${Marks} p=${p} edit=${edit} /></td>
    <td>${p.set_in_file || (edit && !edit.reset) ? html`<${Button} size="s" variant="ghost"
      onClick=${() => setEdit(key, { reset: true })}>${t('settings.build.default')}<//>` : null}</td>
  </tr>`;
}

/** A table of parameters: *rows* are `{stage, p}`. */
export function ParamTable({ rows, edits, setEdit, label }) {
  return html`<table class="cx-settings__table cx-settings__params cx-method-params" aria-label=${label}>
    <thead><tr><th scope="col">${t('settings.build.param')}</th><th scope="col">${t('settings.build.value')}</th>
      <th scope="col">${t('settings.build.origin')}</th><th scope="col"><span class="cx-visually-hidden">${t('settings.build.default')}</span></th></tr></thead>
    <tbody>${rows.map(({ stage, p }) => html`<${ParamRow} key=${`${stage.id}.${p.name}`}
      stage=${stage} p=${p} edits=${edits} setEdit=${setEdit} />`)}</tbody>
  </table>`;
}

/** The rows of *stageIds*' parameters (the seed and the year are set once for the build). */
export function paramRows(data, stageIds) {
  const out = [];
  for (const id of stageIds) {
    const stage = data && (data.stages || []).find((s) => s.id === id);
    if (!stage) continue;
    for (const p of stage.params) if (!GLOBAL.has(p.name)) out.push({ stage, p });
  }
  return out;
}

/**
 * The edits of the parameters read in *params* (a resource: `{data, etag, set, reload}`), and
 * saving them: `{edits, setEdit, dirty, save, undo, problem}`.
 */
export function useParamEdits(ctx, params, toaster) {
  const [edits, setEdits] = useState({});
  const [problem, setProblem] = useState(null);
  const data = params.data;
  const setEdit = (key, edit) => setEdits({ ...edits, [key]: edit });

  const save = async (global = null) => {
    setProblem(null);
    if (Object.values(edits).some((e) => e.invalid)) {
      setProblem(t('settings.build.invalid'));
      return false;
    }
    const stages = {};
    for (const stage of data.stages) {
      for (const p of stage.params) {
        if (GLOBAL.has(p.name)) continue;
        const edit = edits[`${stage.id}.${p.name}`];
        const keep = edit ? !edit.reset : p.set_in_file;
        if (!keep) continue;
        stages[stage.id] = { ...(stages[stage.id] || {}), [p.name]: edit ? edit.value : p.value };
      }
    }
    const body = { seed: data.global.seed.value, pinned_year: data.global.pinned_year.value, ...(global || {}), stages };
    const result = await ctx.api.put('/api/params', body, { ifMatch: params.etag });
    if (result.ok) {
      params.set(result.data, result.etag);
      setEdits({});
      toaster.show({ kind: 'success', title: t('settings.build.saved') });
      return true;
    }
    if (result.kind === 'stale') {
      setProblem(t('settings.stale'));
      params.reload();
    } else {
      setProblem((result.error && result.error.message) || refusal(result.error));
    }
    return false;
  };
  return { edits, setEdit, dirty: Object.keys(edits).length > 0, save, undo: () => setEdits({}), problem };
}

/** Save and undo, and the problem of the last save. */
export function ParamActions({ editor, onSave }) {
  return html`${editor.problem ? html`<p class="cx-settings__problem" role="alert">
      <${State} kind="warning">${editor.problem}<//></p>` : null}
    <div class="cx-settings__actions">
      <${Button} variant="primary" disabled=${!editor.dirty} onClick=${onSave || (() => editor.save())}>${t('common.save')}<//>
      ${editor.dirty ? html`<${Button} variant="ghost" onClick=${editor.undo}>${t('settings.build.undo')}<//>` : null}
    </div>`;
}
