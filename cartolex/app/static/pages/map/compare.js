// SPDX-License-Identifier: MIT
/**
 * « Compare with… »: the person or organisation selected on the map beside a second one,
 * picked by its name (`GET /api/atlas/compare`): how close they are in the space of the
 * themes, in their keyword use (cosine, and the share of keywords in common), in their
 * themes (the overlap of their top-level shares), the keywords and themes they share and
 * the texts with an author on each side.
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatNumber, formatPercent, locale, t } from '../../core/i18n.js';
import { Button, Dialog, ErrorCard } from '../../components/index.js';
import { Find } from './find.js';
import { themeName } from './model.js';

const KINDS = ['person', 'organisation'];
const two = (v) => (v === null || v === undefined ? '—'
  : formatNumber(v, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));

function nameOf(index, sel) {
  if (!sel) return '';
  if (sel.kind === 'person' && index.byPerson.has(sel.id)) return index.people[index.byPerson.get(sel.id)].name;
  if (sel.kind === 'organisation' && index.byOrg.has(sel.id)) return index.orgs[index.byOrg.get(sel.id)].name;
  return sel.id;
}

function Result({ index, data }) {
  const lang = locale.value;
  const kw = data.keywords;
  return html`<div class="cx-atlas-compare">
    <dl class="cx-atlas-compare__measures">
      <div><dt>${t('map.compare.space')}</dt><dd>${two(data.space)}</dd></div>
      <div><dt>${t('map.compare.keywords')}</dt><dd>${two(kw.cosine)}</dd></div>
      <div><dt>${t('map.compare.jaccard')}</dt>
        <dd>${two(kw.jaccard)} <span class="cx-atlas-panel__muted">${t('map.compare.common',
          { common: kw.common, a: kw.a, b: kw.b })}</span></dd></div>
      <div><dt>${t('map.compare.themes')}</dt><dd>${formatPercent(data.themes.overlap)}</dd></div>
      <div><dt>${t('map.compare.texts')}</dt><dd>${formatNumber(data.texts.shared)}</dd></div>
    </dl>
    <p class="cx-atlas-panel__muted">${t('map.compare.help')}</p>
    ${kw.shared.length ? html`<section>
      <h3 class="cx-atlas-panel__subtitle">${t('map.compare.shared_keywords')}</h3>
      <table class="cx-atlas-compare__table">
        <thead><tr><th scope="col">${t('map.compare.keyword')}</th>
          <th scope="col" class="is-number">${data.a.name}</th><th scope="col" class="is-number">${data.b.name}</th></tr></thead>
        <tbody>${kw.shared.map((s) => html`<tr key=${s.term}><th scope="row">${s.term}</th>
          <td class="is-number">${formatPercent(s.a)}</td><td class="is-number">${formatPercent(s.b)}</td></tr>`)}</tbody>
      </table></section>` : null}
    ${data.themes.shared.length ? html`<section>
      <h3 class="cx-atlas-panel__subtitle">${t('map.compare.shared_themes')}</h3>
      <table class="cx-atlas-compare__table">
        <thead><tr><th scope="col">${t('map.compare.theme')}</th>
          <th scope="col" class="is-number">${data.a.name}</th><th scope="col" class="is-number">${data.b.name}</th></tr></thead>
        <tbody>${data.themes.shared.map((s) => html`<tr key=${s.node}>
          <th scope="row">${index.nodes.has(s.node) ? themeName(index, s.node, lang) : s.node}</th>
          <td class="is-number">${formatPercent(s.a)}</td><td class="is-number">${formatPercent(s.b)}</td></tr>`)}</tbody>
      </table></section>` : null}
    ${data.texts.items.length ? html`<section>
      <h3 class="cx-atlas-panel__subtitle">${t('map.compare.shared_texts')}</h3>
      <ul class="cx-atlas-list">${data.texts.items.map((x) => html`<li key=${x.id}>
        <span>${x.title || x.id}</span>
        ${x.year ? html`<span class="cx-atlas-panel__muted">${String(x.year)}</span>` : null}</li>`)}</ul>
    </section>` : null}
  </div>`;
}

/** The dialog. *a* is the selection (`{kind, id}`). */
export function CompareDialog({ ctx, index, a, open, onClose, onSelect }) {
  const [b, setB] = useState(null);
  const [answer, setAnswer] = useState(null);
  useEffect(() => {
    setAnswer(null);
    if (!open || !a || !b) return;
    ctx.api.get('/api/atlas/compare', { query: { a: `${a.kind}:${a.id}`, b: `${b.kind}:${b.id}` } })
      .then((r) => setAnswer(r.ok ? { data: r.data } : { error: r.error }));
  }, [open, a && a.id, b && b.id]);
  useEffect(() => { if (!open) setB(null); }, [open]);
  return html`<${Dialog} open=${open} onClose=${onClose} size="l"
    title=${t('map.compare.title', { name: nameOf(index, a) })} description=${t('map.compare.lead')}
    footer=${html`${b ? html`<${Button} onClick=${() => { onSelect(b); onClose(); }}>
      ${t('map.compare.show', { name: nameOf(index, b) })}<//>` : null}
      <${Button} variant="primary" onClick=${onClose}>${t('common.close')}<//>`}>
    <${Find} index=${index} texts=${null} kinds=${KINDS} label=${t('map.compare.pick')}
      placeholder=${t('map.compare.placeholder')}
      onSelect=${(sel) => setB(sel)} />
    ${b ? html`<p class="cx-atlas-compare__pair">${t('map.compare.pair', { a: nameOf(index, a), b: nameOf(index, b) })}</p>` : html`<p class="cx-atlas-panel__muted">${t('map.compare.pick_help')}</p>`}
    ${b && !answer ? html`<p aria-busy="true">${t('common.loading')}</p>` : null}
    ${answer && answer.error ? html`<${ErrorCard} error=${answer.error} compact />` : null}
    ${answer && answer.data ? html`<${Result} index=${index} data=${answer.data} />` : null}
  <//>`;
}
