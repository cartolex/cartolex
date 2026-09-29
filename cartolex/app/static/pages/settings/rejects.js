/**
 * Rejected automatically: cartolex's list of rejections and this computer's
 * cache of an AI's « never » answers. The card says what each holds, switches
 * them on or off for the project (the keywords need a build after), shows
 * the cache's terms on demand, removes some of them, or empties the cache
 * (shared by every project on this computer). A visit reads one route; the
 * terms, one more when asked for.
 */

import { html, useState } from '../../core/preact.js';
import { formatDate, formatNumber, t } from '../../core/i18n.js';
import { Button, Checkbox, ConfirmDialog, Table } from '../../components/index.js';
import { Block, State, languageLabel, refusal, useResource } from './common.js';

const termKey = (row) => `${row.language}\u0000${row.term}`;

/** The cache's terms, the first page, with the removal of the ones selected. */
function CacheTerms({ ctx, app, onChanged }) {
  const terms = useResource(ctx.api, '/api/settings/rejects/terms?limit=500');
  const [chosen, setChosen] = useState(new Set());
  const rows = terms.data ? terms.data.items : [];
  const remove = async () => {
    const picked = rows.filter((r) => chosen.has(termKey(r)));
    const langs = [...new Set(picked.map((r) => r.language))];
    for (const language of langs) {
      const result = await ctx.api.post('/api/settings/rejects/clear', {
        language, terms: picked.filter((r) => r.language === language).map((r) => r.term) });
      if (!result.ok) {
        app.toaster.show({ kind: 'error', title: t('settings.rejects.failed'), message: refusal(result.error) });
        return;
      }
    }
    setChosen(new Set());
    terms.reload();
    onChanged();
    app.toaster.show({ kind: 'success', title: t('settings.rejects.removed', { n: picked.length }) });
  };
  const columns = [
    { id: 'term', label: t('keywords.col.term'), width: 'minmax(10rem, 2fr)' },
    { id: 'language', label: t('keywords.col.language'), width: '5rem',
      render: (r) => html`<code>${r.language}</code>` },
    { id: 'projects', label: t('settings.rejects.projects'), numeric: true, width: '7rem' },
    { id: 'date', label: t('settings.rejects.date'), width: '8rem',
      render: (r) => (r.date ? formatDate(r.date, 'date') : '—') },
  ];
  return html`<div>
    <div class="cx-settings__actions">
      <${Button} size="s" disabled=${!chosen.size} onClick=${remove}>${t('settings.rejects.remove', { n: chosen.size })}<//>
      ${terms.data ? html`<span class="cx-settings__muted">${t('settings.rejects.shown', {
        n: rows.length, total: terms.data.total })}</span>` : null}
    </div>
    <${Table} size="m" label=${t('settings.rejects.terms')} columns=${columns} rows=${rows}
      rowKey=${termKey} loading=${terms.loading} selection=${chosen} onSelectionChange=${setChosen}
      empty=${html`<p class="cx-settings__muted">${t('settings.rejects.empty')}</p>`} />
  </div>`;
}

export function RejectsBlock({ ctx, app }) {
  const info = useResource(ctx.api, '/api/settings/rejects');
  const [showTerms, setShowTerms] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [problem, setProblem] = useState(null);
  const data = info.data;
  const toggle = async (enabled) => {
    setProblem(null);
    const result = await ctx.api.put('/api/settings/rejects', { enabled }, { ifMatch: info.etag });
    if (result.ok) {
      info.set(result.data, result.etag);
      app.toaster.show({ kind: 'success', title: t(enabled ? 'settings.rejects.on' : 'settings.rejects.off') });
    } else setProblem(result.kind === 'stale' ? t('settings.stale') : refusal(result.error));
  };
  const clear = async () => {
    const result = await ctx.api.post('/api/settings/rejects/clear', {});
    if (result.ok) {
      info.reload();
      setShowTerms(false);
      app.toaster.show({ kind: 'success', title: t('settings.rejects.cleared', { n: result.data.removed }) });
    } else app.toaster.show({ kind: 'error', title: t('settings.rejects.failed'), message: refusal(result.error) });
  };
  const machine = data ? data.machine : null;
  return html`<${Block} title=${t('settings.rejects.title')} resource=${info} class="cx-settings__wide">
    ${data ? html`<p class="cx-settings__note">${t('settings.rejects.lead')}</p>
      <${Checkbox} checked=${data.enabled} onChange=${(e) => toggle(e.currentTarget.checked)}
        label=${t('settings.rejects.enabled')} />
      <p class="cx-settings__note">${data.enabled ? html`<${State} kind="ok">${t('settings.rejects.state_on')}<//>`
        : html`<${State} kind="none">${t('settings.rejects.state_off')}<//>`}</p>
      <dl class="cx-settings__facts">
        <div><dt>${t('settings.rejects.shipped')}</dt>
          <dd>${data.languages.map((lang) => t('settings.rejects.in_language', {
            language: languageLabel(lang), n: data.shipped[lang] || 0 })).join(' · ')}</dd></div>
        <div><dt>${t('settings.rejects.machine')}</dt>
          <dd>${machine.available ? t('settings.rejects.machine_total', { n: machine.total })
            : t('settings.rejects.machine_none')}</dd></div>
      </dl>
      ${problem ? html`<p class="cx-settings__problem" role="alert"><${State} kind="warning">${problem}<//></p>` : null}
      ${machine.available ? html`<div class="cx-settings__actions">
        <${Button} aria-expanded=${String(showTerms)} onClick=${() => setShowTerms(!showTerms)}>
          ${t(showTerms ? 'settings.rejects.hide' : 'settings.rejects.see', { n: formatNumber(machine.total) })}<//>
        <${Button} variant="danger" disabled=${!machine.total} onClick=${() => setClearing(true)}>${t('settings.rejects.clear')}<//>
      </div>` : null}
      ${showTerms ? html`<${CacheTerms} ctx=${ctx} app=${app} onChanged=${() => info.reload()} />` : null}
      <p class="cx-settings__muted">${t('settings.rejects.build')}</p>` : null}
    <${ConfirmDialog} open=${clearing} danger title=${t('settings.rejects.clear_title')}
      confirmLabel=${t('settings.rejects.clear')} cancelLabel=${t('common.cancel')} onAnswer=${(yes) => {
        setClearing(false);
        if (yes) clear();
      }}><p>${t('settings.rejects.clear_confirm', { n: machine ? machine.total : 0 })}</p><//>
  <//>`;
}
