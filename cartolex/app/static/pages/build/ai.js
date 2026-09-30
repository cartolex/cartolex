// SPDX-License-Identifier: MIT
/**
 * The AI steps of a build: the route chosen for each on the pre-flight sheet
 * (none, a copilot, the API), remembered in the project (`PUT /api/build/ai`),
 * and the card of a build that waits for a copilot, with « Continue the build ».
 */
import { html } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { useUid } from '../../core/dom.js';
import { Button, Card, Icon, StageTracker } from '../../components/index.js';
import { nameOf, namesOf } from './words.js';

/** The AI steps, in build order. */
export const AI_STEPS = ['keywords.triage', 'themes.curation'];

/** A step's plain name (« keyword clean-up »). */
export function stepName(step) {
  return t(`build.ai.step.${step}`);
}

function RouteChoice({ step, ai, busy, onChange }) {
  const choose = (route) => {
    if (!busy) onChange(step, route);
  };
  const uid = useUid('cx-build-ai');
  const value = ai.routes[step];
  const choices = ai.choices[step] || [];
  const options = ['none', 'copilot', 'api'].map((route) => {
    const offered = choices.includes(route);
    let help = t(`build.ai.route.${route}.help`);
    // Never disabled while a choice is saved: the focus stays on the radio button.
    let disabled = false;
    if (route === 'api' && !offered) {
      help = t('build.ai.api_none');
      disabled = true;
    } else if (route === 'api' && !ai.api_ready && value !== 'api') {
      help = t('build.ai.api_not_ready');
      disabled = true;
    }
    return { route, help, disabled };
  });
  return html`<fieldset class="cx-build-ai__step" data-ai-step=${step}>
    <legend class="cx-build-ai__legend">${stepName(step)}</legend>
    ${options.map((o) => html`<label key=${o.route}
      class=${`cx-build-ai__option ${o.disabled && value !== o.route ? 'is-disabled' : ''}`}>
      <input type="radio" name=${`${uid}-${step}`} value=${o.route} checked=${value === o.route}
        disabled=${o.disabled} onChange=${() => choose(o.route)} />
      <span><span class="cx-build-ai__label">${t(`build.ai.route.${o.route}`)}</span>
        <span class="cx-build-ai__help">${o.help}</span></span>
    </label>`)}
  </fieldset>`;
}

/**
 * The choice of a route for each AI step, saved at once and remembered per project.
 * @param {{ai: object, pause: object|null, busy: boolean, onChange: Function}} props
 */
export function AiChoice({ ai, pause, busy, onChange }) {
  if (!ai) return null;
  return html`<${Card} level=${2} title=${t('build.ai.title')} class="cx-build-ai">
    <p class="cx-build-note">${t('build.ai.lead')}</p>
    <div class="cx-build-ai__steps">
      ${AI_STEPS.map((step) => html`<${RouteChoice} key=${step} step=${step} ai=${ai} busy=${busy}
        onChange=${onChange} />`)}
    </div>
    ${pause ? html`<p class="cx-build-ai__pause" role="note" data-pause=${pause.step}>
      <${Icon} name="info" />
      <span>${t('build.ai.pauses', { step: stepName(pause.step), after: nameOf(pause.after),
        n: pause.held.length, stages: namesOf(pause.held) })}</span>
    </p>` : null}
  <//>`;
}

/**
 * A build that ended waiting for a copilot: what to do, then « Continue the build ».
 * @param {{job: object, rows: Array, starting: boolean, onOpen: Function, onContinue: Function,
 *   onOverview: Function, onAgain: Function}} props
 */
export function Waiting({ job, rows, starting, onOpen, onContinue, onOverview, onAgain }) {
  const pause = (job.result && job.result.waiting) || {};
  const step = pause.step || 'keywords.triage';
  const ran = (job.result && job.result.ran) || [];
  return html`<${Card} level=${2} title=${t('build.wait.title', { step: stepName(step) })}
    class="cx-build-wait">
    <p class="cx-build-result__sentence" role="status" data-outcome="waiting">${ran.length
      ? t('build.wait.sentence', { n: ran.length, stages: namesOf(ran), step: stepName(step) })
      : t('build.wait.sentence_none', { step: stepName(step) })}</p>
    <ol class="cx-build-wait__steps">
      <li>${t(`build.wait.export.${step}`)}</li>
      <li>${t('build.wait.give')}</li>
      <li>${t('build.wait.import')}</li>
    </ol>
    <p class="cx-build-note">${t('build.wait.then', { n: (pause.held || []).length,
      stages: namesOf(pause.held || []) })}</p>
    <${StageTracker} stages=${rows} label=${t('build.run.stages')} compact />
    <div class="cx-build-actions">
      <${Button} variant="primary" iconAfter="chevron-right" onClick=${() => onOpen(pause.page)}>
        ${t(`build.wait.open.${step}`)}<//>
      <${Button} variant="secondary" loading=${starting} onClick=${() => onContinue(step)}>
        ${t('build.wait.continue')}<//>
      <${Button} variant="ghost" onClick=${onAgain}>${t('build.again')}<//>
      <${Button} variant="ghost" onClick=${onOverview}>${t('build.to_overview')}<//>
    </div>
  <//>`;
}
