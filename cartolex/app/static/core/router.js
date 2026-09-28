// SPDX-License-Identifier: MIT
/**
 * History-API router with a page lifecycle.
 *
 * Routes are real paths (`/keywords`, `/gallery`): the server answers every
 * app route with the shell document, and `/api/` and `/static/` stay the
 * server's. Real paths keep the fragment free for in-page anchors, give
 * shareable addresses, and let a hosted service apply its access rules per
 * path.
 *
 * Each navigation gets a token and an AbortController. The page's
 * `mount(ctx)` renders into `ctx.root` and returns its teardown (or a promise
 * of it). Leaving a page aborts its signal, runs its teardown, and drops the
 * results of its calls that arrive later: `ctx.api` and `ctx.keep()` return
 * promises that never settle once the page is left, so no callback of a
 * torn-down page ever runs.
 *
 * Guards: a page with unsaved edits registers `ctx.guard({dirty})`; leaving
 * while `dirty()` is true asks first (the shell's dialog), and closing the
 * browser tab asks the browser's question.
 */
import { signal } from './preact.js';

/**
 * A promise that never settles. A fresh one each time: whatever waits on it
 * (a page's `await` of a late answer) is reachable from it only, so it is
 * collected with it once the page is gone. One shared promise would keep
 * every such continuation, and the page it holds, for the life of the app.
 */
const never = () => new Promise(() => {});

/** The page currently shown: `{path, pageId, params, token}` (null before the first route). */
export const currentRoute = signal(null);

/** Compile a route pattern (`/people/:id`) into a matcher. */
export function compileRoute(pattern) {
  const names = [];
  const parts = pattern.replace(/\/+$/, '').split('/').map((seg) => {
    if (seg.startsWith(':')) {
      names.push(seg.slice(1));
      return '([^/]+)';
    }
    return seg.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  });
  const regex = new RegExp(`^${parts.join('/') || ''}/?$`);
  // Static segments rank before parameters: `/people/new` wins over `/people/:id`.
  const score = pattern.split('/').reduce((s, seg) => s + (seg.startsWith(':') ? 1 : 2), 0);
  return {
    pattern,
    score,
    match(path) {
      const m = regex.exec(path);
      if (!m) return null;
      const params = {};
      names.forEach((name, i) => {
        params[name] = decodeURIComponent(m[i + 1]);
      });
      return params;
    },
  };
}

export class Router {
  /**
   * @param {object} options
   * @param {HTMLElement} options.outlet the element pages render into
   * @param {() => Array<object>} options.pages the page entries ({id, route, load})
   * @param {object} options.api the ApiClient
   * @param {string} [options.home] where `/` goes
   * @param {(to: string) => Promise<boolean>} [options.confirmLeave] asks before leaving edits
   * @param {object} [options.notFound] the page entry for unknown paths
   * @param {(error: any, ctx: object) => void} [options.onPageError] shows a page that failed
   * @param {(info: object) => void} [options.onReady] called when a page is ready
   * @param {object} [options.context] extra fields given to every page's ctx
   */
  constructor({ outlet, pages, api, home = '/', confirmLeave, notFound, onPageError, onReady,
    context = {} }) {
    this.outlet = outlet;
    this.pages = pages;
    this.api = api;
    this.home = home;
    this.confirmLeave = confirmLeave || (async () => true);
    this.notFound = notFound;
    this.onPageError = onPageError;
    this.onReady = onReady;
    this.context = context;
    this.seq = 0;
    this.active = null;
    this.guards = new Set();
    this.index = 0;
    this.ignorePop = false;
    this.onClick = this.onClick.bind(this);
    this.onPop = this.onPop.bind(this);
    this.onBeforeUnload = this.onBeforeUnload.bind(this);
  }

  /** Listen to links and history, then show the current address. */
  start() {
    if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
    document.addEventListener('click', this.onClick);
    window.addEventListener('popstate', this.onPop);
    window.addEventListener('beforeunload', this.onBeforeUnload);
    const state = history.state || {};
    this.index = typeof state.cxIndex === 'number' ? state.cxIndex : 0;
    history.replaceState({ ...state, cxIndex: this.index }, '');
    return this.go(location.pathname + location.search + location.hash, { initial: true });
  }

  /** Stop listening and tear the current page down. */
  stop() {
    document.removeEventListener('click', this.onClick);
    window.removeEventListener('popstate', this.onPop);
    window.removeEventListener('beforeunload', this.onBeforeUnload);
    this.leave();
  }

  /**
   * Add a guard `{dirty: () => boolean, confirm?: (to) => Promise<boolean>}`;
   * returns its remover. Pages use `ctx.guard`, removed when they are left.
   */
  addGuard(guard) {
    this.guards.add(guard);
    return () => this.guards.delete(guard);
  }

  /** Whether a guard has unsaved edits now. */
  isDirty() {
    for (const g of this.guards) if (g.dirty()) return true;
    return false;
  }

  /** Go to *to* (a path); resolves to false when a guard kept the page. */
  navigate(to, { replace = false } = {}) {
    return this.go(to, { replace });
  }

  /** Match a path against the page entries: `{entry, params}` or null. */
  match(path) {
    let best = null;
    for (const entry of this.pages()) {
      if (!entry.route) continue;
      const matcher = entry.matcher || (entry.matcher = compileRoute(entry.route));
      const params = matcher.match(path);
      if (params && (!best || matcher.score > best.entry.matcher.score)) best = { entry, params };
    }
    return best;
  }

  async canLeave(to) {
    const dirty = [...this.guards].filter((g) => g.dirty());
    if (!dirty.length) return true;
    const own = dirty.find((g) => typeof g.confirm === 'function');
    return own ? Boolean(await own.confirm(to)) : Boolean(await this.confirmLeave(to));
  }

  async go(to, { replace = false, initial = false, pop = null } = {}) {
    const url = new URL(to, location.href);
    if (url.origin !== location.origin) {
      location.assign(url.href);
      return false;
    }
    let path = url.pathname;
    if (path === '/' && this.home !== '/') {
      path = this.home;
      replace = true;
    }
    const full = path + url.search + url.hash;
    const current = this.active && this.active.full;
    if (!initial && pop === null && current === full) return true;
    // Only the fragment changes: the same page stays, nothing is mounted again.
    if (!initial && current && current.split('#')[0] === path + url.search) {
      if (pop === null) {
        this.index += 1;
        history.pushState({ cxIndex: this.index }, '', full);
      }
      this.active.full = full;
      const target = url.hash && document.getElementById(decodeURIComponent(url.hash.slice(1)));
      if (target) target.scrollIntoView();
      return true;
    }
    if (!initial && !(await this.canLeave(full))) {
      if (pop !== null && pop !== 0) {
        this.ignorePop = true;
        history.go(-pop);
      }
      return false;
    }
    if (this.active) this.saveScroll();
    this.leave();
    if (!initial && pop === null) {
      if (replace) {
        history.replaceState({ cxIndex: this.index }, '', full);
      } else {
        this.index += 1;
        history.pushState({ cxIndex: this.index }, '', full);
      }
    } else if (initial && full !== location.pathname + location.search + location.hash) {
      history.replaceState({ cxIndex: this.index }, '', full);
    }
    return this.enter(url, path, full, { initial, pop });
  }

  async enter(url, path, full, { initial, pop }) {
    this.seq += 1;
    const token = this.seq;
    const controller = new AbortController();
    const found = this.match(path);
    const entry = found ? found.entry : this.notFound;
    const params = found ? found.params : {};
    const active = { token, controller, teardowns: [], full, pageId: entry && entry.id, ready: false };
    this.active = active;
    const pageGuards = new Set();
    active.teardowns.push(() => pageGuards.forEach((g) => this.guards.delete(g)));
    performance.mark(`cx:nav-start:${token}`);
    currentRoute.value = { path, full, pageId: active.pageId, params, token };

    const isCurrent = () => this.active === active;
    const keep = (promise) => Promise.resolve(promise).then(
      (value) => (isCurrent() ? value : never()),
      (error) => (isCurrent() ? Promise.reject(error) : never()),
    );
    const api = this.api.withSignal(controller.signal);
    const pageApi = Object.create(api);
    for (const method of ['get', 'post', 'put', 'patch', 'delete', 'request']) {
      pageApi[method] = (...args) => keep(api[method](...args));
    }
    let deferred = false;
    const ctx = {
      ...this.context,
      token,
      signal: controller.signal,
      root: this.outlet,
      path,
      params,
      query: new URLSearchParams(url.search),
      hash: url.hash,
      pageId: active.pageId,
      api: pageApi,
      keep,
      isCurrent,
      navigate: (to, options) => this.navigate(to, options),
      guard: (guard) => {
        pageGuards.add(guard);
        this.guards.add(guard);
        return () => {
          pageGuards.delete(guard);
          this.guards.delete(guard);
        };
      },
      onLeave: (fn) => active.teardowns.push(fn),
      setTitle: (text) => this.context.setTitle && this.context.setTitle(text),
      deferReady: () => {
        deferred = true;
        return () => this.markReady(active, { initial, pop });
      },
      ready: () => this.markReady(active, { initial, pop }),
    };

    let page;
    try {
      page = entry ? await loadPage(entry) : null;
      if (!isCurrent()) return false;
      if (!page) throw new Error(`no page for ${path}`);
      const teardown = await page.mount(ctx);
      if (typeof teardown === 'function') {
        if (isCurrent()) active.teardowns.push(teardown);
        else safely(teardown);
      }
    } catch (error) {
      if (!isCurrent()) return false;
      if (this.onPageError) this.onPageError(error, ctx);
      else throw error;
    }
    if (!isCurrent()) return false;
    if (!deferred) this.markReady(active, { initial, pop });
    return true;
  }

  markReady(active, { initial, pop }) {
    if (this.active !== active || active.ready) return;
    active.ready = true;
    const { token } = active;
    performance.mark(`cx:route-ready:${token}`);
    let duration = null;
    try {
      duration = performance.measure(`cx:route:${active.pageId}`,
        `cx:nav-start:${token}`, `cx:route-ready:${token}`).duration;
    } catch {
      duration = null;
    }
    // Marks and measures would pile up over a long session: keep none.
    performance.clearMarks(`cx:nav-start:${token}`);
    performance.clearMarks(`cx:route-ready:${token}`);
    performance.clearMeasures(`cx:route:${active.pageId}`);
    document.documentElement.dataset.routeReady = String(token);
    document.documentElement.dataset.route = active.pageId || '';
    if (pop !== null && pop !== undefined) this.restoreScroll();
    else if (!initial) window.scrollTo(0, 0);
    if (this.onReady) this.onReady({ token, pageId: active.pageId, initial, duration });
  }

  /** Tear the current page down: abort its calls, run its teardowns, empty the outlet. */
  leave() {
    const active = this.active;
    if (!active) return;
    this.active = null;
    if (!active.ready) performance.clearMarks(`cx:nav-start:${active.token}`);
    active.controller.abort();
    for (const fn of active.teardowns.reverse()) safely(fn);
    this.outlet.replaceChildren();
  }

  saveScroll() {
    const state = history.state || {};
    history.replaceState({ ...state, cxScroll: [window.scrollX, window.scrollY] }, '');
  }

  restoreScroll() {
    const pos = (history.state && history.state.cxScroll) || [0, 0];
    window.scrollTo(pos[0], pos[1]);
  }

  onPop(event) {
    if (this.ignorePop) {
      this.ignorePop = false;
      return;
    }
    const state = event.state || {};
    let next = state.cxIndex;
    if (typeof next !== 'number') {
      // An entry the browser added itself (a fragment link): it comes after the current one.
      next = this.index + 1;
      history.replaceState({ ...state, cxIndex: next }, '');
    }
    const delta = next - this.index;
    this.index = next;
    this.go(location.pathname + location.search + location.hash, { pop: delta }).then((moved) => {
      if (!moved) this.index -= delta;
    });
  }

  onBeforeUnload(event) {
    if (this.isDirty()) {
      event.preventDefault();
      event.returnValue = '';
    }
  }

  onClick(event) {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target instanceof Element ? event.target.closest('a[href]') : null;
    if (!link || link.hasAttribute('download') || link.getAttribute('rel') === 'external') return;
    if (link.target && link.target !== '_self') return;
    const url = new URL(link.href, location.href);
    if (url.origin !== location.origin) return;
    if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/static/')) return;
    if (url.pathname === location.pathname && url.search === location.search && url.hash) return;
    event.preventDefault();
    this.navigate(url.pathname + url.search + url.hash);
  }
}

function safely(fn) {
  try {
    fn();
  } catch (error) {
    // A failing teardown must not keep the next page from showing.
    console.error(error);
  }
}

/** The page object of an entry: its `page`, or the module its `load()` or `module` gives. */
export async function loadPage(entry) {
  if (entry.page) return entry.page;
  const mod = entry.load ? await entry.load() : await import(entry.module);
  const page = mod.page || mod.default || mod;
  if (!page || typeof page.mount !== 'function') {
    throw new Error(`page ${entry.id}: its module exports no mount()`);
  }
  entry.page = page;
  return page;
}
