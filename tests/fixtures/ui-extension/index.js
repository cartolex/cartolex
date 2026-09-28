// SPDX-License-Identifier: MIT
/**
 * A generic test extension: a page, an overview card and messages. The
 * fixture server serves it at /static/ext/demo/; the fixture manifest lists
 * it in `modules` and its catalogues after the base ones.
 */
export function register(api) {
  const { html, definePage, usePageTitle } = api.ui;
  const { Card, EmptyState } = api.components;
  const { t } = api.i18n;
  api.i18n.add('en', { 'ext.demo.runtime': 'Added at run time' });
  api.i18n.add('fr', { 'ext.demo.runtime': 'Ajouté à l’exécution' });
  api.i18n.add('pt-BR', { 'ext.demo.runtime': 'Adicionado na execução' });

  function DemoPage() {
    usePageTitle(t('ext.demo.title'));
    return html`<div class="cx-page">
      <h1 class="cx-page__title">${t('ext.demo.title')}</h1>
      <${EmptyState} title=${t('ext.demo.empty')} action=${{ label: t('ext.demo.action'), href: '/overview' }}>
        ${t('ext.demo.runtime')}
      <//>
    </div>`;
  }
  api.pages.add({ id: 'demo', route: '/demo', label: 'ext.demo.title', order: 90,
    page: definePage(DemoPage) });
  api.slots.add('overview.cards', {
    id: 'demo-card',
    order: 10,
    component: () => html`<${Card} title=${t('ext.demo.card')}><p>${t('ext.demo.runtime')}</p><//>`,
  });
}
