// SPDX-License-Identifier: MIT
/**
 * The offline site's data source for the atlas (`docs/dev/atlas.md`, « The data
 * source »): it answers from the site's files what the app answers from its
 * server, in the same shapes.
 *
 * - `bundle()`: the atlas bundle (`cartolex-atlas/3`) made from `data/core.js`'s
 *   columns: people by their site ids (`s1`…), with no name in a site of
 *   pseudonyms; organisations `o1`…; projected people `q1`…
 * - `keywordUsers(term)`: from `data/keywords/<n>.js` (the users kept per keyword,
 *   their share of its use); `at` holds those kept, `at_capped` says when there
 *   are more.
 * - `coauthors(…)`: the shared `ringsOf` over the sparse lists of `data/links.js`
 *   (loaded the first time the network is asked for).
 * - `compare(a, b)`: the cosine of the two vectors (`space`, from the people's
 *   parts and `data/orgs.js`), the top-level themes in common (`themes`) and the
 *   texts written together (`texts.shared`, from the links); no keywords part.
 * - `keywordsOf(kind, ids)`: from the people's parts and `data/orgs.js`.
 * - `land()`: the outline of the land (`assets/world.js`), when the site has it.
 *
 * A method the site's data cannot answer is left out, so the atlas does not offer
 * that part (no texts on the map, no time windows).
 */
(function () {
  'use strict';

  const S = window.CxSite;

  /** Unit vector of the base64 int8 *b64* (`data.py`'s `_vector`), or null. */
  function vector(b64) {
    if (!b64) return null;
    const v = S.ints(b64, Int8Array);
    let n = 0;
    for (let i = 0; i < v.length; i += 1) n += v[i] * v[i];
    n = Math.sqrt(n);
    return n ? Array.from(v, (x) => x / n) : null;
  }

  function dot(a, b) {
    let d = 0;
    for (let i = 0; i < Math.min(a.length, b.length); i += 1) d += a[i] * b[i];
    return d;
  }

  /** The sparse lists of a graph of `data/links.js`, decoded once. */
  function decode(raw) {
    if (!raw) return null;
    if (!raw.decoded) {
      raw.decoded = {
        ptr: S.ints(raw.ptr, Int32Array),
        nbr: S.ints(raw.nbr, Int32Array),
        cnt: S.ints(raw.cnt, Uint16Array),
        texts: S.ints(raw.texts, Int32Array),
        outside: S.ints(raw.outside, Int32Array),
      };
    }
    return raw.decoded;
  }

  /** The partners of position *i* in the decoded lists *g*: `[[position, texts]]`. */
  function partners(g, i) {
    const out = [];
    for (let k = g.ptr[i]; k < g.ptr[i + 1]; k += 1) out.push([g.nbr[k], g.cnt[k]]);
    return out;
  }

  /** The bundle the atlas reads, made once from the core's columns. */
  function makeBundle(core) {
    const nodes = core.nodes;
    const shares = (list) => list.map((flat) => {
      const out = {};
      for (let k = 0; k + 1 < flat.length; k += 2) out[nodes[flat[k]].id] = flat[k + 1] / 1000;
      return out;
    });
    const p = core.people;
    const o = core.orgs;
    const k = core.keywords;
    const q = core.projected;
    const peopleExtra = {};
    const people = p.id.map((id, i) => {
      peopleExtra[id] = { role: '', columns: {}, orgs: p.orgs[i].map((j) => o.id[j]) };
      return { person_id: id, name: p.name[i], unit: '', x: p.x[i], y: p.y[i], shares: shares(p.shares[i]) };
    });
    const levels = core.org_levels.map((lv) => ({ id: lv.id, names: lv.names,
      count: o.level.filter((l) => l === lv.id).length }));
    return {
      format: 'cartolex-atlas/3',
      available: true,
      map_version: core.map_version,
      depth: core.depth,
      levels: core.levels,
      nodes,
      people,
      keywords: k.term.map((term, i) => ({ term, x: k.x[i], y: k.y[i], node: k.node[i] >= 0 ? nodes[k.node[i]].id : null,
        level: k.level[i], counts_to: k.counts_to[i], weight: k.weight[i], share: k.share[i], category: k.category[i] })),
      units: [],
      overlays: q.id.map((id, i) => ({ set: '', person_id: id, name: q.name[i], x: q.x[i], y: q.y[i],
        shares: shares(q.shares[i]) })),
      organisations: o.id.map((id, i) => ({ id, name: o.name[i], acronym: o.acronym[i], level: o.level[i],
        parents: o.parents[i].map((j) => o.id[j]), x: o.x[i], y: o.y[i], members: o.members[i],
        members_ever: o.members_ever[i], location: o.location[i] ? { lon: o.location[i][0], lat: o.location[i][1] } : null })),
      organisation_levels: levels,
      people_extra: peopleExtra,
      columns: [],
      years: core.years || {},
      windows: 0,
      window_years: null,
      bases: [],
      bounds: core.bounds,
    };
  }

  /** The atlas's data source over the site's files. */
  S.atlasSource = function atlasSource() {
    const ix = S.ix;
    const core = ix.core;
    const has = core.has || {};
    let bundle = null;
    const source = {
      bundle() {
        if (!bundle) bundle = makeBundle(core);
        return Promise.resolve(bundle);
      },
      keywordsOf(kind, ids) {
        const parts = kind === 'person' ? [...new Set(ids.map((id) => S.partOf('people', id)))] : ['orgs'];
        return Promise.all(parts.map(S.load)).then(() => {
          const out = {};
          ids.forEach((id) => {
            const d = kind === 'person' ? S.personPart('people', id) : (S.data.orgs || {})[id];
            out[id] = (d && d.k) || [];
          });
          return out;
        });
      },
      compare(a, b) {
        return Promise.all([a, b].map(side)).then(([x, y]) => compareSides(x, y));
      },
    };
    if (has.users) {
      source.keywordUsers = function keywordUsers(term, options) {
        const limit = (options && options.limit) || 20;
        const at = ix.byTerm.get(term);
        if (at === undefined) return Promise.resolve({ known: false, count: 0, items: [], at: [] });
        const part = S.partOf('keywords', at);
        return S.load(part).then((ok) => {
          if (!ok) return { error: { code: 'site_part_missing', message: S.t('missing.title') } };
          const flat = (S.data[part] || {})[String(at)];
          if (!flat) return { known: false, count: 0, items: [], at: [] };
          const kept = [];
          for (let k = 1; k + 1 < flat.length; k += 2) kept.push([flat[k], flat[k + 1] / 1000]);
          return {
            known: true,
            term,
            count: flat[0],
            items: kept.slice(0, limit).map(([i, share]) => ({ id: core.people.id[i], name: core.people.name[i], share })),
            at: kept.map(([i]) => i),
            at_capped: flat[0] > kept.length,
          };
        });
      };
    }
    if (has.links && window.CartolexAtlas && window.CartolexAtlas.ringsOf) {
      source.coauthors = function coauthors(query) {
        return S.load('links').then((ok) => {
          if (!ok) return { error: { code: 'site_part_missing', message: S.t('missing.title') } };
          return ringsAround(query);
        });
      };
    }
    if (S.data.world) source.land = () => Promise.resolve(S.data.world);

    /** One side of a comparison, with its parts loaded: `{kind, id, i, vector, themes, people}`. */
    function side(ref) {
      if (ref.kind === 'person' && ix.byPerson.has(ref.id)) {
        const i = ix.byPerson.get(ref.id);
        return S.load(S.partOf('people', ref.id)).then(() => {
          const d = S.personPart('people', ref.id) || {};
          return { kind: 'person', id: ref.id, i, vector: vector(d.v), themes: new Map(S.sharesOf(i, 0)) };
        });
      }
      if (ref.kind === 'organisation' && ix.byOrg.has(ref.id)) {
        const i = ix.byOrg.get(ref.id);
        return S.load('orgs').then(() => {
          const d = (S.data.orgs || {})[ref.id] || {};
          return { kind: 'organisation', id: ref.id, i, vector: vector(d.v), themes: new Map(S.orgSharesOf(i, 0)) };
        });
      }
      return Promise.resolve(null);
    }

    function compareSides(x, y) {
      if (!x || !y) return { error: { code: 'not_found', message: S.t('notfound.title') } };
      const out = {};
      if (x.vector && y.vector) out.space = Math.round(dot(x.vector, y.vector) * 1e4) / 1e4;
      const nodes = [...x.themes.keys()].filter((n) => y.themes.has(n))
        .sort((m, n) => Math.min(y.themes.get(n), x.themes.get(n)) - Math.min(y.themes.get(m), x.themes.get(m)));
      out.themes = {
        overlap: Math.round(nodes.reduce((s, n) => s + Math.min(x.themes.get(n), y.themes.get(n)), 0) * 1e4) / 1e4,
        shared: nodes.map((n) => ({ node: n, a: x.themes.get(n), b: y.themes.get(n) })),
      };
      if (has.links && x.kind === y.kind) {
        return S.load('links').then(() => {
          const g = graphOf(x.kind, x.kind === 'organisation' ? core.orgs.level[x.i] : null);
          if (g) {
            const found = partners(g.lists, g.position(x.i)).find(([j]) => g.entity(j) === y.i);
            out.texts = { shared: found ? found[1] : 0, items: [] };
          }
          return out;
        });
      }
      return out;
    }

    /** The graph of people, or of the organisations of *level*: its decoded lists and the
     * way between its positions and the core's indexes. */
    function graphOf(kind, level) {
      const links = S.data.links;
      if (!links) return null;
      if (kind === 'person') {
        const lists = decode(links.people);
        return lists && { lists, raw: links.people, position: (i) => i, entity: (j) => j };
      }
      const raw = (links.orgs || {})[level];
      if (!raw) return null;
      const where = new Map(raw.ids.map((o, k) => [o, k]));
      return { lists: decode(raw), raw, position: (i) => where.get(i), entity: (j) => j };
    }

    /** The rings around a person or an organisation (`coauthors`'s answer). */
    function ringsAround(query) {
      const person = query.kind === 'person';
      const i = person ? ix.byPerson.get(query.id) : ix.byOrg.get(query.id);
      if (i === undefined) return { error: { code: 'not_found', message: S.t('notfound.title') } };
      const level = person ? null : core.orgs.level[i];
      const g = graphOf(person ? 'person' : 'organisation', level);
      if (!g || g.position(i) === undefined) return { id: query.id, circle: query.circle || 1, count: 0, items: [], lines: [] };
      const ids = person ? core.people.id : core.orgs.id;
      const index = person ? ix.byPerson : ix.byOrg;
      const graph = {
        neighbours: (id) => {
          const at = g.position(index.get(id));
          return at === undefined ? [] : partners(g.lists, at).map(([j, n]) => [ids[g.entity(j)], n]);
        },
        describe: (list) => list.map((id) => {
          const j = index.get(id);
          if (person) return { id, name: core.people.name[j], role: 'mapped', mapped: true, place: 'map' };
          return { id, name: core.orgs.name[j], acronym: core.orgs.acronym[j],
            place: core.orgs.x[j] === null ? null : 'map' };
        }),
        placed: (id) => (person || core.orgs.x[index.get(id)] !== null ? 'map' : null),
      };
      const at = g.position(i);
      const answer = window.CartolexAtlas.ringsOf(graph, query.id, query.circle || 1, query.pages);
      return Object.assign({ texts: g.lists.texts[at], outside: g.lists.outside[at],
        max_authors: g.raw.max_authors }, person ? {} : { level }, answer);
    }

    return source;
  };
}());
