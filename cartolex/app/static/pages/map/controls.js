// SPDX-License-Identifier: MIT
/**
 * The atlas page's controls: what the map shows (one checkbox per kind, with
 * its symbol and how many it shows), points or regions, the organisations'
 * level, the people's filters (from their own columns), the keywords'
 * categories and colour, the period and the button that clears every filter,
 * the period included.
 */
import { html } from '../../core/preact.js';
import { formatNumber, locale, t } from '../../core/i18n.js';
import { Button, Checkbox, MapSymbol, MenuButton, Select } from '../../components/index.js';
import { KINDS, SHAPE_OF, filterGroups, filtered } from './state.js';
import { KEYWORD_CATEGORIES, levelLabel, periodOf } from './model.js';

/** A group of buttons of which one is pressed. */
export function Segmented({ label, options, value, onChange, class: cls = '' }) {
  return html`<div class=${`cx-atlas-segmented ${cls}`} role="group" aria-label=${label}>
    ${options.map((o) => html`<button key=${o.value} type="button"
      class=${`cx-atlas-segmented__item ${o.value === value ? 'is-pressed' : ''}`}
      aria-pressed=${String(o.value === value)} onClick=${() => onChange(o.value)}>${o.label}</button>`)}
  </div>`;
}

/** Which kinds the project has at all (a kind without items is not offered). */
export function available(index, texts) {
  return {
    people: index.people.length > 0,
    keywords: index.keywords.length > 0,
    organisations: index.orgs.some((o) => o.x !== null),
    texts: texts === null || texts.id.length > 0,
    projected: index.projected.length > 0,
    windows: index.windows.size > 0,
  };
}

function Kinds({ index, state, counts, texts, onChange }) {
  const has = available(index, texts);
  const toggle = (kind, on) => {
    const show = on ? [...state.show, kind] : state.show.filter((k) => k !== kind);
    onChange({ show: KINDS.filter((k) => show.includes(k)) });
  };
  return html`<fieldset class="cx-atlas-kinds">
    <legend class="cx-atlas-controls__label">${t('map.show')}</legend>
    ${KINDS.filter((k) => has[k]).map((kind) => {
      const c = counts[kind];
      const label = html`<span class="cx-atlas-kind"><${MapSymbol} shape=${SHAPE_OF[kind]} />
        ${t(`map.kind.${kind}`)}
        ${c ? html`<span class="cx-atlas-kind__count">${formatNumber(c.shown)}</span>` : null}</span>`;
      return html`<${Checkbox} key=${kind} label=${label} checked=${state.show.includes(kind)}
        onChange=${(e) => toggle(kind, e.currentTarget.checked)} />`;
    })}
  </fieldset>`;
}

function Filters({ index, state, onChange }) {
  const groups = filterGroups(state.filters);
  if (!index.columns.length) return null;
  return html`<div class="cx-atlas-filters" role="group" aria-label=${t('map.filters')}>
    <span class="cx-atlas-controls__label" aria-hidden="true">${t('map.filters')}</span>
    ${index.columns.map((col) => {
      const chosen = groups.get(col.column) || new Set();
      const items = col.values.map((v) => ({ id: v.value, kind: 'checkbox', checked: chosen.has(v.value),
        label: t('map.filter.value', { value: v.value, count: v.count }) }));
      const label = chosen.size ? t('map.filter.chosen', { column: col.column, count: chosen.size })
        : col.column;
      return html`<${MenuButton} key=${col.column} size="s" variant=${chosen.size ? 'primary' : 'secondary'}
        label=${label} items=${items}
        onSelect=${(item) => {
          const f = `${col.column}:${item.id}`;
          onChange({ filters: state.filters.includes(f) ? state.filters.filter((x) => x !== f)
            : [...state.filters, f] });
        }} />`;
    })}
  </div>`;
}

/** The keywords' categories shown (none chosen: every one), and their colour: theme or category. */
function KeywordCategories({ index, state, onChange }) {
  const counts = index.categoryCounts || {};
  const known = KEYWORD_CATEGORIES.filter((c) => counts[c]);
  if (!state.show.includes('keywords') || !known.some((c) => c !== 'none')) return null;
  const chosen = new Set(state.kc || []);
  const items = known.map((c) => ({ id: c, kind: 'checkbox', checked: chosen.has(c),
    label: t('map.filter.value', { value: t(`keywords.category.${c}`), count: counts[c] }) }));
  return html`<div class="cx-atlas-filters" role="group" aria-label=${t('map.categories')}>
    <${MenuButton} size="s" variant=${chosen.size ? 'primary' : 'secondary'}
      label=${chosen.size ? t('map.categories.chosen', { count: chosen.size }) : t('map.categories')}
      items=${items}
      onSelect=${(item) => onChange({ kc: chosen.has(item.id) ? [...chosen].filter((c) => c !== item.id)
        : [...chosen, item.id] })} />
    <${Segmented} label=${t('map.categories.colour')} value=${state.kcol}
      options=${[{ value: 'theme', label: t('map.categories.by_theme') },
        { value: 'category', label: t('map.categories.by_category') }]}
      onChange=${(kcol) => onChange({ kcol })} />
  </div>`;
}

/** Two sliders, the first and the last year; the period filters texts and time windows. */
function Period({ index, state, onChange }) {
  const period = periodOf(index, state);
  if (!period || index.years.min === index.years.max) return null;
  const { min, max } = index.years;
  return html`<fieldset class="cx-atlas-period">
    <legend class="cx-atlas-controls__label">${t('map.period')}</legend>
    <label class="cx-atlas-period__field">
      <span class="cx-visually-hidden">${t('map.period.from')}</span>
      <input type="range" min=${min} max=${max} step="1" value=${period[0]}
        aria-valuetext=${String(period[0])}
        onInput=${(e) => {
          const v = Number(e.currentTarget.value);
          onChange({ from: v === min ? null : v, to: state.to !== null && state.to < v ? v : state.to });
        }} />
    </label>
    <label class="cx-atlas-period__field">
      <span class="cx-visually-hidden">${t('map.period.to')}</span>
      <input type="range" min=${min} max=${max} step="1" value=${period[1]}
        aria-valuetext=${String(period[1])}
        onInput=${(e) => {
          const v = Number(e.currentTarget.value);
          onChange({ to: v === max ? null : v, from: state.from !== null && state.from > v ? v : state.from });
        }} />
    </label>
    <output class="cx-atlas-period__value">${t('map.period.value', { from: String(period[0]), to: String(period[1]) })}</output>
  </fieldset>`;
}

/** The controls' bar. */
export function Controls({ index, state, counts, texts, onChange }) {
  const world = state.view === 'world';
  const orgLevels = index.levels.filter((lv) => lv.count > 0);
  const showsOrgs = world || state.show.includes('organisations');
  return html`<div class="cx-atlas-controls">
    ${world ? null : html`<${Kinds} index=${index} state=${state} counts=${counts} texts=${texts}
      onChange=${onChange} />`}
    <div class="cx-atlas-controls__row">
      ${world ? null : html`<${Segmented} label=${t('map.as')} value=${state.as}
        options=${[{ value: 'points', label: t('map.as.points') }, { value: 'regions', label: t('map.as.regions') }]}
        onChange=${(as) => onChange({ as })} />`}
      ${showsOrgs && orgLevels.length > 1 ? html`<label class="cx-atlas-level">
        <span class="cx-atlas-controls__label">${t('map.org_level')}</span>
        <${Select} value=${state.org || orgLevels[0].id}
          options=${orgLevels.map((lv) => ({ value: lv.id, label: levelLabel(lv, locale.value) }))}
          onChange=${(e) => onChange({ org: e.currentTarget.value === orgLevels[0].id ? '' : e.currentTarget.value })} />
      </label>` : null}
      <${Filters} index=${index} state=${state} onChange=${onChange} />
      ${world ? null : html`<${KeywordCategories} index=${index} state=${state} onChange=${onChange} />`}
      <${Period} index=${index} state=${state} onChange=${onChange} />
      <${Button} size="s" variant="ghost" icon="undo" disabled=${!filtered(state)}
        onClick=${() => onChange({ filters: [], kc: [], from: null, to: null })}>${t('map.reset')}<//>
    </div>
  </div>`;
}
