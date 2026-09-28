// SPDX-License-Identifier: MIT
/**
 * The object an extension module's `register(api)` receives.
 *
 * An extension is an ES module listed in the manifest's `modules`; the shell
 * imports it before the first render and calls its `register(api)` (which
 * may be async). Through `api` it adds pages, slot contributions, facets and
 * map layers, adds messages to the catalogues, and uses the same Preact,
 * signals, components, API client and stores as the app. It never touches
 * the DOM outside what it renders.
 *
 *   export function register(api) {
 *     const { html } = api.ui;
 *     api.i18n.add('en', { 'ext.demo.title': 'Demo' });
 *     api.pages.add({ id: 'demo', route: '/demo', label: 'ext.demo.title', order: 90,
 *                     load: () => import('./page.js') });
 *     api.slots.add('overview.cards', { id: 'demo', component: () => html`<p>…</p>` });
 *   }
 */
import * as preact from './preact.js';
import * as i18n from './i18n.js';
import * as components from '../components/index.js';
import { definePage, usePage, usePageTitle } from './page.js';

/** Version of this interface; an extension can refuse to run on another. */
export const EXTENSION_API_VERSION = 1;

/** The `register(api)` argument for the running *app* (see app.js). */
export function createExtensionApi(app) {
  const { registries } = app;
  return Object.freeze({
    version: EXTENSION_API_VERSION,
    manifest: app.manifest,
    pages: Object.freeze({
      /** Add a routed page `{id, route, label, load|module|page, order?, placement?, area?}`. */
      add: (entry) => registries.pages.add({ placement: 'main', ...entry }),
    }),
    slots: Object.freeze({
      /** Add `{id, order?, component}` to the slot *name*. */
      add: (name, contribution) => registries.slots.add(name, contribution),
    }),
    facets: Object.freeze({ add: (facet) => registries.facets.add(facet) }),
    layers: Object.freeze({ add: (layer) => registries.layers.add(layer) }),
    i18n: Object.freeze({
      /** Merge *messages* over the catalogue of *locale* (now and at every switch). */
      add: (locale, messages) => i18n.addMessages(locale, messages),
      t: i18n.t,
      locale: i18n.locale,
      formatNumber: i18n.formatNumber,
      formatPercent: i18n.formatPercent,
      formatDate: i18n.formatDate,
      formatList: i18n.formatList,
    }),
    ui: Object.freeze({ ...preact, definePage, usePage, usePageTitle }),
    components,
    api: app.api,
    stores: app.stores,
    navigate: (path) => app.router && app.router.navigate(path),
    toast: (toast) => app.toaster.show(toast),
  });
}
