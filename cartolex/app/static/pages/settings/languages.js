/**
 * The keyword languages: the languages of the texts (each needs its language
 * model, spaCy's), the reference language keywords are named in, and the
 * languages names are shown in. Apart from the interface's own language.
 */

import { html, useEffect, useState } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { Button, Checkbox, FormField, Select } from '../../components/index.js';
import { Block, State, languageLabel, refusal, useResource } from './common.js';

function Choices({ legend, help, available, chosen, onChange }) {
  return html`<fieldset class="cx-settings__fieldset">
    <legend class="cx-field__label">${legend}</legend>
    ${help ? html`<p class="cx-field__help">${help}</p>` : null}
    <div class="cx-settings__checks">
      ${available.map((code) => html`<${Checkbox} key=${code} label=${languageLabel(code)}
        checked=${chosen.includes(code)}
        onChange=${(e) => onChange(e.currentTarget.checked ? [...chosen, code] : chosen.filter((c) => c !== code))} />`)}
    </div>
  </fieldset>`;
}

export function LanguagesSection({ ctx, app }) {
  const settings = useResource(ctx.api, '/api/settings');
  const [form, setForm] = useState(null);
  const [problem, setProblem] = useState(null);
  const data = settings.data;
  useEffect(() => {
    if (data) setForm({ corpus: [...data.languages.corpus], reference: data.languages.reference, display: [...data.languages.display] });
  }, [data]);
  const available = data ? data.languages.available : [];
  const save = async () => {
    setProblem(null);
    const result = await ctx.api.put('/api/settings', { languages: form }, { ifMatch: settings.etag });
    if (result.ok) {
      settings.set(result.data, result.etag);
      app.toaster.show({ kind: 'success', title: t('settings.saved') });
    } else setProblem(result.kind === 'stale' ? t('settings.stale') : refusal(result.error));
  };
  const valid = form && form.corpus.length && form.display.length;
  return html`<div class="cx-settings__grid">
    <${Block} title=${t('settings.languages.title')} resource=${settings}>
      ${form ? html`<div class="cx-settings__form">
        <${Choices} legend=${t('settings.languages.corpus')} help=${t('settings.languages.corpus_help')}
          available=${available} chosen=${form.corpus} onChange=${(corpus) => setForm({ ...form, corpus })} />
        <${FormField} label=${t('settings.languages.reference')} help=${t('settings.languages.reference_help')}>
          ${(field) => html`<${Select} ...${field} value=${form.reference}
            options=${available.map((code) => ({ value: code, label: languageLabel(code) }))}
            onChange=${(e) => setForm({ ...form, reference: e.currentTarget.value })} />`}
        <//>
        <${Choices} legend=${t('settings.languages.display')} help=${t('settings.languages.display_help')}
          available=${available} chosen=${form.display} onChange=${(display) => setForm({ ...form, display })} />
        ${problem ? html`<p class="cx-settings__problem" role="alert"><${State} kind="warning">${problem}<//></p>` : null}
        <div class="cx-settings__actions">
          <${Button} variant="primary" disabled=${!valid} onClick=${save}>${t('common.save')}<//>
        </div>
      </div>` : null}
    <//>
    <${Block} title=${t('settings.models.title')} resource=${settings}>
      ${data ? html`<p class="cx-settings__note">${t('settings.models.lead')}</p>
      <table class="cx-settings__table">
        <thead><tr>
          <th scope="col">${t('settings.models.language')}</th>
          <th scope="col">${t('settings.models.model')}</th>
          <th scope="col">${t('settings.models.state')}</th>
        </tr></thead>
        <tbody>${data.models.map((m) => html`<tr key=${m.language}>
          <th scope="row">${languageLabel(m.language)}</th>
          <td><code>${m.model}</code> <span class="cx-settings__muted">${m.licence}</span></td>
          <td>${m.installed ? html`<${State} kind="ok">${t('settings.models.installed', { version: m.installed })}<//>`
            : m.needed ? html`<${State} kind="warning">${t('settings.models.missing')}<//>
              <div><code>${m.install}</code></div>`
              : html`<${State} kind="none">${t('settings.models.not_needed')}<//>`}</td>
        </tr>`)}</tbody>
      </table>` : null}
    <//>
  </div>`;
}
