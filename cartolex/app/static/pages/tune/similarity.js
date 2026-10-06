// SPDX-License-Identifier: MIT
/**
 * « Distances » in the map's « Tune » panel: how two people (or two organisations) are
 * compared, `params.json`'s `similarity` (`docs/dev/api.md`, `PUT /api/params/similarity`):
 * meaning in the map's space (the default), shared vocabulary, keywords in common, shared
 * themes, each with what it means. It drives Compare's headline, the nearest and the
 * distances' exports; saved at once (`If-Match`), it needs no rebuild. It reads nothing of
 * its own: the parameters the panel read carry it (`global.similarity`).
 */
import { html, useState } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { ErrorCard } from '../../components/index.js';
import { paramsSaved } from './params.js';

/** The measures, in the order they are offered. */
export const MEASURES = ['space', 'keywords', 'jaccard', 'themes'];

/** The section; *params* is the panel's resource of `GET /api/params`. */
export function SimilarityField({ ctx, params }) {
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const own = params.data && params.data.global && params.data.global.similarity;
  if (!own) return null;
  const value = own.value || 'space';
  const choose = async (measure) => {
    if (measure === value || busy) return;
    setBusy(true);
    const r = await ctx.api.put('/api/params/similarity', { measure }, { ifMatch: params.etag });
    setBusy(false);
    if (!r.ok) {
      setError(r.error);
      return;
    }
    setError(null);
    params.set(r.data, r.etag);
    paramsSaved.value = { data: r.data, etag: r.etag };
    ctx.app.toaster.show({ kind: 'success', title: t('similarity.saved', { name: t(`atlas.similarity.${measure}`) }) });
    ctx.app.stores.project.refresh();
  };
  return html`<section class="cx-similarity" id="cx-map-similarity" aria-labelledby="cx-map-similarity-title">
    <h4 class="cx-method-subtitle" id="cx-map-similarity-title" tabindex="-1">${t('similarity.title')}</h4>
    <fieldset class="cx-share-choice" aria-busy=${busy ? 'true' : 'false'}>
      <legend class="cx-share-choice__legend">${t('similarity.legend')}</legend>
      <p class="cx-share__note">${t('similarity.lead')}</p>
      ${MEASURES.map((m) => html`<label key=${m} class="cx-share-choice__option" data-measure=${m}>
        <input type="radio" name="cx-map-similarity" value=${m} checked=${value === m} disabled=${busy}
          onChange=${() => choose(m)} />
        <span><span class="cx-share-choice__label">${t(`atlas.similarity.${m}`)}${
          m === own.default ? html` <span class="cx-settings__muted">${t('similarity.default')}</span>` : null}</span>
          <span class="cx-share-choice__help">${t(`atlas.similarity.${m}.help`)}</span></span>
      </label>`)}
    </fieldset>
    ${error ? html`<${ErrorCard} error=${error} compact />` : null}
  </section>`;
}
