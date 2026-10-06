// SPDX-License-Identifier: MIT
/**
 * « Your first map »: the steps from a new project to a shared map (the `steps` of
 * `GET /api/overview`), the current one with its button, a link to the guide, and
 * « Hide this list ». Hidden per project in the person's preferences
 * (`/api/me/preferences`, the key the overview names); « Show « Your first map » »
 * brings it back.
 */
import { html } from '../../core/preact.js';
import { formatNumber, t } from '../../core/i18n.js';
import { runtime } from '../../core/runtime.js';
import { Button, Card, Icon, Stepper } from '../../components/index.js';

/** The guide's page, served with the app's documentation. */
export const GUIDE = '/static/docs/first-map.html';

/** Where each step's button goes. */
const PLACES = {
  project: '/start',
  people: '/people?start=list',
  identities: '/people?tab=identities',
  texts: '/people?collect=harvest',
  build: '/build',
  review: '/keywords',
  themes: '/themes',
  map: '/build?scope=map',
  share: '/share',
};
/** Where a done step's button goes back to. */
const BACK = { ...PLACES, people: '/people', texts: '/people?tab=texts', map: '/map' };

function label(step) {
  return t(`overview.first_map.step.${step.id}`, { n: formatNumber(step.n || 0) });
}

/** Keep the person's other settings, change one: read, then write them all. */
export async function setHidden(api, key, hidden) {
  const r = await api.get('/api/me/preferences');
  const prefs = r.ok ? r.data.preferences : {};
  const other = { ...(prefs.other || {}) };
  if (hidden) other[key] = true;
  else delete other[key];
  // A person with many projects: the oldest hidden lists come back rather than refuse.
  const lists = Object.keys(other).filter((k) => k.startsWith('first_map.') && k !== key);
  for (const k of lists.slice(0, Math.max(0, lists.length - 30))) delete other[k];
  return api.put('/api/me/preferences', { ...prefs, other });
}

/**
 * @param {{steps: Array<{id: string, state: string, n?: number}>, onHide: Function}} props
 */
export function FirstMap({ steps, onHide }) {
  const current = steps.find((s) => s.state !== 'done');
  const stepper = steps.map((s) => ({
    id: s.id,
    label: label(s),
    state: s.state === 'done' ? 'done' : s === current ? 'current' : 'todo',
  }));
  return html`<${Card} level=${2} title=${t('overview.first_map.title')}
    class="cx-grid__wide cx-overview-first" data-first-map
    actions=${html`<${Button} size="s" variant="ghost" icon="close" onClick=${onHide}>
      ${t('overview.first_map.hide')}<//>`}>
    <${Stepper} steps=${stepper} label=${t('overview.first_map.title')} class="cx-overview-first__steps"
      onSelect=${(id) => runtime.navigate(BACK[id])} />
    <div class="cx-overview-first__now">
      ${current ? html`<p>${t(`overview.first_map.now.${current.id}`, { n: current.n || 0 })}</p>
        <${Button} size="s" variant="secondary" iconAfter="chevron-right"
          onClick=${() => runtime.navigate(PLACES[current.id])}>
          ${t(`overview.first_map.go.${current.id}`)}<//>`
        : html`<p>${t('overview.first_map.all_done')}</p>`}
      <a class="cx-link" href=${GUIDE} target="_blank" rel="noopener">
        ${t('overview.first_map.guide')}${' '}<${Icon} name="external" /></a>
    </div>
  <//>`;
}
