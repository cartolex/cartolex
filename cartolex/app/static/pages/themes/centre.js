/**
 * The centre column: the treemap, the map and the playground, as three tabs. The map panel
 * and the playground are drawn only while their tab is shown; the playground takes the
 * outline's and the side panel's room (`pages/themes/playground/`). The centre goes full screen
 * (`pages/map/fullscreen.js`), its tabs with it.
 */

import { html } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { Tabs } from '../../components/index.js';
import { TreemapPanel } from './treemap.js';
import { MapPanel } from './map.js';
import { PlaygroundPanel } from './playground/panel.js';
import { FullscreenButton, useFullscreen } from '../map/fullscreen.js';

export function CentrePane({ ctx, editor, ui, atlas, atlasError, onRetryAtlas }) {
  const fs = useFullscreen();
  if (!editor.index.value) return null;
  return html`<section ref=${fs.ref} class=${`cx-themes-centre ${fs.className}`} aria-label=${t('themes.centre.region')}>
    <div class="cx-themes-centre__fullscreen"><${FullscreenButton} fs=${fs} /></div>
    <${Tabs} class="cx-themes-centre__tabs" label=${t('themes.centre.tabs')} selected=${ui.centreTab.value}
      onSelect=${(id) => {
        ui.centreTab.value = id;
      }}
      tabs=${[{ id: 'treemap', label: t('themes.tab.treemap') }, { id: 'map', label: t('themes.tab.map') },
        { id: 'playground', label: t('themes.tab.playground') }]}
      panel=${(id) => {
        if (id === 'map') {
          return html`<${MapPanel} editor=${editor} ui=${ui} atlas=${atlas} atlasError=${atlasError}
            onRetryAtlas=${onRetryAtlas} />`;
        }
        if (id === 'playground') return html`<${PlaygroundPanel} ctx=${ctx} editor=${editor} ui=${ui} />`;
        return html`<${TreemapPanel} editor=${editor} ui=${ui} />`;
      }} />
  </section>`;
}
