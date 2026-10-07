// SPDX-License-Identifier: MIT
/**
 * The ranked list: a person or an organisation (Find), and every person, or every
 * organisation of a level, from the most to the least alike, with the themes they share and
 * whether they write together; a theme narrows the list to those whose main theme it is. Each
 * row opens its page, Compare (two of a kind) and its own ranked list; « Download CSV » saves
 * the whole list. A national field is scored in blocks (`scoreAll`), the page staying alive.
 */
import { fill, h } from '../atlas/dom.js';
import { createFind } from '../atlas/find.js';
import { distQuery, rankOrder, scoreAll, sharedThemes, togetherWith } from './engine.js';
import { RANK_PAGE, distRef, scopeOf } from './scope.js';
import {
  amongOf, amongOptions, compareLink, csvButton, distSelect, distTable, pager, runner, scoreCell, statusLine,
  themeChips, themeSelect, togetherCell,
} from './controls.js';

/** The focus of the state as `{kind, i}` (null when unknown). */
export function rankFocus(index, text) {
  const ref = distRef(text);
  if (ref && ref.kind === 'person' && index.byPerson.has(ref.id)) return { kind: 'person', i: index.byPerson.get(ref.id) };
  if (ref && ref.kind === 'organisation' && index.byOrg.has(ref.id)) return { kind: 'organisation', i: index.byOrg.get(ref.id) };
  return null;
}

/** The catalogue key that says how a row writes with the focus. */
function togetherKey(focus, kind) {
  if (focus.kind === kind) return 'atlas.dist.together.texts';
  return focus.kind === 'person' ? 'atlas.dist.together.coauthors' : 'atlas.dist.together.members';
}

export function createRankView(ctx) {
  const { t, index } = ctx;
  const work = runner();
  const find = createFind({ t, kinds: ['person', 'organisation'], label: t('atlas.dist.of'), placeholder: t('atlas.dist.of.placeholder'),
    entries: ctx.entries,
    onPick: (sel) => ctx.set({ of: `${sel.kind}:${sel.id}` }) });
  const controls = h('div', { class: 'cx-dist__controls' });
  const head = h('div', { class: 'cx-dist__head' });
  const status = statusLine();
  const body = h('div', { class: 'cx-dist__body' });
  const el = h('section', { class: 'cx-dist-view', 'aria-label': t('atlas.dist.view.rank') },
    h('div', { class: 'cx-dist__controls' }, h('span', { class: 'cx-dist-field' },
      h('span', { class: 'cx-dist__label', 'aria-hidden': 'true', text: t('atlas.dist.of') }), find.el)), controls, head, status, body);
  let last = { key: '', result: null };

  function draw(state, focus, among, result) {
    const { scores, targets, order, together } = result;
    const total = order.length;
    const pages = Math.max(1, Math.ceil(total / RANK_PAGE));
    const page = Math.min(pages, Math.max(1, Number.parseInt(state.pg, 10) || 1));
    const rows = [];
    for (let r = (page - 1) * RANK_PAGE; r < Math.min(total, page * RANK_PAGE); r += 1) {
      const k = order[r];
      const j = targets[k];
      const other = { kind: among.kind, i: j };
      rows.push([
        h('span', { class: 'cx-atlas-val', text: ctx.fmt.number(r + 1) }),
        ctx.pageOf(among.kind, j),
        scoreCell(ctx, scores[k]),
        togetherCell(ctx, together ? together.get(j) || 0 : null, togetherKey(focus, among.kind)),
        themeChips(ctx, sharedThemes(ctx.data, focus, other)),
        h('span', { class: 'cx-dist-actions' },
          focus.kind === among.kind ? compareLink(ctx, focus, other) : null,
          h('button', { type: 'button', class: 'cx-atlas-btn', text: t('atlas.dist.its_list'),
            onClick: () => ctx.set({ of: `${among.kind}:${among.kind === 'person' ? index.people[j].person_id : index.orgs[j].id}` }) })),
      ]);
    }
    fill(body, total ? distTable([t('atlas.dist.col.rank'), t('atlas.dist.col.name'), t(`atlas.similarity.${ctx.measureNow}`),
      t('atlas.dist.col.together'), t('atlas.dist.col.themes'), t('atlas.dist.col.actions')], rows,
    { caption: t('atlas.dist.rank.title', { name: ctx.nameOf(focus.kind, focus.i) }), numeric: [0, 2] })
      : h('p', { class: 'cx-atlas-note', text: t('atlas.dist.nobody') }),
    pager(ctx, page, pages, total, RANK_PAGE, (p) => ctx.set({ pg: String(p) })));
  }

  function csv(focus, among, result) {
    const { scores, targets, order, together } = result;
    const rows = [[t('atlas.dist.col.rank'), t('atlas.dist.col.name'), t(`atlas.similarity.${ctx.measureNow}`),
      t('atlas.dist.col.together'), t('atlas.dist.col.themes')]];
    order.forEach((k, r) => {
      const j = targets[k];
      rows.push([r + 1, ctx.nameOf(among.kind, j), Number.isNaN(scores[k]) ? '' : Math.round(scores[k] * 1e4) / 1e4,
        together ? together.get(j) || 0 : '', sharedThemes(ctx.data, focus, { kind: among.kind, i: j }).map(([n]) => ctx.themeName(n)).join('; ')]);
    });
    ctx.download(`similar-${ctx.nameOf(focus.kind, focus.i)}`, rows);
  }

  function update(state) {
    const focus = rankFocus(index, state.of);
    const among = amongOf(ctx, state.among || (focus && focus.kind === 'organisation' ? `organisation:${index.orgs[focus.i].level}` : 'person'));
    fill(controls,
      distSelect({ label: t('atlas.dist.among'), value: among.kind === 'person' ? 'person' : `organisation:${among.level}`,
        options: amongOptions(ctx), onChange: (v) => ctx.set({ among: v }), key: 'among' }),
      themeSelect(ctx, state.th, (v) => ctx.set({ th: v })));
    if (!focus) {
      fill(head, h('p', { class: 'cx-atlas-note', text: t('atlas.dist.rank.empty') }));
      fill(body);
      status.textContent = '';
      return;
    }
    const title = h('h3', { class: 'cx-dist__title' }, t('atlas.dist.rank.of'), ' ', ctx.pageOf(focus.kind, focus.i));
    fill(head, title);
    const measure = ctx.measureNow;
    const key = [measure, state.of, among.kind, among.level, state.th].join('|');
    if (last.key === key && last.result) {
      head.append(csvButton(ctx, () => csv(focus, among, last.result)));
      draw(state, focus, among, last.result);
      return;
    }
    status.textContent = t('atlas.dist.computing');
    status.setAttribute('aria-busy', 'true');
    const parts = [`links:${focus.kind}`, `links:${among.kind}`, 'links:person'];
    if (measure === 'space') parts.push(`vectors:${focus.kind}`, `vectors:${among.kind}`);
    work.run(async (signal) => {
      await ctx.need([...new Set(parts)]);
      const all = scopeOf(index, among.kind, { level: among.level, theme: state.th || null });
      const targets = all.filter((j) => !(j === focus.i && among.kind === focus.kind));
      const q = distQuery(ctx.data, focus.kind, focus.i);
      const started = performance.now();
      const scores = await scoreAll(ctx.data, measure, q, among.kind, targets, signal);
      const order = rankOrder(scores);
      const crossLevel = focus.kind === 'organisation' && among.kind === 'organisation' && index.orgs[focus.i].level !== among.level;
      const together = crossLevel ? null : togetherWith(ctx.data, focus, among.kind);
      return { scores, targets, order, together, placed: measure !== 'space' || Boolean(q.v), ms: performance.now() - started };
    }).then((r) => {
      if (!r) return;
      status.removeAttribute('aria-busy');
      if (r.error) {
        status.textContent = t('atlas.dist.failed');
        return;
      }
      last = { key, result: r.value };
      status.textContent = r.value.placed ? t('atlas.dist.rank.count', { count: r.value.order.length, kind: among.kind })
        : t('atlas.dist.unplaced', { name: ctx.nameOf(focus.kind, focus.i) });
      head.append(csvButton(ctx, () => csv(focus, among, r.value)));
      draw(ctx.state(), focus, among, r.value);
    });
  }

  return {
    el,
    update,
    repaint: () => update(ctx.state()),
    destroy() {
      work.stop();
      find.destroy();
    },
  };
}
