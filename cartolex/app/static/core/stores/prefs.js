// SPDX-License-Identifier: MIT
/**
 * The person's interface preferences: theme and interface language.
 *
 * They are kept in the browser's local storage under one key, per app id;
 * `boot-theme.js` reads the same key before the first paint so a dark theme
 * never flashes white. The interface language is independent of a project's
 * keyword languages.
 */
import { effect, signal } from '../preact.js';

export const PREFS_KEY = 'cartolex.prefs/1';
export const THEMES = ['system', 'light', 'dark'];

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
 * The preferences store: `theme` ('system'|'light'|'dark') and `locale`
 * (a tag, or null to follow the browser) as signals, saved on every change.
 */
export function createPrefs(storage = safeStorage()) {
  const saved = read(storage);
  const theme = signal(THEMES.includes(saved.theme) ? saved.theme : 'system');
  const locale = signal(typeof saved.locale === 'string' ? saved.locale : null);
  const dismissedJobs = signal(Array.isArray(saved.dismissedJobs) ? saved.dismissedJobs.slice(-100) : []);
  const dispose = effect(() => {
    const data = { theme: theme.value, locale: locale.value, dismissedJobs: dismissedJobs.value };
    try {
      if (storage) storage.setItem(PREFS_KEY, JSON.stringify(data));
    } catch {
      // Private browsing or a full storage: the preferences last for this visit.
    }
  });
  return { theme, locale, dismissedJobs, dispose };
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
