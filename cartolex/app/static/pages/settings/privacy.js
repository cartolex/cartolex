/**
 * Privacy: what leaves this computer and what never does, in plain words.
 */

import { html } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { Card } from '../../components/index.js';

const ITEMS = ['ai_api', 'ai_handoff', 'collection', 'keys', 'project', 'diagnostic'];

export function PrivacySection() {
  return html`<div class="cx-settings__grid">
    <${Card} title=${t('settings.privacy.title')} level=${3} class="cx-settings__wide">
      <dl class="cx-settings__facts">
        ${ITEMS.map((id) => html`<div key=${id}><dt>${t(`settings.privacy.${id}`)}</dt>
          <dd>${t(`settings.privacy.${id}.text`)}</dd></div>`)}
      </dl>
    <//>
  </div>`;
}
