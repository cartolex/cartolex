// SPDX-License-Identifier: MIT
/**
 * The identity queue: each person whose identity waits, with the candidate
 * records every finder proposed (OpenAlex with the ORCID registry as evidence,
 * HAL, SciELO), their score and evidence. Keyboard first:
 *
 *   ↑ ↓   the previous or next person      1–9   pick a candidate
 *   N     none of these (no record exists) ⏎     confirm the picked candidate
 *
 * Every decision is saved at once and the next person comes up. The single
 * clear matches (one candidate, a high score) can be accepted in bulk.
 *
 * The saves go one after the other, each with the version (ETag) the previous
 * one answered; a list read asked before a save neither gives its older version
 * nor brings back a person just decided (a slow machine answers late).
 */
import { html, useEffect, useRef, useState } from '../../core/preact.js';
import { formatNumber, formatPercent, has, t } from '../../core/i18n.js';
import {
  Button, EmptyState, ErrorCard, Input, Select, Table,
} from '../../components/index.js';
import { coded, personName, usePaged } from './common.js';

const FINDERS = ['openalex', 'hal', 'scielo'];

function finderLabel(finder) {
  return has(`corpus.finder.${finder}`) ? t(`corpus.finder.${finder}`) : finder;
}

function defaultPick(person) {
  if (!person || !person.candidates) return -1;
  const clear = person.candidates.findIndex((c) => c.clear);
  if (clear >= 0) return clear;
  return person.candidates.findIndex((c) => c.record);
}

/** One candidate record, numbered for the keyboard. */
function Candidate({ candidate, number, picked, onPick }) {
  const usable = Boolean(candidate.record);
  return html`<li class=${`cx-corpus-cand ${picked ? 'is-picked' : ''} ${usable ? '' : 'is-unusable'}`}>
    <button type="button" class="cx-corpus-cand__pick" aria-pressed=${String(picked)}
      disabled=${!usable} onClick=${onPick}>
      <kbd class="cx-corpus-kbd">${number}</kbd>
      <span class="cx-corpus-cand__name">${candidate.name || candidate.record}</span>
      <span class="cx-corpus-cand__finder">${finderLabel(candidate.finder)}</span>
      ${typeof candidate.score === 'number' ? html`<span class="cx-corpus-cand__score">
        ${t('corpus.identities.score', { score: formatPercent(candidate.score) })}</span>` : null}
      ${candidate.clear ? html`<span class="cx-corpus-chip">${t('corpus.identities.clear')}</span>` : null}
    </button>
    <div class="cx-corpus-cand__body">
      ${candidate.record ? html`<code class="cx-corpus-cand__record">${candidate.record}</code>`
        : html`<p class="cx-corpus-muted">${t('corpus.identities.no_record')}</p>`}
      ${candidate.detail ? html`<p class="cx-corpus-cand__detail">${coded('corpus.detail',
        { code: candidate.detail_code, params: candidate.detail_params, message: candidate.detail })}</p>` : null}
      ${candidate.evidence && candidate.evidence.length ? html`<ul class="cx-corpus-cand__evidence"
        aria-label=${t('corpus.identities.evidence')}>
        ${candidate.evidence.map((e, i) => html`<li key=${i}>${coded('corpus.evidence', { ...e, message: e.text })}${typeof e.points === 'number'
          ? html` <span class="cx-corpus-muted">${t('corpus.identities.points', { points: e.points.toFixed(2) })}</span>` : null}</li>`)}</ul>` : null}
    </div>
  </li>`;
}

/** The identity queue tab. */
export function IdentityQueue({ ctx, version, bump, toast, openSheet, openCollect, canCollect, refresh }) {
  const [filter, setFilter] = useState('');
  const [finder, setFinder] = useState('');
  const [active, setActive] = useState(null);
  const [pick, setPick] = useState(-1);
  const [pasted, setPasted] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const etag = useRef(null);
  const grid = useRef(null);
  const list = usePaged(ctx, '/api/collection/identities', {
    state: 'pending',
    clear: filter === 'clear' ? true : filter === 'unclear' ? false : undefined,
    finder: finder || undefined,
    $v: version,
  }, (row) => row.person_id);
  const data = list.data || {};
  const counts = data.counts || {};
  // The saves, one after the other; when the last one answered; the people decided since.
  const queue = useRef(Promise.resolve());
  const saves = useRef({ pending: 0, answeredAt: -1 });
  const decided = useRef(new Set());
  const fresh = data.etag && saves.current.pending === 0
    && data.$asked > saves.current.answeredAt;
  if (fresh && data.etag !== etag.current) etag.current = data.etag;
  if (fresh && decided.current.size) decided.current = new Set();
  const latest = useRef(list);
  latest.current = list;
  const answered = (result) => {
    saves.current.answeredAt = performance.now();
    if (result.etag) etag.current = result.etag;
  };

  const picked = useRef(-1);
  const choosePick = (i) => {
    picked.current = i;
    setPick(i);
  };
  useEffect(() => {
    choosePick(defaultPick(active));
    setPasted('');
  }, [active && active.person_id]);

  const focusGrid = () => {
    const el = grid.current && grid.current.querySelector('.cx-table__scroller');
    if (el) el.focus({ preventScroll: true });
  };

  // Keys may come before a render: the person and the pending state are read from refs.
  const current = useRef(null);
  const choose = (row) => {
    // A person decided here and still in an older answer of the list is not shown again.
    const next = row && decided.current.has(row.person_id) ? null : row;
    current.current = next;
    setActive(next);
  };

  /** Run *write* after the saves before it; *write* sends `etag.current` as it is then. */
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

  function decide(body) {
    const person = current.current;
    if (!person || decided.current.has(person.person_id)) return;
    decided.current.add(person.person_id);
    // The decided person leaves the queue at once: nothing acts on them any more; the
    // list's next person becomes the active one when the list comes back.
    choose(null);
    setError(null);
    serial(async () => {
      const result = await ctx.api.post(
        `/api/collection/identities/${encodeURIComponent(person.person_id)}`, body,
        { ifMatch: etag.current },
      );
      answered(result);
      if (!result.ok) {
        decided.current.delete(person.person_id);
        setError(result.error);
        latest.current.reload();
        return;
      }
      toast({ kind: 'success', title: t(`corpus.identities.saved.${body.decision}`,
        { name: personName(person) }), timeout: 2500 });
      latest.current.reload();
      refresh();
      focusGrid();
    });
  }
  const confirmPicked = () => {
    const person = current.current;
    const cand = person && person.candidates[picked.current];
    if (cand && cand.record) decide({ decision: 'accept', record: cand.record });
  };

  const acceptClear = () => serial(async () => {
    setError(null);
    const ids = [];
    for (let offset = 0; offset < (counts.clear || 0); offset += 500) {
      const page = await ctx.api.get('/api/collection/identities',
        { query: { state: 'pending', clear: true, offset, limit: 500 } });
      if (!page.ok) {
        setError(page.error);
        return;
      }
      if (page.etag) etag.current = page.etag;
      ids.push(...page.data.items.map((i) => i.person_id));
    }
    let accepted = 0;
    for (let i = 0; i < ids.length; i += 5000) {
      const result = await ctx.api.post('/api/collection/identities/accept',
        { person_ids: ids.slice(i, i + 5000) }, { ifMatch: etag.current });
      answered(result);
      if (!result.ok) {
        setError(result.error);
        break;
      }
      accepted += result.data.accepted.length;
    }
    toast({ kind: 'success', title: t('corpus.identities.accepted', { n: accepted }) });
    bump();
  });

  const onKeyDown = (event) => {
    const target = event.target;
    if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA'
      || target.tagName === 'SELECT')) return;
    const person = current.current;
    if (event.ctrlKey || event.metaKey || event.altKey || !person) return;
    if (/^[1-9]$/.test(event.key)) {
      const i = Number(event.key) - 1;
      if (person.candidates[i] && person.candidates[i].record) choosePick(i);
    } else if (event.key === 'n' || event.key === 'N') {
      decide({ decision: 'none' });
    } else {
      return;
    }
    event.preventDefault();
  };

  const columns = [
    { id: 'name', label: t('corpus.col.name'), width: 'minmax(10rem, 2fr)',
      render: (p) => personName(p) },
    { id: 'unit', label: t('corpus.col.unit'), width: 'minmax(6rem, 1fr)' },
    { id: 'candidates', label: t('corpus.col.candidates'), width: '9rem',
      render: (p) => (p.candidates.some((c) => c.clear)
        ? html`<span class="cx-corpus-chip">${t('corpus.identities.clear')}</span>`
        : t('corpus.identities.count', { n: p.candidates.length })) },
  ];
  const noQueue = !list.loading && !list.total && !filter && !finder;

  return html`<div class="cx-corpus-tab cx-corpus-queue" onKeyDown=${onKeyDown}>
    <div class="cx-corpus-filters" role="group" aria-label=${t('corpus.filters')}>
      <label class="cx-corpus-filters__select"><span class="cx-visually-hidden">${t('corpus.identities.show')}</span>
        <${Select} aria-label=${t('corpus.identities.show')} value=${filter} onChange=${(e) => setFilter(e.currentTarget.value)} options=${[
          { value: '', label: t('corpus.identities.all', { n: (counts.clear || 0) + (counts.unclear || 0) + (counts.no_candidate || 0) }) },
          { value: 'clear', label: t('corpus.identities.only_clear', { n: counts.clear || 0 }) },
          { value: 'unclear', label: t('corpus.identities.only_unclear', { n: (counts.unclear || 0) + (counts.no_candidate || 0) }) },
        ]} /></label>
      <label class="cx-corpus-filters__select"><span class="cx-visually-hidden">${t('corpus.identities.finder')}</span>
        <${Select} aria-label=${t('corpus.identities.finder')} value=${finder} onChange=${(e) => setFinder(e.currentTarget.value)} options=${[
          { value: '', label: t('corpus.identities.any_finder') },
          ...FINDERS.map((f) => ({ value: f, label: finderLabel(f) })),
        ]} /></label>
      <${Button} size="s" variant="primary" icon="check" loading=${busy && !active}
        disabled=${!counts.clear} onClick=${acceptClear}>
        ${t('corpus.identities.accept_clear', { n: counts.clear || 0 })}<//>
      <p class="cx-corpus-keys" aria-hidden="true">${t('corpus.identities.keys')}</p>
    </div>
    ${error ? html`<${ErrorCard} error=${error} compact onDismiss=${() => setError(null)} />` : null}
    ${noQueue ? html`<${EmptyState} icon="check" title=${t('corpus.identities.empty')}
        action=${canCollect ? { label: t('corpus.collect.action.identify'), onClick: () => openCollect('identify') } : null}>
        ${t('corpus.identities.empty_text')}<//>`
      : html`<div class="cx-corpus-split">
      <div class="cx-corpus-split__list" ref=${grid}>
        <${Table} size="fill" label=${t('corpus.tab.identities')} columns=${columns}
          rows=${list.rows} rowKey=${list.rowKey} loading=${list.loading} error=${list.error}
          onRetry=${list.reload} sortMode="server" onRange=${list.onRange}
          onActiveChange=${(row) => choose(row && !row.$pending ? row : null)}
          onActivate=${(row) => !row.$pending && confirmPicked()} />
      </div>
      <section class="cx-corpus-split__panel" aria-live="polite"
        aria-label=${t('corpus.identities.panel')}>
        ${active ? html`
          <header class="cx-corpus-panel__head">
            <h2 class="cx-corpus-panel__title">${personName(active)}</h2>
            <p class="cx-corpus-muted">${[active.unit, active.orcid && `ORCID ${active.orcid}`,
              ...Object.values(active.columns || {})].filter(Boolean).join(' · ')}</p>
            <${Button} size="s" variant="ghost" onClick=${() => openSheet(active.person_id)}>
              ${t('corpus.people.open_sheet')}<//>
          </header>
          ${active.candidates.length ? html`<ol class="cx-corpus-cands">
            ${active.candidates.slice(0, 9).map((c, i) => html`<${Candidate} key=${`${c.finder}-${c.record}-${i}`}
              candidate=${c} number=${i + 1} picked=${i === pick} onPick=${() => choosePick(i)} />`)}
          </ol>` : html`<p class="cx-corpus-muted">${t('corpus.identities.none_found')}</p>`}
          <div class="cx-corpus-panel__actions">
            <${Button} variant="primary" icon="check" loading=${busy}
              disabled=${pick < 0 || !active.candidates[pick]} onClick=${confirmPicked}>
              ${t('corpus.identities.confirm')} <kbd class="cx-corpus-kbd">⏎</kbd><//>
            <${Button} onClick=${() => decide({ decision: 'none' })} disabled=${busy}>
              ${t('corpus.identities.none')} <kbd class="cx-corpus-kbd">${t('corpus.identities.key_none')}</kbd><//>
          </div>
          <form class="cx-corpus-paste" onSubmit=${(e) => {
            e.preventDefault();
            if (pasted.trim()) decide({ decision: 'id', record: pasted.trim() });
          }}>
            <label class="cx-field__label" for="cx-corpus-paste">${t('corpus.identities.paste')}</label>
            <div class="cx-corpus-paste__row">
              <${Input} id="cx-corpus-paste" value=${pasted} placeholder=${t('corpus.identities.paste_example')}
                onInput=${(e) => setPasted(e.currentTarget.value)} />
              <${Button} type="submit" disabled=${!pasted.trim() || busy}>${t('corpus.identities.use_record')}<//>
            </div>
          </form>`
        : html`<p class="cx-corpus-muted">${list.total ? t('corpus.identities.choose')
          : t('corpus.identities.nobody')}</p>`}
      </section>
    </div>`}
    <p class="cx-corpus-muted">${t('corpus.identities.waiting', { n: formatNumber(list.total) })}</p>
  </div>`;
}
