/**
 * The versions of the tree: every saved version, the current one first, each
 * with when it was saved and by which action; open one read-only, compare it
 * with the current tree, restore it (a restore is a new version).
 */

import { html, useEffect, useState } from '../../core/preact.js';
import { formatDate, t } from '../../core/i18n.js';
import { Button, Drawer, EmptyState, ErrorCard } from '../../components/index.js';
import { describe } from './labels.js';

/**
 * A saved action's name in the interface language (its parts, joined by « ; »),
 * with the nodes' names *before* the action and *after* it.
 */
export function actionText(action, names = {}, after = {}) {
  if (!action) return '';
  return action.split('; ').map((part) => describe(part, names, after)).join(' · ');
}

/**
 * @param {object} props
 * @param {boolean} props.open
 * @param {object} props.api the page's API client
 * @param {object} props.editor
 * @param {(version: object) => void} props.onOpenVersion read-only view
 * @param {(version: object) => void} props.onCompare
 * @param {(version: object) => void} props.onRestore
 */
export function VersionsDrawer({ open, api, editor, onClose, onOpenVersion, onCompare, onRestore }) {
  const [state, setState] = useState({ loading: true, items: [], error: null });
  const load = async () => {
    setState({ loading: true, items: [], error: null });
    const result = await api.get('/api/themes/versions');
    if (result.ok) setState({ loading: false, items: result.data.items, error: null });
    else setState({ loading: false, items: [], error: result.error });
  };
  useEffect(() => {
    if (open) load();
  }, [open]);
  // The names a version's action gives come from the versions around it (`names`, `names_after`);
  // the tree being edited names what they do not.
  const names = {};
  const tree = editor.tree.value;
  if (tree) for (const n of tree.nodes) names[n.id] = n.names;
  const viewing = editor.viewing.value;
  let body;
  if (state.error) body = html`<${ErrorCard} error=${state.error} onRetry=${load} compact />`;
  else if (state.loading) body = html`<p class="cx-themes-empty-line" aria-busy="true">${t('common.loading')}</p>`;
  else if (!state.items.length) {
    body = html`<${EmptyState} icon="file" title=${t('themes.versions.empty')}>${t('themes.versions.empty.text')}<//>`;
  } else {
    body = html`<ol class="cx-themes-versions">
      ${state.items.map((v, i) => html`<li key=${v.id} class=${`cx-themes-versions__item ${viewing && viewing.id === v.id ? 'is-open' : ''}`}>
        <p class="cx-themes-versions__when">
          ${v.made_at ? formatDate(v.made_at, 'datetime') : t('themes.versions.unknown')}
          ${i === 0 ? html` <span class="cx-themes-badge">${t('themes.versions.current')}</span>` : null}
        </p>
        <p class="cx-themes-versions__what">${v.made_by ? actionText(v.made_by, { ...names, ...(v.names || {}) },
          v.names_after || {}) : t('themes.versions.first')}</p>
        <div class="cx-themes-versions__actions">
          <${Button} size="s" variant="ghost" onClick=${() => onOpenVersion(v)}>${t('themes.versions.open')}<//>
          ${i > 0 ? html`<${Button} size="s" variant="ghost" onClick=${() => onCompare(v)}>
            ${t('themes.versions.compare')}<//>
            <${Button} size="s" variant="ghost" onClick=${() => onRestore(v)}>${t('themes.versions.restore')}<//>` : null}
        </div>
      </li>`)}
    </ol>`;
  }
  return html`<${Drawer} open=${open} onClose=${onClose} title=${t('themes.versions.title')}
    description=${t('themes.versions.lead')}>
    ${body}
  <//>`;
}
