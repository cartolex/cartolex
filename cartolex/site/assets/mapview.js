// SPDX-License-Identifier: MIT
/**
 * The site's map frame and the map page.
 *
 * `S.mapFrame` is the app's MapFrame without Preact: a focusable box holding
 * the canvas, driven by the shared controller (`CartolexMap`, the app's map
 * modules as one classic script): arrows pan, + and − zoom, 0 fits; a click
 * picks, hovering shows a card; buttons zoom and fit; the frame takes the
 * size of its container.
 *
 * The map page (`#/map?sel=person:s3&show=people,keywords&view=world`) adds
 * the permanent legend (one symbol per kind, one colour per top-level
 * theme), the kinds shown, the organisations' level, the world view when
 * organisations have an address, the panel of the selection with a link to
 * its page, and the plain caveat about distances.
 */
(function () {
  'use strict';

  const S = window.CxSite;
  const h = S.h;
  const t = S.t;

  /**
   * A map in *parent*: `{label, scene: () => scene, onPick(hit), hover(hit) → {title, detail}}`.
   * Answers `{redraw(refit), centreOn(x, y, zoom), destroy()}`.
   */
  S.mapFrame = function mapFrame(parent, opts) {
    const canvas = h('canvas', { class: 'cx-map__canvas', 'aria-hidden': 'true' });
    const card = h('div', { class: 'cx-map__card', 'aria-hidden': 'true', hidden: true });
    const box = h('div', { class: 'cx-map__box', tabindex: '0', role: 'group', 'aria-label': opts.label,
      'aria-describedby': 'cx-map-keys' }, [canvas, card]);
    let control = null;
    const button = (label, glyph, fn) => h('button', { type: 'button', class: 'cx-icon-button', 'aria-label': label,
      title: label, onclick: () => control && fn() }, glyph);
    const tools = h('div', { class: 'cx-map__tools' }, [
      button(t('map.zoom_in'), '+', () => control.zoomBy(1.4)),
      button(t('map.zoom_out'), '−', () => control.zoomBy(1 / 1.4)),
      button(t('map.fit'), '⤢', () => control.fit()),
    ]);
    const keys = h('p', { id: 'cx-map-keys', class: 'cx-visually-hidden', text: t('map.keys') });
    const frame = h('div', { class: `cx-map ${opts.cls || ''}` }, [box, tools, keys]);
    parent.appendChild(frame);
    control = window.CartolexMap.createMapController({
      box,
      canvas,
      scene: opts.scene,
      onPick: (hit) => opts.onPick && opts.onPick(hit),
      onHover: (hit, point) => {
        const content = hit && opts.hover ? opts.hover(hit) : null;
        if (!content || !point) {
          card.hidden = true;
          return;
        }
        card.replaceChildren(h('strong', { class: 'cx-map__card-title', text: content.title }),
          content.detail ? h('span', { class: 'cx-map__card-detail', text: content.detail }) : null);
        card.hidden = false;
        card.classList.toggle('is-left', point.x > box.clientWidth - 260);
        card.classList.toggle('is-below', point.y < 120);
        card.style.setProperty('--cx-card-x', `${point.x}px`);
        card.style.setProperty('--cx-card-y', `${point.y}px`);
      },
    });
    box.dataset.renderer = control.renderer;
    box.cxMap = control;
    return {
      el: frame,
      redraw: (refit) => control.redraw(refit),
      centreOn: (x, y, zoom) => control.centreOn(x, y, zoom),
      destroy: () => {
        control.destroy();
        delete box.cxMap;
      },
    };
  };

  /** The words of a hovered or selected item: `{title, detail}`. */
  S.describe = function describe(sel) {
    const ix = S.ix;
    const core = ix.core;
    if (!sel) return null;
    if (sel.kind === 'person' && ix.byPerson.has(sel.id)) {
      const i = ix.byPerson.get(sel.id);
      return { title: S.personName(i), detail: core.people.top[i] ? S.nodeName(core.people.top[i]) : '' };
    }
    if (sel.kind === 'org' && ix.byOrg.has(sel.id)) {
      const i = ix.byOrg.get(sel.id);
      return { title: core.orgs.name[i], detail: S.tn('org.members', core.orgs.members[i]) };
    }
    if (sel.kind === 'keyword' && ix.byTerm.has(sel.id)) {
      const i = ix.byTerm.get(sel.id);
      return { title: sel.id, detail: core.keywords.node[i] ? S.nodeName(core.keywords.node[i]) : '' };
    }
    if (sel.kind === 'projected' && ix.byProjected.has(sel.id)) {
      return { title: S.projectedName(ix.byProjected.get(sel.id)), detail: t('kind1.projected') };
    }
    if (sel.kind === 'theme' && ix.nodes.has(sel.id)) {
      return { title: S.nodeName(sel.id), detail: t('theme.level', { level: ix.nodes.get(sel.id).level }) };
    }
    return null;
  };

  /** The page of a selection (`/person/s3`), or null for a keyword. */
  S.pageOf = function pageOf(sel) {
    if (!sel) return null;
    if (sel.kind === 'person') return `/person/${sel.id}`;
    if (sel.kind === 'org') return `/org/${sel.id}`;
    if (sel.kind === 'theme') return `/themes/${sel.id}`;
    return null;
  };

  function readState(query) {
    const sel = query.get('sel') || '';
    const cut = sel.indexOf(':');
    const show = query.has('show') ? query.get('show').split(',').filter((k) => S.KINDS.includes(k)) : ['people', 'keywords'];
    return {
      sel: cut > 0 ? { kind: sel.slice(0, cut), id: sel.slice(cut + 1) } : null,
      show: new Set(show),
      view: query.get('view') === 'world' ? 'world' : 'map',
      org: query.get('org') || '',
    };
  }

  function writeState(state) {
    const q = new URLSearchParams();
    if (state.sel) q.set('sel', `${state.sel.kind}:${state.sel.id}`);
    const show = [...state.show].join(',');
    if (show !== 'people,keywords') q.set('show', show);
    if (state.view !== 'map') q.set('view', state.view);
    if (state.org) q.set('org', state.org);
    const next = `#/map${q.toString() ? `?${q}` : ''}`;
    if (window.location.hash !== next) window.history.replaceState(null, '', next);
  }

  /** The panel of the selection. */
  function panel(state, onSelect) {
    const words = S.describe(state.sel);
    if (!words) {
      return h('div', { class: 'cx-map-panel' }, [h('p', { class: 'cx-muted', text: t('map.panel.empty') })]);
    }
    const ix = S.ix;
    const details = S.data.details;
    const body = [h('h2', { class: 'cx-map-panel__title', text: words.title }),
      words.detail ? h('p', { class: 'cx-muted', text: words.detail }) : null];
    const page = S.pageOf(state.sel);
    if (page) body.push(h('p', {}, S.link(page, t('map.panel.open'), 'cx-button cx-button--primary')));
    if (state.sel.kind === 'person' && S.personPart('people', state.sel.id)) {
      const near = S.personPart('people', state.sel.id).near;
      body.push(h('h3', { class: 'cx-map-panel__head', text: t('person.near') }),
        h('ol', { class: 'cx-list' }, near.map(([id]) => h('li', {}, h('button', { type: 'button', class: 'cx-link-button',
          onclick: () => onSelect({ kind: 'person', id }) }, S.personName(ix.byPerson.get(id)))))),
        h('p', { class: 'cx-muted cx-small', text: t('map.near.note') }));
    }
    if (state.sel.kind === 'keyword' && details) {
      const users = details.used_by[state.sel.id] || [];
      body.push(h('h3', { class: 'cx-map-panel__head', text: S.tn('keyword.used_by', users.length) }),
        h('ul', { class: 'cx-list' }, users.slice(0, 20).map((id) => h('li', {},
          S.link(`/person/${id}`, S.personName(ix.byPerson.get(id)))))));
    }
    return h('div', { class: 'cx-map-panel' }, body);
  }

  function legend(state, counts, onSelect) {
    const kinds = S.KINDS.filter((k) => counts[k] !== undefined);
    return h('details', { class: 'cx-legend', open: true }, [
      h('summary', { class: 'cx-legend__summary', text: t('map.legend') }),
      h('ul', { class: 'cx-legend__kinds', 'aria-label': t('map.legend.kinds') }, kinds.map((k) => h('li', {}, [
        S.symbol(S.SHAPE[k]), ' ', t(`kind.${k}`), ' ', h('span', { class: 'cx-muted', text: `(${S.fmt(counts[k])})` }),
      ]))),
      h('p', { class: 'cx-legend__lead', text: t('map.legend.colour') }),
      h('ul', { class: 'cx-legend__themes', 'aria-label': t('map.legend.colour') }, S.ix.tops.slice(0, 12).map((id) => {
        const selected = Boolean(state.sel && state.sel.kind === 'theme' && state.sel.id === id);
        return h('li', {}, h('button', { type: 'button', class: `cx-legend__theme${selected ? ' is-selected' : ''}`,
          'aria-pressed': String(selected), onclick: () => onSelect(selected ? null : { kind: 'theme', id }) }, [
          h('span', { class: 'cx-chip-dot', 'aria-hidden': 'true', style: { '--cx-chip': `var(--cx-hue-${S.colourOf(id) + 1})` } }),
          h('span', { text: S.nodeName(id) }),
        ]));
      })),
    ]);
  }

  function controls(state, update) {
    const core = S.ix.core;
    const kinds = S.KINDS.filter((k) => (k === 'orgs' ? core.orgs.id.length : k === 'projected' ? core.projected.id.length : true));
    const world = core.orgs.location.some(Boolean);
    const levels = (core.org_levels || []).map((l) => l.id);
    const row = [];
    if (world) {
      row.push(h('div', { class: 'cx-segmented', role: 'group', 'aria-label': t('map.view') }, ['map', 'world'].map((v) => h('button', {
        type: 'button', class: 'cx-segmented__item', 'aria-pressed': String(state.view === v),
        onclick: () => update({ view: v }) }, t(`map.view.${v}`)))));
    }
    if (state.view === 'map') {
      row.push(h('fieldset', { class: 'cx-kinds' }, [h('legend', { class: 'cx-visually-hidden', text: t('map.show') }),
        ...kinds.map((k) => h('label', { class: 'cx-check' }, [
          h('input', { type: 'checkbox', checked: state.show.has(k), onchange: (e) => {
            const show = new Set(state.show);
            if (e.target.checked) show.add(k);
            else show.delete(k);
            update({ show });
          } }), S.symbol(S.SHAPE[k]), ' ', t(`kind.${k}`)]))]));
    }
    if (levels.length > 1 && (state.view === 'world' || state.show.has('orgs'))) {
      const select = h('select', { class: 'cx-select', onchange: (e) => update({ org: e.target.value }) },
        levels.map((l) => h('option', { value: l, selected: l === state.org }, S.orgLevelName(l))));
      row.push(h('label', { class: 'cx-field-inline' }, [h('span', { text: t('map.org_level') }), select]));
    }
    return h('div', { class: 'cx-map-controls' }, row);
  }

  /** The map page. */
  S.pages.map = function mapPage(main, route) {
    let state = readState(route.query);
    if (!state.org) state.org = S.defaultOrgLevel(state.view === 'world');
    let built = null;
    let frame = null;
    const head = h('div', { class: 'cx-page-head' }, [h('h1', { class: 'cx-page__title', tabindex: '-1', text: t('nav.map') }),
      h('p', { class: 'cx-lead', text: t('map.lead') })]);
    const bar = h('div', {});
    const stage = h('div', { class: 'cx-map-stage' });
    const side = h('aside', { class: 'cx-map-side', 'aria-label': t('map.panel') });
    const caveat = h('p', { class: 'cx-note' }, [h('strong', { text: t('map.caveat.title') }), ' ', t('map.caveat'), ' ',
      S.link('/about', t('map.caveat.more'))]);
    main.append(head, bar, h('div', { class: 'cx-map-layout' }, [stage, side]), caveat);

    const compute = () => {
      built = state.view === 'world' ? S.worldScene(state) : S.mapScene(state);
      return built;
    };
    const select = (sel) => update({ sel });
    const paint = (refit) => {
      compute();
      bar.replaceChildren(controls(state, update));
      side.replaceChildren(legend(state, built.counts, select), panel(state, select));
      if (frame) frame.redraw(refit);
    };
    /** Load what the selection shows: the details, and a person's own part. */
    function needed() {
      if (!state.sel) return;
      if (!S.data.details) S.need('details', () => paint(false));
      if (state.sel.kind === 'person' && !S.data[S.partOf('people', state.sel.id)]) {
        S.need(S.partOf('people', state.sel.id), () => paint(false));
      }
    }
    function update(patch) {
      const viewChanged = patch.view !== undefined && patch.view !== state.view;
      state = Object.assign({}, state, patch);
      if (viewChanged && !patch.org) state.org = S.defaultOrgLevel(state.view === 'world');
      writeState(state);
      needed();
      paint(viewChanged);
    }
    compute();
    frame = S.mapFrame(stage, {
      label: t('map.label'),
      scene: () => built.scene,
      onPick: (hit) => { const sel = built.pick(hit); if (sel || state.sel) select(sel); },
      hover: (hit) => S.describe(built.pick(hit)),
    });
    needed();
    paint(false);
    return () => frame.destroy();
  };
}());
