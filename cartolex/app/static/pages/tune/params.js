// SPDX-License-Identifier: MIT
/**
 * Editing build parameters (`GET/PUT /api/params`), shared by the pages'
 * « Tune » panels and the settings' build options: each parameter in the
 * shared ParamField (`components/param-field.js`: the control of its shape,
 * where the value comes from, the marks, « back to default ») under its short
 * label (`param.label.<stage>.<name>`, the code name beside it, small),
 * grouped by stage and section, and in a panel by tier (essential, « More »,
 * « Advanced »). The edits are kept until saved (with the version read,
 * `If-Match`) or undone.
 */

import { html, signal, useEffect, useState } from '../../core/preact.js';
import { formatNumber, has, t } from '../../core/i18n.js';
import { Button, ParamField } from '../../components/index.js';
import { State, refusal } from '../settings/common.js';

/**
 * The parameters last saved on this page visit, by a « Tune » panel or the themes playground:
 * `{data, etag}` (the answer of `PUT /api/params`). Each reader of `GET /api/params` follows
 * it, so a change saved in one shows in the others.
 */
export const paramsSaved = signal(null);

/** Follow the saved parameters in *params* (a resource: `{etag, set}`). */
export function useParamsFollow(params) {
  useEffect(() => paramsSaved.subscribe((saved) => {
    if (saved && saved.etag && saved.etag !== params.etag) params.set(saved.data, saved.etag);
  }), [params.etag]);
}

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

/**
 * A parameter's short label in the interface language (`param.label.<group>.<name>`, where the
 * group is a stage id, `build` or `layout`); its code name when the catalogue has none.
 */
export function paramLabel(group, name) {
  const key = `param.label.${group}.${name}`;
  return has(key) ? t(key) : name;
}

/** The label of a field: the short label, then the code name, small (for the docs and the recipe). */
export function LabelWithCode({ group, name }) {
  const label = paramLabel(group, name);
  return label === name ? html`<code>${name}</code>`
    : html`<span class="cx-param__label">${label}</span> <code class="cx-param__code">${name}</code>`;
}

/** One parameter's field (the shared ParamField), keyed `<stage>.<name>`. */
export function ParamRow({ stage, p, edits, setEdit }) {
  const key = `${stage.id}.${p.name}`;
  const id = `cx-param-${key.replace(/[^a-z0-9]/gi, '-')}`;
  return html`<${ParamField} p=${p} id=${id} dataKey=${key} edit=${edits[key]}
    label=${html`<${LabelWithCode} group=${stage.id} name=${p.name} />`} controlLabel=${paramLabel(stage.id, p.name)}
    help=${explanation(stage.id, p)} onEdit=${(e) => setEdit(key, e)} />`;
}

/** The heading of a group of rows: the parameter's section, or its stage's name. */
function sectionTitle(stage, section) {
  return section ? t(`method.section.${section}`) : t(`stage.${stage.id}`);
}

/** Rows under a heading where the stage or the parameter's section changes. */
function Grouped({ rows, edits, setEdit }) {
  const body = [];
  let last = null;
  for (const { stage, p } of rows) {
    const group = `${stage.id}/${p.section || ''}`;
    if (group !== last) {
      body.push(html`<h4 key=${`section-${group}`} class="cx-method-params__section">${sectionTitle(stage, p.section)}</h4>`);
      last = group;
    }
    body.push(html`<${ParamRow} key=${`${stage.id}.${p.name}`} stage=${stage} p=${p} edits=${edits} setEdit=${setEdit} />`);
  }
  return body;
}

/** Whether a row is changed from its default, or edited and not saved. */
function touched({ stage, p }, edits) {
  return p.differs || p.set_in_file || Boolean(edits[`${stage.id}.${p.name}`]);
}

/**
 * The parameters of *rows* (`{stage, p}`), grouped by stage and section. With *tiers*, the
 * essential ones first, then « More » (intermediate) and « Advanced » folded; a fold opens
 * when it holds a changed parameter.
 */
export function ParamTable({ rows, edits, setEdit, label, tiers = false }) {
  if (!tiers) {
    return html`<div class="cx-method-params" role="group" aria-label=${label}>
      <${Grouped} rows=${rows} edits=${edits} setEdit=${setEdit} /></div>`;
  }
  const of = (tier) => rows.filter((r) => (r.p.tier || 'advanced') === tier);
  const fold = (tier) => {
    const list = of(tier);
    if (!list.length) return null;
    return html`<details class="cx-method-tier" open=${list.some((r) => touched(r, edits)) || undefined}>
      <summary class="cx-method-tier__summary">${t(`method.tier.${tier}`, { n: list.length })}</summary>
      <${Grouped} rows=${list} edits=${edits} setEdit=${setEdit} /></details>`;
  };
  return html`<div class="cx-method-params" role="group" aria-label=${label}>
    <${Grouped} rows=${of('essential')} edits=${edits} setEdit=${setEdit} />
    ${fold('intermediate')}${fold('advanced')}</div>`;
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
      paramsSaved.value = { data: result.data, etag: result.etag };
      setEdits({});
      toaster.show({ kind: 'success', title: t('settings.build.saved') });
      // the stages after a changed value need an update now: their states say so at once
      if (ctx.app && ctx.app.stores) ctx.app.stores.project.refresh();
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
