// SPDX-License-Identifier: MIT
/**
 * The keywords step's diagnostic: the candidates by band and by reason, the
 * spread of their scores (a histogram by band, on a log scale), the
 * vocabulary against its cap. The ranked candidates are the keywords page's own list (sorted
 * by score), beside the panel.
 */

import { html } from '../../core/preact.js';
import { formatNumber, t } from '../../core/i18n.js';
import { Icon } from '../../components/index.js';
import { BandMark, extractionReason } from '../keywords/common.js';
import { BarChart } from './charts.js';
import { BAND_SERIES, Facts, Lead } from './common.js';

/** The words of a reason code; a code that names a word (« part-of: … ») gets « … ». */
const WITH_WORD = new Set(['part-of', 'common-modifier', 'name', 'stop-word-edge']);
function reasonWords(code) {
  if (code === 'none') return '—';
  return extractionReason(WITH_WORD.has(code) ? `${code}: …` : code);
}

export function KeywordsDiagnostic({ view }) {
  const bands = view.bands || {};
  const vocab = view.vocabulary || {};
  const capped = vocab.max_keywords && vocab.kept_keywords >= vocab.max_keywords;
  const hist = view.histogram || { edges: [], counts: {} };
  const bars = hist.edges.slice(0, -1).map((edge, i) => ({
    label: formatNumber(edge, { maximumSignificantDigits: 2 }),
    values: Object.fromEntries(BAND_SERIES.map((b) => [b.id, (hist.counts[b.id] || [])[i] || 0])),
  }));
  const series = BAND_SERIES.filter((b) => (hist.counts[b.id] || []).some(Boolean));
  return html`<${Lead}>${t('method.keywords.lead')}<//>
    <${Facts} items=${[
      [t('method.keywords.candidates'), formatNumber(view.candidates || 0)],
      ...BAND_SERIES.map((b) => [html`<${BandMark} band=${b.id} />`, formatNumber(bands[b.id] || 0)]),
      [t('method.keywords.vocabulary'), vocab.kept_keywords === null || vocab.kept_keywords === undefined ? '—'
        : t('method.keywords.of_cap', { n: vocab.kept_keywords, cap: vocab.max_keywords || 0 })],
      [t('method.keywords.unit'), view.counting_unit || '—'],
    ]} />
    ${capped ? html`<p class="cx-method-mark cx-method-mark--changed"><${Icon} name="warning" />
      <span>${t('method.keywords.capped')}</span></p>` : null}
    ${bars.length ? html`<${BarChart} bars=${bars} series=${series} every=${4}
      label=${t('method.keywords.histogram')} caption=${t('method.keywords.histogram_caption')}
      xLabel=${t('method.keywords.score')} yLabel=${t('method.keywords.count')} />` : null}
    <table class="cx-settings__table" aria-label=${t('method.keywords.reasons')}>
      <caption class="cx-method-caption">${t('method.keywords.reasons')}</caption>
      <thead><tr><th scope="col">${t('keywords.col.band')}</th><th scope="col">${t('keywords.col.reason')}</th>
        <th scope="col" class="cx-num">${t('method.keywords.count')}</th></tr></thead>
      <tbody>${BAND_SERIES.flatMap((b) => Object.entries((view.reasons || {})[b.id] || {}).map(([code, n]) => html`
        <tr key=${`${b.id}-${code}`}><td><${BandMark} band=${b.id} /></td><td>${reasonWords(code)}</td>
          <td class="cx-num">${formatNumber(n)}</td></tr>`))}</tbody>
    </table>`;
}
