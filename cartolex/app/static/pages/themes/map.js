/**
 * The map panel: the people and keywords of `GET /api/atlas`, drawn by a
 * MapFrame. Keywords take the colour of their top-level node in the tree
 * being edited; people keep the one of their largest share at the last apply
 * (their shares, and the map itself, refresh after « Save and apply »).
 */

import { html, useMemo, useRef } from '../../core/preact.js';
import { formatNumber, locale, t } from '../../core/i18n.js';
import { Button, EmptyState, ErrorCard, IconButton, MapFrame } from '../../components/index.js';
import { lang2, nodeName } from './model.js';

const NEUTRAL = 12; // palette index of what no top-level node holds

/** The palette of the map: one token per hue family, then a neutral grey. */
export const PALETTE = [...Array.from({ length: 12 }, (_, i) => `--cx-hue-${i + 1}`), '--cx-text-muted'];

/** The people's top-level node on the map (their largest usage share at level 1). */
function topNode(shares) {
  let best = null;
  let value = 0;
  for (const [id, share] of Object.entries((shares && shares[0]) || {})) {
    if (share > value) {
      best = id;
      value = share;
    }
  }
  return best;
}

/**
 * The map's scene from the atlas, coloured by the tree being edited: each
 * keyword by its top-level node now, each person by the top-level node of
 * their largest share at the last apply.
 */
export function mapScene(atlas, index, focus) {
  if (!atlas || !atlas.available || !index) return null;
  const hueOf = (node) => (node && index.nodes.has(node) ? index.hue.get(node) % 12 : NEUTRAL);
  const kws = atlas.keywords.filter((k) => k.x !== null && k.y !== null);
  const people = atlas.people.filter((p) => p.x !== null && p.y !== null);
  const kx = new Float32Array(kws.length);
  const ky = new Float32Array(kws.length);
  const kc = new Uint16Array(kws.length);
  const kh = new Uint8Array(kws.length);
  let khn = 0;
  const under = focus && focus.kind === 'node' ? new Set([focus.id]) : null;
  if (under) {
    for (const id of index.order) {
      const parent = index.nodes.get(id).parent;
      if (parent !== null && under.has(parent)) under.add(id);
    }
  }
  const terms = focus && focus.kind === 'keywords' ? new Set(focus.terms) : null;
  kws.forEach((k, i) => {
    kx[i] = k.x;
    ky[i] = k.y;
    const node = index.tree.keywords[k.term];
    kc[i] = node !== undefined ? hueOf(index.topOf.get(node)) : NEUTRAL;
    if ((under && node !== undefined && under.has(node)) || (terms && terms.has(k.term))) {
      kh[i] = 1;
      khn += 1;
    }
  });
  const px = new Float32Array(people.length);
  const py = new Float32Array(people.length);
  const pc = new Uint16Array(people.length);
  const ph = new Uint8Array(people.length);
  let phn = 0;
  const level = focus && focus.kind === 'node' ? index.level.get(focus.id) : 0;
  people.forEach((p, i) => {
    px[i] = p.x;
    py[i] = p.y;
    pc[i] = hueOf(topNode(p.shares));
    const share = level && p.shares[level - 1] ? p.shares[level - 1][focus.id] || 0 : 0;
    if (share >= 0.2 || (focus && focus.kind === 'person' && focus.id === p.person_id)) {
      ph[i] = 1;
      phn += 1;
    }
  });
  const labels = [];
  const lang = lang2(locale.value);
  for (const top of index.tops) {
    let sx = 0;
    let sy = 0;
    let n = 0;
    kws.forEach((k, i) => {
      const node = index.tree.keywords[k.term];
      if (node !== undefined && index.topOf.get(node) === top) {
        sx += kx[i];
        sy += ky[i];
        n += 1;
      }
    });
    if (n) labels.push({ x: sx / n, y: sy / n, text: nodeName(index.nodes.get(top), lang), weight: index.weight.get(top) });
  }
  labels.sort((a, b) => b.weight - a.weight);
  return {
    layers: [
      { id: 'keywords', x: kx, y: ky, color: kc, palette: PALETTE, radius: 3, alpha: 0.8,
        highlight: kh, highlightCount: khn, items: kws },
      { id: 'people', x: px, y: py, color: pc, palette: PALETTE, radius: 4, alpha: 0.95,
        highlight: ph, highlightCount: phn, items: people },
    ],
    labels,
    bounds: atlas.bounds,
  };
}

/** The map tab of the centre column. */
export function MapPanel({ editor, ui, atlas, atlasError, onRetryAtlas }) {
  const index = editor.index.value;
  const lang = lang2(locale.value);
  const frame = useRef(null);
  ui.mapFrame = frame;
  const focus = ui.focus.value;
  const scene = useMemo(() => mapScene(atlas, index, focus), [atlas, index, focus]);
  if (!index) return null;
  if (atlasError) return html`<${ErrorCard} error=${atlasError} onRetry=${onRetryAtlas} compact />`;
  if (!atlas) return html`<p class="cx-themes-empty-line" aria-busy="true">${t('common.loading')}</p>`;
  if (!atlas.available) {
    return html`<${EmptyState} icon="file" title=${t('themes.map.none')}
      action=${{ label: t('themes.map.apply'), onClick: () => ui.saveAndApply() }}>
      ${t('themes.map.none.text')}<//>`;
  }
  const selectedNode = focus && focus.kind === 'node' ? focus.id : null;
  const people = atlas.people.length;
  const hover = ui.mapHover.value;
  const lit = (scene.layers[0].highlightCount || 0) + (scene.layers[1].highlightCount || 0);
  return html`<div class="cx-themes-map">
    <div class="cx-themes-map__bar">
      <p class="cx-themes-map__lead">${t('themes.map.lead', { people, keywords: atlas.keywords.length })}</p>
      <div class="cx-themes-treemap__tools">
        <${IconButton} icon="plus" size="s" label=${t('themes.map.zoom_in')}
          onClick=${() => frame.current && frame.current.zoomBy(1.4)} />
        <${IconButton} icon="dash" size="s" label=${t('themes.map.zoom_out')}
          onClick=${() => frame.current && frame.current.zoomBy(1 / 1.4)} />
        <${Button} size="s" variant="ghost" onClick=${() => frame.current && frame.current.fit()}>
          ${t('themes.map.fit')}<//>
      </div>
    </div>
    <${MapFrame} class="cx-themes-map__frame" scene=${scene} frameRef=${frame}
      label=${t('themes.map.label')} status=${focus ? t('themes.map.status', { count: lit }) : ''}
      onPick=${(hit) => ui.pickOnMap(hit, scene)}
      onHover=${(hit) => {
        ui.mapHover.value = hit ? (hit.layer === 'people'
          ? { kind: 'person', text: scene.layers[1].items[hit.index].name }
          : { kind: 'keyword', text: scene.layers[0].items[hit.index].term }) : null;
      }} />
    <p class="cx-themes-map__hover" aria-hidden="true">${hover ? hover.text : t('themes.map.keys')}</p>
    <ul class="cx-themes-legend" aria-label=${t('themes.map.legend')}>
      ${index.tops.map((id) => html`<li key=${id}>
        <button type="button" class=${`cx-themes-legend__item ${selectedNode === id ? 'is-selected' : ''}`}
          aria-pressed=${String(selectedNode === id)} onClick=${() => ui.openNode(id, { from: 'map' })}>
          <span class="cx-themes-chip" style=${{ '--cx-chip': `var(--cx-hue-${(index.hue.get(id) % 12) + 1})` }}
            aria-hidden="true"></span>
          ${nodeName(index.nodes.get(id), lang)}
        </button></li>`)}
    </ul>
    <p class="cx-themes-map__note">${t('themes.map.note', { people: formatNumber(people) })}</p>
  </div>`;
}
