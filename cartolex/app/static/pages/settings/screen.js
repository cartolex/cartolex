/**
 * The settings screen: a list of sections on the left, one section at a time
 * on the right (its address is `/settings?section=<id>`, so it can be shared
 * and the back button returns to the previous one). Each section reads what
 * it shows when it opens: a visit costs the calls of the section shown.
 */

import { html } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { usePage, usePageTitle } from '../../core/page.js';
import { EmptyState } from '../../components/index.js';
import { InterfaceSection } from './interface.js';
import { ProjectSection } from './project.js';
import { LanguagesSection } from './languages.js';
import { AiSection } from './ai.js';
import { SourcesSection } from './sources.js';
import { BuildSection } from './build.js';
import { WordsSection, PromptsSection } from './words.js';
import { PrivacySection } from './privacy.js';
import { CareSection } from './care.js';

/** The sections, in order: id, component, and whether it needs an open project. */
export const SECTIONS = [
  { id: 'interface', component: InterfaceSection, project: false },
  { id: 'project', component: ProjectSection, project: true },
  { id: 'languages', component: LanguagesSection, project: true },
  { id: 'ai', component: AiSection, project: false },
  { id: 'sources', component: SourcesSection, project: false },
  { id: 'build', component: BuildSection, project: true },
  { id: 'words', component: WordsSection, project: true },
  { id: 'prompts', component: PromptsSection, project: true },
  { id: 'privacy', component: PrivacySection, project: false },
  { id: 'care', component: CareSection, project: false },
];

export function SettingsScreen() {
  const ctx = usePage();
  const { app } = ctx;
  usePageTitle(t('nav.settings'));
  const wanted = (ctx.query && ctx.query.get('section')) || 'interface';
  const section = SECTIONS.find((s) => s.id === wanted) || SECTIONS[0];
  const open = Boolean(app.manifest.project && app.manifest.project.open);
  const go = (id) => ctx.navigate(`/settings?section=${id}`);
  const Section = section.component;
  return html`<div class="cx-page cx-settings">
    <h1 class="cx-page__title">${t('nav.settings')}</h1>
    <div class="cx-settings__body">
      <nav class="cx-settings__nav" aria-label=${t('settings.sections')}>
        <ul>
          ${SECTIONS.map((s) => html`<li key=${s.id}>
            <a href=${`/settings?section=${s.id}`} class="cx-settings__link"
              aria-current=${s.id === section.id ? 'page' : undefined}
              onClick=${(e) => {
                if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
                e.preventDefault();
                go(s.id);
              }}>${t(`settings.section.${s.id}`)}</a></li>`)}
        </ul>
      </nav>
      <section class="cx-settings__section" aria-labelledby="cx-settings-title">
        <h2 class="cx-settings__title" id="cx-settings-title">${t(`settings.section.${section.id}`)}</h2>
        <p class="cx-page__lead">${t(`settings.lead.${section.id}`)}</p>
        ${section.project && !open ? html`<${EmptyState} icon="file" level=${3}
          title=${t('settings.no_project.title')} action=${{ label: t('settings.no_project.action'), href: '/start' }}>
          ${t('settings.no_project.text')}<//>`
          : html`<${Section} key=${section.id} ctx=${ctx} app=${app} open=${open} />`}
      </section>
    </div>
  </div>`;
}
