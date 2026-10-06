// SPDX-License-Identifier: MIT
/**
 * A keyword's drawer: the people who use it most (the share of their keyword use it holds,
 * from the space of the themes: `GET /api/atlas/keyword-people`), each with a link to their
 * sheet and to their place on the map, and the keyword shown on the map or in the themes.
 * A candidate merged into another keyword shows the keyword it was merged into.
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatPercent, t } from '../../core/i18n.js';
import { Drawer, ErrorCard } from '../../components/index.js';
import { Links, linkTo } from '../map/links.js';

const LIMIT = 100;

/** The keyword of the vocabulary a row stands for: the one it was merged into, else itself. */
export function vocabularyTerm(row) {
  const d = row && row.decision;
  return d && d.decision === 'merge' && d.target ? d.target : (row ? row.term : '');
}

export function KeywordPeopleDrawer({ ctx, term, onClose }) {
  const [answer, setAnswer] = useState(null);
  useEffect(() => {
    setAnswer(null);
    if (!term) return;
    ctx.api.get('/api/atlas/keyword-people', { query: { term, limit: LIMIT } })
      .then((r) => setAnswer(r.ok ? { data: r.data } : { error: r.error }));
  }, [term]);
  const data = answer && answer.data;
  let body;
  if (!answer) body = html`<p aria-busy="true">${t('common.loading')}</p>`;
  else if (answer.error && answer.error.code === 'atlas_item_not_found') {
    body = html`<p class="cx-corpus-muted">${t('keywords.people.unknown')}</p>`;
  } else if (answer.error) body = html`<${ErrorCard} error=${answer.error} compact />`;
  else {
    body = html`<p>${t('keywords.people.count', { count: data.count })}</p>
      <ol class="cx-kw-people">
        ${data.items.map((it) => html`<li key=${it.id}>
          <span class="cx-kw-people__name">${it.name || it.id}</span>
          <span class="cx-kw-people__share">${formatPercent(it.share)}</span>
          <a class="cx-link" href=${linkTo.person(it.id)}>${t('keywords.people.open')}</a>
          <a class="cx-link" href=${linkTo.map('person', it.id)}>${t('keywords.people.map')}</a>
        </li>`)}
      </ol>
      ${data.count > data.items.length ? html`<p class="cx-corpus-muted">${t('map.users.more',
        { count: data.count - data.items.length })}</p>` : null}
      <p class="cx-corpus-muted">${t('map.users.help')}</p>`;
  }
  return html`<${Drawer} open=${Boolean(term)} onClose=${onClose} title=${term || ''}
    description=${t('keywords.people.title')}>
    <${Links} label=${t('map.links')} links=${[{ href: linkTo.map('keyword', term), label: t('keywords.action.show_map') },
      { href: linkTo.themesKeyword(term), label: t('map.links.keyword_themes') }]} />
    ${body}
  <//>`;
}
