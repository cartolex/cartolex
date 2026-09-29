// SPDX-License-Identifier: MIT
/**
 * A person's sheet, in a drawer: why their profile is what it is. The first
 * blocking cause comes first (a person without a usable profile has a sheet
 * saying why), then the sources used and discarded, each finder's latest
 * attempt, the texts and the affiliations with their years. Actions: retry a
 * failed collection, add documents, exclude, or decide the identity.
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { formatNumber, has, t } from '../../core/i18n.js';
import { Button, Drawer, ErrorCard } from '../../components/index.js';
import {
  CoverageState, Fact, IdentityState, coded, personName, roleLabel,
} from './common.js';

function finderLabel(finder) {
  return has(`corpus.finder.${finder}`) ? t(`corpus.finder.${finder}`) : finder;
}

function span(a) {
  if (!a.start_year && !a.end_year) return t('corpus.years.unknown');
  if (!a.end_year) return t('corpus.years.since', { first: a.start_year });
  return t('corpus.years.span', { first: a.start_year || '…', last: a.end_year });
}

/** The sheet of *personId*. */
export function PersonSheet({ ctx, personId, onClose, bump, toast, openCollect, openImport, canCollect }) {
  const [person, setPerson] = useState(null);
  const [error, setError] = useState(null);
  const load = () => ctx.api.get(`/api/people/${encodeURIComponent(personId)}/sheet`).then((r) => {
    if (r.ok) setPerson(r.data);
    else setError(r.error);
  });
  useEffect(() => {
    setPerson(null);
    load();
  }, [personId]);

  async function exclude() {
    const people = await ctx.api.get('/api/people', { query: { limit: 1 } });
    const result = await ctx.api.patch('/api/people', { person_ids: [personId], role: 'excluded' },
      { ifMatch: people.etag });
    if (!result.ok) {
      setError(result.error);
      return;
    }
    toast({ kind: 'success', title: t('corpus.sheet.excluded', { name: personName(person) }) });
    bump();
    load();
  }

  const sheet = person && person.sheet;
  const decision = (person && person.decision) || {};
  const actions = (sheet && sheet.actions) || [];
  return html`<${Drawer} open=${true} onClose=${onClose} size="l"
    title=${person ? personName(person) : t('common.loading')}
    description=${person ? [roleLabel(decision.role || ''), person.orcid && `ORCID ${person.orcid}`]
      .filter(Boolean).join(' · ') : null}>
    ${error ? html`<${ErrorCard} error=${error} compact onRetry=${load} />` : null}
    ${person ? html`<div class="cx-corpus-sheet">
      ${sheet ? html`<section class=${`cx-corpus-verdict cx-corpus-verdict--${sheet.state}`}>
        <${CoverageState} state=${sheet.state} />
        <p class="cx-corpus-verdict__counts">${t('corpus.sheet.counts', { words: sheet.with_abstract,
          titles: sheet.titles_only })}</p>
        ${sheet.cause ? html`<p class="cx-corpus-verdict__cause">
          <strong>${t('corpus.sheet.cause')}</strong> ${has(`corpus.cause.${sheet.cause}`)
            ? t(`corpus.cause.${sheet.cause}`) : sheet.cause}
          ${sheet.failure ? html`<span class="cx-corpus-muted"> ${t('corpus.sheet.failure', {
            finder: finderLabel(sheet.failure.finder), cause: sheet.failure.cause || '' })}</span>` : null}</p>` : null}
      </section>` : html`<p class="cx-corpus-muted">${t('corpus.sheet.merged', { into: decision.merged_into || '' })}</p>`}
      <div class="cx-corpus-actions-row">
        ${canCollect && actions.includes('retry') ? html`<${Button} size="s" icon="undo"
          onClick=${() => openCollect('retry', { people: [personId] })}>${t('corpus.sheet.retry')}<//>` : null}
        ${actions.includes('add_documents') ? html`<${Button} size="s" icon="upload"
          onClick=${() => openImport('folder', { person_id: personId, name: personName(person) })}>
          ${t('corpus.sheet.add_documents')}<//>` : null}
        ${actions.includes('exclude') ? html`<${Button} size="s" variant="ghost" onClick=${exclude}>
          ${t('corpus.sheet.exclude')}<//>` : null}
      </div>
      <dl class="cx-corpus-facts">
        <${Fact} label=${t('corpus.col.role')}>${roleLabel(decision.role || '')}${decision.set ? ` · ${decision.set}` : ''}<//>
        <${Fact} label=${t('corpus.col.identity')}><${IdentityState} state=${decision.identity} /><//>
        <${Fact} label=${t('corpus.sheet.records')}>${(decision.records || []).length
          ? (decision.records || []).map((r) => html`<code key=${r}>${r}</code> `) : '—'}<//>
        ${Object.entries(person.columns || {}).map(([k, v]) => html`<${Fact} key=${k} label=${k}>${v}<//>`)}
        ${person.aliases.length ? html`<${Fact} label=${t('corpus.sheet.aliases')}>${person.aliases.join(' · ')}<//>` : null}
      </dl>
      ${sheet ? html`<section><h3 class="cx-corpus-h3">${t('corpus.sheet.used')}</h3>
        ${sheet.sources.length ? html`<ul class="cx-corpus-list">${sheet.sources.map((s) => html`<li key=${s.finder}>
          ${t('corpus.sheet.source', { finder: finderLabel(s.finder), n: s.texts })}</li>`)}
          ${sheet.providers.map((p) => html`<li key=${`p-${p.provider}`} class="cx-corpus-muted">
          ${t('corpus.sheet.provider', { provider: finderLabel(p.provider), n: p.texts })}</li>`)}</ul>`
          : html`<p class="cx-corpus-muted">${t('corpus.sheet.no_source')}</p>`}
      </section>` : null}
      ${sheet && sheet.discarded.length ? html`<section><h3 class="cx-corpus-h3">${t('corpus.sheet.discarded', { n: sheet.discarded.length })}</h3>
        <ul class="cx-corpus-list">${sheet.discarded.slice(0, 50).map((d, i) => html`<li key=${i}>
          ${coded('corpus.discarded_what', { code: d.code, params: d.params, message: d.what })}
          <span class="cx-corpus-muted"> — ${coded('corpus.discarded_why', { code: d.code, params: d.params, message: d.why })}</span></li>`)}</ul></section>` : null}
      ${sheet && sheet.attempts.length ? html`<section><h3 class="cx-corpus-h3">${t('corpus.sheet.attempts')}</h3>
        <ul class="cx-corpus-list">${sheet.attempts.map((a) => html`<li key=${a.finder}>
          ${a.ok ? t('corpus.sheet.attempt_ok', { finder: finderLabel(a.finder) })
            : t('corpus.sheet.attempt_failed', { finder: finderLabel(a.finder), cause: a.cause || '' })}</li>`)}</ul>
      </section>` : null}
      <section><h3 class="cx-corpus-h3">${t('corpus.sheet.affiliations', { n: person.affiliations.length })}</h3>
        ${person.affiliations.length ? html`<table class="cx-corpus-simple">
          <thead><tr><th scope="col">${t('corpus.col.name')}</th><th scope="col">${t('corpus.col.level')}</th>
            <th scope="col">${t('corpus.col.years')}</th><th scope="col">${t('corpus.col.source')}</th></tr></thead>
          <tbody>${person.affiliations.map((a, i) => html`<tr key=${`${a.org_id}-${i}`}>
            <td>${a.name}</td><td>${a.level}</td><td>${span(a)}</td><td>${a.source}</td></tr>`)}</tbody>
        </table>` : html`<p class="cx-corpus-muted">${t('corpus.sheet.no_affiliation')}</p>`}
      </section>
      <section><h3 class="cx-corpus-h3">${t('corpus.sheet.texts', { n: formatNumber(person.texts.length) })}</h3>
        <ul class="cx-corpus-list cx-corpus-texts">${person.texts.slice(0, 100).map((x) => html`<li key=${x.text_id}>
          <span class="cx-corpus-texts__year">${x.year || '—'}</span>
          <span>${x.title}</span>
          <span class="cx-corpus-muted"> ${t(`corpus.content.${x.content}`)}</span></li>`)}</ul>
      </section>
    </div>` : null}
  <//>`;
}
