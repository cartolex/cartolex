// SPDX-License-Identifier: MIT
/**
 * ParamField and ParamControl: a build parameter edited with the control its
 * shape asks for, never as code.
 *
 * A parameter is an item of `GET /api/params` (`name`, `type`, `value`,
 * `default_value`, `from`, `rule_description`, `widget`, `choices`, `keys`,
 * `suggestions`, `minimum`, `maximum`, `items`, `nullable`…), or any object of
 * that shape (the layout parameters of a map version). Its `widget` names the
 * control; without one, `shapeOf()` infers it as the server does:
 *
 * | widget   | control |
 * | -------- | ------- |
 * | `switch` | a switch |
 * | `choice` | a segmented control (four choices at most), else a select; `unavailable: {value: reason}` shows a choice switched off, with its reason |
 * | `slider` | a slider and a number field, the default marked on the slider |
 * | `number` | a number field |
 * | `text`   | a text field |
 * | `chips`  | removable chips and a field that adds one (numbers for `ints`, `floats`) |
 * | `order`  | a reorderable list: move up and down with the buttons or Alt+↑ and Alt+↓, remove, add |
 * | `range`  | two numbers, the fewest and the most |
 * | `levels` | one number per level, levels added and removed; off (`null`): computed |
 * | `grid`   | items × keys (the slot kinds) of checkboxes; « every » for a nullable key |
 *
 * A nullable number or list gets « Not set ». `ParamControl({p, value, onChange,
 * id, labelId, label})` is the control alone: `onChange(value, invalid)`, where
 * *invalid* is true when the control holds something the parameter cannot take.
 * `ParamField({p, edit, onEdit, id, label, controlLabel, help})` is the whole field: its name
 * (*label*, markup; *controlLabel*, the words naming the control, else the code name)
 * and explanation, the control, its default, where its value comes from (a
 * rule's reason), the marks (changed, unsaved, not built yet) and « Back to
 * default »; *edit* is `{value, invalid}` or `{reset: true}` (or undefined),
 * sent through `onEdit`.
 */
import { html, useEffect, useRef, useState } from '../core/preact.js';
import { formatNumber, has, t } from '../core/i18n.js';
import { Button, IconButton } from './button.js';
import { Icon } from './icons.js';
import { Input, Select } from './form-field.js';

/** The shapes edited by one field, which the parameter's name labels (the others are groups). */
const SINGLE = new Set(['switch', 'slider', 'number', 'text']);

/** Whole-number ranges at most this wide get a slider (as `SLIDER_SPAN` on the server). */
const SLIDER_SPAN = 500;

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const isNum = (p) => p.type === 'int' || p.type === 'float';
const bounded = (p) => p.minimum !== null && p.minimum !== undefined && p.maximum !== null && p.maximum !== undefined;

/** The control a parameter is edited with: its `widget`, else inferred from its type. */
export function shapeOf(p) {
  if (p.widget) return p.widget;
  if (p.type === 'bool') return 'switch';
  if (p.keys) return 'grid';
  if ((p.type === 'ints' || p.type === 'floats') && p.items && p.items[0] === 2 && p.items[1] === 2) return 'range';
  if (p.type === 'list' || p.type === 'ints' || p.type === 'floats') return 'chips';
  if (p.choices && p.choices.length) return 'choice';
  if (isNum(p)) return bounded(p) && (p.type === 'float' || p.maximum - p.minimum <= SLIDER_SPAN) ? 'slider' : 'number';
  return 'text';
}

/** An item of a list or a choice, in words when the catalogue has them. */
export function itemLabel(p, item) {
  for (const key of [`param.item.${p.name}.${item}`, `param.item.${item}`]) if (has(key)) return t(key);
  if (p.name === 'provider_priority' && has(`corpus.provider.${item}`)) return t(`corpus.provider.${item}`);
  return typeof item === 'number' ? formatNumber(item) : String(item);
}

/** « Not set », or what a parameter's empty value means when the catalogue says
 * (`param.unset.<name>`). */
function unsetLabel(p) {
  return p && has(`param.unset.${p.name}`) ? t(`param.unset.${p.name}`) : t('param.field.unset');
}

/** A value in a few words. */
export function shownValue(p, value) {
  if (value === null || value === undefined) return unsetLabel(p);
  if (typeof value === 'boolean') return value ? t('param.field.on') : t('param.field.off');
  if (typeof value === 'number') return formatNumber(value);
  if (Array.isArray(value)) return value.map((v) => itemLabel(p, v)).join(', ');
  if (typeof value === 'object') {
    return Object.entries(value).map(([k, v]) => `${itemLabel(p, k)}: ${shownValue(p, v)}`).join(' · ');
  }
  return itemLabel(p, value);
}

/** « Not set »: a nullable value switched to null, and back to *fallback*. */
function Unset({ p, value, fallback, onChange, disabled }) {
  return html`<label class="cx-param__unset">
    <input type="checkbox" checked=${value === null} disabled=${disabled}
      onChange=${(e) => onChange(e.currentTarget.checked ? null : fallback)} />
    <span>${unsetLabel(p)}</span></label>`;
}

function Switch({ id, label, value, onChange, disabled }) {
  return html`<label class="cx-switch">
    <input type="checkbox" role="switch" class="cx-switch__control" id=${id} aria-label=${label}
      checked=${Boolean(value)} disabled=${disabled} onChange=${(e) => onChange(e.currentTarget.checked)} />
    <span class="cx-switch__track" aria-hidden="true"><span class="cx-switch__thumb"></span></span>
    <span class="cx-switch__label">${value ? t('param.field.on') : t('param.field.off')}</span></label>`;
}

function Choice({ p, id, labelId, label, value, onChange, disabled }) {
  const off = p.unavailable || {};
  const reasons = Object.entries(off).map(([c, why]) => html`<p class="cx-param__reason" key=${c}>
    <${Icon} name="info" /><span><strong>${itemLabel(p, c)}</strong> ${why}</span></p>`);
  if (p.choices.length > 4) {
    return html`<${Select} id=${id} aria-label=${label} value=${String(value)} disabled=${disabled}
      options=${p.choices.map((c) => ({ value: String(c), label: itemLabel(p, c), disabled: c in off }))}
      onChange=${(e) => onChange(p.choices.find((c) => String(c) === e.currentTarget.value))} />${reasons}`;
  }
  return html`<fieldset class="cx-segmented cx-param__choice" id=${id} aria-labelledby=${labelId}>
    ${p.choices.map((c) => html`<label class="cx-segmented__option" key=${String(c)}>
      <input type="radio" name=${id} value=${String(c)} checked=${c === value}
        disabled=${disabled || c in off} onChange=${() => onChange(c)} />
      <span>${itemLabel(p, c)}</span></label>`)}
  </fieldset>${reasons}`;
}

/** A number in a field: `null` while empty. */
function NumberInput({ p, id, label, value, onChange, disabled, cls = '' }) {
  return html`<${Input} id=${id} aria-label=${label} type="number" class=${`cx-param__number ${cls}`}
    value=${value === null || value === undefined ? '' : value} disabled=${disabled}
    min=${p.minimum ?? undefined} max=${p.maximum ?? undefined}
    step=${p.type === 'int' || p.type === 'ints' ? 1 : 'any'}
    onInput=${(e) => {
      const raw = e.currentTarget.value;
      const n = raw === '' ? null : Number(raw);
      onChange(n, n === null || Number.isNaN(n) || !inBounds(p, n));
    }} />`;
}

function inBounds(p, n) {
  if (p.type === 'int' || p.type === 'ints') { if (!Number.isInteger(n)) return false; }
  if (p.minimum !== null && p.minimum !== undefined && n < p.minimum) return false;
  return !(p.maximum !== null && p.maximum !== undefined && n > p.maximum);
}

function Slider({ p, id, label, value, onChange, disabled }) {
  const step = p.type === 'int' ? 1 : niceStep(p.maximum - p.minimum);
  const def = typeof p.default_value === 'number' ? p.default_value : null;
  const off = value === null || value === undefined;
  return html`<div class="cx-param__slider">
    <input type="range" class="cx-param__range" aria-label=${label} min=${p.minimum} max=${p.maximum} step=${step}
      value=${off ? (def ?? p.minimum) : value} disabled=${disabled || off} list=${def !== null ? `${id}-default` : undefined}
      onInput=${(e) => onChange(Number(e.currentTarget.value))} />
    ${def !== null ? html`<datalist id=${`${id}-default`}><option value=${def}></option></datalist>` : null}
    <${NumberInput} p=${p} id=${id} label=${label} value=${value} onChange=${onChange} disabled=${disabled || off} />
  </div>`;
}

/** A step of about a hundredth of *span*, rounded to 1, 2 or 5 times a power of ten. */
function niceStep(span) {
  const raw = span / 100;
  const power = 10 ** Math.floor(Math.log10(raw));
  const unit = [1, 2, 5, 10].find((m) => m * power >= raw);
  return unit * power;
}

/** Chips, and a field that adds one. */
function Chips({ p, id, labelId, value, onChange, disabled }) {
  const items = value || [];
  const [text, setText] = useState('');
  const numeric = p.type !== 'list';
  const add = () => {
    const raw = text.trim();
    if (!raw) return;
    const item = numeric ? Number(raw) : raw;
    if (numeric && (Number.isNaN(item) || !inBounds(p, item))) return;
    if (!items.includes(item)) onChange([...items, item].sort((a, b) => (numeric ? a - b : 0)));
    setText('');
  };
  const listId = `${id}-suggestions`;
  const offered = (p.suggestions || []).filter((s) => !items.includes(s));
  return html`<div class="cx-param__chips" role="group" aria-labelledby=${labelId}>
    <ul class="cx-chips">${items.map((item) => html`<li class="cx-chip" key=${String(item)}>
      <span>${itemLabel(p, item)}</span>
      <${IconButton} icon="close" size="s" disabled=${disabled || items.length <= (p.minimum ?? 0) && !numeric}
        label=${t('param.field.remove', { item: shownValue(p, item) })}
        onClick=${() => onChange(items.filter((x) => x !== item))} /></li>`)}</ul>
    <span class="cx-param__add">
      <${Input} id=${id} aria-label=${t('param.field.add_label')} value=${text} disabled=${disabled}
        inputmode=${numeric ? 'decimal' : undefined} list=${offered.length ? listId : undefined}
        onInput=${(e) => setText(e.currentTarget.value)}
        onKeyDown=${(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }} />
      ${offered.length ? html`<datalist id=${listId}>${offered.map((s) => html`<option value=${s} key=${s}></option>`)}</datalist>` : null}
      <${Button} size="s" icon="plus" disabled=${disabled || !text.trim()} onClick=${add}>${t('param.field.add')}<//></span>
  </div>`;
}

/** A reorderable list: the first item wins. */
function Order({ p, id, labelId, value, onChange, disabled }) {
  const items = value || [];
  const list = useRef(null);
  const [focus, setFocus] = useState(-1);
  useEffect(() => {
    if (focus < 0 || !list.current) return;
    const row = list.current.children[focus];
    if (row) row.focus();
    setFocus(-1);
  }, [focus]);
  const move = (i, by) => {
    const j = i + by;
    if (j < 0 || j >= items.length) return;
    const next = [...items];
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
    setFocus(j);
  };
  const [text, setText] = useState('');
  const add = () => {
    const item = text.trim();
    if (item && !items.includes(item)) onChange([...items, item]);
    setText('');
  };
  return html`<div class="cx-param__order" role="group" aria-labelledby=${labelId}>
    <ol class="cx-order" ref=${list}>${items.map((item, i) => html`<li class="cx-order__item" key=${item} tabindex="0"
        aria-label=${t('param.field.position', { item: itemLabel(p, item), n: i + 1, total: items.length })}
        onKeyDown=${(e) => {
          if (!e.altKey || disabled) return;
          if (e.key === 'ArrowUp') { e.preventDefault(); move(i, -1); }
          if (e.key === 'ArrowDown') { e.preventDefault(); move(i, 1); }
        }}>
      <span class="cx-order__rank">${formatNumber(i + 1)}</span><span class="cx-order__name">${itemLabel(p, item)}</span>
      <${IconButton} icon="chevron-up" size="s" disabled=${disabled || i === 0}
        label=${t('param.field.up', { item: itemLabel(p, item) })} onClick=${() => move(i, -1)} />
      <${IconButton} icon="chevron-down" size="s" disabled=${disabled || i === items.length - 1}
        label=${t('param.field.down', { item: itemLabel(p, item) })} onClick=${() => move(i, 1)} />
      <${IconButton} icon="close" size="s" disabled=${disabled}
        label=${t('param.field.remove', { item: itemLabel(p, item) })} onClick=${() => onChange(items.filter((x) => x !== item))} />
    </li>`)}</ol>
    <p class="cx-param__hint">${t('param.field.order_help')}</p>
    <span class="cx-param__add">
      <${Input} id=${id} aria-label=${t('param.field.add_label')} value=${text} disabled=${disabled}
        onInput=${(e) => setText(e.currentTarget.value)}
        onKeyDown=${(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }} />
      <${Button} size="s" icon="plus" disabled=${disabled || !text.trim()} onClick=${add}>${t('param.field.add')}<//></span>
  </div>`;
}

/** Two numbers, the fewest and the most. */
function Range({ p, id, labelId, value, onChange, disabled }) {
  const [low, high] = value || [p.minimum ?? 0, p.maximum ?? 0];
  const set = (i, n, bad) => {
    const next = i === 0 ? [n, high] : [low, n];
    onChange(next, bad || next[0] === null || next[1] === null || next[0] > next[1]);
  };
  return html`<div class="cx-param__pair" role="group" aria-labelledby=${labelId}>
    <label class="cx-param__inline">${t('param.field.from')}
      <${NumberInput} p=${p} id=${id} label=${t('param.field.from')} value=${low} disabled=${disabled}
        onChange=${(n, bad) => set(0, n, bad)} /></label>
    <label class="cx-param__inline">${t('param.field.to')}
      <${NumberInput} p=${p} id=${`${id}-to`} label=${t('param.field.to')} value=${high} disabled=${disabled}
        onChange=${(n, bad) => set(1, n, bad)} /></label>
  </div>`;
}

/** One number per level, from the top; off: computed from the other parameters. */
function Levels({ p, id, labelId, value, onChange, disabled }) {
  const [lo, hi] = p.items || [1, 4];
  const on = Array.isArray(value);
  const levels = on ? value : [];
  const fallback = Array.isArray(p.last_run) ? p.last_run : [15];
  const set = (i, n, bad) => {
    const next = levels.map((v, j) => (j === i ? n : v));
    onChange(next, bad || next.some((v) => v === null));
  };
  return html`<div class="cx-param__levels" role="group" aria-labelledby=${labelId}>
    <label class="cx-param__unset"><input type="checkbox" checked=${on} disabled=${disabled}
      onChange=${(e) => onChange(e.currentTarget.checked ? fallback : null)} />
      <span>${t('param.field.levels_by_hand')}</span></label>
    ${on ? html`<ol class="cx-param__level-list">${levels.map((v, i) => html`<li key=${i}>
        <label class="cx-param__inline">${t('param.field.level', { n: i + 1 })}
          <${NumberInput} p=${{ ...p, type: 'int' }} id=${i === 0 ? id : `${id}-${i}`} label=${t('param.field.level', { n: i + 1 })}
            value=${v} disabled=${disabled} onChange=${(n, bad) => set(i, n, bad)} /></label></li>`)}</ol>
      <span class="cx-param__add">
        <${Button} size="s" icon="plus" disabled=${disabled || levels.length >= hi}
          onClick=${() => onChange([...levels, (levels[levels.length - 1] || 1) * 4])}>${t('param.field.add_level')}<//>
        <${Button} size="s" variant="ghost" disabled=${disabled || levels.length <= lo}
          onClick=${() => onChange(levels.slice(0, -1))}>${t('param.field.remove_level')}<//></span>`
      : html`<p class="cx-param__hint">${t('param.field.levels_computed')}</p>`}
  </div>`;
}

/** Items × keys of checkboxes; a nullable key may take « every » item. */
function Grid({ p, labelId, value, onChange, disabled }) {
  const keys = p.keys || [];
  const map = Array.isArray(value) || value === null || value === undefined
    ? Object.fromEntries(keys.map((k) => [k, value === undefined ? [] : value])) : value;
  const columns = [...(p.choices || p.suggestions || [])];
  for (const k of keys) for (const item of map[k] || []) if (!columns.includes(item)) columns.push(item);
  const put = (key, list) => {
    const next = { ...map, [key]: list };
    onChange(next, keys.some((k) => next[k] !== null && (next[k] || []).length < (p.minimum ?? 0)));
  };
  const toggle = (key, item, on) => {
    const list = map[key] || [];
    put(key, on ? columns.filter((c) => c === item || list.includes(c)) : list.filter((x) => x !== item));
  };
  const kind = (k) => (has(`param.kind.${k}`) ? t(`param.kind.${k}`) : k);
  return html`<div class="cx-param__grid-box"><table class="cx-param-grid" aria-labelledby=${labelId}>
    <thead><tr><td></td>${keys.map((k) => html`<th scope="col" key=${k}>${kind(k)}</th>`)}</tr></thead>
    <tbody>
      ${p.nullable ? html`<tr class="cx-param-grid__every"><th scope="row">${t('param.field.every')}</th>
        ${keys.map((k) => html`<td key=${k}><input type="checkbox" disabled=${disabled} checked=${map[k] === null}
          aria-label=${t('param.field.cell', { key: kind(k), item: t('param.field.every') })}
          onChange=${(e) => put(k, e.currentTarget.checked ? null : [...columns])} /></td>`)}</tr>` : null}
      ${columns.map((item) => html`<tr key=${item}><th scope="row">${itemLabel(p, item)}</th>
        ${keys.map((k) => html`<td key=${k}><input type="checkbox" disabled=${disabled || map[k] === null}
          checked=${map[k] === null || (map[k] || []).includes(item)}
          aria-label=${t('param.field.cell', { key: kind(k), item: itemLabel(p, item) })}
          onChange=${(e) => toggle(k, item, e.currentTarget.checked)} /></td>`)}</tr>`)}
    </tbody></table></div>`;
}

/**
 * The control of *p*, showing *value*. `onChange(value, invalid)`.
 * @param {{p: object, value: any, onChange: Function, id: string, labelId?: string, label?: string, disabled?: boolean}} props
 */
export function ParamControl({ p, value, onChange, id, labelId, label = p.name, disabled = false }) {
  const props = { p, id, labelId, label, value, onChange: (v, bad = false) => onChange(v, bad), disabled };
  const shape = shapeOf(p);
  const fallback = p.default_value ?? p.default ?? p.minimum ?? (shape === 'chips' ? [] : 0);
  const unset = p.nullable && !['levels', 'grid'].includes(shape)
    ? html`<${Unset} p=${p} value=${value} fallback=${fallback} disabled=${disabled} onChange=${(v) => onChange(v, false)} />` : null;
  const off = unset && value === null;
  const control = {
    switch: Switch, choice: Choice, slider: Slider, chips: Chips, order: Order, range: Range,
    levels: Levels, grid: Grid,
  }[shape];
  if (control) {
    return html`<div class="cx-param__control-row">${off && shape !== 'slider' ? null
      : html`<${control} ...${props} />`}${unset}</div>`;
  }
  if (shape === 'number') {
    return html`<div class="cx-param__control-row"><${NumberInput} ...${props} disabled=${disabled || off} />${unset}</div>`;
  }
  return html`<div class="cx-param__control-row"><${Input} id=${id} aria-label=${label} value=${value ?? ''} disabled=${disabled || off}
    onInput=${(e) => onChange(e.currentTarget.value, false)} />${unset}</div>`;
}

/** Where the value comes from, in words: a default, a rule and its reason, the file. */
export function originText(p) {
  if (p.from === 'rule') return t('param.field.rule', { rule: p.rule_description || p.rule || '' });
  const where = { 'params.json': 'file', version: 'version' }[p.from] || 'default';
  return t(`param.field.from_${where}`);
}

/**
 * One parameter: name, explanation, control, default, origin, marks, « Back to default ».
 * @param {{p: object, edit?: object, onEdit: Function, id: string, label?: any, help?: any, dataKey?: string}} props
 */
export function ParamField({ p, edit, onEdit, id, label, controlLabel, help, dataKey }) {
  const labelId = `${id}-label`;
  const name = label || html`<code>${p.name}</code>`;
  const pending = Boolean(edit && !edit.reset);
  const value = pending ? edit.value : edit && edit.reset ? p.default_value : p.value;
  const changed = pending || (edit && edit.reset) ? !same(value, p.default_value) : p.differs;
  return html`<div class=${`cx-param ${changed ? 'is-changed' : ''}`} data-param=${dataKey}>
    <div class="cx-param__head">
      ${SINGLE.has(shapeOf(p)) ? html`<label class="cx-param__name" id=${labelId} for=${id}>${name}</label>`
        : html`<span class="cx-param__name" id=${labelId}>${name}</span>`}
      ${help ? html`<p class="cx-param__help">${help}</p>` : null}
    </div>
    <div class="cx-param__control">
      <${ParamControl} p=${p} id=${id} labelId=${labelId} value=${value} label=${controlLabel || p.name}
        onChange=${(v, invalid) => onEdit({ value: v, invalid: Boolean(invalid) })} />
      ${pending && edit.invalid ? html`<p class="cx-param__problem" role="alert"><${Icon} name="warning" />
        <span>${t('param.field.invalid')}</span></p>` : null}
    </div>
    <div class="cx-param__meta">
      <span>${t('param.field.default', { value: shownValue(p, p.default_value) })}</span>
      <span>${edit && edit.reset ? t('param.field.from_default') : originText(p)}</span>
      ${p.waits_for ? html`<span>${t('param.field.waits', { sizes: p.waits_for.join(', ') })}</span>` : null}
      ${changed ? html`<span class="cx-param__mark cx-param__mark--changed"><${Icon} name="dot" />${t('param.field.changed')}</span>` : null}
      ${pending ? html`<span class="cx-param__mark"><${Icon} name="info" />${t('param.field.unsaved')}</span>` : null}
      ${!pending && p.changed_since_last_run ? html`<span class="cx-param__mark"><${Icon} name="info" />
        ${t('param.field.not_built', { value: shownValue(p, p.last_run) })}</span>` : null}
      ${p.set_in_file || pending ? html`<${Button} size="s" variant="ghost" icon="undo"
        onClick=${() => onEdit({ reset: true })}>${t('param.field.reset')}<//>` : null}
    </div>
  </div>`;
}
