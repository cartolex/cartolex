// SPDX-License-Identifier: MIT
/**
 * The site's home (a search: a person, an organisation, a keyword, a
 * theme), the index (people, organisations and keywords as searchable,
 * paginated lists: the list alternative to the map), the page about the
 * method, and « not found ».
 */
(function () {
  'use strict';

  const S = window.CxSite;
  const h = S.h;
  const t = S.t;
  const PAGE = 50;

  /** Everything the search finds: `[{kind, id, label, sub, path, key}]`. */
  let items = null;
  function searchItems() {
    if (items) return items;
    const core = S.ix.core;
    items = [];
    core.people.id.forEach((id, i) => items.push({ kind: 'people', id, label: S.personName(i),
      sub: core.people.top[i] ? S.nodeName(core.people.top[i]) : '', path: `/person/${id}` }));
    core.orgs.id.forEach((id, i) => items.push({ kind: 'orgs', id, label: core.orgs.name[i],
      sub: [core.orgs.acronym[i], S.orgLevelName(core.orgs.level[i])].filter(Boolean).join(' · '), path: `/org/${id}` }));
    core.nodes.forEach((n) => items.push({ kind: 'themes', id: n.id, label: S.nodeName(n.id),
      sub: t('theme.level', { level: n.level }), path: `/themes/${n.id}`, also: Object.values(n.names || {}).join(' ') }));
    core.keywords.term.forEach((term, i) => items.push({ kind: 'keywords', id: term, label: term,
      sub: core.keywords.node[i] ? S.nodeName(core.keywords.node[i]) : '', path: `/map?sel=${encodeURIComponent(`keyword:${term}`)}` }));
    items.forEach((it) => { it.key = S.fold(`${it.label} ${it.sub} ${it.also || ''}`); });
    return items;
  }

  /** Language change: the labels change with it. */
  S.resetSearch = function resetSearch() {
    items = null;
  };

  function find(query, limit) {
    const words = S.fold(query).split(/\s+/).filter(Boolean);
    if (!words.length) return [];
    const out = [];
    for (const it of searchItems()) {
      if (words.every((w) => it.key.includes(w))) {
        out.push(it);
        if (out.length >= limit) break;
      }
    }
    const first = S.fold(query);
    return out.sort((a, b) => Number(!S.fold(a.label).startsWith(first)) - Number(!S.fold(b.label).startsWith(first)));
  }

  const SHAPE = { people: 'circle', orgs: 'square', keywords: 'diamond', themes: 'square' };

  S.pages.home = function home(main) {
    const core = S.ix.core;
    const input = h('input', { type: 'search', id: 'cx-find', class: 'cx-input cx-input--large', autocomplete: 'off',
      placeholder: t('home.search.placeholder'), 'aria-describedby': 'cx-find-count' });
    const count = h('p', { id: 'cx-find-count', class: 'cx-muted', 'aria-live': 'polite' });
    const results = h('ul', { class: 'cx-results', 'aria-label': t('home.search.results') });
    const run = () => {
      const found = find(input.value, 60);
      count.textContent = input.value.trim() ? S.tn('home.search.count', found.length) : '';
      results.replaceChildren(...found.map((it) => h('li', {}, h('a', { href: `#${it.path}`, class: 'cx-result' }, [
        S.symbol(SHAPE[it.kind], it.kind === 'themes' ? `--cx-hue-${S.colourOf(it.id) + 1}` : null),
        h('span', { class: 'cx-result__label', text: it.label }),
        h('span', { class: 'cx-result__kind', text: t(`kind1.${it.kind}`) }),
        it.sub ? h('span', { class: 'cx-result__sub', text: it.sub }) : null,
      ]))));
    };
    input.addEventListener('input', run);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        const first = results.querySelector('a');
        if (first) window.location.hash = first.getAttribute('href');
      } else if (e.key === 'ArrowDown') {
        const first = results.querySelector('a');
        if (first) {
          e.preventDefault();
          first.focus();
        }
      }
    });
    results.addEventListener('keydown', (e) => {
      const links = [...results.querySelectorAll('a')];
      const at = links.indexOf(document.activeElement);
      if (at < 0) return;
      if (e.key === 'ArrowDown' && at + 1 < links.length) links[at + 1].focus();
      else if (e.key === 'ArrowUp') (at > 0 ? links[at - 1] : input).focus();
      else return;
      e.preventDefault();
    });
    const themes = h('ul', { class: 'cx-chips' }, S.ix.tops.map((id) => h('li', {}, h('a', { href: `#/themes/${id}`, class: 'cx-chip' }, [
      h('span', { class: 'cx-chip-dot', 'aria-hidden': 'true', style: { '--cx-chip': `var(--cx-hue-${S.colourOf(id) + 1})` } }),
      S.nodeName(id)]))));
    main.append(
      h('h1', { class: 'cx-page__title', tabindex: '-1', text: core.title }),
      h('p', { class: 'cx-lead', text: S.t('home.lead', { people: core.people.id.length, orgs: core.orgs.id.length,
        keywords: core.keywords.term.length, themes: S.ix.tops.length }) }),
      h('section', { class: 'cx-card cx-find', 'aria-labelledby': 'cx-find-label' }, [
        h('label', { for: 'cx-find', id: 'cx-find-label', class: 'cx-find__label', text: t('home.search') }),
        input, count, results]),
      h('section', { class: 'cx-card', 'aria-labelledby': 'cx-home-themes' }, [
        h('h2', { id: 'cx-home-themes', text: t('home.themes') }), themes,
        h('p', {}, [S.link('/themes', t('home.themes.open'), 'cx-button'), ' ', S.link('/map', t('home.map.open'), 'cx-button cx-button--primary')])]),
    );
    return null;
  };

  // ── the index: people, organisations, keywords ───────────────────────────

  function rowsOf(tab) {
    const core = S.ix.core;
    if (tab === 'orgs') {
      return core.orgs.id.map((id, i) => ({ path: `/org/${id}`, cells: [core.orgs.name[i],
        S.orgLevelName(core.orgs.level[i]), S.fmt(core.orgs.members[i])], key: S.fold(`${core.orgs.name[i]} ${core.orgs.acronym[i]}`) }));
    }
    if (tab === 'keywords') {
      return core.keywords.term.map((term, i) => ({ path: `/map?sel=${encodeURIComponent(`keyword:${term}`)}`,
        cells: [term, core.keywords.node[i] ? S.nodeName(core.keywords.node[i]) : ''], key: S.fold(term) }))
        .sort((a, b) => a.cells[0].localeCompare(b.cells[0], S.lang));
    }
    return core.people.id.map((id, i) => ({ path: `/person/${id}`,
      cells: [S.personName(i), core.people.top[i] ? S.nodeName(core.people.top[i]) : ''], key: S.fold(S.personName(i)) }));
  }

  const HEADS = { people: ['list.col.name', 'list.col.theme'], orgs: ['list.col.name', 'list.col.level', 'list.col.members'],
    keywords: ['list.col.keyword', 'list.col.theme'] };

  S.pages.list = function list(main, route) {
    const tabs = ['people', 'orgs', 'keywords'].filter((k) => k !== 'orgs' || S.ix.core.orgs.id.length);
    const tab = tabs.includes(route.query.get('tab')) ? route.query.get('tab') : 'people';
    const q = route.query.get('q') || '';
    const words = S.fold(q).split(/\s+/).filter(Boolean);
    const rows = rowsOf(tab).filter((r) => words.every((w) => r.key.includes(w)));
    const pages = Math.max(1, Math.ceil(rows.length / PAGE));
    const page = Math.min(pages, Math.max(1, Number.parseInt(route.query.get('page') || '1', 10) || 1));
    const href = (patch) => {
      const p = new URLSearchParams({ tab, q, page: String(page), ...patch });
      if (!p.get('q')) p.delete('q');
      if (p.get('page') === '1') p.delete('page');
      return `#/list?${p}`;
    };
    const input = h('input', { type: 'search', class: 'cx-input', value: q, 'aria-label': t('list.filter'),
      placeholder: t('list.filter') });
    input.addEventListener('change', () => window.location.replace(href({ q: input.value, page: '1' })));
    const shown = rows.slice((page - 1) * PAGE, page * PAGE);
    main.append(
      h('h1', { class: 'cx-page__title', tabindex: '-1', text: t('nav.list') }),
      h('nav', { class: 'cx-tabs', 'aria-label': t('list.tabs') }, tabs.map((k) => h('a', { href: href({ tab: k, page: '1', q: '' }),
        class: 'cx-tabs__tab', 'aria-current': k === tab ? 'page' : null }, t(`kind.${k}`)))),
      h('div', { class: 'cx-list-bar' }, [input, h('p', { class: 'cx-muted', 'aria-live': 'polite',
        text: S.tn('list.count', rows.length) })]),
      h('div', { class: 'cx-table-wrap' }, h('table', { class: 'cx-table' }, [
        h('thead', {}, h('tr', {}, HEADS[tab].map((k) => h('th', { scope: 'col', text: t(k) })))),
        h('tbody', {}, shown.map((r) => h('tr', {}, r.cells.map((c, j) => h('td', {}, j === 0 ? S.link(r.path, c) : c))))),
      ])),
      pages > 1 ? h('nav', { class: 'cx-pager', 'aria-label': t('list.pages') }, [
        page > 1 ? h('a', { href: href({ page: String(page - 1) }), class: 'cx-button' }, t('list.previous')) : null,
        h('span', { class: 'cx-muted', text: t('list.page', { page, pages }) }),
        page < pages ? h('a', { href: href({ page: String(page + 1) }), class: 'cx-button' }, t('list.next')) : null,
      ]) : null,
    );
    return null;
  };

  // ── about the method ─────────────────────────────────────────────────────

  S.pages.about = function about(main) {
    const core = S.ix.core;
    const section = (key, extra) => h('section', { class: 'cx-card cx-prose' }, [h('h2', { text: t(`about.${key}.title`) }),
      h('p', { text: t(`about.${key}`) }), extra || null]);
    main.append(
      h('h1', { class: 'cx-page__title', tabindex: '-1', text: t('nav.about') }),
      section('map'),
      section('distances'),
      section('near'),
      section('themes'),
      section('contents', h('ul', { class: 'cx-list' }, [
        h('li', { text: t(core.names ? 'about.contents.names' : 'about.contents.pseudonyms') }),
        h('li', { text: t(`about.contents.texts.${core.texts}`) }),
        h('li', { text: t('about.contents.never') }),
        h('li', { text: t('about.contents.built', { date: S.date(core.built_at), version: core.map_version || '' }) }),
      ])),
      core.orgs.location.some(Boolean) ? section('outline') : null,
    );
    return null;
  };

  S.pages.missing = function missing(main) {
    main.append(h('h1', { class: 'cx-page__title', tabindex: '-1', text: t('notfound.title') }),
      h('p', { text: t('notfound.text') }), h('p', {}, S.link('/', t('notfound.home'), 'cx-button cx-button--primary')));
    return null;
  };
}());
