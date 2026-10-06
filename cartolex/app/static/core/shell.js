// SPDX-License-Identifier: MIT
/**
 * The shell: the header (brand, which leads to the About page; the project
 * menu; navigation with each area's status dot; activity; display settings,
 * the version and build at their foot), the outlet pages render into, the
 * Activity drawer, the toasts and the « leave without saving? » question.
 *
 * The outlet is rendered once and never re-rendered by Preact: the router
 * owns what is inside it.
 */
import { Component, html, useRef, useState } from './preact.js';
import { autonym, locale, t } from './i18n.js';
import { currentRoute } from './router.js';
import { runtime } from './runtime.js';
import { ProjectMenu } from './project-menu.js';
import { areaOfPage } from './states.js';
import { THEMES } from './stores/prefs.js';
import {
  ActivityDrawer, ActivityIndicator, ConfirmDialog, Icon, MenuButton, Slot, StatusDot, Toaster,
} from '../components/index.js';

/** The documentation, served by the app with this version (tools/build_docs.py). */
export const DOCS_URL = '/static/docs/index.html';

/** The version and build in one line: « 1.0.0 · 3f2a1c9 · 2026-10-06 ». */
export function versionLine(app) {
  const build = app && app.build;
  return [app && app.version, build && build.commit && build.commit.slice(0, 7), build && build.date]
    .filter(Boolean).join(' · ');
}

/** The element pages render into; Preact renders it once and leaves its content alone. */
class Outlet extends Component {
  shouldComponentUpdate() {
    return false;
  }

  render({ outletRef }) {
    return html`<div class="cx-outlet" ref=${outletRef}></div>`;
  }
}

function NavItem({ entry, areas, active }) {
  const area = areas.get(areaOfPage(entry));
  const label = t(entry.label);
  return html`<li class="cx-nav__item">
    <a href=${entry.route} class=${`cx-nav__link ${active ? 'is-active' : ''}`}
      aria-current=${active ? 'page' : undefined} data-nav=${entry.id}>
      <span class="cx-nav__label">${label}</span>
      ${area ? html`<${StatusDot} state=${area.state} size="s" class="cx-nav__dot" />` : null}
    </a>
  </li>`;
}

/**
 * @param {{app: object}} props the running app (see app.js: manifest, registries,
 *   stores, toaster, outletRef, leaveQuestion, switchLocale, setTheme)
 */
export function Shell({ app }) {
  const { manifest, registries, stores, toaster } = app;
  const [activityOpen, setActivityOpen] = useState(false);
  runtime.openActivity = () => setActivityOpen(true);
  const activityButton = useRef(null);
  const route = currentRoute.value;
  const nav = registries.pages.list().filter((p) => (p.placement || 'main') === 'main' && p.label);
  const areas = stores.project.areas.value;
  const question = app.leaveQuestion.value;
  const branding = manifest.branding || {};
  const brandName = branding.name || manifest.app.name;
  const defaultLogo = !branding.logo || branding.logo === '/static/brand/logo.svg';

  const settingsPages = registries.pages.list().filter((p) => p.placement === 'settings' && p.label);
  const displayItems = [
    ...settingsPages.map((entry) => ({ id: `page:${entry.id}`, label: t(entry.label), icon: 'settings' })),
    ...(settingsPages.length ? [{ kind: 'separator', id: 'sep-pages' }] : []),
    {
      kind: 'group', id: 'theme', label: t('display.theme'),
      items: THEMES.map((theme) => ({
        id: `theme:${theme}`, kind: 'radio', label: t(`display.theme.${theme}`),
        checked: stores.prefs.theme.value === theme,
      })),
    },
    { kind: 'separator', id: 'sep' },
    {
      kind: 'group', id: 'language', label: t('display.language'),
      items: manifest.locales.available.map((code) => ({
        id: `locale:${code}`, kind: 'radio', label: autonym(code), lang: code,
        checked: locale.value === code,
      })),
    },
    { kind: 'separator', id: 'sep-about' },
    { id: 'about', label: t('shell.about_item', { name: brandName }), hint: versionLine(manifest.app) },
  ];
  const onDisplay = (item) => {
    const [kind, value] = item.id.split(/:(.*)/s);
    if (kind === 'page') {
      const entry = registries.pages.get(value);
      if (entry) app.router.navigate(entry.route);
    }
    if (kind === 'about') app.router.navigate('/about');
    if (kind === 'theme') app.setTheme(value);
    if (kind === 'locale') app.switchLocale(value);
  };

  return html`<div class="cx-shell">
    <a class="cx-skip-link" href="#cx-main">${t('shell.skip')}</a>
    <header class="cx-header">
      <a class="cx-brand" href="/about" data-nav="about"
        aria-label=${t('shell.about_link', { name: brandName })}>
        <img class=${`cx-brand__logo ${defaultLogo ? 'cx-brand__logo--mono' : ''}`}
          src=${branding.logo || '/static/brand/logo.svg'} alt="" width="24" height="24" />
        <span class="cx-brand__name">${brandName}</span>
      </a>
      ${manifest.capabilities && manifest.capabilities.hosted ? null : html`<${ProjectMenu} app=${app} />`}
      <nav class="cx-nav" aria-label=${t('shell.nav')}>
        <ul class="cx-nav__list">
          ${nav.map((entry) => html`<${NavItem} key=${entry.id} entry=${entry} areas=${areas}
            active=${route && route.pageId === entry.id} />`)}
        </ul>
      </nav>
      <div class="cx-header__tools">
        <${Slot} slots=${registries.slots} name="header.actions" class="cx-header__slot" />
        <${ActivityIndicator} jobs=${stores.jobs} buttonRef=${activityButton}
          onOpen=${() => setActivityOpen(true)} />
        <a class="cx-button cx-button--ghost cx-button--m cx-header__docs" href=${DOCS_URL}
          target="_blank" rel="noopener" data-nav="docs" aria-label=${t('shell.docs')}>
          <${Icon} name="help" /><span class="cx-button__label">${t('shell.docs')}</span></a>
        <${MenuButton} label=${t('display.menu')} icon="settings" iconOnly variant="ghost"
          items=${displayItems} onSelect=${onDisplay} />
      </div>
    </header>
    <main id="cx-main" class="cx-main" tabindex="-1">
      <${Outlet} outletRef=${app.outletRef} />
    </main>
    <${ActivityDrawer} open=${activityOpen} jobs=${stores.jobs}
      onClose=${() => setActivityOpen(false)} />
    <${ConfirmDialog} open=${Boolean(question)} title=${t('leave.title')} danger
      confirmLabel=${t('leave.confirm')} cancelLabel=${t('leave.stay')}
      onAnswer=${(answer) => question && question.resolve(answer)}>
      <p>${t('leave.text')}</p>
    <//>
    <${Toaster} toaster=${toaster} />
    <div class="cx-visually-hidden" aria-live="polite" aria-atomic="true">${app.announcement.value}</div>
  </div>`;
}
