// SPDX-License-Identifier: MIT
/**
 * The small layers panel over the map's corner, quiet until hovered or focused: show or hide
 * people, keywords and organisations, each with « Aa » to write their names; the
 * organisations' level, named with the project's own level names; « Network 1 · 2 · 3 »,
 * the rings around a person or an organisation (pressing the current one turns them off);
 * « Trajectory », a focused person's time windows joined in time order (`traj`, off by
 * default); and, folded under « More », the other kinds (texts, projected people, time windows) and
 * points or regions. Texts, once shown, come out of the fold with « Texts: all · of the focus
 * · with the network » (`tx`, `texts.js`).
 */
import { fill, h, keepFocus, symbol } from './dom.js';
import { MAIN_KINDS, NAMED_KINDS, SHAPE_OF } from './state.js';
import { TEXT_MODES } from './texts.js';

const MORE_KINDS = ['projected', 'texts', 'windows'];

/** Which kinds the bundle has at all (a kind without items is not offered). */
export function kindsAvailable(index, source) {
  return {
    people: index.people.length > 0,
    keywords: index.keywords.length > 0,
    organisations: index.orgs.some((o) => o.x !== null && o.x !== undefined),
    texts: Boolean(source.texts || source.textsOf),
    textsOf: Boolean(source.textsOf),
    projected: index.projected.length > 0,
    windows: Boolean(source.windows) && (index.bundle.windows || 0) > 0,
  };
}

/**
 * The panel in *el*; *onChange(patch)* changes the state. Answers `{update(view)}` where
 * *view* is `{index, state, t, has, counts, levelName, network}`.
 */
export function createLayers(el, { onChange }) {
  let moreOpen = false;
  const toggleIn = (list, kind) => (list.includes(kind) ? list.filter((k) => k !== kind) : [...list, kind]);
  return {
    update({ index, state, t, has, counts, levelName, network }) {
      const world = state.view === 'world';
      const level = state.org || (index.levels[0] && index.levels[0].id) || '';
      const nameOfKind = (kind) => (kind === 'organisations' ? levelName(level) || t('atlas.kind.organisations')
        : t(`atlas.kinds.${kind}`));
      const row = (kind) => {
        const on = state.show.includes(kind);
        const c = counts[kind];
        const name = nameOfKind(kind);
        return h('div', { class: 'cx-atlas-layers__row' },
          h('button', { type: 'button', class: 'cx-atlas-layers__eye', 'aria-pressed': String(on), dataset: { key: `eye-${kind}` },
            'aria-label': name,
            title: t(on ? 'atlas.layers.hide' : 'atlas.layers.show', { name }),
            onClick: () => onChange({ show: toggleIn(state.show, kind) }) },
          symbol(SHAPE_OF[kind]), h('span', { text: name }),
          c && on ? h('span', { class: 'cx-atlas-layers__count', text: t('atlas.layers.count', { count: c.shown }) }) : null),
          NAMED_KINDS.includes(kind) ? h('button', { type: 'button', class: 'cx-atlas-layers__aa', dataset: { key: `aa-${kind}` },
            'aria-pressed': String(state.names.includes(kind)), 'aria-label': t('atlas.layers.names', { name }),
            title: t('atlas.layers.names', { name }), text: t('atlas.layers.aa'),
            onClick: () => onChange({ names: toggleIn(state.names, kind) }) }) : null);
      };
      const levels = index.levels;
      const parts = [];
      if (!world) for (const kind of MAIN_KINDS) if (has[kind]) parts.push(row(kind));
      if ((world || state.show.includes('organisations')) && levels.length > 1) {
        const select = h('select', { class: 'cx-atlas-layers__level', 'aria-label': t('atlas.layers.level'), dataset: { key: 'level' },
          onChange: (e) => onChange({ org: e.currentTarget.value === levels[0].id ? '' : e.currentTarget.value, ...(state.sel && state.sel.kind === 'organisation' ? { sel: null, with: null } : {}) }) },
        levels.map((lv) => h('option', { value: lv.id, selected: lv.id === level, text: levelName(lv.id) })));
        parts.push(h('div', { class: 'cx-atlas-layers__row' }, select));
      }
      if (network && !world) {
        parts.push(h('div', { class: 'cx-atlas-layers__row cx-atlas-layers__net', role: 'group', 'aria-label': t('atlas.layers.network_label') },
          h('span', { text: t('atlas.layers.network') }),
          [1, 2, 3].map((d) => h('button', { type: 'button', class: 'cx-atlas-layers__aa', 'aria-pressed': String(state.net === d), dataset: { key: `net-${d}` },
            title: t('atlas.layers.rings', { count: d }), text: String(d),
            onClick: () => onChange({ net: state.net === d ? 0 : d }) }))));
      }
      // a focused person's trajectory: their time windows joined in time order
      if (has.windows && !world) {
        const person = state.sel && state.sel.kind === 'person';
        parts.push(h('div', { class: 'cx-atlas-layers__row' },
          h('button', { type: 'button', class: 'cx-atlas-layers__eye', 'aria-pressed': String(state.traj), dataset: { key: 'traj' },
            title: t(person ? 'atlas.layers.trajectory_help' : 'atlas.layers.trajectory_none'),
            onClick: () => onChange({ traj: !state.traj }) },
          symbol(SHAPE_OF.windows), h('span', { text: t('atlas.layers.trajectory') }))));
      }
      // the texts, once shown, out of the fold: which ones are drawn
      const textsOut = !world && has.texts && state.show.includes('texts');
      if (textsOut) {
        parts.push(row('texts'));
        const modes = TEXT_MODES.filter((m) => m !== 'network' || (has.textsOf && network));
        parts.push(h('div', { class: 'cx-atlas-layers__row cx-atlas-layers__net', role: 'group', 'aria-label': t('atlas.layers.texts_label') },
          h('span', { text: t('atlas.layers.texts') }),
          modes.map((m) => h('button', { type: 'button', class: 'cx-atlas-layers__aa', 'aria-pressed': String(state.tx === m),
            dataset: { key: `tx-${m || 'all'}` }, title: t(`atlas.layers.texts.${m || 'all'}_help`),
            text: t(`atlas.layers.texts.${m || 'all'}`), onClick: () => onChange({ tx: m }) }))));
      }
      const more = MORE_KINDS.filter((k) => has[k] && !(k === 'texts' && textsOut));
      if (!world && more.length) {
        const button = h('button', { type: 'button', class: 'cx-atlas-layers__more', 'aria-expanded': String(moreOpen), dataset: { key: 'more' },
          text: t(moreOpen ? 'atlas.layers.less' : 'atlas.layers.more'),
          onClick: () => {
            moreOpen = !moreOpen;
            onChange({});
          } });
        parts.push(button);
        if (moreOpen) {
          for (const kind of more) parts.push(row(kind));
          parts.push(h('div', { class: 'cx-atlas-layers__row' },
            h('button', { type: 'button', class: 'cx-atlas-layers__eye', 'aria-pressed': String(state.as === 'regions'), dataset: { key: 'regions' },
              text: t('atlas.layers.regions'), onClick: () => onChange({ as: state.as === 'regions' ? 'points' : 'regions' }) })));
        }
      }
      keepFocus(el, () => fill(el, ...parts));
      el.hidden = parts.length === 0;
    },
  };
}
