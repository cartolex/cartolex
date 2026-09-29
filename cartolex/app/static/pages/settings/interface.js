/**
 * The interface: its language and its theme, this browser's own choices. The
 * interface language is apart from a project's keyword languages.
 */

import { html } from '../../core/preact.js';
import { autonym, locale, pickLocale, t } from '../../core/i18n.js';
import { Card, FormField, Select } from '../../components/index.js';

export function InterfaceSection({ app }) {
  const { prefs } = app.stores;
  const locales = app.manifest.locales.available;
  // An empty choice follows the browser: its language now, and no choice kept.
  const choose = async (code) => {
    if (code) {
      await app.switchLocale(code);
      return;
    }
    const browser = pickLocale(locales, { browser: navigator.languages || [], fallback: app.manifest.locales.default });
    await app.switchLocale(browser);
    prefs.locale.value = null;
  };
  return html`<div class="cx-settings__grid">
    <${Card} title=${t('settings.interface.title')} level=${3}>
      <${FormField} label=${t('settings.interface.language')} help=${t('settings.interface.language_help')}>
        ${(field) => html`<${Select} ...${field} value=${prefs.locale.value || ''}
          options=${[{ value: '', label: t('settings.interface.browser', { name: autonym(locale.value) }) },
            ...locales.map((code) => ({ value: code, label: autonym(code) }))]}
          onChange=${(e) => choose(e.currentTarget.value)} />`}
      <//>
      <${FormField} label=${t('settings.interface.theme')}>
        ${(field) => html`<${Select} ...${field} value=${prefs.theme.value}
          options=${['system', 'light', 'dark'].map((v) => ({ value: v, label: t(`display.theme.${v}`) }))}
          onChange=${(e) => app.setTheme(e.currentTarget.value)} />`}
      <//>
      <p class="cx-settings__note">${t('settings.interface.kept')}</p>
    <//>
  </div>`;
}
