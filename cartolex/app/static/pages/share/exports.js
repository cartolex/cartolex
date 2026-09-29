// SPDX-License-Identifier: MIT
/**
 * Figures, tables and files: the map as a PNG or SVG image of a chosen size
 * (light or dark; people are never named on a figure), the theme table as
 * CSV, and two files written by a job into `outputs/exports/` under dated
 * names: the map bundle (for another project's base map or a merge) and the
 * project as one zip, without its caches.
 */
import { html, useState } from '../../core/preact.js';
import { formatDate, formatNumber, locale, t } from '../../core/i18n.js';
import { Button, Card, ErrorCard, FormField, Input, ProgressBar, Select } from '../../components/index.js';

const MIN = 200;
const MAX = 6000;

function clamp(value, fallback) {
  const n = Number.parseInt(value, 10);
  return Number.isFinite(n) ? Math.max(MIN, Math.min(MAX, n)) : fallback;
}

export function ExportsCard({ ctx, share, job, running, onStarted }) {
  const [width, setWidth] = useState('1600');
  const [height, setHeight] = useState('1200');
  const [theme, setTheme] = useState('light');
  const [error, setError] = useState(null);
  const [starting, setStarting] = useState('');
  const w = clamp(width, 1600);
  const h = clamp(height, 1200);
  const figure = (format) => `/api/share/figures/map?${new URLSearchParams({ format, width: String(w),
    height: String(h), theme, language: locale.value })}`;
  const start = async (kind) => {
    setStarting(kind);
    setError(null);
    const r = onStarted(await ctx.api.post('/api/share/exports', { kind }));
    setStarting('');
    if (!r.ok) setError(r.error);
  };
  const files = (share && share.exports) || [];
  return html`<${Card} level=${2} title=${t('share.exports.title')} class="cx-share__exports">
    <h3 class="cx-share__head">${t('share.figures')}</h3>
    <div class="cx-share-form">
      <${FormField} label=${t('share.figures.width')}>
        ${(f) => html`<${Input} ...${f} type="number" min=${MIN} max=${MAX} step="10" value=${width}
          onInput=${(e) => setWidth(e.currentTarget.value)} />`}<//>
      <${FormField} label=${t('share.figures.height')}>
        ${(f) => html`<${Input} ...${f} type="number" min=${MIN} max=${MAX} step="10" value=${height}
          onInput=${(e) => setHeight(e.currentTarget.value)} />`}<//>
      <${FormField} label=${t('share.figures.theme')}>
        ${(f) => html`<${Select} ...${f} value=${theme} onChange=${(e) => setTheme(e.currentTarget.value)}
          options=${[{ value: 'light', label: t('share.figures.light') }, { value: 'dark', label: t('share.figures.dark') }]} />`}<//>
    </div>
    <p class="cx-share__note">${t('share.figures.note', { width: w, height: h })}</p>
    <div class="cx-share__actions">
      <a class="cx-button cx-button--secondary cx-button--m" href=${figure('png')} download>${t('share.figures.png')}</a>
      <a class="cx-button cx-button--secondary cx-button--m" href=${figure('svg')} download>${t('share.figures.svg')}</a>
      <a class="cx-button cx-button--secondary cx-button--m" href="/api/share/tables/themes.csv" download>${t('share.tables.themes')}</a>
    </div>
    <h3 class="cx-share__head">${t('share.files')}</h3>
    <p class="cx-share__note">${t('share.files.text')}</p>
    <div class="cx-share__actions">
      <${Button} loading=${starting === 'map_bundle'} disabled=${running} onClick=${() => start('map_bundle')}>
        ${t('share.files.map_bundle')}<//>
      <${Button} loading=${starting === 'project'} disabled=${running} onClick=${() => start('project')}>
        ${t('share.files.project')}<//>
    </div>
    ${job && running ? html`<${ProgressBar} value=${job.progress ? job.progress.fraction : null} label=${t('share.files.progress')} />` : null}
    ${error ? html`<${ErrorCard} error=${error} compact />` : null}
    ${files.length ? html`<ul class="cx-share-files" aria-label=${t('share.files.list')}>
      ${files.map((e) => html`<li key=${e.name}>
        <a href=${`/api/share/exports/${encodeURIComponent(e.name)}`} download><code>${e.name}</code></a>
        <span class="cx-share__note">${' · '}${formatDate(e.made_at, 'datetime')}${' · '}${t('share.size', {
          mb: formatNumber(e.size / 1e6, { maximumFractionDigits: 1 }) })}</span>
      </li>`)}
    </ul>` : null}
  <//>`;
}
