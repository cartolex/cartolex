// SPDX-License-Identifier: MIT
/**
 * The page of a person (`#/person/s3`) and of an organisation (`#/org/o2`):
 * the position on a small map (lines to who they write with), the themes per
 * level, the keywords, the organisations or the members, the co-authors (the
 * organisations it writes with) and, when the site carries them, the texts'
 * titles. Each page can be printed (« Print this page »).
 */
(function () {
  'use strict';

  const S = window.CxSite;
  const h = S.h;
  const t = S.t;

  function card(title, body, cls) {
    return h('section', { class: `cx-card ${cls || ''}` }, [h('h2', { text: title }), body]);
  }

  /** A theme's share as a bar: its name (a link), the share in words and as a bar. */
  function shareRow(node, share) {
    return h('li', { class: 'cx-share' }, [
      h('span', { class: 'cx-share__name' }, S.link(`/themes/${node}`, S.nodeName(node))),
      h('span', { class: 'cx-share__value', text: S.percent(share) }),
      h('span', { class: 'cx-share__bar', 'aria-hidden': 'true',
        style: { '--cx-share': String(Math.max(0.02, share)), '--cx-chip': `var(--cx-hue-${S.colourOf(node) + 1})` } }),
    ]);
  }

  function themesCard(levels) {
    const core = S.ix.core;
    const blocks = levels.map((list, lv) => (list.length ? h('div', { class: 'cx-themes-level' }, [
      h('h3', { text: (core.levels[lv] && (core.levels[lv].names[S.lang] || core.levels[lv].names[S.lang.slice(0, 2)]
        || core.levels[lv].names.en)) || t('theme.level', { level: lv + 1 }) }),
      h('ul', { class: 'cx-shares' }, list.map(([node, share]) => shareRow(node, share))),
    ]) : null));
    return card(t('page.themes'), blocks.some(Boolean) ? blocks : h('p', { class: 'cx-muted', text: t('page.none') }));
  }

  function keywordsCard(terms) {
    return card(t('page.keywords'), terms.length ? h('ul', { class: 'cx-chips' }, terms.map((term) => h('li', {},
      S.link(`/map?sel=${encodeURIComponent(`keyword:${term}`)}`, term, 'cx-chip'))))
      : h('p', { class: 'cx-muted', text: t('page.none') }));
  }

  function printButton() {
    return h('button', { type: 'button', class: 'cx-button no-print', onclick: () => window.print() }, t('page.print'));
  }

  /** A small map centred on (x, y), showing *sel* (organisations of level *org*); a click opens
   * the page of what it hits. */
  function miniMap(parent, sel, at, show, org) {
    let built = S.mapScene({ show: new Set(show), sel, org: org || S.defaultOrgLevel(false) });
    const frame = S.mapFrame(parent, {
      label: t('page.position'),
      cls: 'cx-map--mini',
      scene: () => built.scene,
      onPick: (hit) => {
        const picked = built.pick(hit);
        const page = S.pageOf(picked);
        if (page) window.location.hash = `#${page}`;
        else if (picked) window.location.hash = `#/map?sel=${encodeURIComponent(`${picked.kind}:${picked.id}`)}`;
      },
      hover: (hit) => S.describe(built.pick(hit)),
    });
    if (at && at[0] !== null) frame.centreOn(at[0], at[1], 2.2);
    return () => {
      built = null;
      frame.destroy();
    };
  }

  function withDetails(main, render, also) {
    const wait = h('p', { class: 'cx-muted', 'aria-busy': 'true', text: t('common.loading') });
    main.append(wait);
    let teardown = null;
    let gone = false;
    const parts = ['details'].concat(also || []);
    let left = parts.length;
    let missing = false;
    parts.forEach((part) => S.need(part, (ok) => {
      missing = missing || !ok;
      left -= 1;
      if (gone || left) return;
      wait.remove();
      if (missing) main.append(S.missingNote());
      else teardown = render();
    }));
    return () => {
      gone = true;
      if (teardown) teardown();
    };
  }

  S.pages.person = function person(main, route) {
    const ix = S.ix;
    const id = route.parts[1];
    if (!ix.byPerson.has(id)) return S.pages.missing(main);
    const i = ix.byPerson.get(id);
    const core = ix.core;
    const name = S.personName(i);
    main.append(h('div', { class: 'cx-page-head' }, [
      h('div', {}, [h('p', { class: 'cx-eyebrow', text: t('kind1.people') }),
        h('h1', { class: 'cx-page__title', tabindex: '-1', text: name }),
        core.people.top[i] ? h('p', { class: 'cx-lead', text: t('person.lead', { theme: S.nodeName(core.people.top[i]) }) }) : null]),
      printButton()]));
    return withDetails(main, () => {
      const d = S.personPart('people', id);
      const mapBox = h('div', { class: 'cx-mini' });
      const co = S.partners('people', i) || [];
      const hidden = ((S.data.links || {}).people || { hidden: [] }).hidden[i] || 0;
      const grid = h('div', { class: 'cx-grid' }, [
        card(t('page.position'), [mapBox, h('p', { class: 'cx-muted cx-small' }, [t('person.position.note'), ' ',
          S.link(`/map?sel=${encodeURIComponent(`person:${id}`)}`, t('page.open_map'))])], 'cx-card--wide'),
        themesCard(d.themes),
        keywordsCard(d.keywords),
        card(S.coTitle('people', co.length), [S.partnerList('people', co, null),
          hidden ? h('p', { class: 'cx-muted cx-small', text: S.tn('coauthors.hidden', hidden) }) : null,
          h('p', { class: 'cx-muted cx-small' }, [t('map.coauthors.note'), ' ', S.link('/about', t('map.caveat.more'))])]),
        card(t('person.orgs'), d.orgs.length ? h('ul', { class: 'cx-list' }, d.orgs.filter((o) => ix.byOrg.has(o))
          .map((o) => h('li', {}, S.link(`/org/${o}`, core.orgs.name[ix.byOrg.get(o)]))))
          : h('p', { class: 'cx-muted', text: t('page.none') })),
      ]);
      main.append(grid);
      const texts = h('div', {});
      if (core.texts !== 'none') {
        grid.append(h('section', { class: 'cx-card cx-card--wide' }, [h('h2', { text: t('person.texts') }), texts]));
        S.need(S.partOf('texts', id), (ok) => {
          if (!ok) {
            texts.append(S.missingNote());
            return;
          }
          const list = S.personPart('texts', id) || [];
          texts.append(list.length ? h('ul', { class: 'cx-texts' }, list.map((e) => h('li', {}, [
            h('span', { class: 'cx-texts__title', text: e.title }), e.year ? h('span', { class: 'cx-muted', text: ` (${e.year})` }) : null,
            e.abstract ? h('details', { class: 'cx-texts__abstract' }, [h('summary', { text: t('person.abstract') }),
              h('p', { text: e.abstract })]) : null,
          ]))) : h('p', { class: 'cx-muted', text: t('page.none') }));
        });
      }
      return miniMap(mapBox, { kind: 'person', id }, [core.people.x[i], core.people.y[i]], ['people', 'keywords']);
    }, [S.partOf('people', id), 'links']);
  };

  S.pages.org = function org(main, route) {
    const ix = S.ix;
    const id = route.parts[1];
    if (!ix.byOrg.has(id)) return S.pages.missing(main);
    const i = ix.byOrg.get(id);
    const o = ix.core.orgs;
    const sub = [o.acronym[i], S.orgLevelName(o.level[i])].filter(Boolean).join(' · ');
    const parents = o.parents[i].filter((p) => ix.byOrg.has(p));
    main.append(h('div', { class: 'cx-page-head' }, [
      h('div', {}, [h('p', { class: 'cx-eyebrow', text: t('kind1.orgs') }),
        h('h1', { class: 'cx-page__title', tabindex: '-1', text: o.name[i] }),
        h('p', { class: 'cx-lead', text: sub }),
        parents.length ? h('p', {}, [t('org.part_of'), ' ', ...parents.map((p, k) => [k ? ', ' : '',
          S.link(`/org/${p}`, o.name[ix.byOrg.get(p)])])]) : null]),
      printButton()]));
    return withDetails(main, () => {
      const d = S.data.details.orgs[id];
      const mapBox = h('div', { class: 'cx-mini' });
      const members = d.members.filter((m) => ix.byPerson.has(m));
      const co = S.partners('orgs', i) || [];
      main.append(h('div', { class: 'cx-grid' }, [
        card(t('page.position'), [mapBox, h('p', { class: 'cx-muted cx-small', text: t('org.position.note') })], 'cx-card--wide'),
        themesCard(d.themes),
        keywordsCard(d.keywords),
        card(S.tn('org.members', members.length), members.length ? h('ul', { class: 'cx-list cx-list--columns' },
          members.map((m) => h('li', {}, S.link(`/person/${m}`, S.personName(ix.byPerson.get(m))))))
          : h('p', { class: 'cx-muted', text: t('page.none') }), 'cx-card--wide'),
        card(S.coTitle('orgs', co.length), S.partnerList('orgs', co, null)),
      ]));
      const at = o.x[i] === null ? null : [o.x[i], o.y[i]];
      return miniMap(mapBox, { kind: 'org', id }, at, ['people', 'orgs'], o.level[i]);
    }, ['links']);
  };
}());
