// SPDX-License-Identifier: MIT
/**
 * The pipeline of the About page: six steps in two rows, from the texts to the
 * shared site, as one SVG figure. Its colours are the theme's tokens (classes of
 * about.css), so it follows the light and dark themes; its words come from the
 * catalogues (`about.step.<id>`, `.what`, `.how`).
 */
import { html } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { useUid } from '../../core/dom.js';

/** The steps, in order, each a stage of the build (the first and last around it). */
export const STEPS = ['texts', 'keywords', 'space', 'themes', 'map', 'share'];

const W = 200;
const H = 92;
const GAP = 40;
const ROW_GAP = 48;
const PAD = 8;

function place(index) {
  const row = Math.floor(index / 3);
  const col = index % 3;
  return { x: PAD + col * (W + GAP), y: PAD + row * (H + ROW_GAP) };
}

function Step({ id, index }) {
  const { x, y } = place(index);
  return html`<g class="cx-about-figure__step">
    <rect x=${x} y=${y} width=${W} height=${H} rx="10" class="cx-about-figure__box" />
    <circle cx=${x + 22} cy=${y + 24} r="12" class="cx-about-figure__badge" />
    <text x=${x + 22} y=${y + 28.5} text-anchor="middle" class="cx-about-figure__number">${index + 1}</text>
    <text x=${x + 42} y=${y + 29} class="cx-about-figure__title">${t(`about.step.${id}`)}</text>
    <text x=${x + 16} y=${y + 58} class="cx-about-figure__line">${t(`about.step.${id}.what`)}</text>
    <text x=${x + 16} y=${y + 77} class="cx-about-figure__line">${t(`about.step.${id}.how`)}</text>
  </g>`;
}

/** An arrow from step *a* to step *b* (the next one): along the row, or down to the next row. */
function arrow(a, b) {
  const from = place(a);
  const to = place(b);
  if (from.y === to.y) {
    const y = from.y + H / 2;
    return `M ${from.x + W + 4} ${y} H ${to.x - 6}`;
  }
  const mid = from.y + H + ROW_GAP / 2;
  return `M ${from.x + W / 2} ${from.y + H + 4} V ${mid} H ${to.x + W / 2} V ${to.y - 6}`;
}

/** The pipeline figure, with its caption. */
export function PipelineFigure() {
  const id = useUid('cx-about-figure');
  const width = PAD * 2 + 3 * W + 2 * GAP;
  const height = PAD * 2 + 2 * H + ROW_GAP;
  return html`<figure class="cx-about-figure">
    <svg viewBox=${`0 0 ${width} ${height}`} role="img" aria-labelledby=${`${id}-title ${id}-desc`}
      class="cx-about-figure__svg">
      <title id=${`${id}-title`}>${t('about.pipeline.title')}</title>
      <desc id=${`${id}-desc`}>${STEPS.map((s, i) => `${i + 1}. ${t(`about.step.${s}`)}: ${t(`about.step.${s}.what`)}, ${t(`about.step.${s}.how`)}.`).join(' ')}</desc>
      <defs>
        <marker id=${`${id}-head`} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7"
          markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" class="cx-about-figure__head" />
        </marker>
      </defs>
      ${STEPS.slice(1).map((_, i) => html`<path key=${i} d=${arrow(i, i + 1)}
        class="cx-about-figure__arrow" marker-end=${`url(#${id}-head)`} />`)}
      ${STEPS.map((step, i) => html`<${Step} key=${step} id=${step} index=${i} />`)}
    </svg>
    <figcaption class="cx-about-figure__caption">${t('about.pipeline.caption')}</figcaption>
  </figure>`;
}
