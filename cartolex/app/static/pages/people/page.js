// SPDX-License-Identifier: MIT
/**
 * The People screen (`/people`): who is in the map and what was collected for
 * them. Tabs: People, Identities (the queue), Organisations, Texts,
 * Collaborators, Coverage; the tab is kept in the address (`?tab=`). Importing
 * and collecting open dialogs; collecting always shows what leaves the
 * computer first, then runs as a job followed in the Activity drawer; a
 * person's sheet opens in a drawer from every list.
 *
 * Addresses other pages link to: `/people?person=<id>` opens a person's sheet,
 * `/people?tab=organisations&org=<id>` an organisation, `/people?tab=texts&text=<id>`
 * a text; `/people?tab=duplicates` the groups of people who may be one person.
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatNumber, t } from '../../core/i18n.js';
import { usePage, usePageTitle } from '../../core/page.js';
import { runtime } from '../../core/runtime.js';
import { MenuButton, Tabs } from '../../components/index.js';
import { jobSummary, useJobEnd } from './common.js';
import { PeopleTab } from './people-tab.js';
import { IdentityQueue } from './identities.js';
import { OrganisationsTab } from './orgs-tab.js';
import { TextsTab } from './texts-tab.js';
import { CollaboratorsTab } from './collaborators-tab.js';
import { CoverageTab } from './coverage-tab.js';
import { DuplicatesTab } from './duplicates-tab.js';
import { PersonSheet } from './sheet.js';
import { SamePersonDialog } from './same-person.js';
import { ImportDialog } from './import.js';
import { CollectDialog } from './collect.js';

const TABS = ['people', 'identities', 'duplicates', 'organisations', 'texts', 'collaborators',
  'coverage'];

/** An id named in the address (`person`, `org`, `text`), or null. */
function linked(query, name) {
  const value = query && query.get(name);
  return value && /^[A-Za-z0-9_.:-]{1,64}$/.test(value) ? value : null;
}

/** The address without *name*, once the drawer it opened is closed. */
function dropParam(name) {
  const url = new URL(window.location.href);
  if (!url.searchParams.has(name)) return;
  url.searchParams.delete(name);
  window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
}

/**
 * Where a new project's starting point lands (`?start=`): the tab to show and the
 * dialog to open. `list` (or `people`) opens the import of a list of people;
 * `institutions` and `collaborators` open their tabs.
 */
const STARTS = {
  list: { tab: 'people', importing: 'list' },
  people: { tab: 'people', importing: 'list' },
  institutions: { tab: 'organisations' },
  collaborators: { tab: 'collaborators' },
};

function startOf(query) {
  const start = query && query.get('start');
  return Object.hasOwn(STARTS, start || '') ? STARTS[start] : null;
}

function tabOf(query) {
  const start = startOf(query);
  if (start) return start.tab;
  const tab = query && query.get('tab');
  return TABS.includes(tab) ? tab : 'people';
}

/** The collections `?collect=` may open. */
const COLLECT_ACTIONS = ['identify', 'harvest'];

/** The address without its `start` (and `collect`), so a reload does not open the dialog again. */
function dropStart(tab, also = '') {
  const url = new URL(window.location.href);
  if (!url.searchParams.has('start') && !(also && url.searchParams.has(also))) return;
  url.searchParams.delete('start');
  if (also) url.searchParams.delete(also);
  if (tab === 'people') url.searchParams.delete('tab');
  else url.searchParams.set('tab', tab);
  window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
}

/** The People screen's content. */
export function CorpusScreen() {
  const ctx = usePage();
  const { app } = ctx;
  usePageTitle(t('nav.people'));
  const canCollect = Boolean(app.manifest.capabilities && app.manifest.capabilities.collection);
  const [tab, setTabState] = useState(() => tabOf(ctx.query));
  const [version, setVersion] = useState(0);
  const [summary, setSummary] = useState(null);
  const [sheet, setSheet] = useState(() => linked(ctx.query, 'person'));
  const [focus] = useState(() => ({ org: linked(ctx.query, 'org'), text: linked(ctx.query, 'text') }));
  const [duplicates, setDuplicates] = useState(null);
  const [same, setSame] = useState(null); // « Same person… »: {ids, search}
  const [importing, setImporting] = useState(() => {
    const start = startOf(ctx.query);
    return start && start.importing ? { mode: start.importing, person: null } : null;
  }); // {mode, person}
  // `?collect=harvest` (the overview's next step, a build refused for want of texts) opens
  // the harvest's dialog at once; the address loses it, so a reload does not.
  const [collecting, setCollecting] = useState(() => {
    const action = ctx.query && ctx.query.get('collect');
    return canCollect && COLLECT_ACTIONS.includes(action) ? { action, options: {} } : null;
  }); // {action, options}
  const [peopleFilter, setPeopleFilter] = useState(null); // a filter set from another tab
  const [watched, setWatched] = useState(null);
  const toast = (item) => app.toaster.show(item);
  const bump = () => setVersion((v) => v + 1);
  useEffect(() => dropStart(tab, 'collect'), []);

  const setTab = (id) => {
    setTabState(id);
    const url = new URL(window.location.href);
    if (id === 'people') url.searchParams.delete('tab');
    else url.searchParams.set('tab', id);
    window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
  };

  // A job started here (a collection, an import) ends: read the lists again, say so.
  useJobEnd(app, watched, (job) => {
    setWatched(null);
    bump();
    // The Activity drawer says a job ended; a search or a proposal also says where to go next,
    // an identification leads to the identities, a harvest to the build.
    const action = job.state === 'succeeded' && job.result ? job.result.action : null;
    if (action === 'institutions') {
      toast({ kind: 'info', title: t('corpus.job.done'), message: jobSummary(job.result) });
    } else if (action === 'identify') {
      toast({ kind: 'info', title: t('corpus.job.done'), message: jobSummary(job.result),
        action: { label: t('job.link.identities'), onClick: () => setTab('identities') } });
    } else if (action === 'harvest') {
      toast({ kind: 'info', title: t('corpus.job.done'), message: jobSummary(job.result),
        action: { label: t('job.link.build'), onClick: () => ctx.navigate('/build') } });
    }
  });

  // The head's counts: read with the page, again after each change.
  const [tick, setTick] = useState(0);
  const refresh = () => setTick((n) => n + 1);
  useEffect(() => {
    ctx.api.get('/api/people', { query: { limit: 1 } }).then((r) => {
      if (!r.ok) return;
      const role = r.data.counts.role || {};
      setSummary({
        people: Object.values(role).reduce((a, b) => a + b, 0),
        mapped: role.mapped || 0,
        projected: role.projected || 0,
        pending: (r.data.counts.identity || {}).pending || 0,
      });
    });
  }, [version, tick]);
  // How many groups of people may be one person: the tab's count and the People tab's note.
  useEffect(() => {
    ctx.api.get('/api/people/duplicates/groups', { query: { limit: 1 } }).then((r) => {
      if (r.ok) setDuplicates(r.data.counts || null);
    });
  }, [version, tick]);
  // *line*: what the collection sends, when it needed no notice (« OpenAlex: about 1 request »).
  const started = (job, line = null) => {
    setWatched(job.id);
    app.stores.jobs.refresh();
    toast({ kind: 'info', title: t('corpus.job.started'), message: line || t('corpus.job.follow'),
      action: { label: t('activity.title'), onClick: () => runtime.openActivity() } });
  };

  const common = {
    ctx, version, bump, toast, canCollect, refresh,
    openSheet: (id) => setSheet(id),
    showOnMap: (kind, id) => ctx.navigate(`/map?sel=${encodeURIComponent(`${kind}:${id}`)}`),
    duplicates,
    openTab: (id) => setTab(id),
    sameAs: (ids, search = false) => setSame({ ids, search }),
    openCollect: (action, options = {}) => setCollecting({ action, options }),
    openImport: (mode, person = null) => setImporting({ mode, person }),
    showPeople: (filter) => {
      setPeopleFilter({ ...filter, $at: Date.now() });
      setTab('people');
    },
  };

  // « Undo » after « Same person… »: the rows merged stand on their own again.
  async function undoSame(ids) {
    const people = await ctx.api.get('/api/people', { query: { limit: 1 } });
    const result = await ctx.api.post('/api/people/unmerge', { person_ids: ids }, { ifMatch: people.etag });
    toast(result.ok ? { kind: 'success', title: t('corpus.dup.auto.undone', { n: result.data.unmerged.length }) }
      : { kind: 'error', title: t('corpus.same.undo_failed'), message: result.error && result.error.message });
    bump();
    refresh();
  }

  const importItems = [
    { id: 'list', label: t('corpus.import.list') },
    { id: 'folder', label: t('corpus.import.folder') },
    { id: 'corpus', label: t('corpus.import.corpus') },
  ];
  const collectItems = [
    { id: 'identify', label: t('corpus.collect.action.identify') },
    { id: 'harvest', label: t('corpus.collect.action.harvest') },
    { id: 'institutions', label: t('corpus.collect.action.institutions') },
    { id: 'collaborators', label: t('corpus.collect.action.collaborators') },
    { kind: 'separator', id: 'sep' },
    { id: 'retry', label: t('corpus.collect.action.retry') },
  ];
  const counts = summary || {};
  const tabs = TABS.map((id) => ({
    id,
    label: t(`corpus.tab.${id}`),
    count: id === 'identities' && counts.pending ? formatNumber(counts.pending)
      : id === 'duplicates' && duplicates && duplicates.open ? formatNumber(duplicates.open)
        : undefined,
  }));

  return html`<div class="cx-page cx-corpus">
    <header class="cx-corpus__head">
      <div>
        <h1 class="cx-page__title" tabindex="-1">${t('nav.people')}</h1>
        ${summary ? html`<p class="cx-corpus__summary">${t('corpus.summary', {
          people: summary.people, mapped: summary.mapped || 0, projected: summary.projected || 0,
        })}</p>` : null}
      </div>
      <div class="cx-corpus__actions">
        <${MenuButton} label=${t('corpus.import.button')} icon="upload" items=${importItems}
          onSelect=${(item) => setImporting({ mode: item.id, person: null })} />
        ${canCollect ? html`<${MenuButton} label=${t('corpus.collect.button')} icon="download"
          variant="primary" items=${collectItems}
          onSelect=${(item) => setCollecting({ action: item.id, options: {} })} />` : null}
      </div>
    </header>
    ${!canCollect ? html`<p class="cx-corpus__note" role="note">${t('corpus.collect.unavailable')}</p>`
      : null}
    <${Tabs} tabs=${tabs} selected=${tab} onSelect=${setTab} label=${t('corpus.tabs')}
      class="cx-corpus__tabs"
      panel=${(id) => {
        if (id === 'identities') return html`<${IdentityQueue} ...${common} />`;
        if (id === 'duplicates') return html`<${DuplicatesTab} ...${common} />`;
        if (id === 'organisations') return html`<${OrganisationsTab} ...${common} focus=${focus.org}
          onFocusClosed=${() => dropParam('org')} />`;
        if (id === 'texts') return html`<${TextsTab} ...${common} focus=${focus.text}
          onFocusClosed=${() => dropParam('text')} />`;
        if (id === 'collaborators') return html`<${CollaboratorsTab} ...${common} />`;
        if (id === 'coverage') return html`<${CoverageTab} ...${common} />`;
        return html`<${PeopleTab} ...${common} preset=${peopleFilter} />`;
      }} />
    ${sheet ? html`<${PersonSheet} ...${common} personId=${sheet} onClose=${() => {
      setSheet(null);
      dropParam('person');
    }} />` : null}
    ${same ? html`<${SamePersonDialog} ctx=${ctx} ids=${same.ids} search=${same.search}
      openSheet=${(id) => setSheet(id)} onClose=${() => setSame(null)}
      onDone=${(result) => {
        setSame(null);
        toast({ kind: 'success', timeout: 8000, title: t('corpus.same.done', { n: result.merged.length }),
          action: { label: t('corpus.dup.auto.undo'), onClick: () => undoSame(result.merged) } });
        if (sheet) setSheet(result.keep);
        bump();
        refresh();
      }} />` : null}
    ${importing ? html`<${ImportDialog} ...${common} mode=${importing.mode}
      person=${importing.person} onStarted=${started}
      onClose=${(changed) => { setImporting(null); if (changed) bump(); }} />` : null}
    ${collecting ? html`<${CollectDialog} ...${common} action=${collecting.action}
      options=${collecting.options} onStarted=${started}
      onClose=${() => setCollecting(null)} />` : null}
  </div>`;
}
