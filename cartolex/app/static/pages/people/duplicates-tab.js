// SPDX-License-Identifier: MIT
/**
 * The Duplicates tab (`/people?tab=duplicates`): groups of people who may all be
 * one person (two, or three records of one person and more), the most likely
 * first, compared side by side from the project's own data. Keyboard first:
 *
 *   ↑ ↓     the previous or next group   1…9   one person: keep that one, merge the ticked
 *   ⇧1…⇧9   tick or untick that one      D     two people (every one, or the unticked apart)
 *   L       later
 *
 * Every decision is saved at once, one after the other (each with the version the
 * previous one answered), and the next group comes up; a merge is undone in one
 * step. « Merge the clear pairs » and « Merge above a likelihood… »
 * (`duplicates-auto.js`) join the pairs into groups the same way.
 */
import { html, useEffect, useRef, useState } from '../../core/preact.js';
import { formatDate, formatNumber, formatPercent, t } from '../../core/i18n.js';
import {
  Button, ConfirmDialog, EmptyState, ErrorCard, Input, Select, Table,
} from '../../components/index.js';
import { usePaged } from './common.js';
import { GroupCompare } from './duplicates-compare.js';
import { AutoMerge } from './duplicates-auto.js';

const SHOWS = ['open', 'clear', 'later', 'all'];

/** Whether two of *people* (briefs with `orcids`) carry different ORCIDs. */
export function orcidsConflict(people) {
  const sets = people.filter((p) => (p.orcids || []).length).map((p) => p.orcids);
  return sets.some((x, i) => sets.slice(i + 1).some((y) => !x.some((o) => y.includes(o))));
}

/** The Duplicates tab. */
export function DuplicatesTab({ ctx, version, bump, toast, openSheet, refresh }) {
  const [show, setShow] = useState('open');
  const [q, setQ] = useState('');
  const [active, setActive] = useState(null);
  const [compare, setCompare] = useState(null);
  const [keep, setKeep] = useState(null);
  const [chosen, setChosen] = useState(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [auto, setAuto] = useState(false);
  const [asking, setAsking] = useState(null); // a merge of two ORCIDs to confirm: {keep}
  const list = usePaged(ctx, '/api/people/duplicates/groups', { show, q: q || undefined, $v: version },
    (row) => row.key);
  const data = list.data || {};
  const counts = data.counts || {};
  const etag = useRef(null);
  const saves = useRef({ pending: 0, answeredAt: -1 });
  const decided = useRef(new Set());
  const queue = useRef(Promise.resolve());
  const current = useRef(null);
  const ticks = useRef(new Set());
  ticks.current = chosen;
  const grid = useRef(null);
  const fresh = data.etag && saves.current.pending === 0 && data.$asked > saves.current.answeredAt;
  if (fresh && data.etag !== etag.current) etag.current = data.etag;
  if (fresh && decided.current.size) decided.current = new Set();
  const latest = useRef(list);
  latest.current = list;

  const choose = (row) => {
    const next = row && !row.$pending && !decided.current.has(row.key) ? row : null;
    current.current = next;
    setActive(next);
    setKeep(next ? next.keep : null);
    setChosen(new Set(next ? next.ids : []));
  };
  useEffect(() => {
    setCompare(null);
    if (!active) return;
    ctx.api.get('/api/people/duplicates/group', { query: { ids: active.ids.join(',') } })
      .then((r) => {
        if (current.current && current.current.key === active.key) {
          if (r.ok) setCompare(r.data);
          else setError(r.error);
        }
      });
  }, [active && active.key]);

  const focusGrid = () => {
    const el = grid.current && grid.current.querySelector('.cx-table__scroller');
    if (el) el.focus({ preventScroll: true });
  };

  function serial(write) {
    saves.current.pending += 1;
    setBusy(true);
    const run = queue.current.then(write).finally(() => {
      saves.current.pending -= 1;
      if (!saves.current.pending) setBusy(false);
    });
    queue.current = run.catch(() => {});
    return run;
  }

  async function unmerge(ids, remember = null) {
    setError(null);
    const people = await ctx.api.get('/api/people', { query: { limit: 1 } });
    const body = remember ? { person_ids: ids, remember } : { person_ids: ids };
    const result = await ctx.api.post('/api/people/unmerge', body, { ifMatch: people.etag });
    if (!result.ok) {
      setError(result.error);
      return;
    }
    toast({ kind: 'success', title: t('corpus.dup.auto.undone', { n: result.data.unmerged.length }) });
    bump();
  }

  /** Decide the group in view: merge the ticked into *into*, two people, or later. */
  function decide(decision, into = null, override = false) {
    const group = current.current;
    if (!group || decided.current.has(group.key)) return;
    const ticked = group.size > 2 ? ticks.current : new Set(group.ids);
    const ids = group.ids.filter((id) => ticked.has(id) || id === into);
    if (decision === 'merge' && ids.length < 2) return;
    const apart = decision === 'distinct' && ids.length < group.ids.length && ids.length
      ? group.ids.filter((id) => !ticked.has(id)) : [];
    if (decision === 'merge' && !override
      && orcidsConflict(group.people.filter((p) => ids.includes(p.person_id)))) {
      setAsking({ keep: into });
      return;
    }
    decided.current.add(group.key);
    choose(null);
    setError(null);
    const body = { ids: decision === 'merge' ? ids : group.ids, decision,
      keep: decision === 'merge' ? into : null, apart, override };
    serial(async () => {
      const result = await ctx.api.post('/api/people/duplicates/group', body, { ifMatch: etag.current });
      saves.current.answeredAt = performance.now();
      if (result.etag) etag.current = result.etag;
      if (!result.ok) {
        decided.current.delete(group.key);
        setError(result.error);
        latest.current.reload();
        return;
      }
      const names = group.people.filter((p) => body.ids.includes(p.person_id)).map((p) => p.name);
      const merged = result.data.merged || [];
      toast({ kind: 'success', timeout: merged.length ? 8000 : 2500,
        title: t(`corpus.dup.saved.${decision}`, { names, n: names.length }),
        action: merged.length ? { label: t('corpus.dup.auto.undo'), onClick: () => unmerge(merged) }
          : undefined });
      latest.current.reload();
      refresh();
      focusGrid();
    });
  }

  function toggle(id) {
    const next = new Set(ticks.current);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setChosen(next);
  }

  const onKeyDown = (event) => {
    const target = event.target;
    if (target && ['TEXTAREA', 'SELECT'].includes(target.tagName)) return;
    if (target && target.tagName === 'INPUT' && !['checkbox', 'radio'].includes(target.type)) return;
    const group = current.current;
    if (event.ctrlKey || event.metaKey || event.altKey || !group) return;
    // The digits by their place on the keyboard (⇧1 is « ! » on some layouts).
    const digit = /^Digit([1-9])$/.exec(event.code || '') || /^([1-9])$/.exec(event.key);
    const key = event.key.toLowerCase();
    if (digit) {
      const id = group.ids[Number(digit[1]) - 1];
      if (!id) return;
      if (event.shiftKey) {
        if (group.size > 2) toggle(id);
      } else decide('merge', id);
    } else if (key === t('corpus.dup.key_distinct').toLowerCase()) decide('distinct');
    else if (key === t('corpus.dup.key_later').toLowerCase()) decide('later');
    else return;
    event.preventDefault();
  };

  const columns = [
    { id: 'names', label: t('corpus.dup.col.pair'), width: 'minmax(12rem, 3fr)',
      render: (r) => html`${r.size > 2 ? html`<span class="cx-corpus-count">${formatNumber(r.size)}</span> ` : null}${
        r.people.map((p, i) => html`<span key=${p.person_id}>${i ? html`<span class="cx-corpus-muted"> · </span>` : null}<span
          class="cx-corpus-name">${p.name}</span></span>`)}` },
    { id: 'score', label: t('corpus.dup.col.score'), width: '6rem', align: 'end',
      render: (r) => formatPercent(r.score) },
    { id: 'state', label: t('corpus.dup.col.state'), width: '8rem',
      render: (r) => (r.conflict ? html`<span class="cx-corpus-chip cx-dup-chip--conflict">${t('corpus.dup.conflict')}</span>`
        : r.decision === 'later' ? html`<span class="cx-corpus-chip">${t('corpus.dup.later')}</span>`
          : r.clear ? html`<span class="cx-corpus-chip">${t('corpus.dup.clear')}</span>` : '') },
  ];
  const noGroups = !list.loading && !list.total && show === 'open' && !q;
  const last = data.last_auto;
  const common = data.common_names || [];
  const several = active && active.size > 2;
  const ticked = active ? active.ids.filter((id) => chosen.has(id) || id === keep) : [];
  const keptName = active && keep ? (active.people.find((p) => p.person_id === keep) || {}).name : '';
  const partial = several && active.ids.some((id) => !chosen.has(id));

  return html`<div class="cx-corpus-tab cx-corpus-queue" onKeyDown=${onKeyDown}>
    <div class="cx-corpus-filters" role="group" aria-label=${t('corpus.filters')}>
      <label class="cx-corpus-filters__search"><span class="cx-visually-hidden">${t('corpus.dup.search')}</span>
        <${Input} type="search" value=${q} placeholder=${t('corpus.dup.search')}
          onInput=${(e) => setQ(e.currentTarget.value)} /></label>
      <label class="cx-corpus-filters__select"><span class="cx-visually-hidden">${t('corpus.dup.show')}</span>
        <${Select} aria-label=${t('corpus.dup.show')} value=${show}
          onChange=${(e) => setShow(e.currentTarget.value)}
          options=${SHOWS.map((s) => ({ value: s, label: t(`corpus.dup.show_${s}`,
            { n: s === 'all' ? (counts.open || 0) + (counts.later || 0) : counts[s] || 0 }) }))} /></label>
      <${Button} size="s" variant="primary" icon="check" disabled=${!counts.clear}
        onClick=${() => setAuto('clear')}>${t('corpus.dup.auto.button', { n: counts.clear || 0 })}<//>
      <${Button} size="s" variant="secondary" disabled=${!counts.open}
        onClick=${() => setAuto('above')}>${t('corpus.dup.above.button')}<//>
      <p class="cx-corpus-keys" aria-hidden="true">${t('corpus.dup.keys')}</p>
    </div>
    ${last ? html`<p class="cx-corpus-note cx-dup-last" role="status">
      ${t('corpus.dup.auto.last', { n: last.person_ids.length, at: formatDate(last.at, 'datetime', 'short') })}
      <${Button} size="s" variant="ghost" icon="undo" onClick=${() => unmerge(last.person_ids, 'later')}>
        ${t('corpus.dup.auto.undo')}<//></p>` : null}
    ${common.length ? html`<p class="cx-corpus-note" role="note">${t('corpus.dup.common_names', {
      n: common.length, names: common.slice(0, 5).map((c) => t('corpus.dup.common_name',
        { name: c.name, n: c.people })).join(' · ') })}</p>` : null}
    ${error ? html`<${ErrorCard} error=${error} compact onDismiss=${() => setError(null)} />` : null}
    ${noGroups ? html`<${EmptyState} icon="check" title=${t('corpus.dup.empty')}>${t('corpus.dup.empty_text')}<//>`
      : html`<div class="cx-corpus-split cx-dup-split">
      <div class="cx-corpus-split__list" ref=${grid}>
        <${Table} size="fill" label=${t('corpus.tab.duplicates')} columns=${columns}
          rows=${list.rows} rowKey=${list.rowKey} loading=${list.loading} error=${list.error}
          onRetry=${list.reload} sortMode="server" onRange=${list.onRange}
          onActiveChange=${choose}
          empty=${html`<${EmptyState} icon="file" title=${t('corpus.empty.empty_no_match')} />`} />
      </div>
      <section class="cx-corpus-split__panel" aria-live="polite" aria-label=${t('corpus.dup.panel')}>
        ${active ? html`
          <header class="cx-corpus-panel__head">
            <h2 class="cx-corpus-panel__title">${t('corpus.dup.compare_title', { n: active.size })}</h2>
          </header>
          <${GroupCompare} people=${active.people} compare=${compare} pairs=${active.pairs}
            openSheet=${openSheet} keep=${several ? keep : null} onKeep=${several ? setKeep : null}
            name=${`cx-dup-keep-${active.key}`} chosen=${several ? chosen : null}
            onToggle=${several ? toggle : null} numbered=${true} />
          <div class="cx-corpus-panel__actions cx-dup-actions">
            ${several ? html`<${Button} size="s" variant="primary" loading=${busy}
              disabled=${ticked.length < 2} onClick=${() => decide('merge', keep)}>
              ${t('corpus.dup.merge_group', { n: ticked.length, name: keptName })}<//>`
            : active.people.map((p, i) => html`<${Button} key=${p.person_id} size="s"
              variant=${p.person_id === active.keep ? 'primary' : 'secondary'} loading=${busy && i === 0}
              disabled=${busy && i > 0} onClick=${() => decide('merge', p.person_id)}>
              ${t('corpus.dup.keep', { name: p.name })} <kbd class="cx-corpus-kbd">${i + 1}</kbd><//>`)}
            <${Button} size="s" disabled=${busy || (partial && !active.ids.some((id) => chosen.has(id)))}
              onClick=${() => decide('distinct')}>
              ${!several ? t('corpus.dup.distinct') : partial ? t('corpus.dup.set_apart')
                : t('corpus.dup.all_distinct')} <kbd class="cx-corpus-kbd">${t('corpus.dup.key_distinct')}</kbd><//>
            <${Button} size="s" variant="ghost" disabled=${busy} onClick=${() => decide('later')}>
              ${t('corpus.dup.later_action')} <kbd class="cx-corpus-kbd">${t('corpus.dup.key_later')}</kbd><//>
          </div>`
        : html`<p class="cx-corpus-muted">${list.total ? t('corpus.dup.choose') : t('corpus.dup.nothing')}</p>`}
      </section>
    </div>`}
    <p class="cx-corpus-muted">${t('corpus.dup.waiting', { n: formatNumber(list.total),
      people: formatNumber(counts.people || 0) })}</p>
    ${auto ? html`<${AutoMerge} ctx=${ctx} etag=${() => etag.current} above=${auto === 'above'}
      onClose=${() => setAuto(false)}
      onDone=${(result) => {
        setAuto(false);
        toast({ kind: 'success', title: t('corpus.dup.auto.done', { n: result.data.merged }),
          action: { label: t('corpus.dup.auto.undo'), onClick: () => unmerge(result.data.person_ids, 'later') } });
        bump();
      }} />` : null}
    ${asking ? html`<${ConfirmDialog} open=${true} danger title=${t('corpus.dup.conflict_title')}
      confirmLabel=${t('corpus.dup.conflict_confirm')}
      onAnswer=${(yes) => {
        const { keep: into } = asking;
        setAsking(null);
        if (yes) decide('merge', into, true);
      }}>${t('corpus.dup.conflict_text')}<//>` : null}
  </div>`;
}
