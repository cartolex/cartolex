// SPDX-License-Identifier: MIT
/**
 * The site builds, the newest first: when each was built, whether it is the
 * latest, whether it is stale (what it was built from changed since: a
 * decision, the tables, a stage's results), names or pseudonyms, its size;
 * open it in the browser, or download it as one zip to send (its README
 * says to unzip the whole folder first).
 */
import { html } from '../../core/preact.js';
import { formatDate, formatNumber, t } from '../../core/i18n.js';
import { Card, EmptyState, Icon, StatusDot } from '../../components/index.js';

function size(bytes) {
  return t('share.size', { mb: formatNumber((bytes || 0) / 1e6, { maximumFractionDigits: 1 }) });
}

export function BuildsCard({ share }) {
  const items = (share && share.items) || [];
  return html`<${Card} level=${2} title=${t('share.builds.title')} loading=${!share} class="cx-share__builds">
    ${share && !items.length ? html`<${EmptyState} icon="upload" level=${3} title=${t('share.builds.none')}>
      ${t('share.builds.none.text')}<//>` : null}
    ${items.length ? html`<ul class="cx-share-builds">
      ${items.map((b) => html`<li key=${b.id} class="cx-share-build">
        <div class="cx-share-build__head">
          <${StatusDot} state=${b.stale ? 'needs_update' : 'up_to_date'} />
          <strong>${b.built_at ? formatDate(b.built_at, 'datetime') : b.id}</strong>
          ${b.latest ? html`<span class="cx-share-build__tag">${t('share.builds.latest')}</span>` : null}
        </div>
        <p class="cx-share__note">
          ${b.stale ? html`<span class="cx-share-build__stale"><${Icon} name="warning" /> ${t('share.builds.stale')}</span>${' · '}` : null}
          ${b.names === true ? t('share.builds.names') : b.names === false ? t('share.builds.pseudonyms') : null}
          ${b.size ? html` · ${size(b.size)}` : null}
          ${b.counts && b.counts.people !== undefined ? html` · ${t('share.builds.people', { n: b.counts.people })}` : null}
        </p>
        <div class="cx-share__actions">
          <a class="cx-button cx-button--secondary cx-button--s" href=${`/api/share/builds/${b.id}/site/index.html`}
            target="_blank" rel="noopener">${t('share.builds.open')}</a>
          <a class="cx-button cx-button--secondary cx-button--s" href=${`/api/share/builds/${b.id}/zip`} download>
            ${t('share.builds.zip')}</a>
        </div>
      </li>`)}
    </ul>` : null}
    ${share && share.total > items.length ? html`<p class="cx-share__note">${t('share.builds.more', { n: share.total - items.length })}</p>` : null}
  <//>`;
}
