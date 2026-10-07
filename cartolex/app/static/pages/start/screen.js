/**
 * The start screen (`/start`): the project open now, the recent projects, a
 * folder to open, the demo project, and a new project (`/start?new=1`). Opening
 * or creating a project reloads the interface on the project's first page, so
 * every screen starts from the new project's manifest; the opening itself
 * (a held project opened anyway after a warning, a busy one) is shared with the
 * header's project menu (`components/project-open.js`). « Remove… » takes a recent
 * project out of the list or deletes its folder (`remove.js`).
 */

import { html, useState } from '../../core/preact.js';
import { formatDate, t } from '../../core/i18n.js';
import { usePage, usePageTitle } from '../../core/page.js';
import { Button, Card, EmptyState, FormField, Input } from '../../components/index.js';
import { OpenProblem, restartAt, useProjectOpener } from '../../components/project-open.js';
import { Block, State, refusal, useResource } from '../settings/common.js';
import { NewProject } from './new.js';
import { RemoveDialog } from './remove.js';


function Recent({ ctx, recent }) {
  const opener = useProjectOpener(ctx.api);
  const [path, setPath] = useState('');
  const [removing, setRemoving] = useState(null);
  const open = (folder) => opener.open(folder);
  const items = recent.data ? recent.data.items || [] : [];
  return html`<${Block} title=${t('start.recent')} resource=${recent} class="cx-settings__wide">
    ${items.length ? html`<ul class="cx-start__list">
      ${items.map((item) => html`<li key=${item.path || item.id} class="cx-start__item">
        <div class="cx-start__what">
          <span class="cx-start__name">${item.name || item.id}</span>
          ${item.path ? html`<code class="cx-start__path">${item.path}</code>` : null}
          ${item.opened_at ? html`<span class="cx-settings__muted">${t('start.opened', { when: formatDate(item.opened_at, 'datetime') })}</span>` : null}
        </div>
        <div class="cx-start__actions">
          ${item.exists === false ? html`<${State} kind="warning">${t('start.missing')}<//>`
            : html`<${Button} onClick=${() => open(item.path || item.id)}
              loading=${opener.opening === (item.path || item.id)}
              aria-label=${t('start.open_named', { name: item.name || item.id })}>${t('start.open')}<//>`}
          ${item.path ? html`<${Button} variant="ghost" onClick=${() => setRemoving(item)} aria-haspopup="dialog"
            aria-label=${t('start.remove_named', { name: item.name || item.id })}>${t('start.remove')}<//>` : null}
        </div>
      </li>`)}
    </ul>` : html`<${EmptyState} icon="file" level=${3} title=${t('start.no_recent')}>${t('start.no_recent.text')}<//>`}
    <form class="cx-settings__inline" onSubmit=${(e) => {
      e.preventDefault();
      if (path.trim()) open(path.trim());
    }}>
      <${FormField} label=${t('start.folder')} help=${t('start.folder_help')}>
        ${(field) => html`<${Input} ...${field} value=${path} spellcheck="false" autocomplete="off"
          onInput=${(e) => setPath(e.currentTarget.value)} />`}
      <//>
      <${Button} type="submit" disabled=${!path.trim()}>${t('start.open')}<//>
    </form>
    <${OpenProblem} opener=${opener} />
    ${removing ? html`<${RemoveDialog} ctx=${ctx} item=${removing} onClose=${() => setRemoving(null)}
      onDone=${() => {
        setRemoving(null);
        recent.reload();
      }} />` : null}
  <//>`;
}

function Demo({ ctx, defaults }) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState(null);
  const make = async () => {
    setBusy(true);
    setProblem(null);
    const result = await ctx.api.post('/api/projects/demo', {});
    setBusy(false);
    if (result.ok) restartAt(result.data.next);
    else setProblem(refusal(result.error));
  };
  const folder = defaults.data && defaults.data.folder
    ? `${defaults.data.folder}${defaults.data.separator}demo` : null;
  return html`<${Block} title=${t('start.demo')} resource=${defaults}>
    <p class="cx-settings__note">${t('start.demo_text')}</p>
    ${folder ? html`<p class="cx-settings__muted">${t('start.demo_folder')} <code>${folder}</code></p>` : null}
    <div class="cx-settings__actions"><${Button} loading=${busy} onClick=${make}>${t('start.demo_action')}<//></div>
    ${problem ? html`<p class="cx-settings__problem" role="alert"><${State} kind="warning">${problem}<//></p>` : null}
  <//>`;
}

export function StartScreen() {
  const ctx = usePage();
  const { app } = ctx;
  const creating = ctx.query && ctx.query.get('new') === '1';
  usePageTitle(creating ? t('start.new') : t('nav.start'));
  const recent = useResource(ctx.api, creating ? null : '/api/projects');
  const defaults = useResource(ctx.api, '/api/projects/defaults');
  const current = app.manifest.project && app.manifest.project.open ? app.manifest.project : null;
  if (creating) {
    return html`<div class="cx-page cx-start">
      <h1 class="cx-page__title">${t('start.new')}</h1>
      <p class="cx-page__lead">${t('start.new_lead')}</p>
      <${NewProject} ctx=${ctx} defaults=${defaults} onDone=${restartAt} />
    </div>`;
  }
  return html`<div class="cx-page cx-start">
    <h1 class="cx-page__title">${t('nav.start')}</h1>
    <p class="cx-page__lead">${t('start.lead')}</p>
    <div class="cx-settings__grid">
      ${current ? html`<${Card} title=${t('start.current')} level=${2} class="cx-settings__wide">
        <p class="cx-start__name">${current.name}</p>
        <div class="cx-settings__actions">
          <${Button} variant="primary" onClick=${() => ctx.navigate('/')}>${t('start.continue')}<//>
        </div>
      <//>` : null}
      <${Card} title=${t('start.new')} level=${2}>
        <p class="cx-settings__note">${t('start.new_text')}</p>
        <div class="cx-settings__actions">
          <${Button} variant=${current ? 'secondary' : 'primary'} icon="plus"
            onClick=${() => ctx.navigate('/start?new=1')}>${t('start.new_action')}<//>
        </div>
      <//>
      <${Demo} ctx=${ctx} defaults=${defaults} />
      <${Recent} ctx=${ctx} recent=${recent} />
    </div>
  </div>`;
}
