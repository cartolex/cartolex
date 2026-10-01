// SPDX-License-Identifier: MIT
/**
 * The playground's side panel: the balance across levels (per level, the share of the
 * keywords on its nodes and the keywords on each node, median and middle half, against the
 * target: few keywords on the top level, about as many on each node of a level), how the
 * preview differs from the editor's tree (themes split and merged, keywords that would move),
 * and « Adopt as my draft » and « Send to AI ».
 */

import { html } from '../../../core/preact.js';
import { formatNumber, formatPercent, locale, t } from '../../../core/i18n.js';
import { Button } from '../../../components/index.js';
import { lang2, levelName } from '../model.js';

/** The value at quantile *q* of sorted *values* (linear between ranks). */
export function quantile(values, q) {
  if (!values.length) return 0;
  const at = (values.length - 1) * q;
  const lo = Math.floor(at);
  const hi = Math.ceil(at);
  return values[lo] + (values[hi] - values[lo]) * (at - lo);
}

/** Per level: `{level, nodes, keywords, share, median, low, high}` (keywords on the nodes). */
export function balanceOf(index) {
  const placed = Math.max(1, Object.keys(index.tree.keywords || {}).length);
  const out = [];
  for (let lv = 1; lv <= index.depth; lv += 1) {
    const own = index.order.filter((id) => index.level.get(id) === lv)
      .map((id) => index.keywordsOn.get(id).length).sort((a, b) => a - b);
    const keywords = own.reduce((a, b) => a + b, 0);
    out.push({
      level: lv, nodes: own.length, keywords, share: keywords / placed,
      median: quantile(own, 0.5), low: quantile(own, 0.25), high: quantile(own, 0.75),
    });
  }
  return out;
}

const n1 = (v) => formatNumber(v, { maximumFractionDigits: 1 });

/** The balance as bars: the share of the keywords per level, the keywords per node. */
function Balance({ index }) {
  const lang = lang2(locale.value);
  const rows = balanceOf(index);
  const most = Math.max(1, ...rows.map((r) => r.high));
  return html`<section class="cx-pg-side__part" aria-labelledby="cx-pg-balance">
    <h3 class="cx-pg-side__title" id="cx-pg-balance">${t('playground.balance')}</h3>
    <p class="cx-settings__note">${t('playground.target')}</p>
    <table class="cx-pg-balance">
      <caption class="cx-visually-hidden">${t('playground.balance_caption')}</caption>
      <thead><tr><th scope="col">${t('playground.level')}</th><th scope="col">${t('playground.share')}</th>
        <th scope="col">${t('playground.per_node')}</th></tr></thead>
      <tbody>${rows.map((r) => html`<tr key=${r.level}>
        <th scope="row">${levelName(index.tree, r.level, lang)}</th>
        <td><span class="cx-pg-bar" style=${{ '--cx-pg-w': `${Math.round(r.share * 100)}%` }} aria-hidden="true"></span>
          <span class="cx-num">${formatPercent(r.share, { maximumFractionDigits: 0 })}</span></td>
        <td><span class="cx-pg-range" aria-hidden="true"
            style=${{ '--cx-pg-lo': `${(r.low / most) * 100}%`, '--cx-pg-hi': `${(r.high / most) * 100}%`,
              '--cx-pg-mid': `${(r.median / most) * 100}%` }}><span class="cx-pg-range__band"></span>
            <span class="cx-pg-range__mid"></span></span>
          <span class="cx-num">${t('playground.median', { median: n1(r.median), low: n1(r.low), high: n1(r.high) })}</span></td>
      </tr>`)}</tbody>
    </table>
  </section>`;
}

function Against({ against }) {
  if (!against) return null;
  const items = [
    ['split', against.split], ['merged', against.merged], ['moved', against.moved],
    ['set_aside', against.set_aside], ['put_back', against.put_back],
  ];
  return html`<section class="cx-pg-side__part" aria-labelledby="cx-pg-against">
    <h3 class="cx-pg-side__title" id="cx-pg-against">${t('playground.against')}</h3>
    <dl class="cx-pg-against">${items.map(([k, v]) => html`<div key=${k}>
      <dt>${t(`playground.against.${k}`)}</dt><dd class="cx-num">${formatNumber(v || 0)}</dd></div>`)}</dl>
  </section>`;
}

/** The side panel of a preview: the balance, the comparison and the actions. */
export function PlaygroundSide({ index, against, onAdopt, onSendAi, busy, disabled, step }) {
  return html`<aside class="cx-pg-side" aria-label=${t('playground.side')}>
    ${index ? html`<${Balance} index=${index} />` : null}
    <${Against} against=${against} />
    <p class="cx-settings__muted">${t('playground.stability_skipped')}</p>
    <div class="cx-pg-side__actions">
      <${Button} variant="primary" disabled=${disabled} loading=${busy === 'adopt'} onClick=${onAdopt}>
        ${t('playground.adopt')}<//>
      <${Button} disabled=${disabled} loading=${busy === 'ai'} onClick=${onSendAi}>${t('playground.send_ai')}<//>
    </div>
    ${step ? html`<p class="cx-settings__muted" role="status">${t(`playground.step.${step}`)}</p>` : null}
  </aside>`;
}
