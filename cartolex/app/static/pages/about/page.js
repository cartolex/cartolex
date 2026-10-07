// SPDX-License-Identifier: MIT
/**
 * The About page, short: the logo (the host's own, else the mark), what cartolex does and
 * what it measures (who talks the same), the pipeline (a figure), what it is built on, the
 * three key references (every reference is in the documentation's References page), the
 * authors and how to cite it (Zenodo), its licence, its version and build. It reads
 * `GET /api/app/about` only (the package's metadata: authors, licence, source,
 * the citation); everything else is in the catalogues and `references.js`. The
 * DOIs and the source open outside the app, in a new tab (`noopener noreferrer`).
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatList, t } from '../../core/i18n.js';
import { usePage, usePageTitle } from '../../core/page.js';
import { DOCS_URL, versionLine } from '../../core/shell.js';
import { ErrorCard, Icon } from '../../components/index.js';
import { PipelineFigure } from './figure.js';
import { KEY_REFERENCES, doiLink } from './references.js';

/** The documentation's page of every reference. */
const REFERENCES_URL = DOCS_URL.replace(/index\.html$/, 'references.html');

/** A link that leaves the app: a new tab, without telling the page where it came from. */
function Outside({ href, children }) {
  return html`<a class="cx-link" href=${href} target="_blank" rel="noopener noreferrer">${children}</a>`;
}

function Reference({ item }) {
  return html`<li class="cx-about__ref">
    ${item.authors} (${item.year}). ${item.title}. <cite>${item.venue}</cite>${item.details
      ? html`, ${item.details}` : null}.${item.doi ? html` <${Outside} href=${doiLink(item.doi)}>
        <code>doi:${item.doi}</code><//>` : null}
  </li>`;
}

function Background() {
  return html`<section class="cx-about__section" aria-labelledby="cx-about-science">
    <h2 id="cx-about-science" class="cx-about__heading">${t('about.science.title')}</h2>
    <p>${t('about.science.text')}</p>
    <ul class="cx-about__refs">${KEY_REFERENCES.map((item) => html`<${Reference} key=${item.title} item=${item} />`)}</ul>
    <p class="cx-about__muted">${t('about.science.more')} <a class="cx-link" href=${REFERENCES_URL}
      target="_blank" rel="noopener">${t('about.science.more_link')}</a>.</p>
  </section>`;
}

function Credits({ about, app }) {
  const authors = about && about.authors && about.authors.length ? formatList(about.authors) : '';
  return html`<section class="cx-about__section" aria-labelledby="cx-about-cite">
    <h2 id="cx-about-cite" class="cx-about__heading">${t('about.cite.title')}</h2>
    ${authors ? html`<p>${t('about.cite.authors', { authors })}</p>` : null}
    <p>${t('about.cite.text')}</p>
    ${about ? html`<blockquote class="cx-about__citation"><code>${about.citation}</code></blockquote>` : null}
    <h2 class="cx-about__heading">${t('about.licence.title')}</h2>
    <p>${t('about.licence.text', { licence: (about && about.licence) || '' })}</p>
    ${about && about.source ? html`<p>${t('about.source')} <${Outside} href=${about.source}>
      <code>${about.source}</code><//></p>` : null}
    <h2 class="cx-about__heading">${t('about.version.title')}</h2>
    <p><code class="cx-about__version">${versionLine({ ...app, build: (about && about.build) || app.build })}</code></p>
  </section>`;
}

/** The page. */
export function About() {
  const ctx = usePage();
  const app = ctx.app.manifest.app;
  const name = (ctx.app.manifest.branding && ctx.app.manifest.branding.name) || app.name;
  usePageTitle(t('about.title', { name }));
  const [about, setAbout] = useState(null);
  const [error, setError] = useState(null);
  const load = () => ctx.api.get('/api/app/about').then((result) => {
    if (result.ok) setAbout(result.data);
    else setError(result.error);
  });
  useEffect(() => {
    load();
  }, []);
  const branding = ctx.app.manifest.branding || {};
  const ownLogo = branding.logo && branding.logo !== '/static/brand/logo.svg';
  return html`<div class="cx-page cx-about">
    <div class="cx-about__head">
      <img class="cx-about__logo"
        src=${ownLogo ? branding.logo : '/static/brand/mark.svg'} alt="" width="96" height="96" />
      <h1 class="cx-page__title">${t('about.title', { name })}</h1>
    </div>
    <p class="cx-page__lead">${t('about.lead')}</p>
    <section class="cx-about__section" aria-labelledby="cx-about-measure">
      <h2 id="cx-about-measure" class="cx-about__heading">${t('about.measure.title')}</h2>
      <p>${t('about.measure.text')}</p>
      <p class="cx-about__muted">${t('about.scope')}</p>
    </section>
    <section class="cx-about__section" aria-labelledby="cx-about-how">
      <h2 id="cx-about-how" class="cx-about__heading">${t('about.pipeline.title')}</h2>
      <${PipelineFigure} />
      <p>${t('about.purpose.local')}</p>
    </section>
    <section class="cx-about__section" aria-labelledby="cx-about-built">
      <h2 id="cx-about-built" class="cx-about__heading">${t('about.built.title')}</h2>
      <p>${t('about.built.text')}</p>
    </section>
    <${Background} />
    ${error ? html`<${ErrorCard} error=${error} compact onRetry=${load} />` : null}
    <${Credits} about=${about} app=${app} />
    <p class="cx-about__docs">
      <a class="cx-link" href=${DOCS_URL} target="_blank" rel="noopener">${t('about.docs')}</a>
    </p>
  </div>`;
}
