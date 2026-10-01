// SPDX-License-Identifier: MIT
/**
 * The space step's diagnostic: the share of the variance each dimension
 * explains (and the running total), and how many of each person's nearest
 * people (by their keywords) the space keeps with its first dimensions.
 */

import { html } from '../../core/preact.js';
import { formatNumber, formatPercent, t } from '../../core/i18n.js';
import { BarChart, LineChart } from './charts.js';
import { Facts, Lead } from './common.js';

export function SpaceDiagnostic({ view }) {
  const explained = view.explained || [];
  let running = 0;
  const bars = explained.map((r, i) => {
    running += r;
    return { label: String(i + 1), values: { share: r * 100 }, running };
  });
  const curve = (view.neighbours && view.neighbours.curve) || [];
  const every = Math.max(1, Math.ceil(bars.length / 20));
  const last = curve.length ? curve[curve.length - 1].overlap : null;
  return html`<${Lead}>${t('method.space.lead')}<//>
    <${Facts} items=${[
      [t('method.space.dimensions'), formatNumber(view.dimensions)],
      view.wanted && view.wanted !== view.dimensions ? [t('method.space.wanted'), formatNumber(view.wanted)] : null,
      [t('method.space.explained_total'), formatPercent(view.explained_total || 0, { maximumFractionDigits: 1 })],
      [t('method.space.kept'), last === null ? '—' : formatPercent(last, { maximumFractionDigits: 0 })],
      [t('method.space.people'), formatNumber(view.people || 0)],
      [t('method.space.terms'), formatNumber(view.terms || 0)],
    ]} />
    ${bars.length ? html`<${BarChart} bars=${bars} every=${every}
      series=${[{ id: 'share', hue: 2, label: t('method.space.share') }]}
      label=${t('method.space.variance')} caption=${t('method.space.variance_caption')}
      xLabel=${t('method.space.dimension')} yLabel=${t('method.space.percent')} />` : null}
    ${curve.length > 1 ? html`<${LineChart} yMax=${1}
      series=${[{ id: 'overlap', hue: 5, label: t('method.space.kept'),
        points: curve.map((c, i) => [i, c.overlap]) }]}
      xFormat=${(i) => formatNumber(curve[i] ? curve[i].dimensions : i)}
      format=${(v) => formatPercent(v, { maximumFractionDigits: 0 })}
      label=${t('method.space.neighbours')}
      caption=${t('method.space.neighbours_caption', { k: view.neighbours.k, n: view.neighbours.sample })}
      xLabel=${t('method.space.first_dimensions')} yLabel=${t('method.space.kept')} />` : null}`;
}
