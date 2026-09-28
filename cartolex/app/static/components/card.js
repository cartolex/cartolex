// SPDX-License-Identifier: MIT
/**
 * Card: a titled region with optional actions and footer. A loading card
 * keeps its size, shows placeholder lines and is announced as busy.
 */
import { html } from '../core/preact.js';
import { t } from '../core/i18n.js';
import { useUid } from '../core/dom.js';

/**
 * @param {object} props
 * @param {any} [props.title] the card's heading
 * @param {2|3|4} [props.level] the heading level (3 by default)
 * @param {any} [props.actions] buttons shown beside the title
 * @param {any} [props.footer]
 * @param {boolean} [props.loading]
 * @param {'default'|'quiet'} [props.tone]
 */
export function Card({ title, level = 3, actions, footer, loading = false, tone = 'default',
  class: cls = '', children, ...rest }) {
  const id = useUid('cx-card');
  const Heading = `h${level}`;
  return html`<section class=${`cx-card cx-card--${tone} ${cls}`}
    aria-labelledby=${title ? id : undefined} aria-busy=${loading ? 'true' : undefined} ...${rest}>
    ${title || actions ? html`<header class="cx-card__header">
      ${title ? html`<${Heading} id=${id} class="cx-card__title">${title}<//>` : null}
      ${actions ? html`<div class="cx-card__actions">${actions}</div>` : null}
    </header>` : null}
    <div class="cx-card__body">
      ${loading ? html`<div class="cx-skeleton" aria-hidden="true">
          <span class="cx-skeleton__line"></span>
          <span class="cx-skeleton__line"></span>
          <span class="cx-skeleton__line cx-skeleton__line--short"></span>
        </div>
        <span class="cx-visually-hidden">${t('common.loading')}</span>` : children}
    </div>
    ${footer ? html`<footer class="cx-card__footer">${footer}</footer>` : null}
  </section>`;
}
