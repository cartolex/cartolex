// SPDX-License-Identifier: MIT
/**
 * The atlas's side columns (the treemap, the panel) folded away or shown, remembered in this
 * browser; and the people the map's filters keep, for an export of their distances.
 */
import { html, useState } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { IconButton } from '../../components/index.js';
import { matching } from './model.js';

const KEY = 'cartolex.atlas.columns';

function read() {
  try {
    const v = JSON.parse(window.localStorage.getItem(KEY) || '{}');
    return { tree: Boolean(v && v.tree), panel: Boolean(v && v.panel) };
  } catch {
    return { tree: false, panel: false };
  }
}

/** `[hidden, toggle]`: which columns are folded away (`{tree, panel}`), and a toggle by name. */
export function useColumns() {
  const [hidden, setHidden] = useState(read);
  const toggle = (name) => setHidden((h) => {
    const next = { ...h, [name]: !h[name] };
    try {
      window.localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      // a browser that keeps nothing: the columns are as chosen until the page is left
    }
    return next;
  });
  return [hidden, toggle];
}

/** The two buttons that fold or show the columns. */
export function ColumnButtons({ hidden, onToggle }) {
  return html`
    <${IconButton} icon="panel-left" size="s" onClick=${() => onToggle('tree')}
      label=${hidden.tree ? t('map.columns.show_tree') : t('map.columns.hide_tree')} />
    <${IconButton} icon="panel-right" size="s" onClick=${() => onToggle('panel')}
      label=${hidden.panel ? t('map.columns.show_panel') : t('map.columns.hide_panel')} />`;
}

/** The ids of the people on the map the filters keep (all of them without a filter). */
export function shownPeople(index, state) {
  const mask = matching(index, state);
  const out = [];
  index.people.forEach((p, i) => {
    if (mask[i] && p.person_id && p.x !== null) out.push(p.person_id);
  });
  return out;
}
