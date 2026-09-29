/**
 * Stop words (words that are never keywords, per language: added to or
 * removed from cartolex's lists), the candidates rejected automatically
 * (rejects.js) and the prompts the AI clean-up sends (the
 * packaged text, or the project's own).
 */

import { html, useEffect, useState } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { Button, FormField, Textarea } from '../../components/index.js';
import { Block, State, languageLabel, refusal, useResource } from './common.js';
import { RejectsBlock } from './rejects.js';

const lines = (text) => text.split(/[\n,]/).map((w) => w.trim()).filter(Boolean);

export function WordsSection({ ctx, app }) {
  const words = useResource(ctx.api, '/api/settings/stopwords');
  const [form, setForm] = useState(null);
  const [problem, setProblem] = useState(null);
  const data = words.data;
  useEffect(() => {
    if (!data) return;
    const out = {};
    const langs = [...new Set([...data.languages, ...Object.keys(data.add), ...Object.keys(data.remove)])];
    for (const lang of langs) out[lang] = { add: (data.add[lang] || []).join('\n'), remove: (data.remove[lang] || []).join('\n') };
    setForm(out);
  }, [data]);
  const save = async () => {
    setProblem(null);
    const add = {};
    const remove = {};
    for (const [lang, f] of Object.entries(form)) {
      if (lines(f.add).length) add[lang] = lines(f.add);
      if (lines(f.remove).length) remove[lang] = lines(f.remove);
    }
    const result = await ctx.api.put('/api/settings/stopwords', { add, remove }, { ifMatch: words.etag });
    if (result.ok) {
      words.set(result.data, result.etag);
      app.toaster.show({ kind: 'success', title: t('settings.words.saved') });
    } else setProblem(result.kind === 'stale' ? t('settings.stale') : refusal(result.error));
  };
  return html`<div class="cx-settings__grid">
    <${Block} title=${t('settings.words.title')} resource=${words} class="cx-settings__wide">
      ${form ? html`<p class="cx-settings__note">${t('settings.words.lead')}</p>
        ${Object.entries(form).map(([lang, f]) => html`<fieldset key=${lang} class="cx-settings__fieldset cx-settings__pair">
          <legend class="cx-field__label">${languageLabel(lang)}</legend>
          <${FormField} label=${t('settings.words.add')} help=${t('settings.words.one_per_line')}>
            ${(field) => html`<${Textarea} ...${field} rows=${4} lang=${lang} value=${f.add}
              onInput=${(e) => setForm({ ...form, [lang]: { ...f, add: e.currentTarget.value } })} />`}
          <//>
          <${FormField} label=${t('settings.words.remove')} help=${t('settings.words.one_per_line')}>
            ${(field) => html`<${Textarea} ...${field} rows=${4} lang=${lang} value=${f.remove}
              onInput=${(e) => setForm({ ...form, [lang]: { ...f, remove: e.currentTarget.value } })} />`}
          <//>
        </fieldset>`)}
        ${problem ? html`<p class="cx-settings__problem" role="alert"><${State} kind="warning">${problem}<//></p>` : null}
        <div class="cx-settings__actions"><${Button} variant="primary" onClick=${save}>${t('common.save')}<//></div>
        <p class="cx-settings__muted">${t('settings.words.build')}</p>` : null}
    <//>
    <${RejectsBlock} ctx=${ctx} app=${app} />
  </div>`;
}

function PromptCard({ ctx, app, item, onSaved }) {
  const [text, setText] = useState(item.own || item.packaged);
  const [problem, setProblem] = useState(null);
  useEffect(() => setText(item.own || item.packaged), [item]);
  const send = async (value) => {
    setProblem(null);
    const result = await ctx.api.put(`/api/settings/prompts/${item.name}`, { text: value }, { ifMatch: item.version });
    if (result.ok) {
      onSaved(result.data);
      app.toaster.show({ kind: 'success', title: value === null ? t('settings.prompts.back_done') : t('settings.prompts.saved') });
    } else setProblem(refusal(result.error));
  };
  return html`<${Block} title=${item.name} class="cx-settings__wide">
    <p class="cx-settings__note">${t('settings.prompts.used_by', { what: item.used_by })}</p>
    <p class="cx-settings__note">${item.own !== null ? html`<${State} kind="warning">${t('settings.prompts.own')}<//>`
      : html`<${State} kind="ok">${t('settings.prompts.packaged')}<//>`}</p>
    <p class="cx-settings__muted">${t('settings.prompts.placeholders')} ${item.placeholders.map((p) => html`<code key=${p}>${`{${p}}`}</code> `)}</p>
    <${FormField} label=${t('settings.prompts.text')}>
      ${(field) => html`<${Textarea} ...${field} rows=${14} class="cx-settings__code" spellcheck="false"
        value=${text} onInput=${(e) => setText(e.currentTarget.value)} />`}
    <//>
    ${problem ? html`<p class="cx-settings__problem" role="alert"><${State} kind="warning">${problem}<//></p>` : null}
    <div class="cx-settings__actions">
      <${Button} variant="primary" disabled=${text === (item.own || item.packaged) || !text.trim()}
        onClick=${() => send(text)}>${t('common.save')}<//>
      ${item.own !== null ? html`<${Button} variant="ghost" onClick=${() => send(null)}>${t('settings.prompts.back')}<//>` : null}
    </div>
  <//>`;
}

export function PromptsSection({ ctx, app }) {
  const prompts = useResource(ctx.api, '/api/settings/prompts');
  const items = prompts.data ? prompts.data.items : null;
  return html`<div class="cx-settings__grid">
    <p class="cx-settings__note cx-settings__wide">${t('settings.prompts.lead')}</p>
    ${items ? items.map((item, i) => html`<${PromptCard} key=${item.name} ctx=${ctx} app=${app} item=${item}
      onSaved=${(next) => prompts.set({ ...prompts.data, items: items.map((x, j) => (j === i ? next : x)) })} />`)
      : html`<${Block} title=${t('settings.section.prompts')} resource=${prompts} />`}
  </div>`;
}
