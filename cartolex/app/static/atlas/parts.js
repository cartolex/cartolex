// SPDX-License-Identifier: MIT
/**
 * The pieces the card is made of: links that move the focus, bars of theme shares, sections
 * with a heading, lists that end with « and N more », chips of keywords, the host's links to
 * its other screens (« Open in People ↗ ») and the rings of the network.
 */
import { h, swatch } from './dom.js';
import { nameIn } from './data.js';
import { ringList } from './rings.js';

/** A link that puts *sel* in focus. */
export function goLink(card, sel, text) {
  return h('button', { type: 'button', class: 'cx-atlas-link', dataset: { key: `go-${sel.kind}:${sel.id}` },
    text, onClick: () => card.go(sel) });
}

/** A section with its heading. */
export function section(title, ...kids) {
  return h('section', { class: 'cx-atlas-card__section' }, title ? h('h4', { text: title }) : null, ...kids);
}

/** A list of rows (`[{key, label (node), value?, colour?}]`), the first *most*, then « and N more ». */
export function rowList(card, rows, most, total = rows.length) {
  const { t } = card.view;
  return [
    h('ul', { class: 'cx-atlas-card__list' }, rows.slice(0, most).map((r) => h('li', {},
      r.colour ? swatch(r.colour) : null, r.label, r.value ? h('span', { class: 'cx-atlas-val', text: r.value }) : null))),
    total > most ? h('p', { class: 'cx-atlas-note', text: t('atlas.card.more', { count: total - most }) }) : null,
  ];
}

/** Chips of keywords, each putting its keyword in focus. */
export function keywordChips(card, terms) {
  return h('div', { class: 'cx-atlas-chips' }, terms.map((term) => h('button', { type: 'button', class: 'cx-atlas-chip',
    dataset: { key: `kw-${term}` }, text: term, onClick: () => card.go({ kind: 'keyword', id: term }) })));
}

/** The colour of a node's top-level theme in the scheme. */
export function nodeColour(card, node) {
  const { index, colours } = card.view;
  const i = index.colourOf(node);
  return i < colours.themes.length ? colours.themes[i] : colours.neutral;
}

/** Bars of the largest shares (`{node: share}`), each theme a link. */
export function shareBars(card, shares, most = 5) {
  const { index, locale, fmt } = card.view;
  const list = Object.entries(shares || {}).filter(([id, v]) => v > 0 && index.nodes.has(id))
    .sort((a, b) => b[1] - a[1]).slice(0, most);
  if (!list.length) return h('p', { class: 'cx-atlas-note', text: card.view.t('atlas.card.no_shares') });
  return h('div', { class: 'cx-atlas-bars' }, list.map(([id, v]) => h('div', { class: 'cx-atlas-bar' },
    goLink(card, { kind: 'theme', id }, nameIn(index.nodes.get(id).names, locale)),
    h('span', { class: 'cx-atlas-bar__track', 'aria-hidden': 'true' },
      h('span', { class: 'cx-atlas-bar__fill', vars: { '--cx-share': `${Math.round(v * 100)}%`, '--cx-chip': nodeColour(card, id) } })),
    h('span', { class: 'cx-atlas-val', text: fmt.percent(v) }))));
}

/** The host's links to its other screens: `[[link, labelKey]]`, a link being an address or
 * `{href, label}` (the host's own words); a null link is not offered. */
export function hostLinks(card, links) {
  const { t } = card.view;
  const shown = links.filter(([link]) => link && (typeof link === 'string' || link.href));
  if (!shown.length) return null;
  return h('div', { class: 'cx-atlas-actions' }, shown.map(([link, key]) => {
    const href = typeof link === 'string' ? link : link.href;
    const text = typeof link === 'string' || !link.label ? t(key) : link.label;
    return h('a', { class: 'cx-atlas-btn', href, dataset: { key: `link-${key}` }, text,
      onClick: (e) => card.follow(e, href) });
  }));
}

/** A line saying what is still being read, or that it could not be. */
export function pending(card, answer) {
  const { t } = card.view;
  if (answer && answer.error) return h('p', { class: 'cx-atlas-note', role: 'status', text: t('atlas.card.unread') });
  return h('p', { class: 'cx-atlas-note', 'aria-busy': 'true', text: t('atlas.card.loading') });
}

/**
 * The rings of the network as lists: the first ring with the texts written together, the
 * others with the partner they come through. *words* are the catalogue keys of the rings'
 * headings; *itemOf(item)* answers `{sel, text}` (sel null: not on the map, plain text).
 */
export function ringSections(card, answer, words, itemOf) {
  const { t, fmt } = card.view;
  const out = [];
  ringList(answer).forEach((ring, d) => {
    const most = d ? 10 : 30;
    const rows = (ring.items || []).map((item) => {
      const { sel, text, faint } = itemOf(item);
      const via = d && item.via && item.via.length ? itemOf({ id: item.via[0], via: true }).text : '';
      const value = d ? t('atlas.card.via', { name: via, count: item.paths || 1 })
        : t('atlas.card.texts_together', { count: item.texts });
      return { key: item.id, label: sel ? goLink(card, sel, text) : h('span', { class: 'cx-atlas-quiet', text }),
        value: faint ? `${t('atlas.card.not_mapped')} · ${value}` : value };
    });
    out.push(h('h4', { class: d ? 'cx-atlas-card__ring' : '', text: t(words[d], { count: fmt.number(ring.count) }) }));
    if (!rows.length) out.push(h('p', { class: 'cx-atlas-note', text: t('atlas.card.ring_none') }));
    else out.push(...rowList(card, rows, most, ring.count));
  });
  return out;
}
