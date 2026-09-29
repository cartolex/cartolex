/**
 * The new project form: its name, its field, the description the AI reads
 * as its context, the languages, the folder, and where it starts from (a
 * list of people, institutions, collaborators, a folder of texts, a corpus,
 * or the demo project).
 */

import { html, useState } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { Button, Checkbox, FormField, Input, Select, Textarea } from '../../components/index.js';
import { Block, State, languageLabel, refusal } from '../settings/common.js';

const STARTS = ['people', 'institutions', 'collaborators', 'folder', 'corpus', 'demo'];

/** A folder name from a project's name. */
export function slug(name) {
  return name.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase()
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60);
}

export function NewProject({ ctx, defaults, onDone }) {
  const [form, setForm] = useState({
    name: '', domain_title: '', domain_description: '', languages: ['en'], reference: 'en',
    start: 'people', folder: null,
  });
  const [problem, setProblem] = useState(null);
  const [busy, setBusy] = useState(false);
  const d = defaults.data;
  const set = (patch) => setForm({ ...form, ...patch });
  const demo = form.start === 'demo';
  const folder = form.folder !== null ? form.folder
    : d && d.folder && form.name.trim() ? `${d.folder}${d.separator}${slug(form.name) || 'project'}` : '';
  const ready = demo || (form.name.trim() && form.domain_title.trim() && form.languages.length && folder.trim());

  const submit = async () => {
    setBusy(true);
    setProblem(null);
    const result = demo
      ? await ctx.api.post('/api/projects/demo', form.folder ? { folder: form.folder } : {})
      : await ctx.api.post('/api/projects', {
        folder: folder.trim(), name: form.name.trim(), domain_title: form.domain_title.trim(),
        domain_description: form.domain_description, languages: form.languages,
        reference: form.languages.includes(form.reference) ? form.reference : form.languages[0],
        start: form.start,
      });
    setBusy(false);
    if (result.ok) onDone(result.data.next);
    else setProblem(refusal(result.error));
  };

  const languages = d ? d.languages : [];
  return html`<${Block} title=${t('settings.project.title')} resource=${defaults}>
    <form class="cx-settings__form cx-start__form" onSubmit=${(e) => {
      e.preventDefault();
      if (ready) submit();
    }}>
      <fieldset class="cx-settings__fieldset">
        <legend class="cx-field__label">${t('start.from')}</legend>
        <p class="cx-field__help">${t('start.from_help')}</p>
        <div class="cx-start__starts" role="radiogroup" aria-label=${t('start.from')}>
          ${STARTS.map((id) => html`<label key=${id} class=${`cx-start__start ${form.start === id ? 'is-chosen' : ''}`}>
            <input type="radio" name="cx-start" value=${id} checked=${form.start === id}
              onChange=${() => set({ start: id })} />
            <span class="cx-start__start-name">${t(`start.from.${id}`)}</span>
            <span class="cx-settings__muted">${t(`start.from.${id}.text`)}</span>
          </label>`)}
        </div>
      </fieldset>
      ${demo ? html`<p class="cx-settings__note">${t('start.demo_text')}</p>` : html`
        <${FormField} label=${t('settings.project.name')} required>
          ${(field) => html`<${Input} ...${field} value=${form.name} maxLength=${200} autocomplete="off"
            onInput=${(e) => set({ name: e.currentTarget.value })} />`}
        <//>
        <${FormField} label=${t('settings.project.field')} help=${t('settings.project.field_help')} required>
          ${(field) => html`<${Input} ...${field} value=${form.domain_title} maxLength=${300} autocomplete="off"
            onInput=${(e) => set({ domain_title: e.currentTarget.value })} />`}
        <//>
        <${FormField} label=${t('settings.project.context')} help=${t('settings.project.context_help')}>
          ${(field) => html`<${Textarea} ...${field} rows=${4} value=${form.domain_description} maxLength=${4000}
            onInput=${(e) => set({ domain_description: e.currentTarget.value })} />`}
        <//>
        <fieldset class="cx-settings__fieldset">
          <legend class="cx-field__label">${t('settings.languages.corpus')}</legend>
          <p class="cx-field__help">${t('start.languages_help')}</p>
          <div class="cx-settings__checks">
            ${languages.map((code) => html`<${Checkbox} key=${code} label=${languageLabel(code)}
              checked=${form.languages.includes(code)}
              onChange=${(e) => set({ languages: e.currentTarget.checked ? [...form.languages, code]
                : form.languages.filter((c) => c !== code) })} />`)}
          </div>
        </fieldset>
        <${FormField} label=${t('settings.languages.reference')} help=${t('settings.languages.reference_help')}>
          ${(field) => html`<${Select} ...${field} value=${form.reference}
            options=${(form.languages.length ? form.languages : languages).map((code) => ({ value: code, label: languageLabel(code) }))}
            onChange=${(e) => set({ reference: e.currentTarget.value })} />`}
        <//>
        <${FormField} label=${t('start.folder_new')} help=${t('start.folder_new_help')} required>
          ${(field) => html`<${Input} ...${field} value=${folder} spellcheck="false" autocomplete="off"
            onInput=${(e) => set({ folder: e.currentTarget.value })} />`}
        <//>`}
      ${problem ? html`<p class="cx-settings__problem" role="alert"><${State} kind="warning">${problem}<//></p>` : null}
      <div class="cx-settings__actions">
        <${Button} type="submit" variant="primary" disabled=${!ready} loading=${busy}>
          ${demo ? t('start.demo_action') : t('start.create')}<//>
        <${Button} variant="ghost" onClick=${() => ctx.navigate('/start')}>${t('common.cancel')}<//>
      </div>
    </form>
  <//>`;
}
