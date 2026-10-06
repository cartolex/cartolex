// SPDX-License-Identifier: MIT
/**
 * The atlas page's side panel: what is selected on the map (a person, an
 * organisation, a text, a theme, a keyword, a projected person) with its
 * themes, its keywords, its people or its time windows, each a button that
 * selects it in turn; with nothing selected, what the map shows, each count
 * with what it counts. Links open the selection on another screen (People,
 * Keywords, Themes); a person's co-authors and an organisation's partners (who they
 * sign works with, `coauthors.js`; « Compare with… » measures a similarity), and the
 * people who use a keyword (the space of the themes).
 */
import { html } from '../../core/preact.js';
import { formatNumber, formatPercent, locale, t } from '../../core/i18n.js';
import { Button, MapSymbol } from '../../components/index.js';
import { levelLabel, orgName, periodOf, themeName } from './model.js';
import { SHAPE_OF } from './state.js';
import { Links, linkTo } from './links.js';
import { Users } from './near.js';
import { Coauthors } from './coauthors.js';

function Shares({ index, shares, level = 1, limit = 6 }) {
  const list = Object.entries(shares || {}).sort((a, b) => b[1] - a[1]).slice(0, limit);
  if (!list.length) return html`<p class="cx-atlas-panel__muted">${t('map.panel.no_shares')}</p>`;
  return html`<ul class="cx-atlas-shares" aria-label=${t('map.panel.themes', { level })}>
    ${list.map(([id, v]) => html`<li key=${id} class="cx-atlas-shares__row">
      <span class="cx-atlas-shares__bar" aria-hidden="true"
        style=${{ '--cx-share': `${Math.round(v * 100)}%`, '--cx-chip': `var(--cx-hue-${index.colourOf(id) + 1})` }}></span>
      <span class="cx-atlas-shares__name">${themeName(index, id, locale.value)}</span>
      <span class="cx-atlas-shares__value">${formatPercent(v)}</span>
    </li>`)}
  </ul>`;
}

function Chips({ items, onSelect, label }) {
  if (!items.length) return null;
  return html`<ul class="cx-atlas-chips" aria-label=${label}>
    ${items.map((it) => html`<li key=${it.key}>
      <button type="button" class="cx-atlas-chip" onClick=${() => onSelect(it.sel)}>${it.text}</button>
    </li>`)}
  </ul>`;
}

function Section({ title, children }) {
  return html`<section class="cx-atlas-panel__section">
    <h3 class="cx-atlas-panel__subtitle">${title}</h3>
    ${children}
  </section>`;
}

function Head({ kind, title, detail }) {
  return html`<header class="cx-atlas-panel__head">
    <p class="cx-atlas-panel__kind"><${MapSymbol} shape=${SHAPE_OF[kind] || 'circle'} /> ${t(`map.kind1.${kind}`)}</p>
    <h2 class="cx-atlas-panel__title" tabindex="-1">${title}</h2>
    ${detail ? html`<p class="cx-atlas-panel__detail">${detail}</p>` : null}
  </header>`;
}

const keywordChips = (terms) => terms.map((term) => ({ key: term, text: term, sel: { kind: 'keyword', id: term } }));

/** Who the selection writes with, with « Compare with… ». */
function Partners({ co, onSelect, onCompare, kind }) {
  const count = co && co.data ? co.data.count : null;
  const key = kind === 'organisation' ? 'map.coauthors.orgs_title' : 'map.coauthors.title';
  return html`<${Section} title=${count === null ? t(`${key}_plain`) : t(key, { count })}>
    <${Coauthors} answer=${co} onSelect=${onSelect} second=${co ? co.second : false} onSecond=${co ? co.onSecond : null} />
    ${onCompare ? html`<${Button} size="s" onClick=${onCompare} aria-haspopup="dialog">${t('map.compare.button')}<//>` : null}
  <//>`;
}

function Person({ index, state, id, sets, texts, onSelect, co, onCompare }) {
  const p = index.people[index.byPerson.get(id)];
  const info = index.extra[id] || { columns: {}, orgs: [] };
  const period = periodOf(index, state);
  const windows = index.windows.get(id) || [];
  const terms = sets.get(`person:${id}`);
  // A sample of the texts cannot count a person's: the count is left out then.
  const written = texts && !texts.sampled ? texts.people.reduce((n, ps, i) => n + (ps.includes(id)
    && (!period || texts.year[i] === null || (texts.year[i] >= period[0] && texts.year[i] <= period[1])) ? 1 : 0), 0) : null;
  const orgs = info.orgs.map((o) => index.orgs[index.byOrg.get(o)]).filter(Boolean);
  return html`<div>
    <${Head} kind="people" title=${p.name} detail=${p.unit} />
    <${Links} label=${t('map.links')} links=${[{ href: linkTo.person(id), label: t('map.links.person') }]} />
    ${Object.keys(info.columns).length ? html`<dl class="cx-atlas-facts">
      ${Object.entries(info.columns).map(([k, v]) => html`<div key=${k}><dt>${k}</dt><dd>${v}</dd></div>`)}
    </dl>` : null}
    ${orgs.length ? html`<${Section} title=${t('map.panel.orgs_now')}>
      <${Chips} label=${t('map.panel.orgs_now')} onSelect=${onSelect}
        items=${orgs.map((o) => ({ key: o.id, text: orgName(o), sel: { kind: 'organisation', id: o.id } }))} />
    <//>` : null}
    <${Section} title=${t('map.panel.themes', { level: 1 })}>
      <${Shares} index=${index} shares=${p.shares && p.shares[0]} />
    <//>
    <${Section} title=${t('map.panel.keywords')}>
      ${terms ? html`<${Chips} label=${t('map.panel.keywords')} onSelect=${onSelect} items=${keywordChips(terms.slice(0, 20))} />`
        : html`<p class="cx-atlas-panel__muted" aria-busy="true">${t('common.loading')}</p>`}
    <//>
    <${Partners} co=${co} onSelect=${onSelect} onCompare=${onCompare} kind="person" />
    ${windows.length ? html`<${Section} title=${t('map.panel.windows')}>
      <ol class="cx-atlas-windows">
        ${windows.map((w) => {
          const inside = !period || (w.end >= period[0] && w.start <= period[1]);
          const top = w.top;
          return html`<li key=${w.start} class=${inside ? '' : 'is-outside'}>
            <span class="cx-atlas-windows__years">${t('map.period.value', { from: String(w.start), to: String(w.end) })}</span>
            <span>${top ? themeName(index, top, locale.value) : ''}</span>
            <span class="cx-atlas-panel__muted">${t('map.panel.texts_count', { count: w.texts })}</span>
          </li>`;
        })}
      </ol>
      <p class="cx-atlas-panel__muted">${t('map.panel.windows_help')}</p>
    <//>` : null}
    ${written !== null ? html`<p class="cx-atlas-panel__muted">${t('map.panel.texts_in_period', { count: written })}</p>` : null}
  </div>`;
}

function Organisation({ index, id, sets, onSelect, co, onCompare }) {
  const i = index.byOrg.get(id);
  const o = index.orgs[i];
  const level = index.levels.find((lv) => lv.id === o.level);
  const members = index.members.get(id) || [];
  const terms = sets.get(`organisation:${id}`);
  const parents = (o.parents || []).map((p) => index.orgs[index.byOrg.get(p)]).filter(Boolean);
  return html`<div>
    <${Head} kind="organisations" title=${o.name} detail=${[o.acronym, level ? levelLabel(level, locale.value) : o.level].filter(Boolean).join(' · ')} />
    <${Links} label=${t('map.links')} links=${[{ href: linkTo.organisation(id), label: t('map.links.organisation') }]} />
    <p class="cx-atlas-count">${t('map.panel.members', { now: members.length, ever: o.members_ever })}</p>
    ${parents.length ? html`<${Chips} label=${t('map.panel.parents')} onSelect=${onSelect}
      items=${parents.map((p) => ({ key: p.id, text: orgName(p), sel: { kind: 'organisation', id: p.id } }))} />` : null}
    <${Section} title=${t('map.panel.themes', { level: 1 })}>
      <${Shares} index=${index} shares=${index.orgShares.get(id)} />
    <//>
    <${Section} title=${t('map.panel.keywords')}>
      ${terms ? html`<${Chips} label=${t('map.panel.keywords')} onSelect=${onSelect} items=${keywordChips(terms.slice(0, 20))} />`
        : html`<p class="cx-atlas-panel__muted" aria-busy="true">${t('common.loading')}</p>`}
    <//>
    ${members.length ? html`<${Section} title=${t('map.panel.people')}>
      <${Chips} label=${t('map.panel.people')} onSelect=${onSelect}
        items=${members.slice(0, 60).map((k) => ({ key: index.people[k].person_id, text: index.people[k].name,
          sel: { kind: 'person', id: index.people[k].person_id } }))} />
    <//>` : null}
    <${Partners} co=${co} onSelect=${onSelect} onCompare=${onCompare} kind="organisation" />
  </div>`;
}

function Text({ index, id, texts, onSelect }) {
  if (!texts) return html`<p class="cx-atlas-panel__muted" aria-busy="true">${t('common.loading')}</p>`;
  const i = texts.id.indexOf(id);
  if (i < 0) return html`<p class="cx-atlas-panel__muted">${t('map.panel.gone')}</p>`;
  const people = texts.people[i].filter((pid) => index.byPerson.has(pid));
  return html`<div>
    <${Head} kind="texts" title=${texts.title[i]} detail=${texts.year[i] ? String(texts.year[i]) : ''} />
    <${Links} label=${t('map.links')} links=${[{ href: linkTo.text(id), label: t('map.links.text') }]} />
    <p class="cx-atlas-panel__muted">${t(texts.by[i] === 0 ? 'map.panel.placed_keywords' : 'map.panel.placed_authors',
      { count: texts.terms[i].length })}</p>
    ${people.length ? html`<${Section} title=${t('map.panel.authors')}>
      <${Chips} label=${t('map.panel.authors')} onSelect=${onSelect}
        items=${people.map((pid) => ({ key: pid, text: index.people[index.byPerson.get(pid)].name, sel: { kind: 'person', id: pid } }))} />
    <//>` : null}
    ${texts.terms[i].length ? html`<${Section} title=${t('map.panel.keywords_found')}>
      <${Chips} label=${t('map.panel.keywords_found')} onSelect=${onSelect}
        items=${keywordChips(texts.terms[i].map((k) => index.keywords[k].term))} />
    <//>` : null}
  </div>`;
}

function Theme({ index, id, onSelect }) {
  const node = index.nodes.get(id);
  const level = node.level;
  const path = [];
  for (let at = node.parent; at && index.nodes.has(at); at = index.nodes.get(at).parent) path.unshift(at);
  const people = index.people.map((p, i) => [i, (p.shares && p.shares[level - 1] && p.shares[level - 1][id]) || 0])
    .filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).slice(0, 12);
  return html`<div>
    <${Head} kind="themes" title=${themeName(index, id, locale.value)}
      detail=${node.share !== null && node.share !== undefined ? t('map.panel.theme_share', { share: node.share }) : ''} />
    <${Links} label=${t('map.links')} links=${[{ href: linkTo.themesNode(id), label: t('map.links.theme') }]} />
    ${path.length ? html`<${Chips} label=${t('map.panel.path')} onSelect=${onSelect}
      items=${path.map((p) => ({ key: p, text: themeName(index, p, locale.value), sel: { kind: 'theme', id: p } }))} />` : null}
    ${(index.children.get(id) || []).length ? html`<${Section} title=${t('map.panel.subthemes')}>
      <${Chips} label=${t('map.panel.subthemes')} onSelect=${onSelect}
        items=${index.children.get(id).map((c) => ({ key: c, text: themeName(index, c, locale.value), sel: { kind: 'theme', id: c } }))} />
    <//>` : null}
    <${Section} title=${t('map.panel.top_keywords')}>
      <${Chips} label=${t('map.panel.top_keywords')} onSelect=${onSelect} items=${keywordChips(node.top_keywords || [])} />
    <//>
    <${Section} title=${t('map.panel.weigh_most')}>
      <ul class="cx-atlas-list">
        ${people.map(([i, v]) => html`<li key=${i}>
          <button type="button" class="cx-link-button" onClick=${() => onSelect({ kind: 'person', id: index.people[i].person_id })}>
            ${index.people[i].name}</button>
          <span class="cx-atlas-panel__muted">${formatPercent(v)}</span></li>`)}
      </ul>
      <p class="cx-atlas-panel__muted">${t('map.panel.weigh_help')}</p>
    <//>
  </div>`;
}

function Keyword({ index, id, onSelect, space }) {
  const k = index.keywords[index.byTerm.get(id)];
  const path = [];
  for (let at = k.node; at && index.nodes.has(at); at = index.nodes.get(at).parent) path.unshift(at);
  return html`<div>
    <${Head} kind="keywords" title=${k.term}
      detail=${k.share ? t('map.panel.keyword_share', { share: k.share }) : ''} />
    ${path.length ? html`<${Chips} label=${t('map.panel.path')} onSelect=${onSelect}
      items=${path.map((p) => ({ key: p, text: themeName(index, p, locale.value), sel: { kind: 'theme', id: p } }))} />`
      : html`<p class="cx-atlas-panel__muted">${t('map.panel.aside')}</p>`}
    <${Links} label=${t('map.links')} links=${[{ href: linkTo.keyword(id), label: t('map.links.keyword') },
      { href: linkTo.themesKeyword(id), label: t('map.links.keyword_themes') }]} />
    <${Section} title=${t('map.users.title')}>
      <${Users} answer=${space} onSelect=${onSelect} />
    <//>
  </div>`;
}

function Projected({ index, id, co, onSelect }) {
  const p = index.projected.find((o) => o.person_id === id);
  if (!p) return html`<p class="cx-atlas-panel__muted">${t('map.panel.gone')}</p>`;
  return html`<div>
    <${Head} kind="projected" title=${id} detail=${p.set} />
    <p class="cx-atlas-panel__muted">${t('map.panel.projected_help')}</p>
    <${Section} title=${t('map.panel.themes', { level: 1 })}>
      <${Shares} index=${index} shares=${p.shares && p.shares[0]} />
    <//>
    <${Partners} co=${co} onSelect=${onSelect} kind="projected" />
  </div>`;
}

/** What the map shows, each count with what it counts. */
function Summary({ index, state, counts, base }) {
  const rows = Object.entries(counts);
  return html`<div>
    <h2 class="cx-atlas-panel__title" tabindex="-1">${t('map.panel.summary')}</h2>
    <ul class="cx-atlas-counts">
      ${rows.map(([kind, c]) => html`<li key=${kind}>
        <${MapSymbol} shape=${SHAPE_OF[kind]} />
        <span><strong>${formatNumber(c.shown)}</strong> ${t(`map.count.${kind}`, { shown: c.shown, total: c.total })}</span>
      </li>`)}
    </ul>
    ${state.view === 'world' ? html`<p class="cx-atlas-panel__muted">${t('map.world.help')}</p>` : null}
    ${base ? html`<p class="cx-atlas-panel__muted">${t('map.base.help')}</p>` : null}
    <p class="cx-atlas-panel__muted">${t('map.panel.hint')}</p>
  </div>`;
}

/** The side panel. */
export function Panel({ index, state, counts, sets, texts, onSelect, onClose, base, space, co, onCompare }) {
  const sel = state.sel;
  let body = null;
  if (sel && sel.kind === 'person' && index.byPerson.has(sel.id)) {
    body = html`<${Person} index=${index} state=${state} id=${sel.id} sets=${sets} texts=${texts} onSelect=${onSelect}
      co=${co} onCompare=${onCompare} />`;
  } else if (sel && sel.kind === 'organisation' && index.byOrg.has(sel.id)) {
    body = html`<${Organisation} index=${index} id=${sel.id} sets=${sets} onSelect=${onSelect}
      co=${co} onCompare=${onCompare} />`;
  } else if (sel && sel.kind === 'text') {
    body = html`<${Text} index=${index} id=${sel.id} texts=${texts} onSelect=${onSelect} />`;
  } else if (sel && sel.kind === 'theme' && index.nodes.has(sel.id)) {
    body = html`<${Theme} index=${index} id=${sel.id} onSelect=${onSelect} />`;
  } else if (sel && sel.kind === 'keyword' && index.byTerm.has(sel.id)) {
    body = html`<${Keyword} index=${index} id=${sel.id} onSelect=${onSelect} space=${space} />`;
  } else if (sel && sel.kind === 'projected') {
    body = html`<${Projected} index=${index} id=${sel.id} co=${co} onSelect=${onSelect} />`;
  }
  return html`<aside class="cx-atlas-panel" aria-label=${t('map.panel.label')}>
    ${body ? html`<div class="cx-atlas-panel__close">
      <${Button} size="s" variant="ghost" icon="close" onClick=${onClose}>${t('map.panel.clear')}<//>
    </div>` : null}
    ${body || html`<${Summary} index=${index} state=${state} counts=${counts} base=${base} />`}
  </aside>`;
}
