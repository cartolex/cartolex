// SPDX-License-Identifier: MIT
/**
 * The curator's notes for the AI copilot (Settings › Project): a free text
 * (teams, keywords that belong together or apart, standing context) and the
 * standing rules agreed with a copilot, one per task, each removable. Kept in
 * `decisions/curation-notes.md`; every copilot bundle carries them.
 */

import { html, useEffect, useState } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { Button, FormField, Textarea } from '../../components/index.js';
import { Block, State, refusal, useResource } from './common.js';

export function CurationBlock({ ctx, app }) {
  const resource = useResource(ctx.api, '/api/settings/curation');
  const [notes, setNotes] = useState('');
  const [rules, setRules] = useState([]);
  const [problem, setProblem] = useState(null);
  const [saving, setSaving] = useState(false);
  const data = resource.data;
  useEffect(() => {
    if (data) {
      setNotes(data.notes);
      setRules(data.rules);
    }
  }, [data]);
  const changed = data && (notes !== data.notes || rules.length !== data.rules.length);
  const save = async () => {
    setSaving(true);
    setProblem(null);
    const result = await ctx.api.put('/api/settings/curation', { notes, rules }, { ifMatch: resource.etag });
    setSaving(false);
    if (result.ok) {
      resource.set(result.data, result.etag);
      app.toaster.show({ kind: 'success', title: t('settings.saved') });
    } else if (result.kind === 'stale') {
      setProblem(t('settings.stale'));
      resource.reload();
    } else setProblem(refusal(result.error));
  };
  return html`<${Block} title=${t('settings.curation.title')} resource=${resource}>
    ${data ? html`<form class="cx-settings__form" onSubmit=${(e) => {
      e.preventDefault();
      save();
    }}>
      <${FormField} label=${t('copilot.curation_notes')} help=${t('copilot.curation_notes.help')}>
        ${(field) => html`<${Textarea} ...${field} rows=${6} value=${notes} maxLength=${20000}
          onInput=${(e) => setNotes(e.currentTarget.value)} />`}
      <//>
      <h3 class="cx-settings__subtitle">${t('settings.curation.rules')}</h3>
      ${rules.length ? html`<ul class="cx-settings__list">
        ${rules.map((r, i) => html`<li key=${`${r.task}:${r.text}`}>
          <span>${t(`settings.curation.task.${r.task}`)}: ${r.text}</span>
          ${' '}<${Button} size="s" variant="ghost" onClick=${() => setRules(rules.filter((_, k) => k !== i))}>
            ${t('settings.curation.remove')}<//></li>`)}
      </ul>` : html`<p class="cx-settings__note"><${State} kind="none">${t('settings.curation.no_rules')}<//></p>`}
      ${problem ? html`<p class="cx-settings__problem" role="alert"><${State} kind="warning">${problem}<//></p>` : null}
      <div class="cx-settings__actions">
        <${Button} type="submit" variant="primary" disabled=${!changed} loading=${saving}>${t('copilot.curation_notes.save')}<//>
      </div>
    </form>` : null}
  <//>`;
}
