// SPDX-License-Identifier: MIT
/** The texts step's diagnostic: what gathering the texts gave (people, texts, characters). */

import { html } from '../../core/preact.js';
import { formatNumber, t } from '../../core/i18n.js';
import { Facts, Lead } from './common.js';

export function TextsDiagnostic({ view }) {
  const c = view.counts || {};
  const n = (v) => (v === null || v === undefined ? '—' : formatNumber(v));
  return html`<${Lead}>${t('method.texts.lead')}<//>
    <${Facts} items=${[
      [t('method.texts.people'), n(c.people)],
      [t('method.texts.texts'), n(c.texts)],
      [t('method.texts.characters'), n(c.characters)],
      [t('method.texts.mapped'), n(c.mapped_units)],
    ]} />`;
}
