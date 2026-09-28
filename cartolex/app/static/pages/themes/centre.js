/**
 * The centre column: the treemap and the map, as two tabs. The map panel is
 * drawn only while its tab is shown.
 */

import { html } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { Tabs } from '../../components/index.js';
import { TreemapPanel } from './treemap.js';
import { MapPanel } from './map.js';

export function CentrePane({ editor, ui, atlas, atlasError, onRetryAtlas }) {
  if (!editor.index.value) return null;
  return html`<section class="cx-themes-centre" aria-label=${t('themes.centre.region')}>
    <${Tabs} class="cx-themes-centre__tabs" label=${t('themes.centre.tabs')} selected=${ui.centreTab.value}
      onSelect=${(id) => {
        ui.centreTab.value = id;
      }}
      tabs=${[{ id: 'treemap', label: t('themes.tab.treemap') }, { id: 'map', label: t('themes.tab.map') }]}
      panel=${(id) => (id === 'map'
        ? html`<${MapPanel} editor=${editor} ui=${ui} atlas=${atlas} atlasError=${atlasError}
            onRetryAtlas=${onRetryAtlas} />`
        : html`<${TreemapPanel} editor=${editor} ui=${ui} />`)} />
  </section>`;
}
