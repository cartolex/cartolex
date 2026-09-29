// SPDX-License-Identifier: MIT
/**
 * « Find on the map »: a combobox over people, organisations, themes,
 * keywords and texts (once read). Typing lists the matches (accents and case
 * ignored), arrows move among them, Enter selects one: the map centres on it
 * and the panel shows it. Escape clears the field.
 */
import { html, useMemo, useState } from '../../core/preact.js';
import { locale, t } from '../../core/i18n.js';
import { useUid } from '../../core/dom.js';
import { MapSymbol } from '../../components/index.js';
import { fold } from '../themes/model.js';
import { themeName } from './model.js';
import { SHAPE_OF } from './state.js';

const LIMIT = 12;

/** Everything one can find, with its folded text. */
function entries(index, texts, lang) {
  const out = [];
  index.people.forEach((p) => {
    if (p.person_id && p.x !== null) out.push({ kind: 'person', shape: 'people', id: p.person_id, text: p.name });
  });
  index.orgs.forEach((o) => {
    if (o.x !== null) out.push({ kind: 'organisation', shape: 'organisations', id: o.id, text: o.acronym ? `${o.acronym} · ${o.name}` : o.name });
  });
  for (const id of index.nodes.keys()) out.push({ kind: 'theme', shape: 'themes', id, text: themeName(index, id, lang) });
  index.keywords.forEach((k) => {
    if (k.x !== null) out.push({ kind: 'keyword', shape: 'keywords', id: k.term, text: k.term });
  });
  if (texts) texts.id.forEach((id, i) => out.push({ kind: 'text', shape: 'texts', id, text: texts.title[i] }));
  for (const e of out) e.folded = fold(e.text);
  return out;
}

/** The combobox. */
export function Find({ index, texts, onSelect }) {
  const id = useUid('cx-atlas-find');
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const all = useMemo(() => entries(index, texts, locale.value), [index, texts, locale.value]);
  const matches = useMemo(() => {
    const q = fold(query.trim());
    if (!q) return [];
    const starts = [];
    const within = [];
    for (const e of all) {
      const at = e.folded.indexOf(q);
      if (at === 0) starts.push(e);
      else if (at > 0) within.push(e);
      if (starts.length >= LIMIT) break;
    }
    return starts.concat(within).slice(0, LIMIT);
  }, [all, query]);
  const choose = (e) => {
    if (!e) return;
    onSelect({ kind: e.kind, id: e.id }, { centre: true });
    setQuery('');
  };
  const onKeyDown = (event) => {
    if (event.key === 'ArrowDown') {
      setActive((a) => Math.min(matches.length - 1, a + 1));
    } else if (event.key === 'ArrowUp') {
      setActive((a) => Math.max(0, a - 1));
    } else if (event.key === 'Enter') {
      choose(matches[active]);
    } else if (event.key === 'Escape' && query) {
      setQuery('');
    } else {
      return;
    }
    event.preventDefault();
  };
  const open = matches.length > 0;
  return html`<div class="cx-atlas-find">
    <label class="cx-visually-hidden" for=${id}>${t('map.find')}</label>
    <input id=${id} class="cx-input cx-atlas-find__input" type="search" autocomplete="off"
      placeholder=${t('map.find.placeholder')} value=${query} role="combobox"
      aria-expanded=${String(open)} aria-controls=${`${id}-list`} aria-autocomplete="list"
      aria-activedescendant=${open ? `${id}-${active}` : undefined}
      onInput=${(e) => { setQuery(e.currentTarget.value); setActive(0); }} onKeyDown=${onKeyDown} />
    <ul id=${`${id}-list`} class="cx-atlas-find__list" role="listbox" aria-label=${t('map.find')} hidden=${!open}>
      ${matches.map((e, k) => html`<li key=${`${e.kind}:${e.id}`} id=${`${id}-${k}`} role="option"
        aria-selected=${String(k === active)} class=${k === active ? 'is-active' : ''}
        onMouseDown=${(ev) => { ev.preventDefault(); choose(e); }}>
        <${MapSymbol} shape=${SHAPE_OF[e.shape] || 'circle'} />
        <span class="cx-atlas-find__text">${e.text}</span>
        <span class="cx-atlas-find__kind">${t(`map.kind1.${e.shape}`)}</span>
      </li>`)}
    </ul>
    <p class="cx-visually-hidden" aria-live="polite">${query ? t('map.find.count', { count: matches.length }) : ''}</p>
  </div>`;
}

