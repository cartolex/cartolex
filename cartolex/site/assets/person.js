// SPDX-License-Identifier: MIT
/**
 * The page of a person (`#/person/s3`) and of an organisation (`#/org/o2`): a
 * sheet to read and print beside the atlas (« Show on the atlas »): the themes
 * per level, the keywords, the organisations or the members, who they write
 * with (from `data/links.js`) and, when the site carries them, the texts'
 * titles. « Print this page » prints it without the site's controls.
 */
(function () {
  'use strict';

  const S = window.CxSite;
  const h = S.h;
  const t = S.t;
  /** The partners listed on a page. */
  const PARTNERS = 30;

  function card(title, body, cls) {
    return h('section', { class: `cx-card ${cls || ''}` }, [h('h2', { text: title }), body]);
  }

  /** A theme's share as a bar: its name (a link to the atlas), the share in words and as a bar. */
  function shareRow(node, share) {
    return h('li', { class: 'cx-share' }, [
      h('span', { class: 'cx-share__name' }, S.link(S.atlasPath('theme', node), S.nodeName(node))),
      h('span', { class: 'cx-share__value', text: S.percent(share) }),
      h('span', { class: 'cx-share__bar', 'aria-hidden': 'true',
        style: { '--cx-share': String(Math.max(0.02, share)), '--cx-chip': S.themeColour(node) } }),
    ]);
  }

  /** The themes of each level: *levelsOf(level)* gives `[[node, share]]`. */
  function themesCard(levelsOf) {
    const core = S.ix.core;
    const blocks = [];
    for (let lv = 0; lv < Math.max(1, core.depth); lv += 1) {
      const list = levelsOf(lv).slice(0, 5);
      if (!list.length) continue;
      const names = (core.levels[lv] && core.levels[lv].names) || {};
      blocks.push(h('div', { class: 'cx-themes-level' }, [
        h('h3', { text: names[S.lang] || names[S.lang.slice(0, 2)] || names.en || t('theme.level', { level: lv + 1 }) }),
        h('ul', { class: 'cx-shares' }, list.map(([node, share]) => shareRow(node, share))),
      ]));
    }
    return card(t('page.themes'), blocks.length ? blocks : h('p', { class: 'cx-muted', text: t('page.none') }));
  }

  function keywordsCard(terms) {
    return card(t('page.keywords'), terms.length ? h('ul', { class: 'cx-chips' }, terms.map((term) => h('li', {},
      S.link(S.atlasPath('keyword', term), term, 'cx-chip'))))
      : h('p', { class: 'cx-muted', text: t('page.none') }));
  }

  function head(kind, title, lead, atlas, extra) {
    return h('div', { class: 'cx-page-head' }, [
      h('div', {}, [h('p', { class: 'cx-eyebrow', text: t(kind) }),
        h('h1', { class: 'cx-page__title', tabindex: '-1', text: title }),
        lead ? h('p', { class: 'cx-lead', text: lead }) : null, extra || null]),
      h('p', { class: 'cx-page-head__actions no-print' }, [
        S.link(atlas, t('page.open_map'), 'cx-button cx-button--primary'), ' ',
        h('button', { type: 'button', class: 'cx-button', onclick: () => window.print() }, t('page.print'))]),
    ]);
  }

  /** The list of who writes with whom, filled once `data/links.js` is there. */
  function partnersList(list, render) {
    const body = h('div', {}, h('p', { class: 'cx-muted', 'aria-busy': 'true', text: t('common.loading') }));
    S.need('links', (ok) => {
      if (!ok) {
        body.replaceChildren(S.missingNote());
        return;
      }
      const partners = list();
      body.replaceChildren(partners.length ? h('ol', { class: 'cx-list' }, partners.slice(0, PARTNERS).map(render))
        : h('p', { class: 'cx-muted', text: t('page.none') }));
      if (partners.length > PARTNERS) {
        body.append(h('p', { class: 'cx-muted cx-small', text: S.tn('page.more', partners.length - PARTNERS) }));
      }
    });
    return body;
  }

  function wait(main, parts, render) {
    const note = h('p', { class: 'cx-muted', 'aria-busy': 'true', text: t('common.loading') });
    main.append(note);
    let gone = false;
    Promise.all(parts.map(S.load)).then((oks) => {
      if (gone) return;
      note.remove();
      if (oks.some((ok) => !ok)) main.append(S.missingNote());
      else render();
    });
    return () => { gone = true; };
  }

  const together = (n) => h('span', { class: 'cx-muted', text: S.tn('page.texts_together', n) });

  S.pages.person = function person(main, route) {
    const ix = S.ix;
    const id = route.parts[1];
    if (!ix.byPerson.has(id)) return S.pages.missing(main);
    const i = ix.byPerson.get(id);
    const core = ix.core;
    const top = S.topOfPerson(i);
    main.append(head('kind1.people', S.personName(i), top ? t('person.lead', { theme: S.nodeName(top) }) : '',
      S.atlasPath('person', id)));
    return wait(main, [S.partOf('people', id)], () => {
      const d = S.personPart('people', id) || {};
      const grid = h('div', { class: 'cx-grid' }, [
        themesCard((lv) => S.sharesOf(i, lv)),
        keywordsCard(d.k || []),
        card(t('person.orgs'), core.people.orgs[i].length ? h('ul', { class: 'cx-list' }, core.people.orgs[i]
          .map((o) => h('li', {}, S.link(`/org/${core.orgs.id[o]}`, core.orgs.name[o]))))
          : h('p', { class: 'cx-muted', text: t('page.none') })),
      ]);
      if (core.has && core.has.links) {
        grid.append(card(t('person.coauthors'), [
          partnersList(() => S.partners(S.data.links.people, i), ([j, n]) => h('li', {}, [
            j < core.people.id.length ? S.link(`/person/${core.people.id[j]}`, S.personName(j))
              : S.link(S.atlasPath('projected', core.projected.id[j - core.people.id.length]),
                S.projectedName(j - core.people.id.length)), ' ', together(n)])),
          h('p', { class: 'cx-muted cx-small', text: t('person.coauthors.note') })]));
      }
      main.append(grid);
      if (core.texts !== 'none') {
        const texts = h('div', {});
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
    });
  };

  S.pages.org = function org(main, route) {
    const ix = S.ix;
    const id = route.parts[1];
    if (!ix.byOrg.has(id)) return S.pages.missing(main);
    const i = ix.byOrg.get(id);
    const o = ix.core.orgs;
    const sub = [o.acronym[i], S.orgLevelName(o.level[i])].filter(Boolean).join(' · ');
    const parents = o.parents[i];
    main.append(head('kind1.orgs', o.name[i], sub, S.atlasPath('organisation', id),
      parents.length ? h('p', {}, [t('org.part_of'), ' ', ...parents.map((p, k) => [k ? ', ' : '',
        S.link(`/org/${o.id[p]}`, o.name[p])])]) : null));
    return wait(main, ['orgs'], () => {
      const d = S.data.orgs[id] || {};
      const members = ix.members[i] || [];
      const grid = h('div', { class: 'cx-grid' }, [
        themesCard((lv) => S.orgSharesOf(i, lv)),
        keywordsCard(d.k || []),
        card(S.tn('org.members', members.length), members.length ? h('ul', { class: 'cx-list cx-list--columns' },
          members.map((m) => h('li', {}, S.link(`/person/${ix.core.people.id[m]}`, S.personName(m)))))
          : h('p', { class: 'cx-muted', text: t('page.none') }), 'cx-card--wide'),
      ]);
      if (ix.core.has && ix.core.has.links) {
        grid.append(card(t('org.partners', { level: S.orgLevelName(o.level[i]) }),
          partnersList(() => S.partners(S.data.links.orgs, i),
            ([j, n]) => h('li', {}, [S.link(`/org/${o.id[j]}`, o.name[j]), ' ', together(n)]))));
      }
      main.append(grid);
    });
  };
}());
