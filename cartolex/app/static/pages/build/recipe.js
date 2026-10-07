// SPDX-License-Identifier: MIT
/**
 * The Recipe tab of the Build page (`/build?tab=recipe`), read-only: every
 * parameter of every stage with its value, its default, its origin (the
 * default, a rule with its reason, `params.json`, the map version) and a mark
 * when it differs from the default, then the pinned map version's layout;
 * filters « changed only » and by tier; each row links to the « Tune » panel
 * that edits it; the whole as Markdown or CSV, written by the server
 * (`GET /api/recipe/export`), for a paper's methods section.
 *
 * Opening it reads `GET /api/recipe` only.
 */

import { html, useState } from '../../core/preact.js';
import { formatNumber, has, locale, t } from '../../core/i18n.js';
import { Checkbox, EmptyState, ErrorCard, Icon, Select, shownValue } from '../../components/index.js';
import { useResource } from '../settings/common.js';
import { LabelWithCode, paramLabel } from '../tune/params.js';
import { PANELS } from '../tune/common.js';

const TIERS = ['essential', 'intermediate', 'advanced'];
const PAGE_OF_PANEL = { texts: 'people', keywords: 'keywords', themes: 'themes', map: 'map' };

/** A value in words; the layout's method by its name. */
function valueWords(row, value) {
  if (row.group === 'layout' && row.name === 'method' && value) return t(`settings.layout.method.${value}`);
  if (row.group === 'build' && row.name === 'pinned_year' && value === null) return t('method.global.this_year');
  if (row.group === 'distances' && value) return t(`atlas.similarity.${value}`);
  // a year and a seed are identifiers: no thousands separator
  if ((row.name === 'pinned_year' || row.name.endsWith('seed')) && typeof value === 'number') return String(value);
  return shownValue({ name: row.name }, value);
}

/** Where a value comes from, in words. */
function originWords(row) {
  if (row.from === 'rule') return t('settings.origin.rule_named', { rule: row.rule_description || row.rule || '' });
  if (row.from === 'version') return t('recipe.origin.version', { version: row.version });
  return has(`settings.origin.${row.from}`) ? t(`settings.origin.${row.from}`) : row.from;
}

/** A group's heading: a stage's name and code, the whole build, the map's layout. */
function groupTitle(group, rows) {
  if (group === 'build') return t('recipe.group.build');
  if (group === 'layout') return t('recipe.group.layout_version', { version: rows[0].version });
  if (group === 'distances') return t('recipe.group.distances');
  return html`${has(`stage.${group}`) ? t(`stage.${group}`) : group} <code class="cx-param__code">${group}</code>`;
}

function Row({ ctx, row }) {
  const panel = PANELS[row.panel];
  const page = t(`nav.${PAGE_OF_PANEL[row.panel]}`);
  return html`<tr class=${row.differs ? 'is-changed' : ''} data-recipe=${`${row.group}.${row.name}`}>
    <th scope="row"><${LabelWithCode} group=${row.group} name=${row.name} /></th>
    <td>${valueWords(row, row.value)}
      ${row.differs ? html` <span class="cx-param__mark cx-param__mark--changed"><${Icon} name="dot" />${t('param.field.changed')}</span>` : null}</td>
    <td>${valueWords(row, row.default_value)}</td>
    <td>${originWords(row)}</td>
    <td>${panel ? html`<a href=${panel.href} aria-label=${t('recipe.edit_on', { name: paramLabel(row.group, row.name), page })}
      onClick=${(e) => {
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
        e.preventDefault();
        ctx.navigate(panel.href);
      }}>${page}</a>` : null}</td>
  </tr>`;
}

/** The tokens the AI clean-up by API spent: its last run, and every run of the project. */
function AiUsage({ usage }) {
  if (!usage) return null;
  const { last, total } = usage;
  return html`<p class="cx-settings__muted">${last ? t('recipe.ai_usage.last', {
    sent: formatNumber(last.tokens_in), received: formatNumber(last.tokens_out) }) : ''}
    ${last && total ? ' ' : ''}${total ? t('recipe.ai_usage.total', {
      sent: formatNumber(total.tokens_in), received: formatNumber(total.tokens_out) }) : ''}</p>`;
}

export function RecipeTab({ ctx }) {
  const recipe = useResource(ctx.api, '/api/recipe');
  const [changedOnly, setChangedOnly] = useState(false);
  const [tier, setTier] = useState('');
  if (recipe.error) return html`<${ErrorCard} error=${recipe.error} onRetry=${recipe.reload} />`;
  if (!recipe.data) return html`<p class="cx-build-note" aria-busy="true">${t('common.loading')}</p>`;
  const all = recipe.data.rows;
  const rows = all.filter((r) => (!changedOnly || r.differs) && (!tier || r.tier === tier));
  const groups = [];
  for (const r of rows) {
    const last = groups[groups.length - 1];
    if (last && last.id === r.group) last.rows.push(r);
    else groups.push({ id: r.group, rows: [r] });
  }
  const lang = encodeURIComponent(locale.value || 'en');
  return html`<div class="cx-recipe">
    <p class="cx-page__lead">${t('recipe.lead')}</p>
    <div class="cx-recipe__bar">
      <div class="cx-recipe__filters" role="group" aria-label=${t('recipe.filters')}>
        <${Checkbox} label=${t('recipe.changed_only')} checked=${changedOnly}
          onChange=${(e) => setChangedOnly(e.currentTarget.checked)} />
        <label class="cx-settings__inline-label">${t('recipe.tier')}
          <${Select} value=${tier} onChange=${(e) => setTier(e.currentTarget.value)}
            options=${[{ value: '', label: t('recipe.tier.any') },
              ...TIERS.map((x) => ({ value: x, label: t(`recipe.tier.${x}`) }))]} /></label>
        <span class="cx-settings__muted" aria-live="polite">${t('recipe.count', {
          shown: rows.length, n: all.length, changed: recipe.data.changed })}</span>
      </div>
      <div class="cx-recipe__exports">
        <a class="cx-button cx-button--secondary cx-button--m" href=${`/api/recipe/export?format=md&language=${lang}`}
          download>${t('recipe.export.md')}</a>
        <a class="cx-button cx-button--secondary cx-button--m" href=${`/api/recipe/export?format=csv&language=${lang}`}
          download>${t('recipe.export.csv')}</a>
      </div>
    </div>
    <${AiUsage} usage=${recipe.data.ai_usage} />
    ${recipe.data.valid ? null : html`<ul class="cx-settings__problem" role="alert">
      ${(recipe.data.problems || []).map((p, i) => html`<li key=${i}>${p}</li>`)}</ul>`}
    ${groups.length ? html`<table class="cx-settings__table cx-recipe__table" aria-label=${t('recipe.title')}>
      <thead><tr><th scope="col">${t('recipe.col.parameter')}</th><th scope="col">${t('recipe.col.value')}</th>
        <th scope="col">${t('recipe.col.default')}</th><th scope="col">${t('recipe.col.origin')}</th>
        <th scope="col">${t('recipe.col.edit')}</th></tr></thead>
      ${groups.map((g) => html`<tbody key=${g.id}>
        <tr class="cx-recipe__group"><th scope="rowgroup" colspan="5">${groupTitle(g.id, g.rows)}
          <span class="cx-settings__muted cx-num">${formatNumber(g.rows.length)}</span></th></tr>
        ${g.rows.map((r) => html`<${Row} key=${r.name} ctx=${ctx} row=${r} />`)}</tbody>`)}
    </table>` : html`<${EmptyState} icon="check" level=${3} title=${t('recipe.none')} />`}
  </div>`;
}
