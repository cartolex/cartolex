// SPDX-License-Identifier: MIT
/*
 * Runs before the first paint (a classic script in the document's head, not
 * a module, and not inline: the Content-Security-Policy forbids inline
 * scripts). It applies the theme the person chose, so a dark theme never
 * flashes white. The key and values are those of core/stores/prefs.js.
 */
(function applyStoredTheme() {
  try {
    var raw = window.localStorage.getItem('cartolex.prefs/1');
    var theme = raw ? JSON.parse(raw).theme : null;
    if (theme === 'light' || theme === 'dark') {
      document.documentElement.setAttribute('data-theme', theme);
    }
  } catch (error) {
    // No storage (private browsing): follow the system setting.
  }
})();
