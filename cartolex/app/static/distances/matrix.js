// SPDX-License-Identifier: MIT
/**
 * Matrices, as heatmaps ordered by theme: organisations × organisations (one level) and
 * organisations × themes (their shares); themes × themes (how alike their profiles are);
 * and, for a selection only (an organisation's members, a theme's people, a person and their
 * co-authors), people × people, people × organisations and people × themes. People and
 * organisations are capped at `MATRIX_CAP` (said when passed); never a whole field's people
 * × people. A cell opens Compare (two of a kind), a row's name its ranked list (a theme: the
 * atlas); « Download CSV » saves the matrix.
 */
import { fill, h } from '../atlas/dom.js';
import { createFind } from '../atlas/find.js';
import { crossMatrix, shareMatrix, themeMatrix } from './engine.js';
import { MATRIX_CAP, capped, distRef, scopeOf, selectionOf, themesAt } from './scope.js';
import { amongOf, csvButton, distSelect, runner, statusLine, themeSelect } from './controls.js';
import { createHeatmap } from './heatmap.js';
import { remoteCross } from './remote.js';

/** The matrices of theme shares, which no measure changes. */
export const SHARE_MATRICES = ['orgs_themes', 'people_themes'];

/** The matrices: their rows × columns. */
export const MATRICES = {
  orgs: ['organisation', 'organisation'],
  orgs_themes: ['organisation', 'theme'],
  themes: ['theme', 'theme'],
  people: ['person', 'person'],
  people_orgs: ['person', 'organisation'],
  people_themes: ['person', 'theme'],
};

export function createMatrixView(ctx) {
  const { t, index } = ctx;
  const work = runner();
  const controls = h('div', { class: 'cx-dist__controls' });
  const head = h('div', { class: 'cx-dist__head' });
  const status = statusLine();
  const body = h('div', { class: 'cx-dist__body' });
  const el = h('section', { class: 'cx-dist-view', 'aria-label': t('atlas.dist.view.matrix') }, controls, head, status, body);
  let picker = null;
  let heat = null;
  let last = { key: '', result: null };

  const matrixOf = (state) => (MATRICES[state.mx] ? state.mx : (index.levels.length ? 'orgs' : 'themes'));
  const levelOf = (state) => {
    const among = amongOf(ctx, state.among);
    return among.kind === 'organisation' ? among.level : (index.levels[0] ? index.levels[0].id : '');
  };
  const themeLevelOf = (state) => Math.max(1, Math.min(index.maxLevel, Number.parseInt(state.tl, 10) || 1));
  const known = (ref) => Boolean(ref) && ((ref.kind === 'organisation' && index.byOrg.has(ref.id))
    || (ref.kind === 'theme' && index.nodes.has(ref.id)) || (ref.kind === 'person' && index.byPerson.has(ref.id)));
  const selectionRef = (state) => [distRef(state.in), distRef(state.of)].find(known) || null;

  /** Rows or columns of *kind*: `{kind, items (indexes or theme ids), total}`. */
  function axis(kind, state, selection) {
    if (kind === 'theme') {
      const themes = themesAt(index, themeLevelOf(state));
      return { kind, items: themes, total: themes.length };
    }
    if (kind === 'person') return { kind, ...selection };
    const all = scopeOf(index, 'organisation', { level: levelOf(state), theme: state.th || null });
    return { kind, ...capped(index, 'organisation', all, MATRIX_CAP) };
  }

  function nameAt(ax, k) {
    return ax.kind === 'theme' ? ctx.themeName(ax.items[k]) : ctx.nameOf(ax.kind, ax.items[k]);
  }

  /** The name drawn on an axis: an organisation's acronym when it has one. */
  function shortAt(ax, k) {
    if (ax.kind !== 'organisation') return nameAt(ax, k);
    const o = index.orgs[ax.items[k]];
    return o.acronym || o.name;
  }

  function colourAt(ax, k) {
    if (ax.kind === 'theme') return ctx.colour(ax.items[k]);
    const shares = Object.entries(ax.kind === 'organisation' ? index.orgSharesAt(index.orgs[ax.items[k]].id, 1)
      : (index.people[ax.items[k]].shares || [])[0] || {});
    const top = shares.sort((a, b) => b[1] - a[1])[0];
    return top ? ctx.colour(top[0]) : ctx.colours.neutral;
  }

  function refAt(ax, k) {
    const i = ax.items[k];
    if (ax.kind === 'theme') return { kind: 'theme', id: i };
    return { kind: ax.kind, id: ax.kind === 'person' ? index.people[i].person_id : index.orgs[i].id };
  }

  function controlsOf(state, mx) {
    const [rk, ck] = MATRICES[mx];
    const parts = [distSelect({ label: t('atlas.dist.matrix.what'), value: mx, key: 'matrix',
      options: Object.keys(MATRICES).map((id) => ({ value: id, text: t(`atlas.dist.matrix.${id}`) })), onChange: (v) => ctx.set({ mx: v }) })];
    if ((rk === 'organisation' || ck === 'organisation') && index.levels.length) {
      parts.push(distSelect({ label: t('atlas.dist.level'), value: levelOf(state), key: 'level',
        options: index.levels.map((lv) => ({ value: lv.id, text: ctx.levelName(lv.id) })), onChange: (v) => ctx.set({ among: `organisation:${v}` }) }));
    }
    if (rk === 'organisation' || ck === 'organisation') parts.push(themeSelect(ctx, state.th, (v) => ctx.set({ th: v })));
    if ((rk === 'theme' || ck === 'theme') && index.maxLevel > 1) {
      parts.push(distSelect({ label: t('atlas.dist.theme_level'), value: String(themeLevelOf(state)), key: 'tl',
        options: Array.from({ length: index.maxLevel }, (_, k) => ({ value: String(k + 1), text: t('atlas.dist.theme_level.n', { level: k + 1 }) })),
        onChange: (v) => ctx.set({ tl: v }) }));
    }
    if (rk === 'person') {
      if (!picker) {
        picker = createFind({ t, kinds: ['organisation', 'theme', 'person'], entries: ctx.entries, label: t('atlas.dist.selection'),
          placeholder: t('atlas.dist.selection.placeholder'), onPick: (sel) => ctx.set({ in: `${sel.kind}:${sel.id}` }) });
      }
      parts.push(h('span', { class: 'cx-dist-field' }, h('span', { class: 'cx-dist__label', 'aria-hidden': 'true', text: t('atlas.dist.selection') }), picker.el));
    }
    return parts;
  }

  function selectionWords(ref, total) {
    if (!ref) return '';
    const name = ref.kind === 'theme' ? ctx.themeName(ref.id)
      : ref.kind === 'organisation' ? ctx.nameOf('organisation', index.byOrg.get(ref.id)) : ctx.nameOf('person', index.byPerson.get(ref.id));
    return t(`atlas.dist.selection.${ref.kind}`, { name, count: total });
  }

  function show(state, mx, result) {
    const { rows, cols, values, measure } = result;
    const shares = MATRICES[mx][1] === 'theme' && MATRICES[mx][0] !== 'theme';
    const fmt = shares ? ctx.fmt.percent : ctx.fmt.decimal;
    const notes = [];
    for (const ax of rows === cols ? [rows] : [rows, cols]) {
      if (ax.kind !== 'theme' && ax.total > ax.items.length) {
        notes.push(t('atlas.dist.matrix.capped', { shown: ax.items.length, count: ax.total, kind: ax.kind }));
      }
    }
    status.textContent = [t('atlas.dist.matrix.size', { rows: rows.items.length, cols: cols.items.length }), ...notes].join(' ');
    fill(head, result.selection ? h('h3', { class: 'cx-dist__title', text: result.selection }) : null,
      h('p', { class: 'cx-atlas-note', text: t(shares ? 'atlas.dist.matrix.shares.help'
      : mx === 'themes' ? `atlas.dist.matrix.themes.${measure}` : 'atlas.dist.matrix.help') }),
    csvButton(ctx, () => {
      const out = [['', ...cols.items.map((_, c) => nameAt(cols, c))]];
      rows.items.forEach((_, r) => out.push([nameAt(rows, r), ...cols.items.map((__, c) => {
        const v = values[r * cols.items.length + c];
        return Number.isNaN(v) ? '' : Math.round(v * 1e4) / 1e4;
      })]));
      ctx.download(`matrix-${mx}`, out);
    }));
    if (heat) heat.destroy();
    if (!rows.items.length || !cols.items.length) {
      heat = null;
      fill(body, h('p', { class: 'cx-atlas-note', text: t('atlas.dist.nobody') }));
      return;
    }
    const spec = {
      rows: rows.items.map((_, r) => ({ name: shortAt(rows, r), colour: colourAt(rows, r) })),
      cols: cols.items.map((_, c) => ({ name: shortAt(cols, c), colour: colourAt(cols, c) })),
      values,
      square: rows === cols,
      label: t(`atlas.dist.matrix.${mx}`),
      describe: (r, c) => t('atlas.dist.cell', { row: nameAt(rows, r), col: nameAt(cols, c), value: fmt(values[r * cols.items.length + c]) }),
      onCell: (r, c) => {
        const a = refAt(rows, r);
        const b = refAt(cols, c);
        if (a.kind === b.kind && a.kind !== 'theme' && a.id !== b.id) go(ctx.host.atlas ? ctx.host.atlas(a, b) : null);
        else if (b.kind === 'theme') go(ctx.atlasHref(b));
        else if (a.kind === 'person' && b.kind === 'organisation') ctx.set({ d: 'rank', of: `person:${a.id}`, among: `organisation:${levelOf(state)}` });
      },
      onRow: (r) => {
        const a = refAt(rows, r);
        if (a.kind === 'theme') go(ctx.atlasHref(a));
        else ctx.set({ d: 'rank', of: `${a.kind}:${a.id}`, among: cols.kind === 'organisation' ? `organisation:${levelOf(state)}` : a.kind === 'organisation' ? `organisation:${index.orgs[rows.items[r]].level}` : 'person' });
      },
    };
    heat = createHeatmap(spec, { t, scheme: ctx.scheme, fmt });
    fill(body, heat.el);
  }

  function go(href) {
    if (!href) return;
    if (ctx.host.navigate) ctx.host.navigate(href);
    else window.location.href = href;
  }

  function update(state) {
    const mx = matrixOf(state);
    fill(controls, ...controlsOf(state, mx));
    const [rk, ck] = MATRICES[mx];
    const ref = rk === 'person' ? selectionRef(state) : null;
    if (rk === 'person' && !ref) {
      if (heat) heat.destroy();
      heat = null;
      fill(head, h('p', { class: 'cx-atlas-note', text: t('atlas.dist.selection.empty', { cap: MATRIX_CAP }) }));
      fill(body);
      status.textContent = '';
      return;
    }
    const measure = ctx.measureNow;
    const key = [mx, measure, state.among, state.th, state.tl, ref ? `${ref.kind}:${ref.id}` : ''].join('|');
    if (last.key === key && last.result) {
      show(state, mx, last.result);
      return;
    }
    status.textContent = t('atlas.dist.computing');
    status.setAttribute('aria-busy', 'true');
    const parts = ['links:person'];
    if (measure === 'space') parts.push(...new Set([rk, ck].filter((k) => k !== 'theme').map((k) => `vectors:${k}`)), 'vectors:person');
    work.run(async (signal) => {
      await ctx.need(parts);
      const links = ctx.data.links ? ctx.data.links.person : null;
      const selection = rk === 'person' ? selectionOf(index, links, ref) || { items: [], total: 0 } : null;
      const rows = axis(rk, state, selection);
      const cols = rk === ck ? rows : axis(ck, state, selection);
      let values;
      if (ck === 'theme' && rk !== 'theme') values = shareMatrix(index, rk, rows.items, cols.items, themeLevelOf(state));
      else if (!ctx.local(measure)) values = await remoteCross(ctx, measure, rk, rows.items, ck, cols.items);
      else if (mx === 'themes') values = themeMatrix(ctx.data, measure, rows.items, themeLevelOf(state));
      else values = await crossMatrix(ctx.data, measure, rk, rows.items, ck, cols.items, signal);
      return { rows, cols, values, measure, selection: ref ? selectionWords(ref, rows.total) : '' };
    }).then((r) => {
      if (!r) return;
      status.removeAttribute('aria-busy');
      if (r.error) {
        status.textContent = t('atlas.dist.failed');
        return;
      }
      last = { key, result: r.value };
      show(state, mx, r.value);
    });
  }

  return {
    el,
    update,
    repaint: () => {
      if (last.result) show(ctx.state(), matrixOf(ctx.state()), last.result);
    },
    destroy() {
      work.stop();
      if (heat) heat.destroy();
      if (picker) picker.destroy();
    },
  };
}
