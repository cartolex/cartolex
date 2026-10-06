// SPDX-License-Identifier: MIT
/**
 * The header's project menu, between the logo and the navigation: the open
 * project's name (cut when long) on a button whose menu lists the recent
 * projects (`GET /api/projects`, read when the menu opens), « All projects… »
 * (`/start`) and « New project… » (`/start?new=1`). Opening a project goes
 * through `components/project-open.js`: a project another cartolex holds asks
 * first (the start screen's warning); while a job runs, the person is told so
 * and sent to the Activity drawer. A hosted service has no such menu.
 */
import { html, useEffect, useRef, useState } from './preact.js';
import { formatDate, t } from './i18n.js';
import { useUid } from './dom.js';
import { Button, Dialog, Icon, Menu } from '../components/index.js';
import {
  OverrideDialog, openRefusal, seeActivity, useProjectOpener,
} from '../components/project-open.js';

/** The recent projects as menu items, the open one left out. */
function recentItems(list, currentId) {
  return list.filter((item) => item.id !== currentId).slice(0, 8).map((item) => ({
    id: `open:${item.path || item.id}`,
    label: item.name || item.id,
    hint: item.exists === false ? t('start.missing')
      : item.opened_at ? formatDate(item.opened_at, 'date', 'medium') : '',
    disabled: item.exists === false,
    icon: 'file',
  }));
}

/**
 * @param {{app: object}} props the running app (manifest, api, router)
 */
export function ProjectMenu({ app }) {
  const project = app.manifest.project && app.manifest.project.open ? app.manifest.project : null;
  const opener = useProjectOpener(app.api);
  const [open, setOpen] = useState(null);
  const [recent, setRecent] = useState(null);
  const button = useRef(null);
  const id = useUid('cx-project-menu');
  const name = project ? project.name || project.id : t('projects.none');

  // A held project asks at once (the start screen's warning); other refusals are said below.
  useEffect(() => {
    if (opener.problem && opener.problem.held) opener.override();
  }, [opener.problem]);

  const show = async (focus) => {
    const result = await app.api.get('/api/projects');
    setRecent(result.ok ? (result.data && result.data.items) || [] : []);
    setOpen(focus);
  };
  const close = (reason) => {
    setOpen(null);
    if (reason !== 'outside' && reason !== 'tab' && button.current) button.current.focus();
  };
  const onKeyDown = (event) => {
    if (event.key === 'ArrowDown' || event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      show('first');
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      show('last');
    }
  };
  const recentList = recentItems(recent || [], project && project.id);
  const items = [
    ...(recentList.length ? [{ kind: 'group', id: 'recent', label: t('start.recent'), items: recentList },
      { kind: 'separator', id: 'sep' }] : []),
    { id: 'all', label: t('projects.all') },
    { id: 'new', label: t('projects.new'), icon: 'plus' },
  ];
  const onSelect = (item) => {
    if (item.id === 'all') app.router.navigate('/start');
    else if (item.id === 'new') app.router.navigate('/start?new=1');
    else if (item.id.startsWith('open:')) opener.open(item.id.slice(5));
  };
  const problem = opener.problem;
  return html`<span class="cx-project-menu">
    <button ref=${button} type="button" id=${id} class="cx-button cx-button--ghost cx-button--m cx-project-menu__button"
      aria-haspopup="menu" aria-expanded=${String(Boolean(open))}
      aria-controls=${open ? `${id}-menu` : undefined}
      aria-label=${project ? t('projects.menu', { name }) : t('projects.menu_none')}
      title=${name} data-nav="projects"
      onClick=${() => (open ? setOpen(null) : show('first'))} onKeyDown=${onKeyDown}>
      <span class="cx-button__label cx-project-menu__name">${name}</span>
      <${Icon} name="chevron-down" />
    </button>
    ${open ? html`<${Menu} id=${`${id}-menu`} items=${items} labelledBy=${id}
      anchor=${button.current} focus=${open} onSelect=${onSelect} onClose=${close} />` : null}
    <${Dialog} open=${Boolean(problem && !problem.held)} size="s" role="alertdialog"
      title=${t('projects.open_failed')} onClose=${opener.clear}
      footer=${html`
        ${problem && problem.busy ? html`<${Button} variant="primary" icon="activity" onClick=${() => {
          opener.clear();
          seeActivity();
        }}>${t('projects.see_activity')}<//>` : null}
        <${Button} variant="secondary" onClick=${opener.clear}>${t('common.close')}<//>`}>
      <p>${problem ? openRefusal(problem.error) : ''}</p>
    <//>
    <${OverrideDialog} opener=${opener} />
  </span>`;
}
