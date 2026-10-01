// SPDX-License-Identifier: MIT
/**
 * Start the app, in this order:
 *
 *   1. GET /api/app/manifest
 *   2. load the interface language's catalogues
 *   3. import() each extension module and call its register(api)
 *   4. render the shell (header, navigation)
 *   5. route: mount the page of the current address
 *
 * then refresh the project state (the status dots already show the cached
 * one) and start the jobs poller.
 */
import { html, render, signal } from './preact.js';
import { ApiClient, readCookie } from './api.js';
import { errorFromException, errorFromResponse, networkError } from './errors.js';
import { createExtensionApi } from './extension-api.js';
import { loadLocale, locale, missingKeys, pickLocale, t } from './i18n.js';
import { Router } from './router.js';
import { runtime } from './runtime.js';
import { Shell } from './shell.js';
import { createJobsStore } from './stores/jobs.js';
import { createPrefs, applyTheme } from './stores/prefs.js';
import { createProjectStore } from './stores/project.js';
import { createRegistries } from './registries.js';
import { ErrorCard, createToaster, jobTitle } from '../components/index.js';
import { NotFoundPage } from '../pages/not-found.js';

export const MANIFEST_URL = '/api/app/manifest';
const FALLBACK_CATALOGUES = { en: ['/static/i18n/en.json'] };

/** Pages the shell always offers, whatever the manifest says. */
const BUILTIN_PAGES = [
  { id: 'gallery', route: '/gallery', label: 'nav.gallery', module: '/static/pages/gallery.js',
    placement: 'hidden', order: 1000 },
];

async function fetchManifest() {
  let response;
  try {
    response = await fetch(MANIFEST_URL, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
  } catch (cause) {
    return { ok: false, error: networkError({ method: 'GET', path: MANIFEST_URL, cause }) };
  }
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (!response.ok || !data || data.format !== 'cartolex-manifest/1') {
    return {
      ok: false,
      error: errorFromResponse(data, {
        status: response.status, method: 'GET', path: MANIFEST_URL,
        requestId: response.headers.get('X-Request-Id'),
      }),
    };
  }
  return { ok: true, data };
}

function BootError({ error }) {
  return html`<main class="cx-boot-error" id="cx-main">
    <h1 class="cx-page__title">${t('boot.failed')}</h1>
    <${ErrorCard} error=${error} live />
  </main>`;
}

/** Start the app inside *root*; resolves to the running app (tests use it). */
export async function boot(root) {
  const prefs = createPrefs();
  applyTheme(prefs.theme.value);
  const browserLanguages = navigator.languages || [navigator.language];

  const manifestResult = await fetchManifest();
  if (!manifestResult.ok) {
    const code = pickLocale(['en', 'fr', 'pt-BR'], { preferred: prefs.locale.value, browser: browserLanguages });
    try {
      await loadLocale(code, { ...FALLBACK_CATALOGUES, [code]: [`/static/i18n/${code}.json`] });
    } catch {
      // Without catalogues the error card shows its keys; better than nothing.
    }
    root.removeAttribute('aria-busy');
    render(html`<${BootError} error=${manifestResult.error} />`, root);
    return null;
  }
  const manifest = manifestResult.data;
  runtime.app = { ...manifest.app };
  applyAccent(manifest.branding && manifest.branding.accent);

  const locales = manifest.locales;
  const code = pickLocale(locales.available, {
    preferred: prefs.locale.value, browser: browserLanguages, fallback: locales.default,
  });
  await loadLocale(code, locales.catalogues, { fallback: locales.default });

  const csrf = manifest.security || {};
  const api = new ApiClient({
    csrfHeader: csrf.csrf_header || 'X-Cartolex-CSRF',
    csrfToken: () => csrf.csrf_token || readCookie(csrf.csrf_cookie || 'cartolex_csrf'),
    language: () => locale.value,
  });
  const toaster = createToaster();
  runtime.toast = toaster.show;
  const registries = createRegistries();
  for (const entry of BUILTIN_PAGES) registries.pages.add(entry);
  for (const entry of manifest.nav || []) {
    registries.pages.add({ placement: 'main', ...entry });
  }

  const project = createProjectStore({
    api, projectId: manifest.project && manifest.project.open ? manifest.project.id : null,
  });
  const jobs = createJobsStore({
    api,
    enabled: Boolean(manifest.project && manifest.project.open),
    dismissed: prefs.dismissedJobs,
    onFinished: (job) => {
      // a layout preview changes nothing in the project, and the map shows it: no toast
      if (job.kind === 'preview') return;
      project.refresh();
      if (job.state === 'succeeded') {
        toaster.show({ kind: 'success', title: t('job.toast.succeeded', { title: jobTitle(job) }) });
      } else if (job.state === 'failed' || job.state === 'interrupted') {
        toaster.show({ kind: 'error', id: `job-${job.id}`, title: t('job.toast.failed', { title: jobTitle(job) }) });
      } else if (job.state === 'waiting') {
        toaster.show({ kind: 'info', id: `job-${job.id}`, title: t('job.toast.waiting') });
      } else if (job.state === 'paused') {
        toaster.show({ kind: 'info', id: `job-${job.id}`, title: t('job.toast.paused', { title: jobTitle(job) }) });
      }
    },
  });

  const outletRef = { current: null };
  const leaveQuestion = signal(null);
  const announcement = signal('');
  const brand = (manifest.branding && manifest.branding.name) || manifest.app.name;
  const app = {
    manifest, registries, api, toaster, outletRef, leaveQuestion, announcement,
    stores: { prefs, project, jobs },
    setTheme(theme) {
      prefs.theme.value = theme;
      applyTheme(theme);
    },
    async switchLocale(next) {
      prefs.locale.value = next;
      await loadLocale(next, locales.catalogues, { fallback: locales.default });
      announcement.value = t('display.language_changed');
    },
    router: null,
    problems: [],
    readyLog: [],
  };

  // Extensions: import every module, then call its register(api).
  const extensionApi = createExtensionApi(app);
  const loaded = await Promise.allSettled((manifest.modules || []).map(async (url) => {
    const mod = await import(url);
    if (typeof mod.register !== 'function') throw new Error(`${url} exports no register(api)`);
    await mod.register(extensionApi);
  }));
  loaded.forEach((outcome, i) => {
    if (outcome.status === 'rejected') {
      app.problems.push({ module: manifest.modules[i], error: errorFromException(outcome.reason, { code: 'extension_failed' }) });
    }
  });

  root.removeAttribute('aria-busy');
  render(html`<${Shell} app=${app} />`, root);

  const home = (registries.pages.list().find((p) => (p.placement || 'main') === 'main') || {}).route || '/gallery';
  const router = new Router({
    outlet: outletRef.current,
    pages: () => registries.pages.list(),
    api,
    home,
    notFound: { id: 'not-found', page: NotFoundPage },
    confirmLeave: () => new Promise((resolve) => {
      leaveQuestion.value = {
        resolve: (answer) => {
          leaveQuestion.value = null;
          resolve(answer);
        },
      };
    }),
    onPageError: (error, ctx) => {
      const model = error && error.code ? error : errorFromException(error, { code: 'page_failed' });
      render(html`<div class="cx-page"><h1 class="cx-page__title" tabindex="-1">${t('page.failed')}</h1>
        <${ErrorCard} error=${model} live /></div>`, ctx.root);
      ctx.onLeave(() => render(null, ctx.root));
    },
    onReady: ({ initial, pageId, duration }) => {
      app.readyLog.push({ pageId, duration, at: performance.now() });
      if (app.readyLog.length > 50) app.readyLog.shift();
      if (!initial) {
        const heading = outletRef.current.querySelector('h1');
        if (heading) {
          if (!heading.hasAttribute('tabindex')) heading.setAttribute('tabindex', '-1');
          heading.focus({ preventScroll: true });
          announcement.value = heading.textContent.trim();
        }
      }
    },
    context: {
      app,
      setTitle: (text) => {
        document.title = text ? `${text} · ${brand}` : brand;
      },
    },
  });
  app.router = router;
  runtime.navigate = (path) => router.navigate(path);
  runtime.page = () => (router.active && router.active.pageId) || '';

  for (const problem of app.problems) {
    toaster.show({ kind: 'error', title: t('extension.failed'), message: problem.module });
  }

  project.refresh();
  jobs.start();
  exposeDebug(app);
  await router.start();
  return app;
}

/**
 * A host's accent (`branding.accent: {light, dark}`, `#rrggbb` each, checked
 * by the server for contrast) replaces the accent and focus colour of that
 * theme; tokens.css reads it through `--cx-brand-accent-light|dark`.
 */
function applyAccent(accent) {
  if (!accent) return;
  for (const theme of ['light', 'dark']) {
    const value = accent[theme];
    if (typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value)) {
      document.documentElement.style.setProperty(`--cx-brand-accent-${theme}`, value);
    }
  }
}

/** A read-only handle for the browser tests and for diagnostics (no project data). */
function exposeDebug(app) {
  Object.defineProperty(window, '__cartolex', {
    configurable: true,
    enumerable: false,
    value: Object.freeze({
      app: () => app.manifest.app,
      readyLog: () => app.readyLog.slice(),
      requests: () => app.api.sent,
      problems: () => app.problems.map((p) => p.module),
      missingKeys: () => missingKeys(),
      navigate: (path) => app.router.navigate(path),
    }),
  });
}

