/**
 * The treemap panel: nested rectangles of the tree being edited, sized by the
 * use of the keywords counting toward each node (by keyword counts until the
 * use is known), with the path of the zoom above.
 */

import { html, useMemo } from '../../core/preact.js';
import { locale, t } from '../../core/i18n.js';
import { EmptyState, IconButton, Treemap } from '../../components/index.js';
import { lang2, nodeName, pathOf } from './model.js';
import { shortShare } from './labels.js';

function Breadcrumb({ index, root, onZoom }) {
  const lang = lang2(locale.value);
  const path = root ? pathOf(index, root) : [];
  return html`<nav class="cx-themes-crumbs" aria-label=${t('themes.treemap.path')}>
    <ol>
      <li><button type="button" class="cx-link-button" aria-current=${root ? undefined : 'location'}
        onClick=${() => onZoom(null)}>${t('themes.treemap.all')}</button></li>
      ${path.map((id, i) => html`<li key=${id}>
        <button type="button" class="cx-link-button" aria-current=${i === path.length - 1 ? 'location' : undefined}
          onClick=${() => onZoom(id)}>${nodeName(index.nodes.get(id), lang)}</button>
      </li>`)}
    </ol>
  </nav>`;
}

/** The treemap tab of the centre column. */
export function TreemapPanel({ editor, ui }) {
  const index = editor.index.value;
  const lang = lang2(locale.value);
  const focus = ui.focus.value;
  const funcs = useMemo(() => {
    if (!index) return null;
    const useWeight = index.total > 0;
    const weightOf = (id) => (useWeight ? index.weight.get(id) : index.under.get(id)) || 0;
    const placed = Math.max(1, Object.keys(index.tree.keywords).length);
    return {
      childrenOf: (id) => index.children.get(id) || [],
      weightOf,
      ownWeightOf: (id) => {
        const kids = index.children.get(id) || [];
        return Math.max(0, weightOf(id) - kids.reduce((s, k) => s + weightOf(k), 0));
      },
      hueOf: (id) => index.hue.get(id) || 0,
      parentOf: (id) => (index.nodes.has(id) ? index.nodes.get(id).parent : null),
      nameOf: (id) => nodeName(index.nodes.get(id), lang),
      detailOf: (id) => shortShare(useWeight ? weightOf(id) / index.total : weightOf(id) / placed),
    };
  }, [index, lang]);
  if (!index) return null;
  const root = ui.zoom.value && index.nodes.has(ui.zoom.value) ? ui.zoom.value : null;
  const selectedNode = focus && focus.kind === 'node' ? focus.id : null;
  const marked = focus && focus.kind === 'keywords'
    ? new Set(focus.terms.map((k) => index.tree.keywords[k]).filter(Boolean)) : null;
  const status = selectedNode ? t('themes.treemap.selected', {
    name: funcs.nameOf(selectedNode), share: funcs.detailOf(selectedNode) }) : '';
  return html`<div class="cx-themes-treemap">
    <div class="cx-themes-treemap__bar">
      <${Breadcrumb} index=${index} root=${root} onZoom=${(id) => ui.zoom.value = id} />
      <div class="cx-themes-treemap__tools">
        <${IconButton} icon="plus" size="s" label=${t('themes.treemap.zoom_in')}
          disabled=${!selectedNode || !(index.children.get(selectedNode) || []).length}
          onClick=${() => {
            ui.zoom.value = selectedNode;
          }} />
        <${IconButton} icon="dash" size="s" label=${t('themes.treemap.zoom_out')} disabled=${!root}
          onClick=${() => {
            ui.zoom.value = index.nodes.get(root).parent;
          }} />
      </div>
    </div>
    ${index.order.length ? html`<${Treemap} class="cx-themes-treemap__map" root=${root}
      ...${funcs} selected=${selectedNode} marked=${marked}
      onSelect=${(id) => ui.openNode(id, { from: 'treemap' })}
      onZoom=${(id) => {
        ui.zoom.value = id;
      }}
      label=${t('themes.treemap.label')} status=${status}
      canDrop=${(id, data) => ui.canDrop(id, data)} onDrop=${(id, data) => ui.drop(id, data)} />`
      : html`<${EmptyState} icon="file" title=${t('themes.treemap.empty')}
        action=${editor.readOnly.value ? null : { label: t('themes.outline.new_top'), onClick: () => ui.create(null) }}>
        ${t('themes.treemap.empty.text')}<//>`}
    <p class="cx-themes-treemap__hint">${t('themes.treemap.keys')}</p>
  </div>`;
}
