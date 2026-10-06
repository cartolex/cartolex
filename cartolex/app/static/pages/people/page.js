// SPDX-License-Identifier: MIT
/**
 * The corpus screen (`/people`): who is in the map and what was collected for
 * them. Tabs: People, Identities (the queue), Organisations, Texts,
 * Collaborators, Coverage; the tab is kept in the address (`?tab=`). Importing
 * and collecting open dialogs; collecting always shows what leaves the
 * computer first, then runs as a job followed in the Activity drawer; a
 * person's sheet opens in a drawer from every list.
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
import { PersonSheet } from './sheet.js';
import { ImportDialog } from './import.js';
import { CollectDialog } from './collect.js';

const TABS = ['people', 'identities', 'organisations', 'texts', 'collaborators', 'coverage'];

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

/** The address without its `start`, so a reload does not open the dialog again. */
function dropStart(tab) {
  const url = new URL(window.location.href);
  if (!url.searchParams.has('start')) return;
  url.searchParams.delete('start');
  if (tab === 'people') url.searchParams.delete('tab');
  else url.searchParams.set('tab', tab);
  window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
}

/** The corpus screen's content. */
export function CorpusScreen() {
  const ctx = usePage();
  const { app } = ctx;
  usePageTitle(t('nav.people'));
  const canCollect = Boolean(app.manifest.capabilities && app.manifest.capabilities.collection);
  const [tab, setTabState] = useState(() => tabOf(ctx.query));
  const [version, setVersion] = useState(0);
  const [summary, setSummary] = useState(null);
  const [sheet, setSheet] = useState(null);
  const [importing, setImporting] = useState(() => {
    const start = startOf(ctx.query);
    return start && start.importing ? { mode: start.importing, person: null } : null;
  }); // {mode, person}
  const [collecting, setCollecting] = useState(null); // {action, options}
  const [peopleFilter, setPeopleFilter] = useState(null); // a filter set from another tab
  const [watched, setWatched] = useState(null);
  const toast = (item) => app.toaster.show(item);
  const bump = () => setVersion((v) => v + 1);
  useEffect(() => dropStart(tab), []);

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
    // The Activity drawer says a job ended; a search or a proposal also says where to go next.
    if (job.state === 'succeeded' && job.result && job.result.action === 'institutions') {
      toast({ kind: 'info', title: t('corpus.job.done'), message: jobSummary(job.result) });
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
    openCollect: (action, options = {}) => setCollecting({ action, options }),
    openImport: (mode, person = null) => setImporting({ mode, person }),
    showPeople: (filter) => {
      setPeopleFilter({ ...filter, $at: Date.now() });
      setTab('people');
    },
  };

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
    count: id === 'identities' && counts.pending ? formatNumber(counts.pending) : undefined,
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
        if (id === 'organisations') return html`<${OrganisationsTab} ...${common} />`;
        if (id === 'texts') return html`<${TextsTab} ...${common} />`;
        if (id === 'collaborators') return html`<${CollaboratorsTab} ...${common} />`;
        if (id === 'coverage') return html`<${CoverageTab} ...${common} />`;
        return html`<${PeopleTab} ...${common} preset=${peopleFilter} />`;
      }} />
    ${sheet ? html`<${PersonSheet} ...${common} personId=${sheet} onClose=${() => setSheet(null)} />`
      : null}
    ${importing ? html`<${ImportDialog} ...${common} mode=${importing.mode}
      person=${importing.person} onStarted=${started}
      onClose=${(changed) => { setImporting(null); if (changed) bump(); }} />` : null}
    ${collecting ? html`<${CollectDialog} ...${common} action=${collecting.action}
      options=${collecting.options} onStarted=${started}
      onClose=${() => setCollecting(null)} />` : null}
  </div>`;
}
