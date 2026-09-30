// SPDX-License-Identifier: MIT
/**
 * The method screen's figures, drawn in SVG from the diagnostics' numbers:
 * stacked bars, lines with marks, and a dendrogram. Each figure is an image
 * with a name and a caption for assistive technology, and its numbers can be
 * unfolded as a table below it. Series take the data hues (`--cx-hue-N`, by
 * class); text and rules take the page's tokens.
 */

import { html } from '../../core/preact.js';
import { formatNumber, t } from '../../core/i18n.js';
import { useUid } from '../../core/dom.js';

const W = 640;
const H = 220;
const PAD = { left: 48, right: 12, top: 24, bottom: 34 };

/** Round ticks from 0 to *max*. */
function ticks(max, n = 4) {
  if (!(max > 0)) return [0];
  const raw = max / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
  const out = [];
  // up to the first tick at or above *max*, so that nothing is drawn past the top
  for (let v = 0; out.length < 50; v += step) {
    out.push(Number(v.toPrecision(6)));
    if (v >= max - step * 1e-9) break;
  }
  return out;
}

function fmt(v, digits = 2) {
  return formatNumber(v, { maximumFractionDigits: digits });
}

/** The numbers of a figure, folded under it. */
function Numbers({ head, rows }) {
  return html`<details class="cx-chart__numbers"><summary>${t('method.chart.numbers')}</summary>
    <table class="cx-settings__table"><thead><tr>${head.map((h, i) => html`<th scope="col" key=${i}>${h}</th>`)}</tr></thead>
      <tbody>${rows.map((r, i) => html`<tr key=${i}>${r.map((c, j) => (j === 0
        ? html`<th scope="row" key=${j}>${c}</th>` : html`<td key=${j} class="cx-num">${c}</td>`))}</tr>`)}</tbody></table>
  </details>`;
}

/** A figure's frame: the SVG with its name, the caption, the legend and the numbers. */
function Figure({ label, caption, legend, numbers, children, height = H }) {
  const id = useUid('cx-chart');
  return html`<figure class="cx-chart">
    <svg class="cx-chart__svg" viewBox=${`0 0 ${W} ${height}`} role="img" aria-labelledby=${`${id}-t`}
      preserveAspectRatio="xMidYMid meet"><title id=${`${id}-t`}>${label}</title>${children}</svg>
    ${legend && legend.length ? html`<ul class="cx-chart__legend">${legend.map((s) => html`<li key=${s.id}>
      <span class=${`cx-chart__swatch cx-chart__hue-${s.hue}`} aria-hidden="true"></span>${s.label}</li>`)}</ul>` : null}
    ${caption ? html`<figcaption class="cx-settings__muted">${caption}</figcaption>` : null}
    ${numbers || null}
  </figure>`;
}

/** The y axis: its ticks and rules. */
function YAxis({ max, height, y, format = (v) => fmt(v) }) {
  return html`<g class="cx-chart__axis">${ticks(max).map((v) => html`<g key=${v}>
    <line x1=${PAD.left} x2=${W - PAD.right} y1=${y(v)} y2=${y(v)} class="cx-chart__rule" />
    <text x=${PAD.left - 6} y=${y(v) + 4} text-anchor="end">${format(v)}</text></g>`)}
    <line x1=${PAD.left} x2=${W - PAD.right} y1=${height - PAD.bottom} y2=${height - PAD.bottom} class="cx-chart__base" /></g>`;
}

/**
 * Stacked bars. *bars*: `[{label, values: {seriesId: n}}]`; *series*: `[{id, label, hue}]`.
 * Only some bar labels are written under the axis (*every*).
 */
export function BarChart({ bars, series, label, caption, xLabel, yLabel, every = 1, height = H }) {
  const totals = bars.map((b) => series.reduce((a, s) => a + (b.values[s.id] || 0), 0));
  const max = Math.max(1, ...totals);
  const top = ticks(max).slice(-1)[0] || max;
  const plotW = W - PAD.left - PAD.right;
  const slot = plotW / Math.max(1, bars.length);
  const bw = Math.max(1, slot * 0.8);
  const y = (v) => height - PAD.bottom - (v / top) * (height - PAD.top - PAD.bottom);
  const head = [xLabel, ...series.map((s) => s.label)];
  const rows = bars.map((b) => [b.label, ...series.map((s) => formatNumber(b.values[s.id] || 0))]);
  return html`<${Figure} label=${label} caption=${caption} height=${height}
    legend=${series.length > 1 ? series : null} numbers=${html`<${Numbers} head=${head} rows=${rows} />`}>
    <${YAxis} max=${top} height=${height} y=${y} />
    ${bars.map((b, i) => {
      let acc = 0;
      const x = PAD.left + i * slot + (slot - bw) / 2;
      return html`<g key=${i}>${series.map((s) => {
        const v = b.values[s.id] || 0;
        if (!v) return null;
        const y0 = y(acc);
        acc += v;
        return html`<rect key=${s.id} class=${`cx-chart__hue-${s.hue}`} x=${x} width=${bw}
          y=${y(acc)} height=${Math.max(0.5, y0 - y(acc))} />`;
      })}${i % every === 0 ? html`<text class="cx-chart__tick" x=${x + bw / 2} y=${height - PAD.bottom + 14}
          text-anchor="middle">${b.label}</text>` : null}</g>`;
    })}
    ${xLabel ? html`<text class="cx-chart__label" x=${PAD.left + plotW / 2} y=${height - 4} text-anchor="middle">${xLabel}</text>` : null}
    ${yLabel ? html`<text class="cx-chart__label" x=${PAD.left} y=${PAD.top - 2}>${yLabel}</text>` : null}
  <//>`;
}

/**
 * Lines. *series*: `[{id, label, hue, points: [[x, y], …]}]`; *marks*: vertical marks `[{x, label}]`
 * (a chosen value); *yMax* fixes the top (1 for shares).
 */
export function LineChart({ series, label, caption, xLabel, yLabel, marks = [], yMax = null, format = (v) => fmt(v),
  xFormat = (v) => fmt(v), height = H }) {
  const xs = series.flatMap((s) => s.points.map((p) => p[0]));
  const ys = series.flatMap((s) => s.points.map((p) => p[1]));
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const top = yMax || ticks(Math.max(1e-9, ...ys)).slice(-1)[0];
  const plotW = W - PAD.left - PAD.right;
  const x = (v) => PAD.left + (x1 === x0 ? plotW / 2 : ((v - x0) / (x1 - x0)) * plotW);
  const y = (v) => height - PAD.bottom - (v / top) * (height - PAD.top - PAD.bottom);
  const allX = [...new Set(xs)].sort((a, b) => a - b);
  const head = [xLabel, ...series.map((s) => s.label)];
  const rows = allX.map((v) => [xFormat(v), ...series.map((s) => {
    const p = s.points.find((q) => q[0] === v);
    return p ? format(p[1]) : '—';
  })]);
  return html`<${Figure} label=${label} caption=${caption} height=${height}
    legend=${series.length > 1 ? series : null} numbers=${html`<${Numbers} head=${head} rows=${rows} />`}>
    <${YAxis} max=${top} height=${height} y=${y} format=${format} />
    ${marks.map((m) => html`<g key=${`m${m.x}`} class="cx-chart__mark">
      <line x1=${x(m.x)} x2=${x(m.x)} y1=${PAD.top} y2=${height - PAD.bottom} />
      <text x=${x(m.x) + 4} y=${PAD.top + 10}>${m.label}</text></g>`)}
    ${series.map((s) => html`<g key=${s.id} class=${`cx-chart__line cx-chart__hue-${s.hue}`}>
      <polyline points=${s.points.map((p) => `${x(p[0])},${y(p[1])}`).join(' ')} />
      ${s.points.map((p, i) => html`<circle key=${i} cx=${x(p[0])} cy=${y(p[1])} r="3" />`)}</g>`)}
    ${allX.length <= 12 ? allX.map((v) => html`<text key=${`x${v}`} class="cx-chart__tick" x=${x(v)}
      y=${height - PAD.bottom + 14} text-anchor="middle">${xFormat(v)}</text>`) : null}
    ${xLabel ? html`<text class="cx-chart__label" x=${PAD.left + plotW / 2} y=${height - 4} text-anchor="middle">${xLabel}</text>` : null}
    ${yLabel ? html`<text class="cx-chart__label" x=${PAD.left} y=${PAD.top - 2}>${yLabel}</text>` : null}
  <//>`;
}

/**
 * A dendrogram of Ward's merges (scipy's linkage: `[a, b, height, count]`) over *leaves* (their
 * names), drawn left to right in the linkage's leaf *order*; leaves are coloured by *hue*.
 */
export function Dendrogram({ leaves, order, merges, label, caption }) {
  const n = leaves.length;
  const rowH = 18;
  const height = n * rowH + 24;
  const nameW = 220;
  const top = Math.max(1e-9, ...merges.map((m) => m[2]));
  const x = (h) => W - PAD.right - (h / top) * (W - PAD.right - nameW - 12);
  const pos = new Map();
  order.forEach((leaf, i) => pos.set(leaf, { y: 12 + i * rowH + rowH / 2, h: 0 }));
  const lines = [];
  merges.forEach(([a, b, h], i) => {
    const pa = pos.get(a);
    const pb = pos.get(b);
    lines.push(`M${x(pa.h)},${pa.y}H${x(h)}V${pb.y}H${x(pb.h)}`);
    pos.set(n + i, { y: (pa.y + pb.y) / 2, h });
  });
  const rows = order.map((leaf) => [leaves[leaf].name, formatNumber(leaves[leaf].keywords)]);
  return html`<${Figure} label=${label} caption=${caption} height=${height}
    numbers=${html`<${Numbers} head=${[t('method.grouping.theme'), t('method.grouping.keywords')]} rows=${rows} />`}>
    ${order.map((leaf, i) => html`<g key=${leaf}>
      <rect class=${`cx-chart__hue-${(leaf % 12) + 1}`} x="0" y=${12 + i * rowH + 4} width="8" height=${rowH - 8} />
      <text class="cx-chart__leaf" x="14" y=${12 + i * rowH + rowH / 2 + 4}>${leaves[leaf].name}</text></g>`)}
    <path class="cx-chart__tree" d=${lines.join('')} />
  <//>`;
}
