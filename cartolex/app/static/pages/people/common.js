// SPDX-License-Identifier: MIT
/**
 * What the corpus screen's modules share: the shapes of coverage and identity
 * states (by shape, like the build's states), names, and a paged list read
 * from the server a window at a time.
 */
import { html, useEffect, useRef, useState } from '../../core/preact.js';
import { formatNumber, has, t } from '../../core/i18n.js';
import { StatusDot } from '../../components/index.js';

/** Coverage states → the build state whose shape draws them. */
const COVERAGE_SHAPE = { good: 'up_to_date', thin: 'needs_update', no_data: 'never_built',
  failed: 'failed', none: 'never_built' };
/** Identity states → shapes: confirmed full, auto half, pending empty, none a dash. */
const IDENTITY_SHAPE = { confirmed: 'up_to_date', auto: 'needs_update', pending: 'never_built',
  none: 'skipped' };

/** A coverage state: its shape and word. */
export function CoverageState({ state }) {
  if (!state) return null;
  return html`<${StatusDot} state=${COVERAGE_SHAPE[state] || 'never_built'} size="s"
    label=${t(`corpus.state.${state}`)} />`;
}

/** An identity state: its shape and word. */
export function IdentityState({ state }) {
  return html`<${StatusDot} state=${IDENTITY_SHAPE[state] || 'never_built'} size="s"
    label=${t(`corpus.identity.${state || 'pending'}`)} />`;
}

/** « First Last », or the id when the names are missing. */
export function personName(p) {
  const name = [p.first_name, p.last_name].filter(Boolean).join(' ');
  return name || p.name || p.person_id;
}

/** A catalogue's words for a code, else the server's English words. */
export function coded(prefix, item) {
  if (!item) return '';
  const key = `${prefix}.${item.code}`;
  return has(key) ? t(key, item.params || {}) : item.message || item.code;
}

/** A role's word. */
export function roleLabel(role) {
  return has(`corpus.role.${role}`) ? t(`corpus.role.${role}`) : role;
}

/** A small count in a chip: « 12 ». */
export function Count({ value }) {
  return html`<span class="cx-corpus-count">${formatNumber(value || 0)}</span>`;
}

/** A labelled value in a sheet. */
export function Fact({ label, children }) {
  return html`<div class="cx-corpus-fact"><dt>${label}</dt><dd>${children}</dd></div>`;
}

/** A horizontal bar of a share, with its value beside it (for small aggregates). */
export function Bar({ value, max, label }) {
  const share = max ? Math.max(0, Math.min(1, value / max)) : 0;
  return html`<span class="cx-corpus-bar" title=${label}>
    <span class="cx-corpus-bar__fill" style=${{ '--cx-share': String(share) }}></span>
  </span>`;
}

const PAGE = 200;

/**
 * A list read from the server one page at a time, for a virtualised Table:
 * `rows` has the list's full length, rows not read yet are placeholders, and
 * `onRange` (the Table's) reads the pages in view. A change of *query* starts
 * again from the first page; `reload()` reads again what is in view.
 * @param {object} ctx the page's context (its `api`)
 * @param {string} path
 * @param {object} query filters and sort
 * @param {(row: object) => string} keyOf
 */
export function usePaged(ctx, path, query, keyOf) {
  const [state, setState] = useState({ rows: [], total: 0, data: null, loading: true, error: null });
  const store = useRef({ gen: 0, pages: new Map(), asked: new Set(), total: 0, view: [0, 0] });
  const qkey = JSON.stringify(query);
  // Keys starting with « $ » only restart the list (a version); they are not sent.
  const sent = Object.fromEntries(Object.entries(query).filter(([k]) => !k.startsWith('$')));

  const build = () => {
    const s = store.current;
    const rows = new Array(s.total);
    for (let i = 0; i < s.total; i += 1) rows[i] = null;
    s.pages.forEach((items, pageNo) => {
      items.forEach((item, j) => {
        const i = pageNo * PAGE + j;
        if (i < s.total) rows[i] = item;
      });
    });
    for (let i = 0; i < s.total; i += 1) {
      if (!rows[i]) rows[i] = { $pending: true, $key: `@${i}` };
    }
    return rows;
  };

  const load = (pageNo) => {
    const s = store.current;
    const gen = s.gen;
    if (s.asked.has(pageNo)) return;
    s.asked.add(pageNo);
    ctx.api.get(path, { query: { ...sent, offset: pageNo * PAGE, limit: PAGE } }).then((result) => {
      if (gen !== store.current.gen) return;
      if (!result.ok) {
        s.asked.delete(pageNo);
        setState((prev) => ({ ...prev, loading: false, error: result.error }));
        return;
      }
      s.total = result.data.total;
      s.pages.set(pageNo, result.data.items);
      setState((prev) => ({
        rows: build(),
        total: s.total,
        data: pageNo === 0 || !prev.data ? { ...result.data, etag: result.etag } : prev.data,
        loading: false,
        error: null,
      }));
    });
  };

  const restart = () => {
    const s = store.current;
    s.gen += 1;
    s.pages = new Map();
    s.asked = new Set();
    setState((prev) => ({ ...prev, loading: prev.rows.length === 0, error: null }));
    load(0);
    const [first, last] = s.view;
    for (let p = Math.floor(first / PAGE); p <= Math.floor(last / PAGE); p += 1) load(p);
  };

  useEffect(() => {
    store.current.view = [0, 0];
    restart();
  }, [qkey]);

  const onRange = ({ first, last }) => {
    store.current.view = [first, last];
    for (let p = Math.floor(first / PAGE); p <= Math.floor(last / PAGE); p += 1) load(p);
  };

  return {
    ...state,
    onRange,
    reload: restart,
    rowKey: (row) => (row.$pending ? row.$key : keyOf(row)),
  };
}

/** Call *fn* when a job of *kind* started from this page ends (the jobs store's poller). */
export function useJobEnd(app, jobId, fn) {
  const jobs = app.stores.jobs.jobs.value;
  useEffect(() => {
    if (!jobId) return;
    const job = jobs.find((j) => j.id === jobId);
    if (job && !['queued', 'running', 'cancelling'].includes(job.state)) fn(job);
  }, [jobs, jobId]);
}

/** A finished job's result in words (collections and imports). */
export function jobSummary(result) {
  if (!result) return '';
  switch (result.action) {
    case 'identify':
      return t('corpus.result.identify', { n: result.people || 0, found: result.with_candidates || 0 });
    case 'harvest':
      return t('corpus.result.harvest', { n: result.people || 0, texts: result.texts || 0 });
    case 'institutions':
      return result.institutions ? t('corpus.result.search', { n: result.institutions.length })
        : t('corpus.result.proposal', { n: result.proposed || 0 });
    case 'collaborators':
      return t('corpus.result.collaborators', { n: result.collaborators || 0 });
    case 'retry':
      return t('corpus.result.retry', { n: (result.retried || []).length });
    case 'import_folder':
    case 'import_corpus':
      return t('corpus.result.import', { texts: result.texts || 0, people: result.people_created || 0 });
    case 'collect':
      return t('corpus.result.harvest', { n: result.people || 0, texts: result.texts || 0 });
    default:
      return '';
  }
}
