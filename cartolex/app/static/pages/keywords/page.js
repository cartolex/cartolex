// SPDX-License-Identifier: MIT
/**
 * The keywords screen (`/keywords`): every candidate of the extraction in
 * its band, Kept, To check, Set aside and Rejected automatically (the band in
 * the address, `?band=`), with the counting unit and the languages in the head, a warning
 * when languages would split the themes, the history of the decisions, and
 * the AI filtering by handoff, by API or with a copilot. Opening it reads one list page.
 */
import { html, useState } from '../../core/preact.js';
import { formatNumber, t } from '../../core/i18n.js';
import { usePage, usePageTitle } from '../../core/page.js';
import { runtime } from '../../core/runtime.js';
import { Button, Icon, MenuButton, Slot, Tabs } from '../../components/index.js';
import { useJobEnd } from '../people/common.js';
import { BANDS } from './common.js';
import { KeywordList } from './list.js';
import { HistoryDrawer, MergeDialog } from './dialogs.js';
import { KeywordHandoffDialog } from './handoff.js';
import { ApiDialog } from './api.js';
import { KeywordCopilotDialog } from './copilot.js';

function bandOf(query) {
  const band = query && query.get('band');
  return BANDS.includes(band) ? band : 'check';
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
  const [dialog, setDialog] = useState(null); // {kind, ...}
  const [watched, setWatched] = useState(null);
  const toast = (item) => app.toaster.show(item);
  const bump = () => setVersion((v) => v + 1);

  const setBand = (id) => {
    setBandState(id);
    const url = new URL(window.location.href);
    url.searchParams.set('band', id);
    window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
  };
  useJobEnd(app, watched, (job) => {
    setWatched(null);
    bump();
    if (job.state === 'succeeded') toast({ kind: 'success', title: t('keywords.api.done') });
  });

  const counts = (data && data.counts) || {};
  const tabs = BANDS.map((id) => ({ id, label: t(`keywords.band.${id}`),
    count: data ? formatNumber(counts[id] || 0) : undefined }));
  const languages = (data && data.corpus_languages) || [];
  const aiItems = [
    { id: 'handoff', label: t('keywords.ai.by_handoff') },
    { id: 'api', label: t('keywords.ai.by_api') },
    { id: 'copilot', label: t('keywords.ai.by_copilot') },
  ];
  const closeDialog = () => setDialog(null);
  const finished = (message) => {
    setDialog(null);
    toast({ kind: 'success', title: message });
    bump();
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
      onFilter=${() => setDialog({ kind: 'handoff' })} />` : null}
    <${Slot} slots=${app.registries.slots} name="keywords.cards" class="cx-grid" />
    ${data && data.orphan_count ? html`<p class="cx-corpus__note" role="note">${t('keywords.orphans', {
      n: data.orphan_count })}</p>` : null}
    <${Tabs} tabs=${tabs} selected=${band} onSelect=${setBand} label=${t('keywords.bands')}
      class="cx-corpus__tabs"
      panel=${(id) => html`<${KeywordList} ctx=${ctx} band=${id} version=${version} onData=${setData}
        onChanged=${bump} toast=${toast}
        onMerge=${(rows, etag) => setDialog({ kind: 'merge', rows, etag })} />`} />
    ${dialog && dialog.kind === 'merge' ? html`<${MergeDialog} ctx=${ctx} rows=${dialog.rows}
      version=${dialog.etag} onClose=${closeDialog} onDone=${finished} />` : null}
    ${dialog && dialog.kind === 'history' ? html`<${HistoryDrawer} ctx=${ctx} version=${version}
      onClose=${closeDialog} onChanged=${bump} toast=${toast} />` : null}
    ${dialog && dialog.kind === 'handoff' ? html`<${KeywordHandoffDialog} ctx=${ctx}
      onClose=${closeDialog} onDone=${finished} />` : null}
    ${dialog && dialog.kind === 'copilot' ? html`<${KeywordCopilotDialog} ctx=${ctx}
      onClose=${closeDialog} onDone=${finished} />` : null}
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
