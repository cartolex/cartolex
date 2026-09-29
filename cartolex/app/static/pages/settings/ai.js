/**
 * The AI: with a copilot (a bundle for an assistant that runs code, its
 * result imported back: no key, nothing sent by cartolex) or by API (cartolex
 * calls the provider with a key). A key belongs to this computer, never to a
 * project.
 */

import { html, useState } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { Button, FormField, Input } from '../../components/index.js';
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

export function AiSection({ ctx, app, open }) {
  const machine = useResource(ctx.api, '/api/machine');
  const project = useResource(ctx.api, open ? '/api/settings' : null);
  const settings = open ? project : null;
  const identity = settings && settings.data ? settings.data.identity : null;
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
          label=${t('settings.ai.key')} help=${t('settings.ai.key_help')} />` : null}
    <//>
    ${settings ? html`<${Block} title=${t('settings.ai.identity')} resource=${settings}>
      ${identity ? html`<p class="cx-settings__note">${identity.ai
        ? t('settings.ai.identity_set', { provider: identity.ai.provider, model: identity.ai.model })
        : t('settings.ai.identity_none')}</p>
        <p class="cx-settings__note">${t('settings.ai.identity_help')}</p>` : null}
    <//>` : null}
  </div>`;
}
