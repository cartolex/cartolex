// SPDX-License-Identifier: MIT
/**
 * Telling a local app that this page is open (`POST /api/presence`), so that
 * it stops by itself once every page is closed (the manifest's capability
 * `idle_stop`).
 *
 * The page calls every thirty seconds, hidden or not (a browser slows a hidden
 * tab's timers to about once a minute; the server counts a page closed after
 * ten minutes without a call), and says goodbye when it is closed, reloaded or
 * left. Pass it a client of its own: its calls never count in a page's request
 * budget.
 */
export const PING_MS = 30000;

function pageId() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') {
    return globalThis.crypto.randomUUID();
  }
  return `p${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

/** Start saying this page is open; returns a function that stops it. */
export function startPresence(api, { every = PING_MS } = {}) {
  const page = pageId();
  const say = (bye = false) => api.post('/api/presence', { page, bye }, { keepalive: bye });
  const onHide = () => {
    say(true);
  };
  const onShow = (event) => {
    if (event.persisted) say();
  };
  say();
  const timer = setInterval(() => say(), every);
  window.addEventListener('pagehide', onHide);
  window.addEventListener('pageshow', onShow);
  return () => {
    clearInterval(timer);
    window.removeEventListener('pagehide', onHide);
    window.removeEventListener('pageshow', onShow);
  };
}
