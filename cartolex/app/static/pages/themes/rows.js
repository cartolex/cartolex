/**
 * The rows of the outline and of the review queue: a node or a keyword, with
 * the words of the search marked.
 */

import { html } from '../../core/preact.js';
import { formatNumber, locale, t } from '../../core/i18n.js';
import { highlight, lang2, nodeName } from './model.js';
import { shortShare } from './labels.js';

/** A text with the parts that match the search marked. */
export function Marked({ text, words }) {
  if (!words) return text;
  return highlight(text, words).map((p, i) => (p.match
    ? html`<mark key=${i} class="cx-themes-mark">${p.text}</mark>` : p.text));
}

export function NodeRow({ index, row, words }) {
  const lang = lang2(locale.value);
  const node = index.nodes.get(row.id);
  const hue = (index.hue.get(row.id) % 12) + 1;
  const share = index.total ? index.weight.get(row.id) / index.total : 0;
  return html`<span class="cx-themes-row cx-themes-row--node">
    <span class=${`cx-themes-chip cx-themes-chip--l${Math.min(row.level, 4)}`}
      style=${{ '--cx-chip': `var(--cx-hue-${hue})` }} aria-hidden="true"></span>
    <span class="cx-themes-row__name"><${Marked} text=${nodeName(node, lang)} words=${words} /></span>
    <span class="cx-themes-row__count" title=${t('themes.outline.count', { count: index.under.get(row.id) })}>
      ${formatNumber(index.under.get(row.id))}</span>
    <span class="cx-themes-row__share">${shortShare(share)}</span>
  </span>`;
}

export function KeywordRow({ index, term, words, detail = null, after = null }) {
  const review = (index.tree.review || {})[term];
  const attribution = (index.tree.attribution || {})[term];
  return html`<span class="cx-themes-row cx-themes-row--keyword">
    <span class="cx-themes-row__term"><${Marked} text=${term} words=${words} /></span>
    ${detail ? html`<span class="cx-themes-row__detail">${detail}</span>` : null}
    ${review === 'to_check' ? html`<span class="cx-themes-badge cx-themes-badge--check">
      ${t('themes.badge.to_check')}</span>` : null}
    ${attribution !== undefined ? html`<span class="cx-themes-badge">
      ${attribution === 0 ? t('themes.badge.nowhere') : t('themes.badge.counts', { level: attribution })}</span>` : null}
    ${after}
    <span class="cx-themes-row__count">${formatNumber(index.people(term))}</span>
  </span>`;
}
