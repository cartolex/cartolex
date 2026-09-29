// SPDX-License-Identifier: MIT
/**
 * The themes (`#/themes`, `#/themes/<id>`): a treemap of a theme's
 * sub-themes (the top level at first), each sized by its share of the
 * vocabulary's use and coloured by its family; a click goes down one level.
 * The theme's page shows its path, its sub-themes as a list too (the
 * treemap's alternative), a small map of its keywords and people, its
 * keywords, its people and its organisations.
 */
(function () {
  'use strict';

  const S = window.CxSite;
  const h = S.h;
  const t = S.t;

  function weightOf(id) {
    const n = S.ix.nodes.get(id);
    return (n && (n.weight || n.share || n.keywords)) || 0;
  }

  /** The treemap of *kids* in *box*, laid out again when the box changes size. */
  function treemap(box, kids) {
    const draw = () => {
      const width = box.clientWidth;
      const height = box.clientHeight;
      if (!width || !height) return;
      const cells = window.CartolexMap.layoutTree({
        root: '__root__',
        childrenOf: (id) => (id === '__root__' ? kids : []),
        weightOf,
        width,
        height,
      });
      box.replaceChildren(...cells.map((c) => h('a', {
        href: `#/themes/${c.id}`,
        class: `cx-treemap__cell${c.w < 60 || c.h < 28 ? ' is-small' : ''}`,
        title: S.nodeName(c.id),
        style: { '--cx-x': `${c.x}px`, '--cx-y': `${c.y}px`, '--cx-w': `${c.w}px`, '--cx-h': `${c.h}px`,
          '--cx-cell': `var(--cx-hue-${S.colourOf(c.id) + 1})` },
      }, [h('span', { class: 'cx-treemap__name', text: S.nodeName(c.id) }),
        c.h >= 40 ? h('span', { class: 'cx-treemap__share', text: S.percent(S.ix.nodes.get(c.id).share || 0) }) : null])));
    };
    const observer = new ResizeObserver(draw);
    observer.observe(box);
    draw();
    return () => observer.disconnect();
  }

  function pathOf(id) {
    const out = [];
    let at = id;
    while (at && S.ix.nodes.has(at)) {
      out.unshift(at);
      at = S.ix.nodes.get(at).parent;
    }
    return out;
  }

  S.pages.themes = function themes(main, route) {
    const ix = S.ix;
    const id = route.parts[1] || null;
    if (id && !ix.nodes.has(id)) return S.pages.missing(main);
    const kids = ix.children.get(id) || [];
    const trail = h('nav', { class: 'cx-trail', 'aria-label': t('themes.trail') }, h('ol', {}, [
      h('li', {}, id ? S.link('/themes', t('themes.all')) : h('span', { 'aria-current': 'page', text: t('themes.all') })),
      ...pathOf(id).map((n) => h('li', {}, n === id ? h('span', { 'aria-current': 'page', text: S.nodeName(n) })
        : S.link(`/themes/${n}`, S.nodeName(n)))),
    ]));
    const node = id ? ix.nodes.get(id) : null;
    main.append(trail, h('h1', { class: 'cx-page__title', tabindex: '-1', text: id ? S.nodeName(id) : t('nav.themes') }),
      h('p', { class: 'cx-lead', text: node ? t('themes.lead.node', { share: S.percent(node.share || 0),
        keywords: node.keywords || 0 }) : t('themes.lead') }));
    const teardowns = [];
    if (kids.length) {
      const box = h('div', { class: 'cx-treemap', role: 'group', 'aria-label': t('themes.treemap') });
      main.append(box, h('details', { class: 'cx-card cx-themes-list' }, [
        h('summary', { text: S.tn('themes.children', kids.length) }),
        h('ul', { class: 'cx-shares' }, kids.map((k) => h('li', { class: 'cx-share' }, [
          h('span', { class: 'cx-share__name' }, S.link(`/themes/${k}`, S.nodeName(k))),
          h('span', { class: 'cx-share__value', text: S.percent(ix.nodes.get(k).share || 0) }),
        ]))),
      ]));
      teardowns.push(treemap(box, kids));
    }
    if (id) {
      const wait = h('p', { class: 'cx-muted', 'aria-busy': 'true', text: t('common.loading') });
      main.append(wait);
      let gone = false;
      S.need('details', (ok) => {
        if (gone) return;
        wait.remove();
        if (!ok) {
          main.append(S.missingNote());
          return;
        }
        const d = S.data.details.themes[id];
        const mapBox = h('div', { class: 'cx-mini' });
        const people = d.people.filter(([p]) => ix.byPerson.has(p));
        const orgs = d.orgs.filter(([o]) => ix.byOrg.has(o));
        main.append(h('div', { class: 'cx-grid' }, [
          h('section', { class: 'cx-card cx-card--wide' }, [h('h2', { text: t('themes.on_map') }), mapBox,
            h('p', { class: 'cx-muted cx-small' }, [t('themes.on_map.note'), ' ',
              S.link(`/map?sel=${encodeURIComponent(`theme:${id}`)}`, t('page.open_map'))])]),
          h('section', { class: 'cx-card' }, [h('h2', { text: t('page.keywords') }), d.keywords.length
            ? h('ul', { class: 'cx-chips' }, d.keywords.map((term) => h('li', {},
              S.link(`/map?sel=${encodeURIComponent(`keyword:${term}`)}`, term, 'cx-chip'))))
            : h('p', { class: 'cx-muted', text: t('page.none') })]),
          h('section', { class: 'cx-card' }, [h('h2', { text: S.tn('themes.people', people.length) }), people.length
            ? h('ul', { class: 'cx-shares' }, people.slice(0, 40).map(([p, share]) => h('li', { class: 'cx-share' }, [
              h('span', { class: 'cx-share__name' }, S.link(`/person/${p}`, S.personName(ix.byPerson.get(p)))),
              h('span', { class: 'cx-share__value', text: S.percent(share) })])))
            : h('p', { class: 'cx-muted', text: t('page.none') }),
          h('p', { class: 'cx-muted cx-small', text: t('themes.people.note') })]),
          orgs.length ? h('section', { class: 'cx-card' }, [h('h2', { text: S.tn('themes.orgs', orgs.length) }),
            h('ul', { class: 'cx-shares' }, orgs.slice(0, 40).map(([o, share]) => h('li', { class: 'cx-share' }, [
              h('span', { class: 'cx-share__name' }, S.link(`/org/${o}`, ix.core.orgs.name[ix.byOrg.get(o)])),
              h('span', { class: 'cx-share__value', text: S.percent(share) })])))]) : null,
        ]));
        let built = S.mapScene({ show: new Set(['people', 'keywords']), sel: { kind: 'theme', id }, org: '' });
        const frame = S.mapFrame(mapBox, {
          label: t('themes.on_map'),
          cls: 'cx-map--mini',
          scene: () => built.scene,
          onPick: (hit) => {
            const picked = built.pick(hit);
            const page = S.pageOf(picked);
            if (page) window.location.hash = `#${page}`;
          },
          hover: (hit) => S.describe(built.pick(hit)),
        });
        teardowns.push(() => {
          built = null;
          frame.destroy();
        });
      });
      teardowns.push(() => { gone = true; });
    }
    return () => teardowns.forEach((fn) => fn());
  };
}());
