// SPDX-License-Identifier: MIT
/**
 * Pairs: « talk the same, don't work together » (the most alike pairs that never wrote a
 * text together: possible collaborations, parallel communities) and the opposite, « work
 * together, talk differently » (co-authors the least alike: ties across fields), among the
 * people or the organisations of a level, all or those of one theme. Every pair of the first
 * list is measured, so its scope is capped (`PAIRS_CAP`, said when passed); the second reads
 * the links, so it holds for a whole field. Each pair opens Compare. A measure the browser
 * cannot compute is asked of the host's server (`remote.js`), with the same caps.
 */
import { fill, h } from '../atlas/dom.js';
import { pairsApart, pairsTogether, sharedThemes } from './engine.js';
import { PAIRS_CAP, PAIRS_LIMIT, RANK_PAGE, scopeOf } from './scope.js';
import { remotePairs } from './remote.js';
import {
  amongOf, amongOptions, compareLink, csvButton, distSelect, distTable, pager, runner, scoreCell, statusLine, themeChips,
  themeSelect, togetherCell,
} from './controls.js';

/** The two lists. */
export const PAIR_MODES = ['apart', 'together'];

export function createPairsView(ctx) {
  const { t, index } = ctx;
  const work = runner();
  const modes = h('div', { class: 'cx-dist__controls' });
  const controls = h('div', { class: 'cx-dist__controls' });
  const head = h('div', { class: 'cx-dist__head' });
  const status = statusLine();
  const body = h('div', { class: 'cx-dist__body' });
  const el = h('section', { class: 'cx-dist-view', 'aria-label': t('atlas.dist.view.pairs') }, modes, controls, head, status, body);
  let last = { key: '', result: null };

  function draw(mode, among, pairs) {
    const pages = Math.max(1, Math.ceil(pairs.length / RANK_PAGE));
    const page = Math.min(pages, Math.max(1, Number.parseInt(ctx.state().pg, 10) || 1));
    const first = (page - 1) * RANK_PAGE;
    const rows = pairs.slice(first, first + RANK_PAGE).map((p, k) => {
      const r = first + k;
      const a = { kind: among.kind, i: p.a };
      const b = { kind: among.kind, i: p.b };
      return [
        h('span', { class: 'cx-atlas-val', text: ctx.fmt.number(r + 1) }),
        h('span', { class: 'cx-dist-pair' }, ctx.pageOf(among.kind, p.a), h('span', { class: 'cx-atlas-quiet', text: ' · ' }),
          ctx.pageOf(among.kind, p.b)),
        scoreCell(ctx, p.score),
        togetherCell(ctx, mode === 'together' ? p.texts : 0, 'atlas.dist.together.texts'),
        themeChips(ctx, sharedThemes(ctx.data, a, b)),
        compareLink(ctx, a, b),
      ];
    });
    fill(body, rows.length ? distTable([t('atlas.dist.col.rank'), t('atlas.dist.col.pair'), t(`atlas.similarity.${ctx.measureNow}`),
      t('atlas.dist.col.together'), t('atlas.dist.col.themes'), t('atlas.dist.col.actions')], rows,
    { caption: t(`atlas.dist.pairs.${mode}`), numeric: [0, 2] }) : h('p', { class: 'cx-atlas-note', text: t('atlas.dist.pairs.none') }),
    pager(ctx, page, pages, pairs.length, RANK_PAGE, (pg) => ctx.set({ pg: String(pg) })));
  }

  function csv(mode, among, pairs) {
    const rows = [[t('atlas.dist.col.rank'), t('atlas.dist.col.first'), t('atlas.dist.col.second'),
      t(`atlas.similarity.${ctx.measureNow}`), t('atlas.dist.col.together'), t('atlas.dist.col.themes')]];
    pairs.forEach((p, r) => rows.push([r + 1, ctx.nameOf(among.kind, p.a), ctx.nameOf(among.kind, p.b), Math.round(p.score * 1e4) / 1e4,
      mode === 'together' ? p.texts : 0,
      sharedThemes(ctx.data, { kind: among.kind, i: p.a }, { kind: among.kind, i: p.b }).map(([n]) => ctx.themeName(n)).join('; ')]));
    ctx.download(`pairs-${mode}`, rows);
  }

  function update(state) {
    const mode = PAIR_MODES.includes(state.mode) ? state.mode : 'apart';
    const among = amongOf(ctx, state.among);
    fill(modes, h('span', { class: 'cx-atlas-group', role: 'group', 'aria-label': t('atlas.dist.pairs.which') },
      PAIR_MODES.map((m) => h('button', { type: 'button', class: 'cx-atlas-btn', 'aria-pressed': String(m === mode),
        dataset: { mode: m }, text: t(`atlas.dist.pairs.${m}`), onClick: () => ctx.set({ mode: m }) }))));
    fill(controls,
      distSelect({ label: t('atlas.dist.among'), value: among.kind === 'person' ? 'person' : `organisation:${among.level}`,
        options: amongOptions(ctx), onChange: (v) => ctx.set({ among: v }), key: 'among' }),
      themeSelect(ctx, state.th, (v) => ctx.set({ th: v })));
    fill(head, h('p', { class: 'cx-atlas-note', text: t(`atlas.dist.pairs.${mode}.help`) }));
    const measure = ctx.measureNow;
    const key = [measure, mode, among.kind, among.level, state.th].join('|');
    const show = (result) => {
      if (result.capped) {
        status.textContent = t('atlas.dist.pairs.too_many', { count: result.total, cap: PAIRS_CAP, kind: among.kind });
        fill(body);
        return;
      }
      status.textContent = t('atlas.dist.pairs.count', { count: result.pairs.length, among: result.total, kind: among.kind });
      head.append(csvButton(ctx, () => csv(mode, among, result.pairs)));
      draw(mode, among, result.pairs);
    };
    if (last.key === key && last.result) {
      show(last.result);
      return;
    }
    status.textContent = t('atlas.dist.computing');
    status.setAttribute('aria-busy', 'true');
    fill(body);
    const parts = [`links:${among.kind}`];
    if (measure === 'space') parts.push(`vectors:${among.kind}`);
    work.run(async (signal) => {
      await ctx.need(parts);
      const targets = scopeOf(index, among.kind, { level: among.level, theme: state.th || null });
      if (mode === 'apart' && targets.length > PAIRS_CAP) return { capped: true, total: targets.length };
      let pairs;
      if (!ctx.local(measure)) pairs = await remotePairs(ctx, measure, among.kind, targets, PAIRS_LIMIT, mode);
      else if (mode === 'apart') pairs = await pairsApart(ctx.data, measure, among.kind, targets, PAIRS_LIMIT, signal);
      else pairs = await pairsTogether(ctx.data, measure, among.kind, targets, PAIRS_LIMIT, signal);
      return { pairs, total: targets.length };
    }).then((r) => {
      if (!r) return;
      status.removeAttribute('aria-busy');
      if (r.error) {
        status.textContent = t('atlas.dist.failed');
        return;
      }
      last = { key, result: r.value };
      show(r.value);
    });
  }

  return {
    el,
    update,
    repaint: () => update(ctx.state()),
    destroy: () => work.stop(),
  };
}
