// SPDX-License-Identifier: MIT
/**
 * The atlas page's treemap: the applied theme tree, each theme sized by its
 * weight (the usage that counts toward it), in its hue family. Selecting a
 * theme highlights its keywords and the people who weigh most on it; Enter
 * or a double click zooms into it, and the path above zooms back.
 */
import { html, useMemo } from '../../core/preact.js';
import { formatPercent, locale, t } from '../../core/i18n.js';
import { Treemap } from '../../components/index.js';
import { themeName } from './model.js';

function Path({ index, root, onZoom }) {
  const path = [];
  for (let at = root; at && index.nodes.has(at); at = index.nodes.get(at).parent) path.unshift(at);
  return html`<nav class="cx-atlas-crumbs" aria-label=${t('map.tree.path')}>
    <ol>
      <li><button type="button" class="cx-link-button" aria-current=${root ? undefined : 'location'}
        onClick=${() => onZoom('')}>${t('map.tree.all')}</button></li>
      ${path.map((id, i) => html`<li key=${id}><button type="button" class="cx-link-button"
        aria-current=${i === path.length - 1 ? 'location' : undefined}
        onClick=${() => onZoom(id)}>${themeName(index, id, locale.value)}</button></li>`)}
    </ol>
  </nav>`;
}

/** The treemap panel. */
export function TreePanel({ index, state, onSelect, onZoom }) {
  const lang = locale.value;
  const funcs = useMemo(() => {
    const weightOf = (id) => {
      const n = index.nodes.get(id);
      return Math.max((n && n.weight) || 0, 1e-6);
    };
    return {
      childrenOf: (id) => index.children.get(id === undefined ? null : id) || [],
      weightOf,
      ownWeightOf: (id) => {
        const kids = index.children.get(id) || [];
        return Math.max(0, weightOf(id) - kids.reduce((s, k) => s + weightOf(k), 0));
      },
      hueOf: (id) => index.hue.get(id) || 0,
      parentOf: (id) => (index.nodes.has(id) ? index.nodes.get(id).parent : null),
      nameOf: (id) => themeName(index, id, lang),
      detailOf: (id) => {
        const n = index.nodes.get(id);
        return n && n.share !== null && n.share !== undefined ? formatPercent(n.share) : '';
      },
    };
  }, [index, lang]);
  if (!index.tops.length) return null;
  const root = state.theme && index.nodes.has(state.theme) ? state.theme : null;
  const selected = state.sel && state.sel.kind === 'theme' ? state.sel.id : null;
  return html`<section class="cx-atlas-tree" aria-label=${t('map.tree.label')}>
    <${Path} index=${index} root=${root} onZoom=${onZoom} />
    <${Treemap} class="cx-atlas-tree__map" root=${root} ...${funcs} selected=${selected}
      onSelect=${(id) => onSelect({ kind: 'theme', id })} onZoom=${(id) => onZoom(id || '')}
      label=${t('map.tree.label')}
      status=${selected ? t('map.tree.selected', { name: funcs.nameOf(selected) }) : ''} />
  </section>`;
}
