// SPDX-License-Identifier: MIT
/**
 * Pages written as Preact components.
 *
 * `definePage(Component, {styles})` gives the page object the router mounts:
 * it loads the page's own style sheets (once per visit of the app), renders
 * the component into `ctx.root` and returns the teardown that unmounts it
 * (which runs every effect's cleanup). Inside, `usePage()` gives the
 * navigation's context (`api` bound to the page's signal, `guard`,
 * `setTitle`, `ready`…), and `usePageTitle(text)` keeps the document's title.
 */
import { createContext, html, render, useContext, useEffect } from './preact.js';

export const PageContext = createContext(null);
const sheets = new Map();

/** Load a style sheet once; resolves when it applies (or failed: the page still shows). */
export function loadStylesheet(href) {
  if (!sheets.has(href)) {
    sheets.set(href, new Promise((resolve) => {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = href;
      link.addEventListener('load', () => resolve(true), { once: true });
      link.addEventListener('error', () => resolve(false), { once: true });
      document.head.append(link);
    }));
  }
  return sheets.get(href);
}

/**
 * The page object for *Component*; it receives `ctx` as a prop too.
 * @param {Function} Component
 * @param {{styles?: string[]}} [options] style sheets the page needs
 */
export function definePage(Component, { styles = [] } = {}) {
  return {
    async mount(ctx) {
      if (styles.length) await Promise.all(styles.map(loadStylesheet));
      // Left while the style sheets loaded: the outlet is someone else's now.
      if (!ctx.isCurrent()) return undefined;
      render(html`<${PageContext.Provider} value=${ctx}><${Component} ctx=${ctx} /><//>`, ctx.root);
      return () => render(null, ctx.root);
    },
  };
}

/** The current page's navigation context. */
export function usePage() {
  return useContext(PageContext);
}

/** Keep the document's title as *text* (re-set when it changes, e.g. on a language switch). */
export function usePageTitle(text) {
  const ctx = usePage();
  useEffect(() => {
    if (ctx) ctx.setTitle(text);
  }, [text]);
}
