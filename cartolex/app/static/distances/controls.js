// SPDX-License-Identifier: MIT
/**
 * The pieces Distances' views are made of: labelled selects (who is compared, a theme, a
 * level), the table of a list with its similarity bars, theme chips and « Compare » links, a
 * pager, the status line and « Download CSV ».
 */
import { h, swatch, uid } from '../atlas/dom.js';
import { themesAt } from './scope.js';

/** A labelled select: `options` are `{value, text}` or `{group, options}`. */
export function distSelect({ label, value, options, onChange, key }) {
  const id = uid('cx-dist-select');
  const option = (o) => h('option', { value: o.value, selected: o.value === value, text: o.text });
  return h('span', { class: 'cx-dist-field' },
    h('label', { for: id, class: 'cx-dist__label', text: label }),
    h('select', { id, dataset: { key: key || '' }, onChange: (e) => onChange(e.currentTarget.value) },
      options.map((o) => (o.group ? h('optgroup', { label: o.group }, o.options.map(option)) : option(o)))));
}

/** The kinds one can compare among: `person`, or `organisation:<level>` for each level. */
export function amongOptions(ctx) {
  const { t, index } = ctx;
  return [{ value: 'person', text: t('atlas.dist.among.people') },
    ...index.levels.map((lv) => ({ value: `organisation:${lv.id}`, text: t('atlas.dist.among.orgs', { level: ctx.levelName(lv.id) }) }))];
}

/** What `among` names: `{kind, level}` (people when it names nothing known). */
export function amongOf(ctx, value) {
  const [kind, level] = String(value || '').split(':');
  if (kind === 'organisation' && ctx.index.levels.some((lv) => lv.id === level)) return { kind, level };
  return { kind: 'person', level: null };
}

/** A select of the themes (any, or one of the tree's, by top-level theme). */
export function themeSelect(ctx, value, onChange, label) {
  const { t, index } = ctx;
  const deeper = index.maxLevel > 1;
  const options = [{ value: '', text: t('atlas.dist.theme.any') }];
  for (const top of index.tops) {
    if (!deeper) options.push({ value: top, text: ctx.themeName(top) });
    else {
      const under = themesAt(index, 2).filter((id) => index.topOf.get(id) === top);
      options.push({ group: ctx.themeName(top), options: [{ value: top, text: t('atlas.dist.theme.whole', { name: ctx.themeName(top) }) },
        ...under.map((id) => ({ value: id, text: ctx.themeName(id) }))] });
    }
  }
  return distSelect({ label: label || t('atlas.dist.theme'), value, options, onChange, key: 'theme' });
}

/** A similarity as a number and a bar (its length from 0 to 1). */
export function scoreCell(ctx, v) {
  const bar = Number.isNaN(v) ? null : h('span', { class: 'cx-dist-bar', 'aria-hidden': 'true' },
    h('span', { class: 'cx-dist-bar__fill', vars: { '--cx-share': `${Math.round(Math.max(0, Math.min(1, v)) * 100)}%` } }));
  return h('span', { class: 'cx-dist-score' }, h('span', { class: 'cx-atlas-val', text: ctx.fmt.decimal(v) }), bar);
}

/** The themes two share most, as small chips in their colours. */
export function themeChips(ctx, list) {
  if (!list.length) return h('span', { class: 'cx-atlas-quiet', text: '—' });
  return h('span', { class: 'cx-dist-themes' }, list.map(([node, v]) => h('span', { class: 'cx-dist-theme',
    title: `${ctx.themeName(node)} · ${ctx.fmt.percent(v)}` }, swatch(ctx.colour(node)), ctx.themeName(node))));
}

/** « Compare » for two items (`{kind, i}`): the host's atlas with both, or nothing. */
export function compareLink(ctx, a, b) {
  const href = ctx.compareHref(a, b);
  return href ? h('a', { href, class: 'cx-atlas-btn cx-dist-compare', text: ctx.t('atlas.dist.compare') }) : null;
}

/** Whether they write together, in words: *n* (null: unknown). */
export function togetherCell(ctx, n, key) {
  if (n === null || n === undefined) return h('span', { class: 'cx-atlas-quiet', text: '—' });
  if (!n) return h('span', { class: 'cx-atlas-quiet', text: ctx.t('atlas.dist.together.none') });
  return h('span', { class: 'cx-dist-together', text: ctx.t(key, { count: n }) });
}

/** A table: *heads* (catalogue words) and *rows* (arrays of nodes); `numeric` columns align right. */
export function distTable(heads, rows, { caption, numeric = [] } = {}) {
  return h('div', { class: 'cx-dist-table-wrap' }, h('table', { class: 'cx-dist-table' },
    caption ? h('caption', { class: 'cx-visually-hidden', text: caption }) : null,
    h('thead', {}, h('tr', {}, heads.map((text, k) => h('th', { scope: 'col', class: numeric.includes(k) ? 'is-num' : '', text })))),
    h('tbody', {}, rows.map((cells) => h('tr', {}, cells.map((c, k) => h(k === 1 ? 'th' : 'td',
      { scope: k === 1 ? 'row' : null, class: numeric.includes(k) ? 'is-num' : '' }, c)))))));
}

/** The pager of a list: « Previous », « rows a–b of n », « Next ». */
export function pager(ctx, page, pages, total, size, onPage) {
  const { t, fmt } = ctx;
  if (pages <= 1) return null;
  return h('nav', { class: 'cx-dist-pager', 'aria-label': t('atlas.dist.pages') },
    h('button', { type: 'button', class: 'cx-atlas-btn', disabled: page <= 1, dataset: { key: 'prev' },
      text: `← ${t('atlas.dist.previous')}`, onClick: () => onPage(page - 1) }),
    h('span', { class: 'cx-atlas-note', text: t('atlas.dist.rows', { from: fmt.number((page - 1) * size + 1),
      to: fmt.number(Math.min(total, page * size)), count: total }) }),
    h('button', { type: 'button', class: 'cx-atlas-btn', disabled: page >= pages, dataset: { key: 'next' },
      text: `${t('atlas.dist.next')} →`, onClick: () => onPage(page + 1) }));
}

/** « Download CSV », busy while there is nothing to save. */
export function csvButton(ctx, onClick) {
  return h('button', { type: 'button', class: 'cx-atlas-btn', dataset: { key: 'csv' }, text: `↓ ${ctx.t('atlas.dist.csv')}`, onClick });
}

/** The status line of a view (polite live region). */
export function statusLine() {
  return h('p', { class: 'cx-atlas-note cx-dist__status', role: 'status', 'aria-live': 'polite' });
}

/** A view's abortable computation: `run(fn)` aborts the one before and calls `fn(signal)`;
 * answers whether this run is still the current one when it ends. */
export function runner() {
  let ctrl = null;
  return {
    run(fn) {
      if (ctrl) ctrl.abort();
      const mine = new AbortController();
      ctrl = mine;
      return Promise.resolve().then(() => fn(mine.signal)).then((value) => (mine.signal.aborted ? null : { value }),
        (error) => (mine.signal.aborted ? null : { error }));
    },
    stop() {
      if (ctrl) ctrl.abort();
    },
  };
}
