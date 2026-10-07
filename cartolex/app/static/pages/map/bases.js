// SPDX-License-Identifier: MIT
/**
 * Base maps, in the map versions' dialog: another project's map copied into this one, on
 * which this project's people and keywords are placed by the keywords both share; show the
 * project on one (a flat map only: none while the pinned version is in space), remove one,
 * add one from a project's folder. Read when the dialog opens.
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatNumber, t } from '../../core/i18n.js';
import { Button, FormField, Input } from '../../components/index.js';

/** The section. *onError* shows an error in the dialog. */
export function BasesSection({ ctx, open, base, onBase, pinned3d, onError }) {
  const [bases, setBases] = useState(null);
  const [busy, setBusy] = useState('');
  const [folder, setFolder] = useState('');

  useEffect(() => {
    if (!open) return;
    ctx.api.get('/api/map/bases').then((r) => {
      if (r.ok) setBases({ ...r.data, etag: r.etag });
    });
  }, [open]);

  const addBase = async () => {
    setBusy('base');
    const r = await ctx.api.post('/api/map/bases', { folder }, { ifMatch: bases.etag });
    setBusy('');
    if (!r.ok) {
      onError(r.error);
      return;
    }
    setBases({ ...r.data, etag: r.etag });
    setFolder('');
  };
  const removeBase = async (id) => {
    setBusy(`remove:${id}`);
    const r = await ctx.api.delete(`/api/map/bases/${encodeURIComponent(id)}`, { ifMatch: bases.etag });
    setBusy('');
    if (!r.ok) onError(r.error);
    else {
      setBases({ ...r.data, etag: r.etag });
      if (base === id) onBase('');
    }
  };

  if (!bases) return null;
  return html`<section class="cx-atlas-versions" aria-labelledby="cx-atlas-bases-h">
    <h3 id="cx-atlas-bases-h" class="cx-atlas-panel__subtitle">${t('map.bases.title')}</h3>
    <p class="cx-atlas-panel__muted">${t('map.bases.lead')}</p>
    ${pinned3d && !base ? html`<p class="cx-atlas-panel__muted" id="cx-atlas-bases-2d">${t('map.bases.needs_2d')}</p>` : null}
    ${bases.bases.length ? html`<ul class="cx-atlas-versions__list">
      ${bases.bases.map((b) => html`<li key=${b.id} class="cx-atlas-versions__item">
        <div>
          <strong>${b.name || b.id}</strong> · ${b.map_version}
          <p class="cx-atlas-panel__muted">${b.missing ? t('map.bases.missing')
            : t('map.bases.size', { keywords: formatNumber(b.keywords), people: formatNumber(b.people) })}</p>
        </div>
        <div class="cx-atlas-versions__actions">
          ${b.missing ? null : html`<${Button} size="s" variant=${base === b.id ? 'primary' : 'secondary'}
            disabled=${pinned3d && base !== b.id} aria-describedby=${pinned3d && !base ? 'cx-atlas-bases-2d' : undefined}
            aria-pressed=${String(base === b.id)} onClick=${() => onBase(base === b.id ? '' : b.id)}>
            ${base === b.id ? t('map.bases.shown') : t('map.bases.show')}<//>`}
          <${Button} size="s" variant="ghost" loading=${busy === `remove:${b.id}`}
            onClick=${() => removeBase(b.id)}>${t('map.bases.remove')}<//>
        </div>
      </li>`)}
    </ul>` : null}
    <div class="cx-atlas-versions__form">
      <${FormField} label=${t('map.bases.folder')} help=${t('map.bases.folder_help')}>
        ${(field) => html`<${Input} ...${field} value=${folder}
          onInput=${(e) => setFolder(e.currentTarget.value)} />`}
      <//>
      <${Button} loading=${busy === 'base'} disabled=${!folder.trim()} onClick=${addBase}>${t('map.bases.add')}<//>
    </div>
  </section>`;
}
