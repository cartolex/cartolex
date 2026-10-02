/**
 * Data sources: the bibliographic services texts are collected from (what
 * each is sent), the order in which text providers are preferred, the OpenAlex
 * key of this computer (a free key raises OpenAlex's daily budget) and the
 * folder of an OpenAlex snapshot downloaded to this computer (its state against
 * its manifests, its release, the speed it was last read at): a collection can
 * then read OpenAlex from it instead of the API.
 */

import { html, useState } from '../../core/preact.js';
import { formatBytes, formatDate, formatList, formatNumber, t } from '../../core/i18n.js';
import { Button, FormField, Input } from '../../components/index.js';
import { Block, State, refusal, useResource } from './common.js';
import { KeyField } from './ai.js';

const DOWNLOAD = 'aws s3 sync "s3://openalex/data/jsonl" "openalex-snapshot/data/jsonl" --no-sign-request';

/** The snapshot's state: ready (its release and size), incomplete, or not found. */
function SnapshotState({ status }) {
  if (!status) return html`<${State} kind="none">${t('settings.snapshot.none')}<//>`;
  if (status.state === 'ready') {
    const size = Object.values(status.bytes || {}).reduce((a, b) => a + b, 0);
    return html`<${State} kind="ok">${t('settings.snapshot.ready', {
      release: formatDate(status.release), size: formatBytes(size) })}<//>`;
  }
  if (status.state === 'incomplete') {
    return html`<${State} kind="warning">${status.absent && status.absent.length
      ? t('settings.snapshot.absent', { entities: formatList(status.absent) })
      : t('settings.snapshot.incomplete', { n: status.missing })}<//>`;
  }
  return html`<${State} kind="warning">${t('settings.snapshot.missing')}<//>`;
}

/** Whether the snapshot is indexed, and the command that indexes it (or goes on). */
function IndexState({ status }) {
  const index = status.index;
  if (status.indexed) {
    return html`<p class="cx-settings__muted">${t('settings.snapshot.indexed', { date: formatDate(index.built_at) })}</p>`;
  }
  const command = `cartolex collect snapshot-index "${status.folder}"`;
  return html`<p class="cx-settings__note">${index && index.state === 'building'
    ? t('settings.snapshot.index_building', { done: index.done, total: index.parts })
    : t('settings.snapshot.index_none')}</p>
    <pre class="cx-settings__pre" tabindex="0" aria-label=${t('settings.snapshot.index_label')}><code>${command}</code></pre>`;
}

/** The folder of the snapshot on this computer: saved, changed or removed. */
function SnapshotField({ ctx, app, machine }) {
  const [value, setValue] = useState('');
  const [problem, setProblem] = useState(null);
  const [busy, setBusy] = useState(false);
  const status = machine.data.snapshot;
  const send = async (folder) => {
    setBusy(true);
    setProblem(null);
    const result = await ctx.api.put('/api/machine/snapshot', { folder });
    setBusy(false);
    if (!result.ok) {
      setProblem(refusal(result.error));
      return;
    }
    setValue('');
    machine.set(result.data);
    app.toaster.show({ kind: 'success',
      title: folder ? t('settings.snapshot.saved') : t('settings.snapshot.removed') });
  };
  return html`<div class="cx-settings__key">
    <p class="cx-settings__note"><${SnapshotState} status=${status} /></p>
    ${status ? html`<p class="cx-settings__muted"><code>${status.folder}</code></p>
      <p class="cx-settings__muted">${status.rate_measured
        ? t('settings.snapshot.rate', { speed: formatBytes(status.read_rate, { perSecond: true }),
          date: formatDate(status.measured_at) })
        : t('settings.snapshot.rate_guess', { speed: formatBytes(status.read_rate, { perSecond: true }) })}</p>
      ${status.state !== 'missing' ? html`<${IndexState} status=${status} />` : null}` : null}
    <form class="cx-settings__inline" onSubmit=${(e) => {
      e.preventDefault();
      if (value.trim()) send(value.trim());
    }}>
      <${FormField} label=${t('settings.snapshot.folder')} help=${t('settings.snapshot.folder_help')}>
        ${(field) => html`<${Input} ...${field} autocomplete="off" spellcheck="false"
          value=${value} onInput=${(e) => setValue(e.currentTarget.value)} />`}
      <//>
      <div class="cx-settings__actions">
        <${Button} type="submit" variant="primary" disabled=${!value.trim()} loading=${busy}>
          ${t('settings.snapshot.save')}<//>
        ${status ? html`<${Button} variant="ghost" onClick=${() => send(null)}>${t('settings.snapshot.remove')}<//>` : null}
      </div>
    </form>
    ${problem ? html`<p class="cx-settings__problem" role="alert"><${State} kind="warning">${problem}<//></p>` : null}
  </div>`;
}

function usd(value) {
  return formatNumber(value, { style: 'currency', currency: 'USD', minimumFractionDigits: 2 });
}

export function SourcesSection({ ctx, app, open }) {
  const machine = useResource(ctx.api, '/api/machine');
  const settings = useResource(ctx.api, open ? '/api/settings' : null);
  const params = useResource(ctx.api, open ? '/api/params' : null);
  const budget = machine.data && machine.data.openalex;
  const assemble = params.data && (params.data.stages || []).find((s) => s.id === 'corpus.assemble');
  const priority = assemble && assemble.params.find((p) => p.name === 'provider_priority');
  return html`<div class="cx-settings__grid">
    ${open ? html`<${Block} title=${t('settings.sources.services')} resource=${settings}>
      ${settings.data ? html`<ul class="cx-settings__rows">
        ${settings.data.data_sources.map((s) => html`<li key=${s.id}>
          <span class="cx-settings__row-name">${s.name}</span>
          ${s.enabled ? html`<${State} kind="ok">${t('settings.sources.enabled')}<//>`
            : html`<${State} kind="none">${t('settings.sources.disabled')}<//>`}
          ${s.sends && s.sends.length ? html`<span class="cx-settings__muted">
            ${t('settings.sources.sends', { items: formatList(s.sends) })}</span>` : null}
        </li>`)}
      </ul>` : null}
    <//>` : null}
    ${open ? html`<${Block} title=${t('settings.sources.providers')} resource=${params}>
      ${priority ? html`<p class="cx-settings__note">${t('settings.sources.providers_lead')}</p>
        <ol class="cx-settings__order">${(priority.value || []).map((p) => html`<li key=${p}><code>${p}</code></li>`)}</ol>
        <p class="cx-settings__muted">${t(`settings.origin.${priority.from}`)} · ${t('settings.sources.providers_edit')}</p>`
        : html`<p class="cx-settings__note">${t('settings.sources.providers_none')}</p>`}
    <//>` : null}
    <${Block} title=${t('settings.sources.openalex')} resource=${machine}>
      ${budget ? html`
        <p class="cx-settings__note">${t('settings.sources.openalex_budget', {
          without: usd(budget.without_key_usd), with: usd(budget.with_key_usd),
          lists: usd(budget.list_per_1000_usd), searches: usd(budget.search_per_1000_usd) })}</p>
        <p class="cx-settings__note">${t('settings.sources.openalex_get')}
          <a href=${budget.get_key} rel="noreferrer" target="_blank"> <code>${budget.get_key}</code></a></p>
        <${KeyField} ctx=${ctx} app=${app} machine=${machine} service="openalex"
          label=${t('settings.sources.openalex_key')} help=${t('settings.sources.openalex_key_help')} />` : null}
    <//>
    <${Block} title=${t('settings.snapshot.title')} resource=${machine}>
      ${machine.data ? html`
        <p class="cx-settings__note">${t('settings.snapshot.lead')}</p>
        ${machine.data.hosted ? html`<p class="cx-settings__note">${t('settings.snapshot.hosted')}</p>`
          : html`<${SnapshotField} ctx=${ctx} app=${app} machine=${machine} />
            <p class="cx-settings__note">${t('settings.snapshot.download')}</p>
            <pre class="cx-settings__pre" tabindex="0" aria-label=${t('settings.snapshot.download_label')}><code>${DOWNLOAD}</code></pre>`}` : null}
    <//>
  </div>`;
}
