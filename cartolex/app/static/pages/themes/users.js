// SPDX-License-Identifier: MIT
/**
 * The people who use a keyword most, in the theme editor's panel: the share of their
 * keyword use it holds, from the space of the themes (`GET /api/atlas/keyword-people`),
 * read when a single keyword is shown; and the links to the map and to the keywords.
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatPercent, t } from '../../core/i18n.js';
import { Links, linkTo } from '../map/links.js';

const LIMIT = 8;

/** The links of a keyword (the map, the keywords) and of a node (the map). */
export function ThemeLinks({ term = null, node = null }) {
  const links = term ? [{ href: linkTo.map('keyword', term), label: t('themes.links.map') },
    { href: linkTo.keyword(term), label: t('themes.links.keywords') }]
    : [{ href: linkTo.map('theme', node), label: t('themes.links.map') }];
  return html`<${Links} label=${t('map.links')} links=${links} />`;
}

/** Who uses *term* most. */
export function KeywordUsers({ api, term }) {
  const [answer, setAnswer] = useState(null);
  useEffect(() => {
    setAnswer(null);
    if (!api || !term) return;
    api.get('/api/atlas/keyword-people', { query: { term, limit: LIMIT } })
      .then((r) => setAnswer(r.ok ? { term, data: r.data } : { term, error: r.error }));
  }, [term]);
  if (!answer || answer.term !== term) {
    return html`<p class="cx-themes-empty-line" aria-busy="true">${t('common.loading')}</p>`;
  }
  if (answer.error) return html`<p class="cx-themes-empty-line">${t('themes.users.none')}</p>`;
  const { items, count } = answer.data;
  return html`${items.length ? html`<ol class="cx-themes-panel__people">
      ${items.map((it) => html`<li key=${it.id}>
        <a class="cx-themes-panel__person" href=${linkTo.map('person', it.id)}>${it.name || it.id}</a>
        <span class="cx-themes-bar" aria-hidden="true"><span class="cx-themes-bar__fill"
          style=${{ '--cx-bar': `${Math.min(100, Math.round(it.share * 100))}%` }}></span></span>
        <span class="cx-themes-row__share">${formatPercent(it.share)}</span></li>`)}
    </ol>` : null}
    <p class="cx-themes-panel__note">${t('themes.users.note', { count })}</p>`;
}
