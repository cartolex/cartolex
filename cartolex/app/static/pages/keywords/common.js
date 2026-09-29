// SPDX-License-Identifier: MIT
/**
 * What the keywords screen's modules share: the three bands and their shapes,
 * the routes that decide a keyword (you, an AI in a browser, an AI by API),
 * a keyword's reason in words, and the decisions' requests.
 */
import { html } from '../../core/preact.js';
import { has, t } from '../../core/i18n.js';
import { Icon } from '../../components/index.js';

export const BANDS = ['kept', 'check', 'aside'];
export const ROUTES = ['person', 'ai-handoff', 'ai-api', 'extraction'];
/** A band's shape: a check (kept), a dash (to check), a cross (set aside). */
const BAND_ICON = { kept: 'check', check: 'dash', aside: 'cross' };

/** A band: its shape and its word. */
export function BandMark({ band }) {
  return html`<span class=${`cx-kw-band cx-kw-band--${band}`}>
    <${Icon} name=${BAND_ICON[band] || 'dash'} /><span>${t(`keywords.band.${band}`)}</span></span>`;
}

/** The route that decided a keyword, in a few words. */
export function RouteMark({ route }) {
  if (!route || route === 'extraction') {
    return html`<span class="cx-kw-route cx-kw-route--none">${t('keywords.route.extraction')}</span>`;
  }
  return html`<span class=${`cx-kw-route cx-kw-route--${route}`}>${t(`keywords.route.${route}`)}</span>`;
}

/** The extraction's reason of a band (« multiword », « part-of: X »…) in words. */
export function extractionReason(reason) {
  const [code, ...rest] = String(reason || '').split(':');
  const arg = rest.join(':').trim();
  const key = `keywords.reason.${code.trim()}`;
  return has(key) ? t(key, { word: arg }) : reason || '';
}

/** Why a keyword is in its band: your decision, the AI's verdict, or the extraction's reason. */
export function reasonText(row) {
  const d = row.decision;
  if (d) {
    if (d.decision === 'merge') return t('keywords.why.merged', { target: d.target });
    const verb = t(`keywords.why.${d.decision}`);
    return d.reason ? t('keywords.why.with_reason', { verb, reason: d.reason }) : verb;
  }
  if (row.route === 'ai-api' && row.ai) {
    const code = has(`keywords.ai.code.${row.ai.code}`) ? t(`keywords.ai.code.${row.ai.code}`) : row.ai.code;
    return t('keywords.why.ai', { code });
  }
  return extractionReason(row.reason);
}

/** The key of a keyword row. */
export const keyOf = (row) => `${row.language}\u0000${row.term}`;

/** Keyword references from row keys. */
export function refsOf(keys) {
  return keys.filter((k) => !k.startsWith('@')).map((k) => {
    const [language, term] = k.split('\u0000');
    return { term, language };
  });
}

/**
 * Send decisions (keep, exclude, merge) or restore them; `version` is the
 * keywords.csv version read with the list. Resolves to the API's result.
 */
export function decide(api, version, decisions) {
  return api.post('/api/keywords/decisions', { decisions }, { ifMatch: version });
}

export function restore(api, version, keywords) {
  return api.post('/api/keywords/restore', { keywords }, { ifMatch: version });
}
