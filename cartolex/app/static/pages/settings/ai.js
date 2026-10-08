/**
 * The AI: with a copilot (a bundle for an assistant that runs code, its
 * result imported back: no key, nothing sent by cartolex) or by API (cartolex
 * calls the provider with a key: Mistral AI or Albert, each with its own key,
 * never sent to the other). A key belongs to this computer, never to a
 * project; the provider and model the API route uses belong to the project
 * (its identity: once AI answers are paid for, changing it asks again).
 */

import { html, useEffect, useState } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { Button, ConfirmDialog, FormField, Input, Select } from '../../components/index.js';
import { Block, State, refusal, useResource } from './common.js';

/** A key saved on this computer for *service*: its state, a field to replace it, remove. */
export function KeyField({ ctx, app, machine, service, label, help }) {
  const [value, setValue] = useState('');
  const [problem, setProblem] = useState(null);
  const [busy, setBusy] = useState(false);
  const status = machine.data.keys[service];
  const hosted = machine.data.hosted;
  const send = async (key) => {
    setBusy(true);
    setProblem(null);
    const result = await ctx.api.put('/api/machine/keys', { service, key });
    setBusy(false);
    if (!result.ok) {
      setProblem(refusal(result.error));
      return;
    }
    setValue('');
    machine.set(result.data);
    app.toaster.show({ kind: 'success', title: key ? t('settings.key.saved') : t('settings.key.removed') });
  };
  return html`<div class="cx-settings__key">
    <p class="cx-settings__note">
      ${status.set ? html`<${State} kind="ok">${status.source === 'environment'
        ? t('settings.key.from_env', { ends: status.ends || '' })
        : t('settings.key.saved_here', { ends: status.ends || '' })}<//>
        ${status.source === 'environment' ? html` <code>${status.env_var}</code>` : null}`
        : html`<${State} kind="none">${t('settings.key.none')}<//>`}
    </p>
    ${hosted ? html`<p class="cx-settings__note">${t('settings.key.hosted')}</p>` : html`<form class="cx-settings__inline"
      onSubmit=${(e) => {
        e.preventDefault();
        if (value.trim()) send(value.trim());
      }}>
      <${FormField} label=${label} help=${help}>
        ${(field) => html`<${Input} ...${field} type="password" autocomplete="off" spellcheck="false"
          value=${value} onInput=${(e) => setValue(e.currentTarget.value)} />`}
      <//>
      <div class="cx-settings__actions">
        <${Button} type="submit" variant="primary" disabled=${value.trim().length < 8} loading=${busy}>
          ${t('settings.key.save')}<//>
        ${status.saved ? html`<${Button} variant="ghost" onClick=${() => send(null)}>${t('settings.key.remove')}<//>` : null}
      </div>
    </form>`}
    ${problem ? html`<p class="cx-settings__problem" role="alert"><${State} kind="warning">${problem}<//></p>` : null}
  </div>`;
}

/** The project's AI provider and model: chosen here, kept in the project's identity. */
function IdentityForm({ ctx, app, settings }) {
  const data = settings.data;
  const current = data.identity.ai;
  const providers = data.ai.providers;
  const defaults = data.ai.default_models || {};
  const initial = () => {
    const provider = current ? current.provider : providers[0];
    return { provider, model: current ? current.model : (defaults[provider] || '') };
  };
  const [form, setForm] = useState(initial);
  const [problem, setProblem] = useState(null);
  const [confirm, setConfirm] = useState(false);
  const [saving, setSaving] = useState(false);
  useEffect(() => setForm(initial()), [data]);
  const model = form.model.trim();
  const changed = !current || current.provider !== form.provider || current.model !== model;
  const save = async (confirmed = false) => {
    setSaving(true);
    setProblem(null);
    const result = await ctx.api.put('/api/settings',
      { ai: { provider: form.provider, model }, confirm_identity_change: confirmed }, { ifMatch: settings.etag });
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
  return html`<form class="cx-settings__form" onSubmit=${(e) => {
    e.preventDefault();
    save(false);
  }}>
    <p class="cx-settings__note">${current
      ? html`<${State} kind="ok">${t('settings.ai.identity_set', { provider: current.provider, model: current.model })}<//>`
      : html`<${State} kind="none">${t('settings.ai.identity_none')}<//>`}</p>
    <${FormField} label=${t('settings.ai.provider')}>
      ${(field) => html`<${Select} ...${field} value=${form.provider}
        options=${providers.map((p) => ({ value: p, label: t(`settings.ai.provider.${p}`) }))}
        onChange=${(e) => {
          const provider = e.currentTarget.value;
          setForm({ provider, model: defaults[provider] || form.model });
        }} />`}
    <//>
    <${FormField} label=${t('settings.ai.model')} help=${t('settings.ai.model_help', { model: defaults[form.provider] || '' })}>
      ${(field) => html`<${Input} ...${field} value=${form.model} maxLength=${100} spellcheck="false" autocomplete="off"
        onInput=${(e) => setForm({ ...form, model: e.currentTarget.value })} />`}
    <//>
    <p class="cx-settings__note">${t('settings.ai.identity_help')}</p>
    ${problem ? html`<p class="cx-settings__problem" role="alert"><${State} kind="warning">${problem}<//></p>` : null}
    <div class="cx-settings__actions">
      <${Button} type="submit" variant="primary" disabled=${!changed || !model} loading=${saving}>
        ${t('settings.ai.choose')}<//>
    </div>
    <${ConfirmDialog} open=${confirm} title=${t('settings.project.confirm.title')}
      confirmLabel=${t('settings.project.confirm.yes')} cancelLabel=${t('common.cancel')}
      onAnswer=${(yes) => {
        setConfirm(false);
        if (yes) save(true);
      }}>
      <p>${t('settings.project.confirm.text')}</p>
    <//>
  </form>`;
}

export function AiSection({ ctx, app, open }) {
  const machine = useResource(ctx.api, '/api/machine');
  const settings = useResource(ctx.api, open ? '/api/settings' : null);
  return html`<div class="cx-settings__grid">
    <${Block} title=${t('settings.ai.ways')}>
      <dl class="cx-settings__facts">
        <div><dt>${t('settings.ai.copilot')}</dt><dd>${t('settings.ai.copilot_text')}</dd></div>
        <div><dt>${t('settings.ai.api')}</dt><dd>${t('settings.ai.api_text')}</dd></div>
      </dl>
      <p class="cx-settings__note">${t('settings.ai.sends')}</p>
    <//>
    <${Block} title=${t('settings.ai.key_title')} resource=${machine}>
      ${machine.data ? html`
        <p class="cx-settings__note">${machine.data.ai_api
          ? html`<${State} kind="ok">${t('settings.ai.api_ready')}<//>`
          : html`<${State} kind="none">${t('settings.ai.api_off')}<//>`}</p>
        <${KeyField} ctx=${ctx} app=${app} machine=${machine} service="mistral"
          label=${t('settings.ai.key')} help=${t('settings.ai.key_help')} />
        <${KeyField} ctx=${ctx} app=${app} machine=${machine} service="albert"
          label=${t('settings.ai.key.albert')} help=${t('settings.ai.key_help')} />` : null}
    <//>
    ${open ? html`<${Block} title=${t('settings.ai.identity')} resource=${settings}>
      ${settings.data ? html`<${IdentityForm} ctx=${ctx} app=${app} settings=${settings} />` : null}
    <//>` : null}
  </div>`;
}
