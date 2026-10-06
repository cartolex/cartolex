// SPDX-License-Identifier: MIT
/**
 * The person's interface preferences: theme, interface language, the jobs
 * they dismissed from the Activity list, and a few other settings by key (`get(key)`,
 * `set(key, value)`: the atlas's layout, the colour scheme of the themes).
 *
 * They are kept by the app (`GET` and `PUT /api/me/preferences`), so they
 * outlive the browser's own storage; the browser's local storage keeps a copy
 * under one key, which `boot-theme.js` reads before the first paint so a dark
 * theme never flashes white, and which stands in when the app cannot be
 * reached. The interface language is independent of a project's keyword
 * languages.
 */
import { effect, signal } from '../preact.js';

export const PREFS_KEY = 'cartolex.prefs/1';
export const PREFS_URL = '/api/me/preferences';
export const THEMES = ['system', 'light', 'dark'];
/** Dismissed job ids kept (the server keeps as many). */
export const MAX_DISMISSED = 200;

function read(storage) {
  try {
    const raw = storage && storage.getItem(PREFS_KEY);
    const data = raw ? JSON.parse(raw) : {};
    return data && typeof data === 'object' ? data : {};
  } catch {
    return {};
  }
}

/**
 * The preferences store: `theme` ('system'|'light'|'dark'), `locale` (a tag,
 * or null to follow the browser) and `dismissedJobs` as signals, saved on every
 * change in the browser and, once `connect()`ed, by the app. Between the
 * browser's copy and the app's, the newer wins (`at`, `saved_at`); a browser's
 * copy without a time (from an earlier version) wins and is sent to the app; no
 * copy at all (a new address, a cleared cache) takes the app's.
 */
export function createPrefs(storage = safeStorage()) {
  const saved = read(storage);
  const theme = signal(THEMES.includes(saved.theme) ? saved.theme : 'system');
  const locale = signal(typeof saved.locale === 'string' ? saved.locale : null);
  const dismissedJobs = signal(Array.isArray(saved.dismissedJobs) ? saved.dismissedJobs.slice(-MAX_DISMISSED) : []);
  const other = signal(saved.other && typeof saved.other === 'object' ? saved.other : {});
  // No copy at all (a new address, a cleared cache): the app's preferences are taken.
  const copied = Object.keys(saved).length > 0;
  let at = typeof saved.at === 'number' ? saved.at : null;
  let server = null;
  // The effect's first run and the values taken from the app are not changes.
  let quiet = true;
  // One save at a time, in order: a change made while one is sent is sent after it.
  let sending = false;
  let again = false;
  const keep = () => {
    const data = { theme: theme.value, locale: locale.value, dismissedJobs: dismissedJobs.value, other: other.value, at };
    try {
      if (storage) storage.setItem(PREFS_KEY, JSON.stringify(data));
    } catch {
      // Private browsing or a full storage: the app keeps them.
    }
  };
  const send = async () => {
    if (!server) return;
    if (sending) {
      again = true;
      return;
    }
    sending = true;
    do {
      again = false;
      await server.put(PREFS_URL, {
        theme: theme.value, locale: locale.value, dismissed_jobs: dismissedJobs.value,
        saved_at: at, other: other.value,
      });
    } while (again);
    sending = false;
  };
  const dispose = effect(() => {
    // Read every signal here, so the effect runs again on any change.
    const values = [theme.value, locale.value, dismissedJobs.value, other.value];
    if (values.length && !quiet) {
      at = Date.now();
      keep();
      send();
    }
  });
  quiet = false;
  return {
    theme,
    locale,
    dismissedJobs,
    other,
    /** A setting kept by key (the atlas's layout, the colour scheme), or undefined. */
    get(key) {
      return other.value[key];
    },
    /** Keep a setting by key (a string, a number or a boolean). */
    set(key, value) {
      if (other.value[key] === value) return;
      other.value = { ...other.value, [key]: value };
    },
    /**
     * Take what the app kept (`GET /api/me/preferences`'s answer) when it is newer than
     * the browser's copy, else send the browser's copy; then save every change through
     * *client* (an ApiClient).
     */
    connect(answer, client) {
      const kept = answer && answer.stored ? answer.preferences || {} : null;
      server = client;
      const keptOther = kept && kept.other && typeof kept.other === 'object' ? kept.other : {};
      const keptAt = kept && typeof kept.saved_at === 'number' ? kept.saved_at : 0;
      if (kept && (!copied || (at !== null && keptAt >= at))) {
        quiet = true;
        at = keptAt;
        if (THEMES.includes(kept.theme)) theme.value = kept.theme;
        if (kept.locale === null || (answer.locales || []).includes(kept.locale)) locale.value = kept.locale;
        if (Array.isArray(kept.dismissed_jobs)) dismissedJobs.value = kept.dismissed_jobs.slice(-MAX_DISMISSED);
        other.value = { ...other.value, ...keptOther };
        quiet = false;
        keep();
      } else {
        quiet = true;
        other.value = { ...keptOther, ...other.value };
        quiet = false;
        if (at === null) {
          at = Date.now();
          keep();
        }
        send();
      }
    },
    dispose() {
      server = null;
      dispose();
    },
  };
}

/** The preferences the app keeps for this person, or null when they cannot be read. */
export async function fetchPrefs() {
  try {
    const response = await fetch(PREFS_URL, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

/** Apply *theme* to the document: `data-theme` is set for a manual choice only. */
export function applyTheme(theme, root = document.documentElement) {
  if (theme === 'light' || theme === 'dark') root.dataset.theme = theme;
  else delete root.dataset.theme;
}

/** The theme in effect now ('light' or 'dark'), following the system when asked. */
export function effectiveTheme(theme) {
  if (theme === 'light' || theme === 'dark') return theme;
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function safeStorage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}
