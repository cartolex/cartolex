// SPDX-License-Identifier: MIT
/**
 * The overview's cards: the one next step, the health panel and the recent
 * shared builds. Each item of `GET /api/overview` is a message code with its
 * params and a next action; a build action may carry a scope.
 */
import { html } from '../../core/preact.js';
import { has, t } from '../../core/i18n.js';
import { runAction, runtime } from '../../core/runtime.js';
import { Button, Card, EmptyState, Icon, StatusDot } from '../../components/index.js';
import { messageText, nameOf } from '../build/words.js';

/** Run an item's next action: a build goes to the pre-flight sheet with the item's scope. */
export function follow(item) {
  const action = item.next && item.next.action;
  if (action === 'build') {
    runtime.navigate(item.scope ? `/build?scope=${encodeURIComponent(item.scope.join(','))}` : '/build');
  } else if (action) {
    runAction(action);
  }
}

/** The label of an item's action: the catalogue's (`overview.action.<code>`), else generic. */
export function actionLabel(item) {
  const key = `overview.action.${item.code}`;
  if (has(key)) return t(key);
  const kind = item.next.action.startsWith('open:') ? 'open' : item.next.action;
  return has(`error.action.${kind}`) ? t(`error.action.${kind}`) : item.next.label;
}

/** The single most useful next step. */
export function NextStep({ item }) {
  if (!item) return null;
  const params = { ...(item.params || {}) };
  if (params.stage) params.stage = nameOf(params.stage);
  if (params.step && has(`build.ai.step.${params.step}`)) params.step = t(`build.ai.step.${params.step}`);
  const title = has(`overview.next.${item.code}`) ? t(`overview.next.${item.code}`, params) : item.message;
  return html`<${Card} level=${2} title=${t('overview.next.title')} class="cx-grid__wide cx-overview-next"
    data-next=${item.code}>
    <div class="cx-overview-next__body">
      <p class="cx-overview-next__text">${title}</p>
      ${item.next ? html`<${Button} variant="primary" onClick=${() => follow(item)}
        iconAfter="chevron-right">${actionLabel(item)}<//>` : null}
    </div>
  <//>`;
}

/** The health panel: things that may spoil the map without failing a build. */
export function Health({ items }) {
  return html`<${Card} level=${2} title=${t('overview.health.title')} class="cx-overview-health">
    ${items.length ? html`<ul class="cx-overview-health__list">
      ${items.map((item) => html`<li key=${item.code + JSON.stringify(item.params)}
        class=${`cx-overview-health__item is-${item.level}`} data-health=${item.code}>
        <${Icon} name=${item.level === 'warning' ? 'warning' : 'info'} />
        <div>
          <p><span class="cx-overview-health__word">${t(`overview.health.level.${item.level}`)}</span>
            ${' '}${messageText(item)}</p>
          ${item.next ? html`<${Button} size="s" variant="secondary" onClick=${() => follow(item)}>
            ${actionLabel(item)}<//>` : null}
        </div>
      </li>`)}
    </ul>` : html`<p class="cx-overview-health__ok">
      <${StatusDot} state="up_to_date" label=${t('overview.health.ok')} size="s" /></p>`}
  <//>`;
}

/** The recent shared builds (a placeholder until sharing is built). */
export function Shares({ shares }) {
  const items = (shares && shares.items) || [];
  return html`<${Card} level=${2} title=${t('overview.shares.title')} class="cx-overview-shares">
    ${items.length ? html`<ul class="cx-overview-shares__list">
      ${items.map((b) => html`<li key=${b.id}><code>${b.id}</code>
        ${b.latest ? html`${' · '}${t('overview.shares.latest')}` : null}</li>`)}
    </ul>` : html`<${EmptyState} icon="upload" level=${3} title=${t('overview.shares.none')}
      action=${{ label: t('overview.shares.open'), onClick: () => runtime.navigate('/share') }}>
      ${t(shares && shares.available ? 'overview.shares.none.text' : 'overview.shares.later')}<//>`}
  <//>`;
}
