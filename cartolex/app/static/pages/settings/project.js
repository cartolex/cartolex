/**
 * The project: its name, its field's title and the description the AI reads
 * as its context. Once the first AI answers are in, the identity is frozen:
 * changing the title (or the AI model, or a language's model) means answers
 * already paid for are not reused; the change is asked for again with what it
 * costs.
 */

import { html, useEffect, useState } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { Button, ConfirmDialog, FormField, Input, Textarea } from '../../components/index.js';
import { Block, State, refusal, useResource } from './common.js';

export function ProjectSection({ ctx, app }) {
  const settings = useResource(ctx.api, '/api/settings');
  const [form, setForm] = useState(null);
  const [problem, setProblem] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [saving, setSaving] = useState(false);
  const data = settings.data;
  useEffect(() => {
    if (data) {
      setForm({ name: data.name, domain_title: data.identity.domain_title,
        domain_description: data.identity.domain_description || '' });
    }
  }, [data]);
  const frozen = data && data.identity.frozen;
  const changed = data && form && (form.name !== data.name || form.domain_title !== data.identity.domain_title
    || form.domain_description !== (data.identity.domain_description || ''));

  const save = async (confirmed = false) => {
    setSaving(true);
    setProblem(null);
    const body = { confirm_identity_change: confirmed };
    if (form.name !== data.name) body.name = form.name.trim();
    if (form.domain_title !== data.identity.domain_title) body.domain_title = form.domain_title.trim();
    if (form.domain_description !== (data.identity.domain_description || '')) body.domain_description = form.domain_description;
    const result = await ctx.api.put('/api/settings', body, { ifMatch: settings.etag });
    setSaving(false);
    if (result.ok) {
      settings.set(result.data, result.etag);
      app.toaster.show({ kind: 'success', title: t('settings.saved') });
    } else if (result.error && result.error.code === 'identity_frozen') {
      setConfirm(true);
    } else if (result.kind === 'stale') {
      setProblem(t('settings.stale'));
      settings.reload();
    } else {
      setProblem(refusal(result.error));
    }
  };

  return html`<div class="cx-settings__grid">
    <${Block} title=${t('settings.project.title')} resource=${settings}>
      ${form ? html`<form class="cx-settings__form" onSubmit=${(e) => {
        e.preventDefault();
        save(false);
      }}>
        <${FormField} label=${t('settings.project.name')} required>
          ${(field) => html`<${Input} ...${field} value=${form.name} maxLength=${200}
            onInput=${(e) => setForm({ ...form, name: e.currentTarget.value })} />`}
        <//>
        <${FormField} label=${t('settings.project.field')} help=${t('settings.project.field_help')} required>
          ${(field) => html`<${Input} ...${field} value=${form.domain_title} maxLength=${300}
            onInput=${(e) => setForm({ ...form, domain_title: e.currentTarget.value })} />`}
        <//>
        <${FormField} label=${t('settings.project.context')} help=${t('settings.project.context_help')}>
          ${(field) => html`<${Textarea} ...${field} rows=${6} value=${form.domain_description} maxLength=${4000}
            onInput=${(e) => setForm({ ...form, domain_description: e.currentTarget.value })} />`}
        <//>
        <p class="cx-settings__note">
          ${frozen ? html`<${State} kind="warning">${t('settings.project.frozen')}<//>`
            : html`<${State} kind="none">${t('settings.project.not_frozen')}<//>`}
        </p>
        ${frozen ? html`<ul class="cx-settings__list">
          <li>${t('settings.project.cost.title')}</li>
          <li>${t('settings.project.cost.ai')}</li>
          <li>${t('settings.project.cost.models')}</li>
        </ul>` : null}
        ${problem ? html`<p class="cx-settings__problem" role="alert"><${State} kind="warning">${problem}<//></p>` : null}
        <div class="cx-settings__actions">
          <${Button} type="submit" variant="primary" disabled=${!changed} loading=${saving}>${t('common.save')}<//>
        </div>
      </form>` : null}
    <//>
    <${ConfirmDialog} open=${Boolean(confirm)} title=${t('settings.project.confirm.title')}
      confirmLabel=${t('settings.project.confirm.yes')} cancelLabel=${t('common.cancel')}
      onAnswer=${(yes) => {
        setConfirm(null);
        if (yes) save(true);
      }}>
      <p>${t('settings.project.confirm.text')}</p>
    <//>
  </div>`;
}
