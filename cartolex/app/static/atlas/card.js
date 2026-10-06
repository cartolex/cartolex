// SPDX-License-Identifier: MIT
/**
 * The card of what is in focus, made of links: the field (its themes and its organisations
 * at the chosen level), a theme (its keywords, the people and organisations most in it, the
 * themes next to it on the map), a keyword (the people who use it), an organisation (its
 * themes, its members, the organisations its people write with), a person or a
 * collaborator who is not mapped (their themes, keywords and co-authors, ring by ring), a
 * text; and a comparison (`compare.js`). Each line puts its subject in focus; the host's
 * links open it on another screen. No « most published » list, and no list of the most
 * similar: similarity belongs to Compare.
 */
import { fill, h, keepFocus } from './dom.js';
import { nameIn, pathTo, periodOf, sharesOf } from './data.js';
import {
  goLink, hostLinks, keywordChips, nodeColour, pending, ringSections, rowList, section, shareBars,
} from './parts.js';
import { compareBody, comparePicker } from './compare.js';

function head(card, kind, title, note) {
  return h('header', { class: 'cx-atlas-card__head' },
    h('p', { class: 'cx-atlas-kind', text: kind }),
    h('h3', { class: 'cx-atlas-card__title', tabindex: '-1', text: title }),
    note ? h('p', { class: 'cx-atlas-note' }, ...[].concat(note)) : null);
}

function homeCard(card) {
  const { index, state, t, fmt, levelName, title } = card.view;
  const level = state.org || (index.levels[0] && index.levels[0].id) || '';
  const placed = index.people.filter((p) => p.x !== null && p.x !== undefined).length;
  const orgs = index.orgs.filter((o) => o.level === level && o.x !== null && o.x !== undefined)
    .map((o) => ({ o, n: (index.members.get(o.id) || []).length })).sort((a, b) => b.n - a.n);
  const totalWeight = index.tops.reduce((s, id) => s + (index.nodes.get(id).weight || 0), 0) || 1;
  const shares = {};
  for (const id of index.tops) {
    const n = index.nodes.get(id);
    shares[id] = n.share !== null && n.share !== undefined ? n.share : (n.weight || 0) / totalWeight;
  }
  return [
    head(card, t('atlas.card.field'), title || t('atlas.card.field_title'), t('atlas.card.field_note', {
      people: fmt.number(placed), keywords: fmt.number(index.keywords.length), themes: fmt.number(index.tops.length) })),
    section(t('atlas.card.themes'), shareBars(card, shares, index.tops.length)),
    orgs.length ? section(t('atlas.card.orgs_at', { level: levelName(level), count: fmt.number(orgs.length) }),
      ...rowList(card, orgs.map(({ o, n }) => ({ key: o.id, colour: nodeColour(card, index.orgTop[index.byOrg.get(o.id)]),
        label: goLink(card, { kind: 'organisation', id: o.id }, o.acronym ? `${o.acronym} · ${o.name}` : o.name),
        value: t('atlas.card.people', { count: n }) })), 30)) : null,
    h('p', { class: 'cx-atlas-note', text: t('atlas.card.find_hint') }),
  ];
}

function themeCard(card, id) {
  const { index, state, t, fmt, levelName, locale, links } = card.view;
  const node = index.nodes.get(id);
  const level = node.level || 1;
  const name = nameIn(node.names, locale);
  const parent = node.parent && index.nodes.has(node.parent) ? index.nodes.get(node.parent) : null;
  const people = [];
  index.people.forEach((p, i) => {
    const v = (p.shares && p.shares[level - 1] && p.shares[level - 1][id]) || 0;
    if (v > 0 && p.x !== null) people.push([i, v]);
  });
  people.sort((a, b) => b[1] - a[1]);
  const orgLevel = state.org || (index.levels[0] && index.levels[0].id) || '';
  const orgs = index.orgs.filter((o) => o.level === orgLevel && o.x !== null && o.x !== undefined)
    .map((o) => [o, index.orgSharesAt(o.id, level)[id] || 0]).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  const here = index.centres.get(id);
  const near = here ? [...index.centres].filter(([other]) => other !== id && (index.nodes.get(other).level || 1) === level)
    .map(([other, c]) => [other, (c.x - here.x) ** 2 + (c.y - here.y) ** 2]).sort((a, b) => a[1] - b[1]).slice(0, 3) : [];
  const terms = (node.top_keywords && node.top_keywords.length ? node.top_keywords
    : (index.nodeKeywords.get(id) || []).map((k) => index.keywords[k].term)).slice(0, 12);
  const kids = index.children.get(id) || [];
  return [
    head(card, parent ? t('atlas.card.topic_of', { name: nameIn(parent.names, locale) }) : t('atlas.card.theme'), name,
      node.share !== null && node.share !== undefined ? t('atlas.card.theme_share', { share: fmt.percent(node.share) }) : null),
    kids.length ? section(t('atlas.card.inside'), h('div', { class: 'cx-atlas-chips' }, kids.map((c) => goLink(card, { kind: 'theme', id: c },
      nameIn(index.nodes.get(c).names, locale))))) : null,
    terms.length ? section(t('atlas.card.keywords'), keywordChips(card, terms)) : null,
    section(t('atlas.card.people_most'), ...rowList(card, people.slice(0, 8).map(([i, v]) => ({ key: index.people[i].person_id,
      label: goLink(card, { kind: 'person', id: index.people[i].person_id }, card.view.nameOf('person', index.people[i])),
      value: fmt.percent(v) })), 8)),
    orgs.length ? section(t('atlas.card.orgs_most', { level: levelName(orgLevel) }), ...rowList(card, orgs.slice(0, 5).map(([o, v]) => ({
      key: o.id, label: goLink(card, { kind: 'organisation', id: o.id }, o.acronym || o.name), value: fmt.percent(v) })), 5)) : null,
    near.length ? section(t('atlas.card.next_to'), ...rowList(card, near.map(([other]) => ({ key: other, colour: nodeColour(card, other),
      label: goLink(card, { kind: 'theme', id: other }, nameIn(index.nodes.get(other).names, locale)) })), 3)) : null,
    hostLinks(card, [[links && links.themesNode ? links.themesNode(id) : null, 'atlas.card.edit_themes']]),
  ];
}

function keywordCard(card, term) {
  const { index, t, fmt, locale, links, users } = card.view;
  const k = index.keywords[index.byTerm.get(term)];
  const path = k.node ? pathTo(index, k.node) : [];
  let used = null;
  if (!card.view.usersOffered) used = null;
  else if (!users || users.error) used = pending(card, users);
  else if (!users.data.known) used = h('p', { class: 'cx-atlas-note', text: t('atlas.card.users_unknown') });
  else {
    used = rowList(card, (users.data.items || []).map((it) => ({ key: it.id,
      label: goLink(card, { kind: 'person', id: it.id }, it.name || card.view.nameOfSel({ kind: 'person', id: it.id })),
      value: fmt.percent(it.share) })), 10, users.data.count);
  }
  return [
    head(card, t('atlas.card.keyword'), term, path.length
      ? path.map((id, i) => [i ? ' › ' : '', goLink(card, { kind: 'theme', id }, nameIn(index.nodes.get(id).names, locale))]).flat()
      : t('atlas.card.aside')),
    k.share ? h('p', { class: 'cx-atlas-note', text: t('atlas.card.keyword_share', { share: fmt.percent(k.share) }) }) : null,
    used ? section(users && users.data && users.data.known ? t('atlas.card.used_by', { count: fmt.number(users.data.count) })
      : t('atlas.card.used_by_title'), ...[].concat(used)) : null,
    hostLinks(card, [[links && links.keyword ? links.keyword(term) : null, 'atlas.card.open_keywords'],
      [links && links.themesKeyword ? links.themesKeyword(term) : null, 'atlas.card.edit_themes']]),
  ];
}

function organisationCard(card, id) {
  const { index, t, fmt, levelName, links, rings, sets, ringsOffered, state } = card.view;
  const o = index.orgs[index.byOrg.get(id)];
  const members = (index.members.get(id) || []).slice()
    .sort((a, b) => card.view.nameOf('person', index.people[a]).localeCompare(card.view.nameOf('person', index.people[b])));
  const parents = (o.parents || []).map((p) => index.orgs[index.byOrg.get(p)]).filter(Boolean);
  const terms = sets.get(`organisation:${id}`);
  const network = ringsOffered && state.net > 0 ? (rings && !rings.error ? ringSections(card, rings,
    ['atlas.card.writes_direct', 'atlas.card.ring2', 'atlas.card.ring3'], (item) => ({
      sel: item.place || index.byOrg.has(item.id) ? { kind: 'organisation', id: item.id } : null,
      text: item.acronym || item.name || (index.byOrg.has(item.id) ? index.orgs[index.byOrg.get(item.id)].acronym || index.orgs[index.byOrg.get(item.id)].name : item.id) }))
    : [pending(card, rings)]) : null;
  return [
    head(card, levelName(o.level) || t('atlas.kind.organisation'), o.name, [
      [o.acronym, t('atlas.card.people', { count: members.length })].filter(Boolean).join(' · '),
      ...parents.map((p) => [' · ', goLink(card, { kind: 'organisation', id: p.id }, p.acronym || p.name)]).flat()]),
    section(t('atlas.card.themes'), shareBars(card, index.orgSharesAt(id, 1))),
    terms && terms.length ? section(t('atlas.card.keywords_most'), keywordChips(card, terms.slice(0, 12))) : null,
    section(t('atlas.card.members', { count: fmt.number(members.length) }), ...rowList(card, members.map((i) => ({
      key: index.people[i].person_id, colour: nodeColour(card, index.personTop[i]),
      label: goLink(card, { kind: 'person', id: index.people[i].person_id }, card.view.nameOf('person', index.people[i])) })), 60)),
    network ? section(t('atlas.card.writes_with'), ...network,
      h('p', { class: 'cx-atlas-note', text: t('atlas.card.writes_note', { level: levelName(o.level) }) })) : null,
    comparePicker(card, { kind: 'organisation', id }),
    hostLinks(card, [[links && links.organisation ? links.organisation(id) : null, 'atlas.card.open_people']]),
  ];
}

function personCard(card, sel) {
  const { index, t, fmt, links, rings, sets, ringsOffered, state } = card.view;
  const projected = sel.kind === 'projected';
  const p = projected ? index.projected[index.byProjected.get(sel.id)] : index.people[index.byPerson.get(sel.id)];
  const info = index.extra[sel.id] || { columns: {} };
  const orgs = (index.orgsOfPerson.get(sel.id) || []).map((o) => index.orgs[index.byOrg.get(o)]).filter(Boolean)
    .filter((o) => !index.levels.length || o.level === (state.org || index.levels[0].id));
  const terms = sets.get(`person:${sel.id}`);
  const network = ringsOffered && state.net > 0 ? (rings && !rings.error ? ringSections(card, rings,
    ['atlas.card.coauthors', 'atlas.card.ring2', 'atlas.card.ring3'], (item) => {
      const known = index.byPerson.has(item.id) ? index.people[index.byPerson.get(item.id)]
        : index.byProjected.has(item.id) ? index.projected[index.byProjected.get(item.id)] : null;
      const kind = index.byPerson.has(item.id) ? 'person' : 'projected';
      const text = item.name || (known ? card.view.nameOf(kind, known) : t('atlas.card.someone'));
      return { sel: known ? { kind, id: item.id } : null, text, faint: kind === 'projected' || item.place === 'projected' };
    }) : [pending(card, rings)]) : null;
  const windows = projected ? [] : index.windows.get(sel.id) || [];
  const period = periodOf(index, state);
  const texts = rings && !rings.error && rings.texts ? rings.texts : null;
  return [
    head(card, projected ? t('atlas.card.not_mapped_kind') : t('atlas.card.person'), card.view.nameOf(projected ? 'projected' : 'person', p), [
      ...orgs.map((o, i) => [i ? ' · ' : '', goLink(card, { kind: 'organisation', id: o.id }, o.acronym || o.name)]).flat(),
      texts ? `${orgs.length ? ' · ' : ''}${t('atlas.card.texts', { count: texts })}` : '',
      projected ? t('atlas.card.not_mapped_help') : '']),
    Object.keys(info.columns || {}).length ? h('dl', { class: 'cx-atlas-facts' }, Object.entries(info.columns).map(([k, v]) =>
      h('div', {}, h('dt', { text: k }), h('dd', { text: v })))) : null,
    section(t('atlas.card.themes'), shareBars(card, sharesOf(index, sel, 1))),
    terms && terms.length ? section(t('atlas.card.keywords_most'), keywordChips(card, terms.slice(0, 12))) : null,
    network ? section(null, ...network,
      rings && rings.outside ? h('p', { class: 'cx-atlas-note', text: t('atlas.card.outside', { count: rings.outside }) }) : null,
      h('p', { class: 'cx-atlas-note', text: t('atlas.card.coauthors_note') })) : null,
    windows.length ? section(t('atlas.card.windows'), h('ol', { class: 'cx-atlas-windows' }, windows.map((w) => {
      const inside = !period || (w.end >= period[0] && w.start <= period[1]);
      return h('li', { class: inside ? '' : 'is-outside' },
        h('span', { text: t('atlas.filters.years', { from: fmt.year(w.start), to: fmt.year(w.end) }) }),
        h('span', { text: w.top && index.nodes.has(w.top) ? nameIn(index.nodes.get(w.top).names, card.view.locale) : '' }),
        h('span', { class: 'cx-atlas-val', text: t('atlas.card.texts', { count: w.texts }) }));
    }))) : null,
    projected ? null : comparePicker(card, sel),
    hostLinks(card, [[links && links.person && !projected ? links.person(sel.id) : null, 'atlas.card.open_people']]),
  ];
}

function textCard(card, id) {
  const { index, t, texts, links } = card.view;
  if (!texts) return [pending(card, null)];
  const i = texts.id.indexOf(id);
  if (i < 0) return [h('p', { class: 'cx-atlas-note', text: t('atlas.card.gone') })];
  const people = texts.people[i].filter((pid) => index.byPerson.has(pid));
  return [
    head(card, t('atlas.card.text'), texts.title[i] || id, texts.year[i] ? String(texts.year[i]) : null),
    people.length ? section(t('atlas.card.authors'), ...rowList(card, people.map((pid) => ({ key: pid,
      label: goLink(card, { kind: 'person', id: pid }, card.view.nameOf('person', index.people[index.byPerson.get(pid)])) })), 30)) : null,
    texts.terms[i].length ? section(t('atlas.card.keywords_found'), keywordChips(card, texts.terms[i].map((k) => index.keywords[k].term))) : null,
    hostLinks(card, [[links && links.text ? links.text(id) : null, 'atlas.card.open_people']]),
  ];
}

/** The card in *el*: `go(sel)` to move the focus, `follow(event, href)` for a host's link.
 * Answers `{update(view)}`. */
export function createCard(el, { go, follow, setWith }) {
  const card = { go, follow, setWith, view: null, picker: null };
  const api = {
    card,
    update(view) {
      card.view = view;
      const { index, state } = view;
      const sel = state.sel;
      let body;
      if (!sel) body = homeCard(card);
      else if (state.with && (sel.kind === 'person' || sel.kind === 'organisation')) body = compareBody(card, sel, state.with);
      else if (sel.kind === 'theme' && index.nodes.has(sel.id)) body = themeCard(card, sel.id);
      else if (sel.kind === 'keyword' && index.byTerm.has(sel.id)) body = keywordCard(card, sel.id);
      else if (sel.kind === 'organisation' && index.byOrg.has(sel.id)) body = organisationCard(card, sel.id);
      else if ((sel.kind === 'person' && index.byPerson.has(sel.id)) || (sel.kind === 'projected' && index.byProjected.has(sel.id))) {
        body = personCard(card, sel);
      } else if (sel.kind === 'text') body = textCard(card, sel.id);
      else body = [h('p', { class: 'cx-atlas-note', text: view.t('atlas.card.gone') })];
      keepFocus(el, () => fill(el, h('div', { class: 'cx-atlas-card__cols' }, ...body)));
    },
  };
  card.redraw = () => api.update(card.view);
  return api;
}
