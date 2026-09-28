/**
 * The theme editor's dialogs: one per action that needs a choice or a name
 * (rename, move to, merge with, split, create, set aside, attribution, the
 * levels), and the reports (a comparison, what a merge could not re-apply).
 *
 * Every dialog is a form: Enter submits, Escape cancels, the focus starts on
 * the first field and goes back where it was when the dialog closes.
 */

import { html, useMemo, useState } from '../../core/preact.js';
import { formatNumber, locale, t } from '../../core/i18n.js';
import { useUid } from '../../core/dom.js';
import { Button, Checkbox, Dialog, FormField, Icon, Input, Textarea } from '../../components/index.js';
import { fold, lang2, levelName, nodeName, pathOf, search } from './model.js';
import { entryLabel, languageName, nameLanguages } from './labels.js';

/** A dialog holding a form: `onSubmit` returns a promise of `true` (done) or an error text. */
function FormDialog({ open, title, description, submitLabel, danger = false, onClose, onSubmit,
  size = 'm', disabled = false, children }) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const form = useUid('cx-themes-form');
  const submit = async (event) => {
    event.preventDefault();
    if (busy || disabled) return;
    setBusy(true);
    setProblem('');
    const outcome = await onSubmit();
    setBusy(false);
    if (outcome === true) onClose('done');
    else if (outcome) setProblem(outcome);
  };
  return html`<${Dialog} open=${open} size=${size} title=${title} description=${description}
    onClose=${onClose}
    footer=${html`<${Button} variant="ghost" onClick=${() => onClose('close')}>${t('common.cancel')}<//>
      <${Button} variant=${danger ? 'danger' : 'primary'} type="submit" form=${form} loading=${busy}
        disabled=${disabled}>${submitLabel}<//>`}>
    <form id=${form} class="cx-themes-form" onSubmit=${submit} novalidate>
      ${children}
      ${problem ? html`<p class="cx-themes-form__problem" role="alert">
        <${Icon} name="warning" /><span>${problem}</span></p>` : null}
    </form>
  <//>`;
}

/** The label of a node for a list: its path of names, from the top. */
export function nodePath(index, id) {
  const lang = lang2(locale.value);
  return pathOf(index, id).map((nid) => nodeName(index.nodes.get(nid), lang)).join(' › ');
}

/** Name fields, one per language: `names` in, `onChange(names)` out. */
function NameFields({ names, languages, onChange }) {
  return html`<div class="cx-themes-form__names">
    ${languages.map((code) => html`<${FormField} key=${code}
      label=${t('themes.name.in', { language: languageName(code) })}>
      ${(field) => html`<${Input} ...${field} lang=${code} value=${names[code] || ''}
        autocomplete="off" spellcheck="true"
        onInput=${(e) => onChange({ ...names, [code]: e.currentTarget.value })} />`}
    <//>`)}
  </div>`;
}

function cleanNames(names, before = {}) {
  const out = {};
  for (const [code, value] of Object.entries(names)) {
    const v = String(value || '').trim();
    if (v) out[code] = v;
    else if (before[code]) out[code] = null;
  }
  return out;
}

/** Rename a node, in every language it may be named in. */
export function RenameDialog({ open, node, levelLabel, onClose, onSubmit }) {
  const [names, setNames] = useState(() => ({ ...(node ? node.names : {}) }));
  const languages = useMemo(() => nameLanguages(node && node.names), [node, locale.value]);
  const empty = !Object.values(names).some((v) => String(v || '').trim());
  return html`<${FormDialog} open=${open} onClose=${onClose}
    title=${t('themes.rename.title', { name: nodeName(node, lang2(locale.value)) })}
    description=${levelLabel ? t('themes.rename.lead', { level: levelLabel }) : undefined}
    submitLabel=${t('themes.rename.submit')} disabled=${empty}
    onSubmit=${() => onSubmit(cleanNames(names, node && node.names))}>
    <${NameFields} names=${names} languages=${languages} onChange=${setNames} />
  <//>`;
}

/** Rename a level of the tree. */
export function RenameLevelDialog({ open, tree, level, onClose, onSubmit }) {
  const current = (tree.levels[level - 1] || {}).names || {};
  const [names, setNames] = useState(() => ({ ...current }));
  const languages = useMemo(() => nameLanguages(current), [level, locale.value]);
  const empty = !Object.values(names).some((v) => String(v || '').trim());
  return html`<${FormDialog} open=${open} onClose=${onClose}
    title=${t('themes.level.rename.title', { name: levelName(tree, level, lang2(locale.value)), level })}
    description=${t('themes.level.rename.lead')} submitLabel=${t('themes.rename.submit')}
    disabled=${empty} onSubmit=${() => onSubmit(cleanNames(names, current))}>
    <${NameFields} names=${names} languages=${languages} onChange=${setNames} />
  <//>`;
}

/**
 * Pick one node among *candidates*: a filter field and a list, both driven by
 * the keyboard (arrows in the field move in the list, Enter picks).
 */
export function PickNodeDialog({ open, title, description, submitLabel, index, candidates,
  onClose, onSubmit, extra = null }) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(candidates[0] || null);
  const listId = useUid('cx-themes-pick');
  const lang = lang2(locale.value);
  const shown = useMemo(() => {
    const words = fold(query).split(/\s+/).filter(Boolean);
    return candidates.filter((id) => {
      if (!words.length) return true;
      const text = fold(nodePath(index, id));
      return words.every((w) => text.includes(w));
    });
  }, [query, candidates, index]);
  const current = shown.includes(active) ? active : shown[0] || null;
  const move = (delta) => {
    if (!shown.length) return;
    const i = Math.max(0, shown.indexOf(current));
    const next = shown[Math.max(0, Math.min(shown.length - 1, i + delta))];
    setActive(next);
    const el = document.getElementById(`${listId}-${next}`);
    if (el) el.scrollIntoView({ block: 'nearest' });
  };
  return html`<${FormDialog} open=${open} onClose=${onClose} title=${title} description=${description}
    submitLabel=${submitLabel} disabled=${!current} onSubmit=${() => onSubmit(current)}>
    <${FormField} label=${t('themes.pick.filter')}>
      ${(field) => html`<${Input} ...${field} type="search" value=${query} autocomplete="off"
        role="combobox" aria-expanded="true" aria-controls=${listId}
        aria-activedescendant=${current ? `${listId}-${current}` : undefined}
        onInput=${(e) => setQuery(e.currentTarget.value)}
        onKeyDown=${(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            move(1);
          } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            move(-1);
          } else if (e.key === 'PageDown') {
            e.preventDefault();
            move(8);
          } else if (e.key === 'PageUp') {
            e.preventDefault();
            move(-8);
          }
        }} />`}
    <//>
    <ul class="cx-themes-pick" id=${listId} role="listbox" aria-label=${title} hidden=${!shown.length}>
      ${shown.slice(0, 400).map((id) => {
        const node = index.nodes.get(id);
        return html`<li key=${id} id=${`${listId}-${id}`} role="option"
          aria-selected=${String(id === current)}
          class=${`cx-themes-pick__option ${id === current ? 'is-active' : ''}`}
          onClick=${() => setActive(id)}>
          <span class="cx-themes-chip" style=${{ '--cx-chip': `var(--cx-hue-${(index.hue.get(id) % 12) + 1})` }}></span>
          <span class="cx-themes-pick__name">${nodeName(node, lang)}</span>
          <span class="cx-themes-pick__path">${pathOf(index, id).length > 1 ? nodePath(index, node.parent) : levelName(index.tree, 1, lang)}</span>
          <span class="cx-themes-pick__count">${formatNumber(index.under.get(id))}</span>
        </li>`;
      })}
    </ul>
    ${!shown.length ? html`<p class="cx-themes-form__hint">${t('themes.pick.none')}</p>` : null}
    ${extra}
  <//>`;
}

/** Split a node: tick the keywords and child nodes that go to a new node beside it. */
export function SplitDialog({ open, index, id, onClose, onSubmit }) {
  const lang = lang2(locale.value);
  const node = index.nodes.get(id);
  const kids = index.children.get(id) || [];
  const own = index.keywordsOn.get(id) || [];
  const [picked, setPicked] = useState(() => new Set());
  const [names, setNames] = useState({});
  const [filter, setFilter] = useState('');
  const languages = useMemo(() => nameLanguages(node.names), [id, locale.value]);
  const total = kids.length + own.length;
  const words = fold(filter).split(/\s+/).filter(Boolean);
  const visible = own.filter((k) => !words.length || words.every((w) => fold(k).includes(w)));
  const toggle = (member) => {
    const next = new Set(picked);
    if (next.has(member)) next.delete(member);
    else next.add(member);
    setPicked(next);
  };
  const named = Object.values(names).some((v) => String(v || '').trim());
  const problem = !picked.size ? t('themes.split.none')
    : picked.size >= total ? t('themes.split.all') : !named ? t('themes.split.unnamed') : '';
  return html`<${FormDialog} open=${open} size="l" onClose=${onClose}
    title=${t('themes.split.title', { name: nodeName(node, lang) })}
    description=${t('themes.split.lead')} submitLabel=${t('themes.split.submit', { count: picked.size })}
    disabled=${Boolean(problem)}
    onSubmit=${() => onSubmit([...picked], cleanNames(names))}>
    <${NameFields} names=${names} languages=${languages.slice(0, 1)} onChange=${setNames} />
    <p class="cx-themes-form__hint" aria-live="polite">${problem || t('themes.split.count', { count: picked.size, total })}</p>
    ${kids.length ? html`<fieldset class="cx-themes-form__set">
      <legend>${t('themes.split.nodes', { level: levelName(index.tree, index.level.get(id) + 1, lang) })}</legend>
      <div class="cx-themes-form__checks">
        ${kids.map((kid) => html`<${Checkbox} key=${kid} checked=${picked.has(kid)}
          label=${`${nodeName(index.nodes.get(kid), lang)} (${formatNumber(index.under.get(kid))})`}
          onChange=${() => toggle(kid)} />`)}
      </div>
    </fieldset>` : null}
    ${own.length ? html`<fieldset class="cx-themes-form__set">
      <legend>${t('themes.split.keywords', { count: own.length })}</legend>
      ${own.length > 12 ? html`<${FormField} label=${t('themes.split.filter')}>
        ${(field) => html`<${Input} ...${field} type="search" value=${filter}
          onInput=${(e) => setFilter(e.currentTarget.value)} />`}
      <//>` : null}
      <div class="cx-themes-form__checks cx-themes-form__checks--scroll">
        ${visible.slice(0, 500).map((k) => html`<${Checkbox} key=${k} checked=${picked.has(k)}
          label=${`${k} (${formatNumber(index.people(k))})`} onChange=${() => toggle(k)} />`)}
      </div>
    </fieldset>` : null}
  <//>`;
}

/** Name a new node (under *parent*, or on the top level). */
export function CreateDialog({ open, index, parent, onClose, onSubmit }) {
  const lang = lang2(locale.value);
  const [names, setNames] = useState({});
  const level = parent ? index.level.get(parent) + 1 : 1;
  const languages = useMemo(() => nameLanguages(), [locale.value]);
  const named = Object.values(names).some((v) => String(v || '').trim());
  return html`<${FormDialog} open=${open} onClose=${onClose}
    title=${parent ? t('themes.create.title_in', { name: nodeName(index.nodes.get(parent), lang) })
      : t('themes.create.title_top', { level: levelName(index.tree, 1, lang) })}
    description=${t('themes.create.lead', { level: levelName(index.tree, level, lang) })}
    submitLabel=${t('themes.create.submit')} disabled=${!named}
    onSubmit=${() => onSubmit(cleanNames(names))}>
    <${NameFields} names=${names} languages=${languages.slice(0, 1)} onChange=${setNames} />
  <//>`;
}

const REASONS = ['general', 'field', 'broken', 'other'];

/** Set keywords aside, with a reason (kept with them, shown in the « Set aside » tray). */
export function SetAsideDialog({ open, terms, current = '', onClose, onSubmit }) {
  const [kind, setKind] = useState(current ? 'other' : 'general');
  const [text, setText] = useState(current);
  const group = useUid('cx-themes-reason');
  const reason = kind === 'other' ? text.trim() : t(`themes.aside.reason.${kind}`);
  return html`<${FormDialog} open=${open} onClose=${onClose}
    title=${t('themes.aside.title', { count: terms.length, term: terms[0] })}
    description=${t('themes.aside.lead')} submitLabel=${t('themes.aside.submit', { count: terms.length })}
    onSubmit=${() => onSubmit(reason)}>
    <fieldset class="cx-themes-form__set">
      <legend>${t('themes.aside.reason')}</legend>
      <div class="cx-themes-form__radios" role="radiogroup">
        ${REASONS.map((r) => html`<label class="cx-themes-radio" key=${r}>
          <input type="radio" name=${group} value=${r} checked=${kind === r}
            onChange=${() => setKind(r)} />
          <span>${t(`themes.aside.reason.${r}`)}</span>
        </label>`)}
      </div>
    </fieldset>
    ${kind === 'other' ? html`<${FormField} label=${t('themes.aside.own')}>
      ${(field) => html`<${Textarea} ...${field} rows=${2} value=${text}
        onInput=${(e) => setText(e.currentTarget.value)} />`}
    <//>` : null}
  <//>`;
}

/** How many levels, from the top, keywords count toward. */
export function AttributionDialog({ open, index, terms, onClose, onSubmit }) {
  const lang = lang2(locale.value);
  const tree = index.tree;
  const levels = terms.map((k) => index.level.get(tree.keywords[k]) || 1);
  const lowest = Math.min(...levels);
  const currents = new Set(terms.map((k) => (k in (tree.attribution || {}) ? String(tree.attribution[k]) : 'default')));
  const [choice, setChoice] = useState(currents.size === 1 ? [...currents][0] : 'default');
  const group = useUid('cx-themes-attr');
  const options = [
    { value: 'default', label: t('themes.attr.default'), help: t('themes.attr.default.help') },
    ...Array.from({ length: Math.max(0, lowest - 1) }, (_, i) => ({
      value: String(i + 1),
      label: t('themes.attr.level', { level: levelName(tree, i + 1, lang), count: i + 1 }),
      help: t('themes.attr.level.help', { level: levelName(tree, i + 1, lang) }),
    })),
    { value: '0', label: t('themes.attr.none'), help: t('themes.attr.none.help') },
  ];
  return html`<${FormDialog} open=${open} onClose=${onClose}
    title=${t('themes.attr.title', { count: terms.length, term: terms[0] })}
    description=${t('themes.attr.lead')} submitLabel=${t('themes.attr.submit')}
    onSubmit=${() => onSubmit(choice === 'default' ? null : Number(choice))}>
    <div class="cx-themes-form__radios cx-themes-form__radios--stack" role="radiogroup"
      aria-label=${t('themes.attr.title', { count: terms.length, term: terms[0] })}>
      ${options.map((o) => html`<label class="cx-themes-radio cx-themes-radio--wide" key=${o.value}>
        <input type="radio" name=${group} value=${o.value} checked=${choice === o.value}
          onChange=${() => setChoice(o.value)} />
        <span><span class="cx-themes-radio__label">${o.label}</span>
          <span class="cx-themes-radio__help">${o.help}</span></span>
      </label>`)}
    </div>
  <//>`;
}

/** Insert a level: where, and for a new top level, the name of its one node. */
export function InsertLevelDialog({ open, tree, onClose, onSubmit }) {
  const lang = lang2(locale.value);
  const depth = tree.depth;
  const [at, setAt] = useState(depth + 1);
  const group = useUid('cx-themes-insert');
  const positions = Array.from({ length: depth + 1 }, (_, i) => i + 1).map((pos) => ({
    value: pos,
    label: pos === 1 ? t('themes.level.insert.top', { name: levelName(tree, 1, lang) })
      : pos === depth + 1 ? t('themes.level.insert.bottom', { name: levelName(tree, depth, lang) })
        : t('themes.level.insert.between', { above: levelName(tree, pos - 1, lang), below: levelName(tree, pos, lang) }),
  }));
  return html`<${FormDialog} open=${open} onClose=${onClose} title=${t('themes.level.insert.title')}
    description=${t('themes.level.insert.lead')} submitLabel=${t('themes.level.insert.submit')}
    disabled=${depth >= 4} onSubmit=${() => onSubmit(at)}>
    ${depth >= 4 ? html`<p class="cx-themes-form__hint">${t('themes.level.insert.full')}</p>` : html`
    <div class="cx-themes-form__radios cx-themes-form__radios--stack" role="radiogroup"
      aria-label=${t('themes.level.insert.title')}>
      ${positions.map((p) => html`<label class="cx-themes-radio cx-themes-radio--wide" key=${p.value}>
        <input type="radio" name=${group} checked=${at === p.value} onChange=${() => setAt(p.value)} />
        <span class="cx-themes-radio__label">${p.label}</span>
      </label>`)}
    </div>
    <p class="cx-themes-form__hint">${at === 1 ? t('themes.level.insert.top.help')
      : at === depth + 1 ? t('themes.level.insert.bottom.help') : t('themes.level.insert.between.help')}</p>`}
  <//>`;
}

/** Remove a level: its nodes dissolve into their parents. */
export function RemoveLevelDialog({ open, tree, onClose, onSubmit }) {
  const lang = lang2(locale.value);
  const depth = tree.depth;
  const [at, setAt] = useState(depth);
  const group = useUid('cx-themes-remove');
  return html`<${FormDialog} open=${open} danger onClose=${onClose}
    title=${t('themes.level.remove.title')} description=${t('themes.level.remove.lead')}
    submitLabel=${t('themes.level.remove.submit', { name: levelName(tree, at, lang) })}
    disabled=${depth <= 1} onSubmit=${() => onSubmit(at)}>
    ${depth <= 1 ? html`<p class="cx-themes-form__hint">${t('themes.level.remove.last')}</p>` : html`
    <div class="cx-themes-form__radios cx-themes-form__radios--stack" role="radiogroup"
      aria-label=${t('themes.level.remove.title')}>
      ${tree.levels.map((_, i) => html`<label class="cx-themes-radio cx-themes-radio--wide" key=${i}>
        <input type="radio" name=${group} checked=${at === i + 1} onChange=${() => setAt(i + 1)} />
        <span class="cx-themes-radio__label">${t('themes.level.numbered', { level: i + 1, name: levelName(tree, i + 1, lang) })}</span>
      </label>`)}
    </div>
    <p class="cx-themes-form__hint">${at === 1 ? t('themes.level.remove.top.help') : t('themes.level.remove.help')}</p>`}
  <//>`;
}

const GROUPS = [
  ['nodes', ['node_added', 'node_removed', 'node_renamed', 'node_moved', 'node_reordered', 'node_changed']],
  ['keywords', ['added', 'removed', 'moved', 'set_aside_changed', 'attribution', 'review']],
  ['levels', ['depth', 'level_renamed']],
];

/**
 * What changed between two trees (`POST /api/themes/compare`), grouped:
 * nodes, keywords, levels. *names* names the nodes of both trees.
 */
export function ChangeList({ result, names }) {
  const lang = lang2(locale.value);
  const name = (id) => {
    if (id === '(set aside)') return t('themes.compare.aside');
    const n = names.get(id);
    return n ? nodeName(n, lang) : id;
  };
  if (!result) return null;
  if (!result.total) return html`<p class="cx-themes-form__hint">${t('themes.compare.same')}</p>`;
  const counts = result.counts || {};
  return html`<div class="cx-themes-changes">
    <ul class="cx-themes-changes__counts">
      ${Object.entries(counts).map(([kind, n]) => html`<li key=${kind}>
        <span class="cx-themes-changes__n">${formatNumber(n)}</span>
        <span>${t(`themes.change.${kind}`, { count: n })}</span></li>`)}
    </ul>
    ${GROUPS.map(([group, kinds]) => {
      const items = result.changes.filter((c) => kinds.includes(c.kind));
      if (!items.length) return null;
      return html`<section key=${group} class="cx-themes-changes__group">
        <h3 class="cx-themes-changes__title">${t(`themes.compare.group.${group}`)}</h3>
        <ul class="cx-themes-changes__list">
          ${items.slice(0, 300).map((c, i) => html`<li key=${i}>${changeText(c, name, lang)}</li>`)}
        </ul>
        ${items.length > 300 ? html`<p class="cx-themes-form__hint">${t('themes.compare.more', { count: items.length - 300 })}</p>` : null}
      </section>`;
    })}
  </div>`;
}

function changeText(c, name, lang) {
  const nm = (names) => nodeName({ id: c.node, names: names || {} }, lang);
  switch (c.kind) {
    case 'added': return t('themes.change.text.added', { keyword: c.keyword, place: name(c.after) });
    case 'removed': return t('themes.change.text.removed', { keyword: c.keyword, place: name(c.before) });
    case 'moved': return t('themes.change.text.moved', { keyword: c.keyword, from: name(c.before), to: name(c.after) });
    case 'node_renamed': return t('themes.change.text.renamed', { from: nm(c.before), to: nm(c.after) });
    case 'node_added': return t('themes.change.text.node_added', { name: name(c.node) });
    case 'node_removed': return t('themes.change.text.node_removed', { name: name(c.node) });
    case 'node_moved': return t('themes.change.text.node_moved', { name: name(c.node), to: c.after ? name(c.after) : t('themes.compare.top') });
    case 'attribution': return t('themes.change.text.attribution', { keyword: c.keyword });
    case 'review': return t('themes.change.text.review', { keyword: c.keyword });
    case 'set_aside_changed': return t('themes.change.text.aside', { keyword: c.keyword });
    case 'level_renamed': return t('themes.change.text.level', { level: c.level });
    case 'depth': return t('themes.change.text.depth', { before: c.before, after: c.after });
    default: return t('themes.change.text.other', { name: name(c.node || '') });
  }
}

/** A dialog showing a comparison, with optional actions. */
export function CompareDialog({ open, title, description, result, names, onClose, footer }) {
  return html`<${Dialog} open=${open} size="l" title=${title} description=${description}
    onClose=${onClose} footer=${footer || html`<${Button} onClick=${() => onClose('close')}>
      ${t('common.close')}<//>`}>
    ${result ? html`<${ChangeList} result=${result} names=${names} />`
      : html`<p class="cx-themes-form__hint">${t('common.loading')}</p>`}
  <//>`;
}

/** What « reload and merge » could not apply again, and why. */
export function MergeReportDialog({ open, report, onClose }) {
  const refused = (report && report.refused) || [];
  return html`<${Dialog} open=${open} size="m" title=${t('themes.merge.report.title')}
    description=${t('themes.merge.report.lead', { applied: report ? report.applied : 0, total: report ? report.total : 0 })}
    onClose=${onClose} footer=${html`<${Button} variant="primary" onClick=${() => onClose('close')}>
      ${t('common.close')}<//>`}>
    ${refused.length ? html`<ul class="cx-themes-changes__list">
      ${refused.map((r, i) => html`<li key=${i}><strong>${entryLabel(r.entry)}</strong>
        <span class="cx-themes-form__hint">${r.reason}</span></li>`)}
    </ul>` : html`<p>${t('themes.merge.report.all')}</p>`}
  <//>`;
}
