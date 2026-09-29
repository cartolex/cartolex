// SPDX-License-Identifier: MIT
/**
 * A small preview of the map: an even sample of its people, each in the hue
 * of their top-level theme, in a MapFrame; the map page shows the rest.
 */
import { html, useMemo } from '../../core/preact.js';
import { formatNumber, t } from '../../core/i18n.js';
import { runtime } from '../../core/runtime.js';
import { Button, Card, EmptyState, MapFrame } from '../../components/index.js';
import { follow } from './cards.js';

const NEUTRAL = 12;
const PALETTE = [...Array.from({ length: 12 }, (_, i) => `--cx-hue-${i + 1}`), '--cx-text-muted'];

/** The preview's scene: one layer of points. */
export function previewScene(preview) {
  const n = preview.points.length;
  const x = new Float32Array(n);
  const y = new Float32Array(n);
  const color = new Uint16Array(n);
  preview.points.forEach(([px, py, hue], i) => {
    x[i] = px;
    y[i] = py;
    color[i] = hue >= 0 ? hue % 12 : NEUTRAL;
  });
  return { layers: [{ id: 'people', x, y, color, palette: PALETTE, radius: 2.5, alpha: 0.85 }],
    labels: [], bounds: preview.bounds };
}

/**
 * @param {{preview: object|null, stale: object|null, loading: boolean}} props
 *   `stale` is the health item of a stale map, whose action restores it
 */
export function MapPreview({ preview, stale, loading }) {
  const scene = useMemo(() => (preview ? previewScene(preview) : null), [preview]);
  return html`<${Card} level=${2} title=${t('overview.preview.title')} loading=${loading}
    class="cx-overview-preview"
    actions=${scene ? html`<${Button} size="s" variant="ghost" iconAfter="chevron-right"
      onClick=${() => runtime.navigate('/map')}>${t('overview.preview.open')}<//>` : null}>
    ${scene ? html`
      <${MapFrame} class="cx-overview-preview__frame" scene=${scene}
        label=${t('overview.preview.label', { n: preview.people })} />
      <p class="cx-overview-preview__note">
        ${t('overview.preview.note', { shown: formatNumber(preview.points.length), n: preview.people })}
        ${stale ? html`${' · '}<button type="button" class="cx-link-button" onClick=${() => follow(stale)}>
          ${t('overview.preview.stale')}</button>` : null}
      </p>`
    : html`<${EmptyState} icon="file" level=${3} title=${t('overview.preview.none')}
        action=${{ label: t('overview.preview.build'), onClick: () => runtime.navigate('/build') }}>
        ${t('overview.preview.none.text')}<//>`}
  <//>`;
}
