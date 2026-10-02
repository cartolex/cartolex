// SPDX-License-Identifier: MIT
/**
 * What components need from the running app without importing the shell:
 * the app's name and version (for diagnostics), how to navigate, how to show
 * a toast. The shell fills it at boot; before that (or in a test page) it
 * holds harmless defaults.
 */
export const runtime = {
  app: { id: 'cartolex', name: 'cartolex', version: '' },
  /** Go to an app path (the router's navigate once the shell runs). */
  navigate: (path) => {
    window.location.assign(path);
  },
  /** Show a toast (the toaster's `toast` once the shell runs). */
  toast: () => null,
  /** The current page's id, for diagnostics. */
  page: () => '',
  /** Open the Activity drawer (the shell's, once it runs). */
  openActivity: () => {},
};

/**
 * The next actions of the API's errors (`error.next.action`) the interface
 * runs by itself, and where each goes. `confirm` and `fix-input` belong to the
 * page that made the request (an ErrorCard's `onAction`); `sign-in` and
 * `none` are said in words, with no button. Extensions may also name an
 * address: `open:<path>` or a path.
 */
export const APP_ACTIONS = new Set(['retry', 'reload', 'settings', 'open-project', 'build', 'wait']);

/** The kind of an action: one of APP_ACTIONS, `report`, `open` (an address), or itself. */
export function actionKind(action) {
  if (!action) return 'none';
  if (action.startsWith('open:') || action.startsWith('/')) return 'open';
  return action;
}

/** Run an action of APP_ACTIONS or an address; `onRetry` and `onReload` are the page's own. */
export function runAction(action, { onRetry, onReload } = {}) {
  const kind = actionKind(action);
  if (kind === 'retry') (onRetry || (() => window.location.reload()))();
  else if (kind === 'reload') (onReload || onRetry || (() => window.location.reload()))();
  else if (kind === 'settings') runtime.navigate('/settings');
  else if (kind === 'open-project') runtime.navigate('/start');
  else if (kind === 'build') runtime.navigate('/build');
  else if (kind === 'wait') runtime.openActivity();
  else if (kind === 'open') runtime.navigate(action.startsWith('open:') ? action.slice(5) : action);
}
