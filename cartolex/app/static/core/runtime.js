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
};

/** Run an ErrorCard or EmptyState action: 'reload', 'open:<path>' or a path. */
export function runAction(action, { onRetry } = {}) {
  if (!action) return;
  if (action === 'retry' && onRetry) onRetry();
  else if (action === 'reload' || action === 'retry') window.location.reload();
  else if (action.startsWith('open:')) runtime.navigate(action.slice(5));
  else if (action.startsWith('/')) runtime.navigate(action);
}
