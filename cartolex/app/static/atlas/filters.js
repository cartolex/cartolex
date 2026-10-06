// SPDX-License-Identifier: MIT
/**
 * The atlas's filters, folded under one « Filters » button in the bar: the people's own
 * columns (a person matches one chosen value of each column), the keywords' categories and
 * their colour (by theme or by category), the period (two sliders, the first and the last
 * year) and « Clear the filters ». The button says how many filters are on.
 */
import { fill, h, keepFocus } from './dom.js';
import { KEYWORD_CATEGORIES, filterGroups, periodOf } from './data.js';
import { anyFilter } from './state.js';

/** How many filters are on (each chosen value, each category, the period). */
export function filterCount(state) {
  return state.filters.length + state.kc.length + (state.from !== null || state.to !== null ? 1 : 0);
}

/** Whether the bundle offers any filter at all. */
export function filtersOffered(index) {
  const cats = KEYWORD_CATEGORIES.filter((c) => c !== 'none' && index.categoryCounts[c]);
  return index.columns.length > 0 || cats.length > 0 || (index.years.min !== null && index.years.min !== index.years.max);
}

/** The filters' panel in *el*; *onChange(patch)*. Answers `{update(view)}`, *view* `{index, state, t, fmt}`. */
export function createFilters(el, { onChange }) {
  let built = null;
  return {
    update({ index, state, t, fmt }) {
      const period0 = periodOf(index, state);
      const shape = [index, state.filters.join('|'), state.kc.join('|'), state.kcol, Boolean(period0)];
      if (built && built.shape.every((v, i) => v === shape[i])) {
        // only the period moved: the sliders keep the pointer, the words follow
        built.state = state;
        if (period0) {
          built.from.value = String(period0[0]);
          built.to.value = String(period0[1]);
          built.from.setAttribute('aria-valuetext', String(period0[0]));
          built.to.setAttribute('aria-valuetext', String(period0[1]));
          built.output.textContent = t('atlas.filters.years', { from: fmt.year(period0[0]), to: fmt.year(period0[1]) });
        }
        built.clear.disabled = !anyFilter(state);
        return;
      }
      built = { shape, state };
      const groups = filterGroups(state.filters);
      const parts = [];
      for (const col of index.columns) {
        const chosen = groups.get(col.column) || new Set();
        parts.push(h('fieldset', { class: 'cx-atlas-filters__group' },
          h('legend', { text: chosen.size ? t('atlas.filters.chosen', { column: col.column, count: chosen.size }) : col.column }),
          col.values.map((v) => {
            const f = `${col.column}:${v.value}`;
            return h('label', { class: 'cx-atlas-filters__value' },
              h('input', { type: 'checkbox', checked: state.filters.includes(f), dataset: { key: `f-${f}` },
                onChange: () => onChange({ filters: state.filters.includes(f) ? state.filters.filter((x) => x !== f) : [...state.filters, f] }) }),
              h('span', { text: t('atlas.filters.value', { value: v.value, count: v.count }) }));
          })));
      }
      const cats = KEYWORD_CATEGORIES.filter((c) => index.categoryCounts[c]);
      if (cats.some((c) => c !== 'none')) {
        parts.push(h('fieldset', { class: 'cx-atlas-filters__group' },
          h('legend', { text: t('atlas.filters.categories') }),
          cats.map((c) => h('label', { class: 'cx-atlas-filters__value' },
            h('input', { type: 'checkbox', checked: state.kc.includes(c), dataset: { key: `kc-${c}` },
              onChange: () => onChange({ kc: state.kc.includes(c) ? state.kc.filter((x) => x !== c) : [...state.kc, c] }) }),
            h('span', { text: t('atlas.filters.value', { value: t(`atlas.category.${c}`), count: index.categoryCounts[c] }) }))),
          h('label', { class: 'cx-atlas-filters__value' },
            h('input', { type: 'checkbox', checked: state.kcol === 'category', dataset: { key: 'kcol' },
              onChange: () => onChange({ kcol: state.kcol === 'category' ? 'theme' : 'category' }) }),
            h('span', { text: t('atlas.filters.by_category') }))));
      }
      const period = periodOf(index, state);
      if (period && index.years.min !== index.years.max) {
        const { min, max } = index.years;
        const slider = (key, value, onInput) => h('input', { type: 'range', min, max, step: 1, value,
          'aria-label': t(key), 'aria-valuetext': String(value), dataset: { key }, onInput });
        const now = () => built.state;
        built.from = slider('atlas.filters.from', period[0], (e) => {
          const v = Number(e.currentTarget.value);
          onChange({ from: v === min ? null : v, to: now().to !== null && now().to < v ? v : now().to });
        });
        built.to = slider('atlas.filters.to', period[1], (e) => {
          const v = Number(e.currentTarget.value);
          onChange({ to: v === max ? null : v, from: now().from !== null && now().from > v ? v : now().from });
        });
        built.output = h('output', { text: t('atlas.filters.years', { from: fmt.year(period[0]), to: fmt.year(period[1]) }) });
        parts.push(h('fieldset', { class: 'cx-atlas-filters__group cx-atlas-filters__period' },
          h('legend', { text: t('atlas.filters.period') }), built.from, built.to, built.output));
      }
      built.clear = h('button', { type: 'button', class: 'cx-atlas-btn', disabled: !anyFilter(state), dataset: { key: 'clear' },
        text: t('atlas.filters.clear'), onClick: () => onChange({ filters: [], kc: [], from: null, to: null }) });
      parts.push(built.clear);
      keepFocus(el, () => fill(el, ...parts));
    },
  };
}
