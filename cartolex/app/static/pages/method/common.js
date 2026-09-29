// SPDX-License-Identifier: MIT
/**
 * What the method screen's steps share: the steps in pipeline order with
 * their stages and the stage « rebuild from here » starts at, a step's status
 * (the shapes of its stages' states), facts in a list, a name in the
 * interface's language, and the hues of the keyword bands.
 */

import { html } from '../../core/preact.js';
import { locale, t } from '../../core/i18n.js';
import { StatusDot, summaryState } from '../../components/index.js';

/** The steps: id, stages whose parameters it shows, the stage a rebuild starts at. */
export const STEPS = [
  { id: 'texts', stages: ['corpus.assemble'], from: 'corpus.assemble' },
  { id: 'keywords', stages: ['keywords.extract', 'keywords.build'], from: 'keywords.extract' },
  { id: 'space', stages: ['themes.space'], from: 'themes.space' },
  { id: 'grouping', stages: ['themes.group'], from: 'themes.group' },
  { id: 'layout', stages: ['map.layout', 'map.trajectories'], from: 'map.layout' },
];

/** The keyword bands and their hues in the figures. */
export const BAND_SERIES = ['kept', 'check', 'aside', 'rejected'].map((id, i) => ({
  id, hue: [1, 3, 6, 9][i], label: t(`keywords.band.${id}`),
}));

/** A step's state: the summary of its stages' states. */
export function stepState(step, stages) {
  const states = step.stages.map((id) => stages.get(id)).filter(Boolean).map((s) => s.state);
  return states.length ? summaryState(states) : 'never_built';
}

/** A step's state as a dot with its word. */
export function StepDot({ step, stages }) {
  const state = stepState(step, stages);
  return html`<${StatusDot} state=${state} size="s" />`;
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
