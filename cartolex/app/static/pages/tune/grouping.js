// SPDX-License-Identifier: MIT
/**
 * The grouping step's diagnostic: the proposal's levels (groups, keywords on
 * each level, keywords per group: the balance the comb looks for), the
 * keywords too broad for any theme, the comb's calibration of θ (what each θ
 * of its grid gives, and the one kept), the outline of the top two levels and
 * a dendrogram of the top level's themes.
 */

import { html } from '../../core/preact.js';
import { formatNumber, t } from '../../core/i18n.js';
import { BarChart, Dendrogram, LineChart } from './charts.js';
import { Facts, Lead, nameOf } from './common.js';

const fmt = (v) => formatNumber(v, { maximumFractionDigits: 3 });

/** Keywords per group on one level, the largest first. */
function Sizes({ level }) {
  const n = level.sizes.length;
  if (n < 2) return null;
  const bars = level.sizes.map((v, i) => ({ label: String(i + 1), values: { k: v } }));
  return html`<${BarChart} bars=${bars} every=${Math.max(1, Math.ceil(n / 15))} height=${160}
    series=${[{ id: 'k', hue: 4, label: t('method.grouping.keywords') }]}
    label=${t('method.grouping.sizes', { level: level.level })}
    caption=${t('method.grouping.sizes_caption', { level: level.level, n })}
    xLabel=${t('method.grouping.group_rank')} yLabel=${t('method.grouping.keywords')} />`;
}

function Calibration({ c, depth }) {
  if (!c) return html`<p class="cx-settings__muted">${t('method.grouping.no_calibration')}</p>`;
  const mark = [{ x: c.theta, label: t('method.grouping.kept_theta', { theta: fmt(c.theta) }) }];
  const levels = Array.from({ length: depth }, (_, i) => i);
  return html`<p class="cx-settings__note">${depth > 1 ? t('method.grouping.calibration_lead', { texts: c.texts, min: c.min_texts })
      : t('method.grouping.calibration_depth1', { theta: fmt(c.default_theta) })}</p>
    <${LineChart} marks=${mark} xFormat=${fmt}
      series=${levels.map((lv) => ({ id: `l${lv}`, hue: lv + 1, label: t('method.grouping.level', { level: lv + 1 }),
        points: c.points.map((p) => [p.theta, p.per_node[lv]]) }))}
      label=${t('method.grouping.per_node')} caption=${t('method.grouping.per_node_caption')}
      xLabel=${t('method.grouping.theta')} yLabel=${t('method.grouping.per_group')} />
    <${LineChart} marks=${mark} xFormat=${fmt} height=${160}
      series=${[{ id: 'broad', hue: 7, label: t('method.grouping.too_broad'),
        points: c.points.map((p) => [p.theta, p.too_broad]) }]}
      label=${t('method.grouping.broad_chart')} caption=${t('method.grouping.broad_caption')}
      xLabel=${t('method.grouping.theta')} yLabel=${t('method.grouping.keywords')} />`;
}

/** The top two levels as nested lists, with the keywords under each node. */
function Outline({ outline }) {
  const tops = outline.filter((n) => n.level === 1);
  const children = (id) => outline.filter((n) => n.parent === id);
  return html`<ol class="cx-method-outline" aria-label=${t('method.grouping.outline')}>
    ${tops.map((n) => html`<li key=${n.id}><span class="cx-method-outline__name">${nameOf(n.names, n.id)}</span>
      <span class="cx-settings__muted cx-num">${t('method.grouping.node_keywords', { n: n.keywords })}</span>
      ${children(n.id).length ? html`<ol>${children(n.id).map((c) => html`<li key=${c.id}>
        <span>${nameOf(c.names, c.id)}</span>
        <span class="cx-settings__muted cx-num">${t('method.grouping.node_keywords', { n: c.keywords })}</span></li>`)}</ol>` : null}
    </li>`)}
  </ol>`;
}

export function GroupingDiagnostic({ view }) {
  const levels = view.levels || [];
  const byId = new Map((view.outline || []).map((n) => [n.id, n]));
  const d = view.dendrogram;
  return html`<${Lead}>${t('method.grouping.lead')}<//>
    <${Facts} items=${[
      [t('method.grouping.depth'), formatNumber(view.depth || 0)],
      [t('method.grouping.placed'), formatNumber(view.keywords || 0)],
      [t('method.grouping.too_broad'), formatNumber(view.too_broad || 0)],
      [t('method.grouping.comb'), view.comb ? t('settings.build.on') : t('method.off')],
      view.calibration ? [t('method.grouping.theta'), fmt(view.calibration.theta)] : null,
    ]} />
    <table class="cx-settings__table" aria-label=${t('method.grouping.levels')}>
      <caption class="cx-method-caption">${t('method.grouping.levels')}</caption>
      <thead><tr><th scope="col">${t('method.grouping.level_col')}</th><th scope="col" class="cx-num">${t('method.grouping.groups')}</th>
        <th scope="col" class="cx-num">${t('method.grouping.on_level')}</th><th scope="col" class="cx-num">${t('method.grouping.min')}</th>
        <th scope="col" class="cx-num">${t('method.grouping.median')}</th><th scope="col" class="cx-num">${t('method.grouping.max')}</th></tr></thead>
      <tbody>${levels.map((l) => html`<tr key=${l.level}><th scope="row">${t('method.grouping.level', { level: l.level })}</th>
        <td class="cx-num">${formatNumber(l.groups)}</td><td class="cx-num">${formatNumber(l.keywords)}</td>
        <td class="cx-num">${formatNumber(l.per_group.min)}</td><td class="cx-num">${formatNumber(l.per_group.median)}</td>
        <td class="cx-num">${formatNumber(l.per_group.max)}</td></tr>`)}</tbody>
    </table>
    ${levels.map((l) => html`<${Sizes} key=${l.level} level=${l} />`)}
    <h4 class="cx-method-subtitle">${t('method.grouping.calibration')}</h4>
    ${view.comb ? html`<${Calibration} c=${view.calibration} depth=${view.depth} />`
      : html`<p class="cx-settings__muted">${t('method.grouping.comb_off')}</p>`}
    <h4 class="cx-method-subtitle">${t('method.grouping.hierarchy')}</h4>
    <${Outline} outline=${view.outline || []} />
      ${d ? html`<${Dendrogram} order=${d.order} merges=${d.merges}
        leaves=${d.leaves.map((id) => ({ name: nameOf((byId.get(id) || {}).names, id), keywords: (byId.get(id) || {}).keywords || 0 }))}
        label=${t('method.grouping.dendrogram')} caption=${t('method.grouping.dendrogram_caption')} />` : null}`;
}
