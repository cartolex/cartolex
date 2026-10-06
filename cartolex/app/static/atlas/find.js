// SPDX-License-Identifier: MIT
/**
 * « Find »: a combobox over people, organisations, themes, keywords, projected people and
 * texts (once read). Typing lists the matches (accents and case ignored; those that start
 * with the words first), the arrows move among them, Enter or a click picks one, Escape
 * clears the field. Also used to pick the other side of a comparison (only people or only
 * organisations).
 */
import { folded, h, listen, symbol, uid } from './dom.js';
import { nameIn } from './data.js';
import { SHAPE_OF } from './state.js';

const FIND_LIMIT = 12;
const SHAPE_OF_SEL = { person: 'people', organisation: 'organisations', theme: 'themes', keyword: 'keywords',
  projected: 'projected', text: 'texts' };

/** Everything one can find in *index* (and *texts* when read): `{kind, id, text, folded}`. */
export function findEntries(index, texts, { locale, nameOf, levelName }) {
  const out = [];
  index.people.forEach((p) => {
    if (p.person_id && p.x !== null && p.x !== undefined) out.push({ kind: 'person', id: p.person_id, text: nameOf('person', p) });
  });
  index.projected.forEach((p) => out.push({ kind: 'projected', id: p.person_id, text: nameOf('projected', p) }));
  index.orgs.forEach((o) => {
    if (o.x !== null && o.x !== undefined) {
      out.push({ kind: 'organisation', id: o.id, text: o.acronym ? `${o.acronym} · ${o.name}` : o.name, detail: levelName(o.level) });
    }
  });
  for (const [id, n] of index.nodes) out.push({ kind: 'theme', id, text: nameIn(n.names, locale) });
  index.keywords.forEach((k) => {
    if (k.x !== null && k.x !== undefined) out.push({ kind: 'keyword', id: k.term, text: k.term });
  });
  if (texts) texts.id.forEach((id, i) => out.push({ kind: 'text', id, text: texts.title[i] || id }));
  for (const e of out) e.folded = folded(e.text);
  return out;
}

/** The matches of *query* among *entries* (of *kinds* when given), the first *limit*. */
export function findMatches(entries, query, kinds = null, limit = FIND_LIMIT) {
  const q = folded(query.trim());
  if (!q) return [];
  const starts = [];
  const within = [];
  for (const e of entries) {
    if (kinds && !kinds.includes(e.kind)) continue;
    const at = e.folded.indexOf(q);
    if (at === 0 || (at > 0 && e.folded[at - 1] === ' ')) starts.push(e);
    else if (at > 0 && within.length < limit) within.push(e);
    if (starts.length >= limit) break;
  }
  return starts.concat(within).slice(0, limit);
}

/**
 * The combobox, in a new element: *entries()* answers what can be found (read when one types),
 * *onPick(sel)* is called with `{kind, id}`. Answers `{el, clear(), focus(), destroy()}`.
 */
export function createFind({ t, entries, onPick, kinds = null, label, placeholder }) {
  const id = uid('cx-atlas-find');
  let matches = [];
  let active = 0;
  const input = h('input', { id, class: 'cx-atlas-find__input', type: 'search', autocomplete: 'off', role: 'combobox',
    placeholder, 'aria-expanded': 'false', 'aria-controls': `${id}-list`, 'aria-autocomplete': 'list' });
  const list = h('ul', { id: `${id}-list`, class: 'cx-atlas-find__list', role: 'listbox', 'aria-label': label, hidden: true });
  const live = h('p', { class: 'cx-visually-hidden', 'aria-live': 'polite' });
  const el = h('div', { class: 'cx-atlas-find' }, h('label', { class: 'cx-visually-hidden', for: id, text: label }), input, list, live);

  const render = () => {
    while (list.firstChild) list.removeChild(list.firstChild);
    matches.forEach((e, k) => {
      list.appendChild(h('li', { id: `${id}-${k}`, role: 'option', 'aria-selected': String(k === active),
        class: k === active ? 'is-active' : '', onMousedown: (ev) => { ev.preventDefault(); pick(e); } },
      symbol(SHAPE_OF[SHAPE_OF_SEL[e.kind]] || 'circle'),
      h('span', { class: 'cx-atlas-find__text', text: e.text }),
      h('span', { class: 'cx-atlas-find__kind', text: e.detail || t(`atlas.kind.${e.kind}`) })));
    });
    const open = matches.length > 0;
    list.hidden = !open;
    input.setAttribute('aria-expanded', String(open));
    if (open) input.setAttribute('aria-activedescendant', `${id}-${active}`);
    else input.removeAttribute('aria-activedescendant');
    live.textContent = input.value ? t('atlas.find.count', { count: matches.length }) : '';
  };
  const pick = (e) => {
    if (!e) return;
    input.value = '';
    matches = [];
    render();
    onPick({ kind: e.kind, id: e.id });
  };
  const offs = [
    listen(input, 'input', () => {
      matches = findMatches(entries(), input.value, kinds);
      active = 0;
      render();
    }),
    listen(input, 'keydown', (e) => {
      if (e.key === 'ArrowDown') active = Math.min(matches.length - 1, active + 1);
      else if (e.key === 'ArrowUp') active = Math.max(0, active - 1);
      else if (e.key === 'Enter') pick(matches[active]);
      else if (e.key === 'Escape' && (input.value || matches.length)) {
        input.value = '';
        matches = [];
      } else return;
      e.preventDefault();
      e.stopPropagation();
      render();
    }),
    listen(input, 'blur', () => {
      matches = [];
      render();
    }),
  ];
  return {
    el,
    focus: () => input.focus(),
    clear() {
      input.value = '';
      matches = [];
      render();
    },
    destroy() {
      offs.forEach((off) => off());
      el.remove();
    },
  };
}
