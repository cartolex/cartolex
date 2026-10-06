// SPDX-License-Identifier: MIT
/**
 * The lexicon's word cloud on the overview, once a vocabulary build exists (the
 * overview's `lexicon`): the image the Lexicon tab shows (by score, coloured by theme,
 * for the page's look and colour scheme), leading to that tab. Nothing before.
 */
import { html } from '../../core/preact.js';
import { locale, t } from '../../core/i18n.js';
import { runtime } from '../../core/runtime.js';
import { Button, Card } from '../../components/index.js';
import { cloudSrc } from '../keywords/cloud.js';

const LEXICON = '/keywords?band=lexicon';

/** @param {{lexicon: {run: string}|null, prefs: object}} props */
export function LexiconCloud({ lexicon, prefs }) {
  if (!lexicon || !lexicon.run) return null;
  const src = cloudSrc(prefs, { run: lexicon.run, language: String(locale.value || 'en').slice(0, 2) });
  return html`<${Card} level=${2} title=${t('overview.cloud.title')} class="cx-overview-cloud"
    actions=${html`<${Button} size="s" variant="ghost" iconAfter="chevron-right"
      onClick=${() => runtime.navigate(LEXICON)}>${t('overview.cloud.open')}<//>`}>
    <a class="cx-overview-cloud__link" href=${LEXICON}>
      <img class="cx-overview-cloud__image" src=${src} width="1400" height="760" loading="lazy"
        alt=${t('keywords.lexicon.cloud_alt.score')} />
    </a>
  <//>`;
}
