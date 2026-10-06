// SPDX-License-Identifier: MIT
/**
 * Compare: two people or two organisations side by side in the card. The headline is their
 * similarity by the project's measure when the source names it (`measure`, `similarity`:
 * meaning in the map's space, shared vocabulary, keywords in common or shared themes), then
 * the other measures: the cosine of their vectors in the space of the themes when the source
 * can say it (else of their theme shares), the themes they have in common (Σ min of their
 * shares), the texts they
 * wrote together (two people), the people in both (two organisations), the keywords they
 * share, and their themes as mirrored bars. « Compare with… » picks the other side with Find.
 */
import { h } from './dom.js';
import { nameIn, sharesOf } from './data.js';
import { createFind } from './find.js';
import { goLink, keywordChips, nodeColour, pending, rowList, section } from './parts.js';

/** The cosine of two share vectors (`{node: share}`). */
export function sharesCosine(a, b) {
  let dot = 0;
  let na = 0;
  let nb = 0;
  for (const [k, v] of Object.entries(a)) {
    na += v * v;
    dot += v * (b[k] || 0);
  }
  for (const v of Object.values(b)) nb += v * v;
  return na && nb ? dot / Math.sqrt(na * nb) : 0;
}

/** Σ min of two share vectors: how much of their themes they have in common. */
export function sharesOverlap(a, b) {
  let s = 0;
  for (const [k, v] of Object.entries(a)) s += Math.min(v, b[k] || 0);
  return s;
}

/** « Compare with… » and, once pressed, Find over the same kind; kept across the card's
 * redraws so what one typed stays. */
export function comparePicker(card, sel) {
  const { t } = card.view;
  const kind = sel.kind === 'organisation' ? 'organisation' : 'person';
  const key = `${kind}:${sel.id}`;
  if (card.picker && card.picker.key !== key) {
    card.picker.find.destroy();
    card.picker = null;
  }
  const button = h('button', { type: 'button', class: 'cx-atlas-btn', 'aria-expanded': String(Boolean(card.picker)),
    dataset: { key: 'compare' }, text: t('atlas.compare.with'),
    onClick: () => {
      if (card.picker) {
        card.picker.find.destroy();
        card.picker = null;
      } else {
        const find = createFind({ t, entries: () => card.view.findEntries().filter((e) => e.id !== sel.id || e.kind !== kind), kinds: [kind],
          label: t('atlas.compare.pick'), placeholder: t('atlas.compare.placeholder'),
          onPick: (other) => {
            if (card.picker) card.picker.find.destroy();
            card.picker = null;
            card.setWith(other);
          } });
        card.picker = { key, find };
      }
      card.redraw();
      if (card.picker) card.picker.find.focus();
    } });
  return h('div', { class: 'cx-atlas-actions cx-atlas-compare-pick' }, button, card.picker ? card.picker.find.el : null);
}

/** The card of a comparison of *a* and *b* (`{kind, id}`). */
export function compareBody(card, a, b) {
  const { index, t, fmt, compare, nameOfSel, locale, compareOffered } = card.view;
  const sa = sharesOf(index, a, 1);
  const sb = sharesOf(index, b, 1);
  const data = compare && !compare.error ? compare.data : null;
  // the headline: the project's measure, when the source names it
  const head = data && data.measure && data.similarity !== null && data.similarity !== undefined ? data.measure : '';
  const measures = [];
  if (data && data.space !== null && data.space !== undefined) measures.push(['atlas.compare.space', fmt.decimal(data.space), 'space']);
  else measures.push(['atlas.compare.themes_cosine', fmt.decimal(sharesCosine(sa, sb)), '']);
  measures.push(['atlas.compare.overlap', fmt.percent(sharesOverlap(sa, sb)), 'themes']);
  if (data && data.keywords && data.keywords.cosine !== undefined) {
    measures.push(['atlas.compare.keywords', fmt.decimal(data.keywords.cosine), 'keywords']);
    measures.push(['atlas.compare.jaccard', fmt.decimal(data.keywords.jaccard), 'jaccard']);
  }
  if (data && data.texts && a.kind === 'person') measures.push(['atlas.compare.together', fmt.number(data.texts.shared)]);
  else if (data && data.texts) measures.push(['atlas.compare.texts', fmt.number(data.texts.shared)]);
  const both = a.kind === 'organisation' && b.kind === 'organisation'
    ? (index.members.get(a.id) || []).filter((i) => (index.members.get(b.id) || []).includes(i)) : null;
  const rows = index.tops.map((id) => [id, sa[id] || 0, sb[id] || 0]).filter(([, x, y]) => x > 0.03 || y > 0.03)
    .sort((p, q) => (q[1] + q[2]) - (p[1] + p[2]));
  const most = Math.max(0.01, ...rows.map(([, x, y]) => Math.max(x, y)));
  const shared = data && data.keywords && data.keywords.shared ? data.keywords.shared.slice(0, 12) : [];
  return [
    h('header', { class: 'cx-atlas-card__head' },
      h('p', { class: 'cx-atlas-kind', text: t('atlas.compare.kind') }),
      h('h3', { class: 'cx-atlas-card__title', tabindex: '-1' }, goLink(card, a, nameOfSel(a)), ` ${t('atlas.compare.and')} `,
        goLink(card, b, nameOfSel(b)))),
    section(null, h('ul', { class: 'cx-atlas-card__list' },
      head ? h('li', { class: 'cx-atlas-compare__head', dataset: { measure: head } },
        h('span', { text: t('atlas.compare.headline', { name: t(`atlas.similarity.${head}`) }),
          title: t(`atlas.similarity.${head}.help`) }),
        h('span', { class: 'cx-atlas-val', text: fmt.decimal(data.similarity) })) : null,
      measures.filter((m) => !head || m[2] !== head).map(([key, value]) => h('li', {},
        h('span', { text: t(key) }), h('span', { class: 'cx-atlas-val', text: value })))),
    compareOffered && !compare ? pending(card, null) : null),
    both ? section(t('atlas.compare.both', { count: fmt.number(both.length) }), both.length
      ? rowList(card, both.map((i) => ({ key: index.people[i].person_id,
        label: goLink(card, { kind: 'person', id: index.people[i].person_id }, card.view.nameOf('person', index.people[i])) })), 30)
      : h('p', { class: 'cx-atlas-note', text: t('atlas.compare.nobody') })) : null,
    section(t('atlas.compare.themes', { a: nameOfSel(a), b: nameOfSel(b) }), h('div', { class: 'cx-atlas-fly' },
      rows.map(([id, x, y]) => [
        h('span', { class: 'cx-atlas-fly__l', 'aria-label': fmt.percent(x) }, h('span', { class: 'cx-atlas-fly__b',
          vars: { '--cx-share': `${Math.round((x / most) * 100)}%`, '--cx-chip': nodeColour(card, id) } })),
        goLink(card, { kind: 'theme', id }, nameIn(index.nodes.get(id).names, locale)),
        h('span', { class: 'cx-atlas-fly__r', 'aria-label': fmt.percent(y) }, h('span', { class: 'cx-atlas-fly__b',
          vars: { '--cx-share': `${Math.round((y / most) * 100)}%`, '--cx-chip': nodeColour(card, id) } })),
      ]).flat())),
    shared.length ? section(t('atlas.compare.shared_keywords'), keywordChips(card, shared.map((s) => s.term))) : null,
    data && data.texts && data.texts.items && data.texts.items.length ? section(t('atlas.compare.shared_texts'),
      ...rowList(card, data.texts.items.map((x) => ({ key: x.id, label: h('span', { text: x.title || x.id }),
        value: x.year ? String(x.year) : '' })), 10, data.texts.shared)) : null,
    h('div', { class: 'cx-atlas-actions' }, h('button', { type: 'button', class: 'cx-atlas-btn', dataset: { key: 'uncompare' },
      text: t('atlas.compare.stop'), onClick: () => card.setWith(null) })),
  ];
}
