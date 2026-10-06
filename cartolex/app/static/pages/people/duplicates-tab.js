// SPDX-License-Identifier: MIT
/**
 * The Duplicates tab (`/people?tab=duplicates`): pairs of people who may be one
 * person, the most likely first, each compared side by side from the
 * project's own data. Keyboard first:
 *
 *   ↑ ↓   the previous or next pair       1 / 2   one person, keep the left / right
 *   D     two people (never proposed again)   L   later
 *
 * Every decision is saved at once, one after the other (each with the version
 * the previous one answered), and the next pair comes up. « Merge the clear
 * pairs » shows what it would do first, then merges them in one step that one
 * « Undo » takes back.
 */
import { html, useEffect, useRef, useState } from '../../core/preact.js';
import { formatDate, formatNumber, formatPercent, t } from '../../core/i18n.js';
import {
  Button, ConfirmDialog, Dialog, EmptyState, ErrorCard, Input, Select, Table,
} from '../../components/index.js';
import { usePaged } from './common.js';
import { evidenceText, keepFirst, PairCompare } from './duplicates-compare.js';

const SHOWS = ['open', 'clear', 'later', 'all'];

/** The preview of the automatic merge, then the merge itself. */
function AutoMerge({ ctx, etag, onClose, onDone }) {
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    ctx.api.post('/api/people/duplicates/auto', {}).then((r) => (r.ok ? setPreview(r.data)
      : setError(r.error)));
  }, []);
  async function apply() {
    setBusy(true);
    const result = await ctx.api.post('/api/people/duplicates/auto', { apply: true },
      { ifMatch: etag() });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    onDone(result);
  }
  const none = preview && !preview.merged;
  return html`<${Dialog} open=${true} onClose=${onClose} size="l"
    title=${t('corpus.dup.auto.title')}
    description=${t('corpus.dup.auto.rule')}
    footer=${html`<${Button} onClick=${onClose}>${t('common.cancel')}<//>
      <${Button} variant="primary" icon="check" loading=${busy} disabled=${!preview || none}
        onClick=${apply}>${t('corpus.dup.auto.apply', { n: (preview && preview.merged) || 0 })}<//>`}>
    ${error ? html`<${ErrorCard} error=${error} compact />` : null}
    ${!preview && !error ? html`<p class="cx-corpus-muted">${t('common.loading')}</p>` : null}
    ${preview ? html`<div class="cx-dup-auto">
      <p><strong>${none ? t('corpus.dup.auto.none') : t('corpus.dup.auto.count', {
        n: preview.merged, groups: preview.groups })}</strong></p>
      ${preview.examples.length ? html`<h3 class="cx-corpus-h3">${t('corpus.dup.auto.examples')}</h3>
        <ul class="cx-corpus-list cx-dup-auto__list">${preview.examples.map((g) => html`<li key=${g.keep.person_id}>
          <span>${t('corpus.dup.auto.example', { keep: g.keep.name,
            others: g.merge.map((m) => m.name).join(', ') })}</span>
          <span class="cx-corpus-muted"> ${g.evidence.filter((e) => e.points > 0).slice(0, 3)
            .map(evidenceText).join(' · ')}</span></li>`)}</ul>` : null}
      <p class="cx-corpus-muted">${t('corpus.dup.auto.undo_note')}</p>
    </div>` : null}
  <//>`;
}

/** The Duplicates tab. */
export function DuplicatesTab({ ctx, version, bump, toast, openSheet, refresh }) {
  const [show, setShow] = useState('open');
  const [q, setQ] = useState('');
  const [active, setActive] = useState(null);
  const [compare, setCompare] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [auto, setAuto] = useState(false);
  const [asking, setAsking] = useState(null); // a merge of two ORCIDs to confirm: {keep}
  const list = usePaged(ctx, '/api/people/duplicates', { show, q: q || undefined, $v: version },
    (row) => row.key);
  const data = list.data || {};
  const counts = data.counts || {};
  const etag = useRef(null);
  const saves = useRef({ pending: 0, answeredAt: -1 });
  const decided = useRef(new Set());
  const queue = useRef(Promise.resolve());
  const current = useRef(null);
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
  };
  useEffect(() => {
    setCompare(null);
    if (!active) return;
    ctx.api.get('/api/people/duplicates/compare', { query: { a: active.a, b: active.b } })
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

  function decide(decision, keep = null, override = false) {
    const pair = current.current;
    if (!pair || decided.current.has(pair.key)) return;
    if (decision === 'merge' && pair.conflict && !override) {
      setAsking({ keep });
      return;
    }
    decided.current.add(pair.key);
    choose(null);
    setError(null);
    serial(async () => {
      const body = { a: pair.a, b: pair.b, decision, keep, override };
      const result = await ctx.api.post('/api/people/duplicates/decide', body,
        { ifMatch: etag.current });
      saves.current.answeredAt = performance.now();
      if (result.etag) etag.current = result.etag;
      if (!result.ok) {
        decided.current.delete(pair.key);
        setError(result.error);
        latest.current.reload();
        return;
      }
      const names = pair.people.map((p) => p.name);
      toast({ kind: 'success', timeout: 2500, title: t(`corpus.dup.saved.${decision}`,
        { a: names[0], b: names[1] }) });
      latest.current.reload();
      refresh();
      focusGrid();
    });
  }

  const onKeyDown = (event) => {
    const target = event.target;
    if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
    const pair = current.current;
    if (event.ctrlKey || event.metaKey || event.altKey || !pair) return;
    // The letters are the interface language's (« D », « L » in English).
    const key = event.key.toLowerCase();
    if (key === '1') decide('merge', pair.a);
    else if (key === '2') decide('merge', pair.b);
    else if (key === t('corpus.dup.key_distinct').toLowerCase()) decide('distinct');
    else if (key === t('corpus.dup.key_later').toLowerCase()) decide('later');
    else return;
    event.preventDefault();
  };

  async function undoAuto(ids) {
    setError(null);
    const people = await ctx.api.get('/api/people', { query: { limit: 1 } });
    const result = await ctx.api.post('/api/people/unmerge', { person_ids: ids, remember: 'later' },
      { ifMatch: people.etag });
    if (!result.ok) {
      setError(result.error);
      return;
    }
    toast({ kind: 'success', title: t('corpus.dup.auto.undone', { n: result.data.unmerged.length }) });
    bump();
  }

  const columns = [
    { id: 'names', label: t('corpus.dup.col.pair'), width: 'minmax(12rem, 3fr)',
      render: (r) => html`<span class="cx-corpus-name">${r.people[0].name}</span>
        <span class="cx-corpus-muted"> · </span><span class="cx-corpus-name">${r.people[1].name}</span>` },
    { id: 'score', label: t('corpus.dup.col.score'), width: '6rem', align: 'end',
      render: (r) => formatPercent(r.score) },
    { id: 'state', label: t('corpus.dup.col.state'), width: '8rem',
      render: (r) => (r.conflict ? html`<span class="cx-corpus-chip cx-dup-chip--conflict">${t('corpus.dup.conflict')}</span>`
        : r.decision === 'later' ? html`<span class="cx-corpus-chip">${t('corpus.dup.later')}</span>`
          : r.clear ? html`<span class="cx-corpus-chip">${t('corpus.dup.clear')}</span>` : '') },
  ];
  const noPairs = !list.loading && !list.total && show === 'open' && !q;
  const last = data.last_auto;
  const keepFirstSide = active ? keepFirst(active) : true;

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
        onClick=${() => setAuto(true)}>${t('corpus.dup.auto.button', { n: counts.clear || 0 })}<//>
      <p class="cx-corpus-keys" aria-hidden="true">${t('corpus.dup.keys')}</p>
    </div>
    ${last ? html`<p class="cx-corpus-note cx-dup-last" role="status">
      ${t('corpus.dup.auto.last', { n: last.person_ids.length, at: formatDate(last.at, 'datetime', 'short') })}
      <${Button} size="s" variant="ghost" icon="undo" onClick=${() => undoAuto(last.person_ids)}>
        ${t('corpus.dup.auto.undo')}<//></p>` : null}
    ${error ? html`<${ErrorCard} error=${error} compact onDismiss=${() => setError(null)} />` : null}
    ${noPairs ? html`<${EmptyState} icon="check" title=${t('corpus.dup.empty')}>${t('corpus.dup.empty_text')}<//>`
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
          <${PairCompare} pair=${active} compare=${compare} openSheet=${openSheet} />
          <div class="cx-corpus-panel__actions cx-dup-actions">
            <${Button} size="s" variant=${keepFirstSide ? 'primary' : 'secondary'} loading=${busy}
              onClick=${() => decide('merge', active.a)}>
              ${t('corpus.dup.keep', { name: active.people[0].name })} <kbd class="cx-corpus-kbd">1</kbd><//>
            <${Button} size="s" variant=${keepFirstSide ? 'secondary' : 'primary'} disabled=${busy}
              onClick=${() => decide('merge', active.b)}>
              ${t('corpus.dup.keep', { name: active.people[1].name })} <kbd class="cx-corpus-kbd">2</kbd><//>
            <${Button} size="s" disabled=${busy} onClick=${() => decide('distinct')}>
              ${t('corpus.dup.distinct')} <kbd class="cx-corpus-kbd">${t('corpus.dup.key_distinct')}</kbd><//>
            <${Button} size="s" variant="ghost" disabled=${busy} onClick=${() => decide('later')}>
              ${t('corpus.dup.later_action')} <kbd class="cx-corpus-kbd">${t('corpus.dup.key_later')}</kbd><//>
          </div>`
        : html`<p class="cx-corpus-muted">${list.total ? t('corpus.dup.choose') : t('corpus.dup.nothing')}</p>`}
      </section>
    </div>`}
    <p class="cx-corpus-muted">${t('corpus.dup.waiting', { n: formatNumber(list.total) })}</p>
    ${auto ? html`<${AutoMerge} ctx=${ctx} etag=${() => etag.current}
      onClose=${() => setAuto(false)}
      onDone=${(result) => {
        setAuto(false);
        toast({ kind: 'success', title: t('corpus.dup.auto.done', { n: result.data.merged }),
          action: { label: t('corpus.dup.auto.undo'), onClick: () => undoAuto(result.data.person_ids) } });
        bump();
      }} />` : null}
    ${asking ? html`<${ConfirmDialog} open=${true} danger title=${t('corpus.dup.conflict_title')}
      confirmLabel=${t('corpus.dup.conflict_confirm')}
      onAnswer=${(yes) => {
        const { keep } = asking;
        setAsking(null);
        if (yes) decide('merge', keep, true);
      }}>${t('corpus.dup.conflict_text')}<//>` : null}
  </div>`;
}
