// SPDX-License-Identifier: MIT
/**
 * The site's shell and routes. The header names the site and leads to the
 * home, the map, the themes, the index and the method; the language and the
 * theme (light, dark or the system's) are chosen there and remembered. The
 * footer says what the site is and how it treats names; it heads every
 * printed page. Routes live in the fragment (`#/person/s3`), which a page
 * opened from `file://` keeps: Back and Forward work, and a link can be
 * shared inside the folder. An unknown address says « not found ».
 *
 * When the scripts or the data are missing (the folder was opened from
 * inside a zip, or copied in part), the page keeps the message
 * `index.html` shows first: unzip the whole folder.
 */
(function () {
  'use strict';

  const S = window.CxSite;
  const data = window.CX_SITE || {};
  const missing = document.getElementById('cx-missing');
  const root = document.getElementById('cx-site');
  if (!S || !S.pages || !data.core || !data.i18n || !window.CartolexMap || !root) return;
  const h = S.h;
  const t = S.t;
  S.ix = S.indexData(data.core);
  if (missing) missing.remove();
  root.hidden = false;

  const NAV = [['/', 'nav.home', 'home'], ['/map', 'nav.map', 'map'], ['/themes', 'nav.themes', 'themes'],
    ['/list', 'nav.list', 'list'], ['/about', 'nav.about', 'about']];
  const THEMES = ['system', 'light', 'dark'];

  function applyTheme(choice) {
    if (choice === 'light' || choice === 'dark') document.documentElement.dataset.theme = choice;
    else delete document.documentElement.dataset.theme;
  }
  let theme = THEMES.includes(S.store('cx-site-theme')) ? S.store('cx-site-theme') : 'system';
  applyTheme(theme);

  function route() {
    const hash = window.location.hash.replace(/^#/, '') || '/';
    const cut = hash.indexOf('?');
    const path = cut >= 0 ? hash.slice(0, cut) : hash;
    const parts = path.split('/').filter(Boolean).map((p) => {
      try {
        return decodeURIComponent(p);
      } catch (e) {
        return p;
      }
    });
    return { path, parts, query: new URLSearchParams(cut >= 0 ? hash.slice(cut + 1) : '') };
  }

  const PAGES = { '': 'home', map: 'map', themes: 'themes', list: 'list', about: 'about', person: 'person', org: 'org' };

  let teardown = null;
  let first = true;
  const main = h('main', { id: 'cx-main', class: 'cx-main', tabindex: '-1' });

  function header(current) {
    const lang = h('select', { class: 'cx-select', 'aria-label': t('shell.language'), onchange: (e) => {
      S.lang = e.target.value;
      S.store('cx-site-lang', S.lang);
      S.resetSearch();
      render();
    } }, S.LANGS.map((code) => h('option', { value: code, selected: code === S.lang }, t(`shell.language.${code}`))));
    const themeButton = h('button', { type: 'button', class: 'cx-button cx-button--ghost', onclick: () => {
      theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
      S.store('cx-site-theme', theme === 'system' ? null : theme);
      applyTheme(theme);
      render();
    } }, t(`shell.theme.${theme}`));
    return h('header', { class: 'cx-header' }, [
      h('a', { href: '#/', class: 'cx-skip', text: t('shell.skip') }),
      h('a', { href: '#/', class: 'cx-brand', text: data.core.title }),
      h('nav', { class: 'cx-nav', 'aria-label': t('shell.nav') }, h('ul', {}, NAV.map(([path, key, id]) => h('li', {},
        h('a', { href: `#${path}`, class: 'cx-nav__link', 'aria-current': id === current ? 'page' : null }, t(key)))))),
      h('div', { class: 'cx-header__tools no-print' }, [lang, themeButton]),
    ]);
  }

  function footer() {
    const core = data.core;
    return h('footer', { class: 'cx-footer' }, [
      h('p', { class: 'cx-footer__notice', text: t(core.names ? 'shell.notice.names' : 'shell.notice.pseudonyms') }),
      h('p', { class: 'cx-muted cx-small', text: t('shell.built', { date: S.date(core.built_at) }) }),
    ]);
  }

  function render() {
    const r = route();
    const name = PAGES[r.parts[0] || ''];
    const page = name ? S.pages[name] : S.pages.missing;
    if (teardown) {
      try {
        teardown();
      } catch (e) {
        // A page that fails to tear down must not keep the next one from showing.
      }
      teardown = null;
    }
    document.documentElement.lang = S.lang;
    main.replaceChildren();
    root.replaceChildren(header(name), main, footer());
    teardown = page(main, r) || null;
    const title = main.querySelector('h1');
    document.title = title && name !== 'home' ? `${title.textContent} · ${data.core.title}` : data.core.title;
    if (!first && title) title.focus();
    first = false;
  }

  // A printed page shows what is folded (abstracts, lists).
  window.addEventListener('beforeprint', () => {
    document.querySelectorAll('details').forEach((d) => { d.open = true; });
  });
  window.addEventListener('hashchange', render);
  render();
}());
