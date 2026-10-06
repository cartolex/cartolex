/**
 * The side panel: the selected node, keywords or person — what it holds, its
 * usage, the people who weigh most on it, its names, and its actions; links to
 * the map and the keywords, and who uses a keyword most (`users.js`).
 */

import { html } from '../../core/preact.js';
import { formatNumber, locale, t } from '../../core/i18n.js';
import { Button, MenuButton } from '../../components/index.js';
import { lang2, levelName, nodeName, pathOf } from './model.js';
import { languageName, shortShare } from './labels.js';
import { SuggestedPlaces } from './fit.js';
import { KeywordUsers, ThemeLinks } from './users.js';

function Path({ index, id, ui }) {
  const lang = lang2(locale.value);
  const path = pathOf(index, id);
  if (path.length < 2) return null;
  return html`<nav class="cx-themes-panel__path" aria-label=${t('themes.panel.path')}>
    <ol>${path.slice(0, -1).map((pid) => html`<li key=${pid}>
      <button type="button" class="cx-link-button" onClick=${() => ui.openNode(pid)}>
        ${nodeName(index.nodes.get(pid), lang)}</button></li>`)}</ol>
  </nav>`;
}

function Fact({ label, children }) {
  return html`<div class="cx-themes-facts__item"><dt>${label}</dt><dd>${children}</dd></div>`;
}

/** The people with the largest share on a node, from the last apply. */
function weighers(atlas, id, level, limit = 8) {
  if (!atlas || !atlas.available) return null;
  if (!atlas.nodes.some((n) => n.id === id)) return null;
  return atlas.people
    .map((p) => ({ p, share: (p.shares[level - 1] || {})[id] || 0 }))
    .filter((e) => e.share > 0)
    .sort((a, b) => b.share - a.share)
    .slice(0, limit);
}

function NodePanel({ editor, ui, atlas, id }) {
  const index = editor.index.value;
  const lang = lang2(locale.value);
  const node = index.nodes.get(id);
  const level = index.level.get(id);
  const own = index.keywordsOn.get(id) || [];
  const kids = index.children.get(id) || [];
  const under = index.under.get(id);
  const share = index.total ? index.weight.get(id) / index.total : 0;
  const readOnly = editor.readOnly.value;
  const people = weighers(atlas, id, level);
  const top = [...own];
  if (top.length < 30) {
    // A node with few keywords of its own: its most used keywords below it.
    const below = [];
    const walk = (nid) => {
      for (const kid of index.children.get(nid) || []) {
        below.push(...(index.keywordsOn.get(kid) || []));
        walk(kid);
      }
    };
    walk(id);
    below.sort((a, b) => index.weightOf(b) - index.weightOf(a));
    top.push(...below.slice(0, 30 - top.length));
  }
  const saved = editor.base.value && editor.base.value.tree.nodes.find((n) => n.id === id);
  const mapped = atlas && atlas.available ? atlas.nodes.find((n) => n.id === id) : null;
  const names = node.names || {};
  const before = saved && nodeName(saved, lang) !== nodeName(node, lang) ? nodeName(saved, lang) : null;
  const onMap = mapped && nodeName(mapped, lang) !== nodeName(node, lang) ? nodeName(mapped, lang) : null;
  const hue = (index.hue.get(id) % 12) + 1;
  return html`<div class="cx-themes-panel__body">
    <header class="cx-themes-panel__head">
      <p class="cx-themes-panel__kind">
        <span class="cx-themes-chip" style=${{ '--cx-chip': `var(--cx-hue-${hue})` }} aria-hidden="true"></span>
        ${t('themes.panel.level', { level: levelName(index.tree, level, lang), number: level })}</p>
      <h2 class="cx-themes-panel__title" id="cx-themes-panel-title">${nodeName(node, lang)}</h2>
      <${Path} index=${index} id=${id} ui=${ui} />
    </header>
    <${ThemeLinks} node=${id} />
    ${readOnly ? null : html`<div class="cx-themes-panel__actions">
      <${Button} size="s" onClick=${() => ui.rename(id)}>${t('themes.action.rename')}<//>
      <${Button} size="s" onClick=${() => ui.moveNode(id)} disabled=${level === 1 && !ui.canMoveNode(id)}>
        ${t('themes.action.move')}<//>
      <${Button} size="s" onClick=${() => ui.merge(id)}>${t('themes.action.merge')}<//>
      <${MenuButton} size="s" label=${t('themes.action.more')} items=${ui.nodeMenu(id, { panel: true })}
        onSelect=${(item) => ui.onMenu(item, [`n:${id}`])} />
    </div>`}
    <dl class="cx-themes-facts">
      <${Fact} label=${t('themes.panel.keywords')}>
        ${t('themes.panel.keywords.value', { count: under, own: own.length })}<//>
      ${level < index.depth ? html`<${Fact} label=${t('themes.panel.children', { level: levelName(index.tree, level + 1, lang) })}>
        ${formatNumber(kids.length)}<//>` : null}
      <${Fact} label=${t('themes.panel.share')}>${shortShare(share)}<//>
    </dl>
    <section class="cx-themes-panel__section" aria-labelledby="cx-themes-panel-kw">
      <h3 class="cx-themes-panel__subtitle" id="cx-themes-panel-kw">${own.length === top.length
        ? t('themes.panel.top_keywords') : t('themes.panel.top_keywords_under')}</h3>
      ${top.length ? html`<ul class="cx-themes-panel__keywords">
        ${top.map((term) => html`<li key=${term}>
          <button type="button" class="cx-themes-panel__keyword" onClick=${() => ui.openKeyword(term)}>
            <span>${term}</span>
            <span class="cx-themes-row__count" aria-label=${t('themes.panel.people_count', { count: index.people(term) })}>
              ${formatNumber(index.people(term))}</span>
          </button></li>`)}
      </ul>` : html`<p class="cx-themes-empty-line">${t('themes.panel.no_keywords')}</p>`}
    </section>
    <section class="cx-themes-panel__section" aria-labelledby="cx-themes-panel-people">
      <h3 class="cx-themes-panel__subtitle" id="cx-themes-panel-people">${t('themes.panel.people')}</h3>
      ${people === null ? html`<p class="cx-themes-empty-line">${t('themes.panel.people.none')}</p>`
        : people.length ? html`<ol class="cx-themes-panel__people">
          ${people.map((e) => html`<li key=${e.p.person_id || e.p.name}>
            <span class="cx-themes-panel__person">${e.p.name}</span>
            <span class="cx-themes-bar" aria-hidden="true"><span class="cx-themes-bar__fill"
              style=${{ '--cx-bar': `${Math.round(e.share * 100)}%` }}></span></span>
            <span class="cx-themes-row__share">${shortShare(e.share)}</span>
          </li>`)}
        </ol>
        <p class="cx-themes-panel__note">${t('themes.panel.people.note')}</p>`
        : html`<p class="cx-themes-empty-line">${t('themes.panel.people.empty')}</p>`}
    </section>
    <section class="cx-themes-panel__section" aria-labelledby="cx-themes-panel-names">
      <h3 class="cx-themes-panel__subtitle" id="cx-themes-panel-names">${t('themes.panel.names')}</h3>
      <dl class="cx-themes-facts cx-themes-facts--names">
        ${Object.entries(names).map(([code, value]) => html`<${Fact} key=${code} label=${languageName(code)}>
          <span lang=${code}>${value}</span><//>`)}
        ${!Object.keys(names).length ? html`<${Fact} label=${t('themes.panel.names.none')}><code>${id}</code><//>` : null}
        ${before ? html`<${Fact} label=${t('themes.panel.names.saved')}>${before}<//>` : null}
        ${onMap ? html`<${Fact} label=${t('themes.panel.names.map')}>${onMap}<//>` : null}
        <${Fact} label=${t('themes.panel.id')}><code>${id}</code><//>
      </dl>
    </section>
  </div>`;
}

function KeywordsPanel({ editor, ui, terms, api }) {
  const index = editor.index.value;
  const lang = lang2(locale.value);
  const tree = index.tree;
  const readOnly = editor.readOnly.value;
  const single = terms.length === 1 ? terms[0] : null;
  const aside = terms.filter((k) => tree.set_aside && k in tree.set_aside);
  const placed = terms.filter((k) => tree.keywords[k] !== undefined);
  const checking = terms.filter((k) => (tree.review || {})[k] === 'to_check');
  const people = terms.reduce((s, k) => s + index.people(k), 0);
  const weight = terms.reduce((s, k) => s + index.weightOf(k), 0);
  const nodes = [...new Set(placed.map((k) => tree.keywords[k]))];
  const entry = single && aside.length ? tree.set_aside[single] : null;
  const attr = single && placed.length ? (tree.attribution || {})[single] : undefined;
  const level = single && placed.length ? index.level.get(tree.keywords[single]) : 0;
  return html`<div class="cx-themes-panel__body">
    <header class="cx-themes-panel__head">
      <p class="cx-themes-panel__kind">${aside.length === terms.length ? t('themes.panel.aside_kind')
        : t('themes.panel.keyword_kind', { count: terms.length })}</p>
      <h2 class="cx-themes-panel__title" id="cx-themes-panel-title">${single
        || t('themes.panel.keywords_title', { count: terms.length })}</h2>
      ${single && placed.length ? html`<${Path} index=${index} id=${tree.keywords[single]} ui=${ui} />
        <p class="cx-themes-panel__on">${t('themes.panel.on', { name: nodeName(index.nodes.get(tree.keywords[single]), lang) })}</p>` : null}
    </header>
    ${readOnly ? null : html`<div class="cx-themes-panel__actions">
      ${placed.length ? html`<${Button} size="s" onClick=${() => ui.moveKeywords(placed)}>${t('themes.action.move')}<//>` : null}
      ${aside.length ? html`<${Button} size="s" onClick=${() => ui.putBack(aside)}>${t('themes.action.put_back')}<//>
        <${Button} size="s" onClick=${() => ui.putBackInto(aside)}>${t('themes.action.put_back_into')}<//>` : null}
      ${placed.length ? html`<${Button} size="s" onClick=${() => ui.setAside(placed)}>${t('themes.action.set_aside')}<//>` : null}
      ${checking.length ? html`<${Button} size="s" onClick=${() => ui.accept(checking)}>${t('themes.action.accept')}<//>` : null}
      <${MenuButton} size="s" label=${t('themes.action.more')} items=${ui.keywordMenu(terms)}
        onSelect=${(item) => ui.onMenu(item, terms.map((k) => (aside.includes(k) ? `a:${k}` : `k:${k}`)))} />
    </div>`}
    <dl class="cx-themes-facts">
      <${Fact} label=${t('themes.panel.people_using')}>${formatNumber(people)}<//>
      <${Fact} label=${t('themes.panel.usage')}>${shortShare(index.total ? weight / index.total : 0)}<//>
      ${nodes.length > 1 ? html`<${Fact} label=${t('themes.panel.on_nodes')}>${formatNumber(nodes.length)}<//>` : null}
      ${checking.length ? html`<${Fact} label=${t('themes.panel.review')}>${t('themes.panel.review.to_check', { count: checking.length })}<//>` : null}
      ${single && placed.length ? html`<${Fact} label=${t('themes.panel.counts')}>${attr === undefined
        ? t('themes.panel.counts.default', { level: levelName(tree, level, lang) })
        : attr === 0 ? t('themes.panel.counts.none')
          : t('themes.panel.counts.level', { level: levelName(tree, attr, lang) })}<//>` : null}
      ${entry ? html`<${Fact} label=${t('themes.panel.aside.reason')}>${entry.reason || t('themes.aside.no_reason')}<//>
        <${Fact} label=${t('themes.panel.aside.from')}>${entry.from && index.nodes.has(entry.from)
          ? nodeName(index.nodes.get(entry.from), lang) : t('themes.panel.aside.from.none')}<//>` : null}
    </dl>
    ${single ? html`<${ThemeLinks} term=${single} />
      <section class="cx-themes-panel__section" aria-labelledby="cx-themes-panel-users">
        <h3 class="cx-themes-panel__subtitle" id="cx-themes-panel-users">${t('themes.users.title')}</h3>
        <${KeywordUsers} api=${api} term=${single} />
      </section>` : null}
    ${single && (aside.length || checking.length) ? html`<${SuggestedPlaces} editor=${editor} ui=${ui} term=${single} />` : null}
    ${terms.length > 1 ? html`<ul class="cx-themes-panel__keywords">
      ${terms.slice(0, 60).map((term) => html`<li key=${term}>
        <button type="button" class="cx-themes-panel__keyword" onClick=${() => ui.openKeyword(term)}>
          <span>${term}</span><span class="cx-themes-row__count">${formatNumber(index.people(term))}</span>
        </button></li>`)}
    </ul>` : null}
  </div>`;
}

function PersonPanel({ editor, atlas, id }) {
  const index = editor.index.value;
  const lang = lang2(locale.value);
  const person = atlas && atlas.people.find((p) => p.person_id === id);
  if (!person) return null;
  return html`<div class="cx-themes-panel__body">
    <header class="cx-themes-panel__head">
      <p class="cx-themes-panel__kind">${t('themes.panel.person_kind')}</p>
      <h2 class="cx-themes-panel__title" id="cx-themes-panel-title">${person.name}</h2>
      <p class="cx-themes-panel__on">${person.unit}</p>
    </header>
    ${person.shares.map((shares, i) => {
      const list = Object.entries(shares).sort((a, b) => b[1] - a[1]).slice(0, 5);
      if (!list.length) return null;
      return html`<section class="cx-themes-panel__section" key=${i}>
        <h3 class="cx-themes-panel__subtitle">${levelName(index.tree, i + 1, lang) || t('themes.level.numbered', { level: i + 1, name: '' })}</h3>
        <ol class="cx-themes-panel__people">
          ${list.map(([nid, share]) => html`<li key=${nid}>
            <span class="cx-themes-panel__person">${index.nodes.has(nid) ? nodeName(index.nodes.get(nid), lang)
              : nodeName((atlas.nodes.find((n) => n.id === nid) || { id: nid }), lang)}</span>
            <span class="cx-themes-bar" aria-hidden="true"><span class="cx-themes-bar__fill"
              style=${{ '--cx-bar': `${Math.round(share * 100)}%` }}></span></span>
            <span class="cx-themes-row__share">${shortShare(share)}</span></li>`)}
        </ol>
      </section>`;
    })}
    <p class="cx-themes-panel__note">${t('themes.panel.people.note')}</p>
  </div>`;
}

function Overview({ editor }) {
  const index = editor.index.value;
  const lang = lang2(locale.value);
  const perLevel = index.tree.levels.map((_, i) => index.order.filter((id) => index.level.get(id) === i + 1).length);
  return html`<div class="cx-themes-panel__body">
    <header class="cx-themes-panel__head">
      <p class="cx-themes-panel__kind">${t('themes.panel.tree_kind')}</p>
      <h2 class="cx-themes-panel__title" id="cx-themes-panel-title">${t('themes.panel.tree_title', { depth: index.depth })}</h2>
    </header>
    <dl class="cx-themes-facts">
      ${index.tree.levels.map((_, i) => html`<${Fact} key=${i} label=${levelName(index.tree, i + 1, lang)}>
        ${formatNumber(perLevel[i])}<//>`)}
      <${Fact} label=${t('themes.panel.placed')}>${formatNumber(Object.keys(index.tree.keywords).length)}<//>
      <${Fact} label=${t('themes.tab.aside')}>${formatNumber(index.setAside.length)}<//>
      <${Fact} label=${t('themes.tab.check')}>${formatNumber(index.toCheck.length)}<//>
    </dl>
    <p class="cx-themes-panel__note">${t('themes.panel.hint')}</p>
  </div>`;
}

/** The right column. */
export function SidePanel({ editor, ui, atlas, api }) {
  const index = editor.index.value;
  if (!index) return null;
  const focus = ui.focus.value;
  let body;
  if (focus && focus.kind === 'node' && index.nodes.has(focus.id)) {
    body = html`<${NodePanel} editor=${editor} ui=${ui} atlas=${atlas} id=${focus.id} />`;
  } else if (focus && focus.kind === 'keywords' && focus.terms.length) {
    body = html`<${KeywordsPanel} editor=${editor} ui=${ui} terms=${focus.terms} api=${api} />`;
  } else if (focus && focus.kind === 'person') {
    body = html`<${PersonPanel} editor=${editor} atlas=${atlas} id=${focus.id} />`;
  } else {
    body = html`<${Overview} editor=${editor} />`;
  }
  // Escape gives the focus back to the outline (Enter there brought it here).
  const onKeyDown = (event) => {
    if (event.key === 'Escape' && !event.defaultPrevented && ui.outlineRef.current
      && !document.querySelector('[role=menu]')) {
      event.preventDefault();
      ui.outlineRef.current.focus();
    }
  };
  return html`<aside class="cx-themes-panel" aria-labelledby="cx-themes-panel-title" tabindex="-1"
    ref=${ui.panelRef} onKeyDown=${onKeyDown}>${body}</aside>`;
}
