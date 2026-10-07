// SPDX-License-Identifier: MIT
/**
 * What the « Tune » panels share: the panels (the page each lives on, the
 * stages whose parameters it edits, the stage « rebuild from here » starts at,
 * the diagnostics it shows), the shapes of their stages' states, how many of
 * their values differ from the defaults (the project state's
 * `changed_params`), the stage that makes a page up to date again, facts in a
 * list, a name in the interface's language, and the hues of the keyword bands.
 */

import { html } from '../../core/preact.js';
import { has, locale, t } from '../../core/i18n.js';
import { summaryState } from '../../components/index.js';

/**
 * The panels: `stages` (whose parameters it edits), `groups` (the recipe's groups it counts:
 * its stages, `build` for the seed and the year, `layout` for the map version), `from` (where a
 * rebuild starts), `steps` (the diagnostics, `GET /api/method/<step>`), `upTo` (the last stage
 * the page's outputs depend on) and `href` (the page, with the panel open).
 */
export const PANELS = {
  texts: { stages: ['corpus.assemble'], groups: ['corpus.assemble', 'build'], from: 'corpus.assemble',
    steps: ['texts'], upTo: 'corpus.assemble', href: '/people?tab=texts&tune=1' },
  keywords: { stages: ['keywords.extract', 'keywords.triage', 'keywords.build'],
    groups: ['keywords.extract', 'keywords.triage', 'keywords.build'], from: 'keywords.extract',
    steps: ['keywords'], upTo: 'keywords.build', href: '/keywords?tune=1' },
  themes: { stages: ['themes.space', 'themes.group'], groups: ['themes.space', 'themes.group'],
    from: 'themes.space', steps: ['space', 'grouping'], upTo: 'themes.group', href: '/themes?tune=1' },
  map: { stages: ['map.layout', 'map.trajectories'], groups: ['map.layout', 'map.trajectories', 'layout', 'distances'],
    from: 'map.layout', steps: ['layout'], upTo: 'map.trajectories', href: '/map?tune=1' },
};

/** The keyword bands and their hues in the figures. */
export const BAND_SERIES = ['kept', 'check', 'aside', 'rejected'].map((id, i) => ({
  id, hue: [1, 3, 6, 9][i], label: t(`keywords.band.${id}`),
}));

/** A panel's state: the summary of its stages' states. */
export function panelState(panel, stages) {
  const states = panel.stages.map((id) => stages.get(id)).filter(Boolean).map((s) => s.state);
  return states.length ? summaryState(states) : 'never_built';
}

/** How many of a panel's values differ from their defaults (from the project state). */
export function changedCount(panel, state) {
  const counts = (state && state.changed_params) || {};
  return panel.groups.reduce((n, g) => n + (counts[g] || 0), 0);
}

/**
 * The first stage, in the build's order, that needs an update among those a page's outputs
 * depend on (every stage up to *upTo*): the stage a rebuild starts at; null when none does.
 */
export function firstStale(stages, upTo) {
  for (const [id, s] of stages) {
    if (s.state === 'needs_update') return s;
    if (id === upTo) break;
  }
  return null;
}

/** A stage's name in the interface's language. */
export function stageWords(stage) {
  return has(`stage.${stage.id}`) ? t(`stage.${stage.id}`) : stage.name || stage.id;
}

/** Facts: `[[label, value], …]` as a description list. */
export function Facts({ items }) {
  return html`<dl class="cx-settings__facts cx-settings__facts--grid">
    ${items.filter(Boolean).map(([k, v], i) => html`<div key=${i}><dt>${k}</dt><dd>${v}</dd></div>`)}
  </dl>`;
}

/** A name from `{lang: name}` in the interface's language, else English, else any. */
export function nameOf(names, fallback = '') {
  const lang = String(locale.value || 'en').slice(0, 2);
  return (names && (names[lang] || names.en || Object.values(names).find(Boolean))) || fallback;
}

/** The address of « rebuild from here »: every stage from *stage* on runs again. */
export function rebuildHref(stage) {
  return `/build?force=${encodeURIComponent(stage)}`;
}

/** A paragraph that explains a figure or a panel. */
export function Lead({ children }) {
  return html`<p class="cx-settings__note">${children}</p>`;
}

/** The words of a step's diagnostic before its stage ran. */
export function notBuilt(view) {
  return view && view.empty ? t(`method.empty.${view.empty.code}`) : t('method.empty.generic');
}
