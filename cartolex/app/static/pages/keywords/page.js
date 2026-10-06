// SPDX-License-Identifier: MIT
/**
 * The keywords screen (`/keywords`): every candidate of the extraction in
 * its band, Kept, To check, Set aside and Rejected automatically, and the
 * Lexicon the last build made of them (the tab in the address, `?band=`), with the counting unit and the languages in the head, a warning
 * when languages would split the themes, the history of the decisions, and
 * the triage with AI (a copilot, or by API), and the keywords' « Tune » panel
 * (`pages/tune/`). Opening it reads one list page. `?q=<term>` (a link from the map or the
 * themes) shows that keyword and the candidates merged into it, in the band that holds them.
 */
import { html, useState } from '../../core/preact.js';
import { formatNumber, t } from '../../core/i18n.js';
import { usePage, usePageTitle } from '../../core/page.js';
import { runtime } from '../../core/runtime.js';
import { Button, Icon, MenuButton, Slot, Tabs } from '../../components/index.js';
import { useJobEnd } from '../people/common.js';
import { BANDS } from './common.js';
import { KeywordList } from './list.js';
import { LexiconPanel } from './lexicon.js';
import { HistoryDrawer, MergeDialog } from './dialogs.js';
import { ApiDialog } from './api.js';
import { KeywordCopilotDialog } from './copilot.js';
import { KeywordPeopleDrawer } from './people.js';
import { UnjudgedNote } from './gate.js';
import { TunePanel } from '../tune/panel.js';

/** The tabs: the bands of the candidates, then the lexicon the last build made. */
const TABS = [...BANDS, 'lexicon'];

function bandOf(query) {
  const band = query && query.get('band');
  if (TABS.includes(band)) return band;
  return query && query.get('q') ? 'kept' : 'check';
}

/** The warning: several corpus languages and no AI filtering yet. */
function LanguagesWarning({ warning, onFilter }) {
  return html`<div class="cx-kw-warning" role="note">
    <${Icon} name="warning" />
    <p><strong>${t('keywords.warning.word')}</strong>${' '}
      ${t('keywords.warning.languages', { languages: warning.params.languages })}</p>
    <${Button} size="s" onClick=${onFilter}>${t('keywords.ai.button')}<//>
  </div>`;
}

export function KeywordsScreen() {
  const ctx = usePage();
  const { app } = ctx;
  usePageTitle(t('nav.keywords'));
  const [band, setBandState] = useState(() => bandOf(ctx.query));
  const [version, setVersion] = useState(0);
  const [data, setData] = useState(null);
  // `?copilot=1` (a build waiting for the copilot) opens its dialog at once; with
  // `&proposal=<id>` (a result imported, not accepted yet), at that result's review.
  const [dialog, setDialog] = useState(() => (ctx.query && ctx.query.get('copilot') === '1'
    ? { kind: 'copilot', proposal: ctx.query.get('proposal') } : null)); // {kind, ...}
  const [watched, setWatched] = useState(null);
  const [term, setTerm] = useState(() => (ctx.query && ctx.query.get('q')) || '');
  const [people, setPeople] = useState('');
  const toast = (item) => app.toaster.show(item);
  const bump = () => setVersion((v) => v + 1);

  const setBand = (id) => {
    setBandState(id);
    const url = new URL(window.location.href);
    url.searchParams.set('band', id);
    window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
  };
  const clearTerm = () => {
    setTerm('');
    const url = new URL(window.location.href);
    url.searchParams.delete('q');
    window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
  };
  // A keyword from the address: its band is the one that holds it.
  const onData = (d) => {
    setData(d);
    if (!term || d.total || !d.matched_bands) return;
    const other = BANDS.find((b) => d.matched_bands[b]);
    if (other && other !== band) setBand(other);
  };
  useJobEnd(app, watched, (job) => {
    setWatched(null);
    bump();
    if (job.state === 'succeeded') toast({ kind: 'success', title: t('keywords.api.done') });
  });

  const counts = (data && data.counts) || {};
  const tabs = [...BANDS.map((id) => ({ id, label: t(`keywords.band.${id}`),
    count: data ? formatNumber(counts[id] || 0) : undefined })),
  { id: 'lexicon', label: t('keywords.lexicon.tab') }];
  const languages = (data && data.corpus_languages) || [];
  const aiItems = [
    { id: 'copilot', label: t('keywords.ai.by_copilot') },
    { id: 'api', label: t('keywords.ai.by_api') },
  ];
  const closeDialog = () => setDialog(null);
  const finished = (message) => {
    setDialog(null);
    toast({ kind: 'success', title: message });
    bump();
    app.stores.project.refresh(); // a decision makes the vocabulary out of date
  };

  return html`<div class="cx-page cx-corpus cx-kw">
    <header class="cx-corpus__head">
      <div>
        <h1 class="cx-page__title" tabindex="-1">${t('nav.keywords')}</h1>
        ${data && data.run ? html`<p class="cx-corpus__summary">${t('keywords.summary', {
          n: (counts.kept || 0) + (counts.check || 0) + (counts.aside || 0) + (counts.rejected || 0),
          unit: t(`keywords.unit.${data.counting_unit || 'person'}`),
          languages: languages.join(', ') })}</p>` : null}
      </div>
      <div class="cx-corpus__actions">
        <${Button} icon="undo" onClick=${() => setDialog({ kind: 'history' })}>${t('keywords.history.button')}<//>
        <${MenuButton} label=${t('keywords.ai.button')} variant="primary" items=${aiItems}
          onSelect=${(item) => setDialog({ kind: item.id })} />
      </div>
    </header>
    ${data && data.warning ? html`<${LanguagesWarning} warning=${data.warning}
      onFilter=${() => setDialog({ kind: 'copilot' })} />` : null}
    ${data && data.gate && data.gate.mode === 'copilot' && data.gate.unjudged ? html`<${UnjudgedNote}
      ctx=${ctx} gate=${data.gate} version=${data.etag} onDone=${finished}
      onSend=${() => setDialog({ kind: 'copilot', scope: 'unjudged' })} />` : null}
    <${TunePanel} ctx=${ctx} id="keywords" />
    <${Slot} slots=${app.registries.slots} name="keywords.cards" class="cx-grid" />
    ${data && data.orphan_count ? html`<p class="cx-corpus__note" role="note">${t('keywords.orphans', {
      n: data.orphan_count })}</p>` : null}
    <${Tabs} tabs=${tabs} selected=${band} onSelect=${setBand} label=${t('keywords.bands')}
      class="cx-corpus__tabs"
      panel=${(id) => (id === 'lexicon'
        ? html`<${LexiconPanel} ctx=${ctx} version=${version} onChanged=${bump} toast=${toast}
            onMerge=${(rows, etag) => setDialog({ kind: 'merge', rows, etag })} />`
        : html`<${KeywordList} ctx=${ctx} band=${id} version=${version} onData=${onData}
            onChanged=${bump} toast=${toast} term=${term} onClearTerm=${clearTerm} onPeople=${setPeople}
            onMerge=${(rows, etag) => setDialog({ kind: 'merge', rows, etag })} />`)} />
    ${dialog && dialog.kind === 'merge' ? html`<${MergeDialog} ctx=${ctx} rows=${dialog.rows}
      version=${dialog.etag} onClose=${closeDialog} onDone=${finished} />` : null}
    <${KeywordPeopleDrawer} ctx=${ctx} term=${people} onClose=${() => setPeople('')} />
    ${dialog && dialog.kind === 'history' ? html`<${HistoryDrawer} ctx=${ctx} version=${version}
      onClose=${closeDialog} onChanged=${bump} toast=${toast} />` : null}
    ${dialog && dialog.kind === 'copilot' ? html`<${KeywordCopilotDialog} ctx=${ctx}
      proposal=${dialog.proposal} scope=${dialog.scope} onClose=${closeDialog} onDone=${finished} />` : null}
    ${dialog && dialog.kind === 'api' ? html`<${ApiDialog} ctx=${ctx} onClose=${closeDialog}
      onStarted=${(job) => {
        setDialog(null);
        setWatched(job.id);
        app.stores.jobs.refresh();
        toast({ kind: 'info', title: t('keywords.api.started'),
          action: { label: t('activity.title'), onClick: () => runtime.openActivity() } });
      }} />` : null}
  </div>`;
}
