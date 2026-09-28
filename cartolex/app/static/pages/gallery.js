// SPDX-License-Identifier: MIT
/**
 * The component gallery (`/gallery`): every component in every state, with
 * switchers for the theme and the interface language. It uses fixture data
 * only (gallery-data.js) and is the page the browser checks run axe, the
 * keyboard scripts and the screenshots on.
 *
 * States a mouse or the keyboard produce (hover, focus) are shown with the
 * classes `is-hover` and `is-focus`, which the style sheet draws exactly as
 * `:hover` and `:focus-visible`.
 */
import { computed, html, signal, useMemo, useState } from '../core/preact.js';
import { autonym, formatNumber, locale, t } from '../core/i18n.js';
import { definePage, usePageTitle } from '../core/page.js';
import { THEMES } from '../core/stores/prefs.js';
import {
  ActivityDrawer, ActivityIndicator, AiHandoffDialog, Button, Card, Checkbox, ConfirmDialog,
  ContextMenuArea, Dialog, Drawer, EmptyState, ErrorCard, FormField, Help, ICON_NAMES, Icon,
  IconButton, Input, Menu, MenuButton, ProgressBar, STATES, Select, StageTracker, StatusDot,
  StatusPill, Stepper, Table, Tabs, Textarea, Toaster, Tooltip, createToaster,
} from '../components/index.js';
import {
  ERRORS, HANDOFF_ANSWER, HANDOFF_BUNDLE, HANDOFF_PROMPT, JOBS, STAGES, checkAnswer, tableRows,
} from './gallery-data.js';

const SECTIONS = ['tokens', 'button', 'card', 'status', 'tracker', 'stepper', 'tabs', 'table',
  'menu', 'dialog', 'toast', 'form', 'empty', 'error', 'progress', 'tooltip', 'handoff',
  'activity', 'icons'];

function Section({ id, children }) {
  return html`<section class="cx-gallery__section" id=${`g-${id}`} aria-labelledby=${`g-${id}-title`}
    data-gallery=${id}>
    <h2 class="cx-gallery__title" id=${`g-${id}-title`}>${t(`gallery.section.${id}`)}</h2>
    <p class="cx-gallery__lead">${t(`gallery.section.${id}.lead`)}</p>
    ${children}
  </section>`;
}

function Example({ label, wide = false, children }) {
  return html`<figure class=${`cx-gallery__example ${wide ? 'cx-gallery__example--wide' : ''}`}>
    <div class="cx-gallery__stage">${children}</div>
    <figcaption class="cx-gallery__caption">${label}</figcaption>
  </figure>`;
}

function Switchers({ app }) {
  const theme = app.stores.prefs.theme.value;
  return html`<div class="cx-gallery__switchers">
    <fieldset class="cx-segmented">
      <legend class="cx-segmented__legend">${t('display.theme')}</legend>
      ${THEMES.map((th) => html`<label class="cx-segmented__option" key=${th}>
        <input type="radio" name="g-theme" value=${th} checked=${theme === th}
          onChange=${() => app.setTheme(th)} />
        <span>${t(`display.theme.${th}`)}</span>
      </label>`)}
    </fieldset>
    <fieldset class="cx-segmented">
      <legend class="cx-segmented__legend">${t('display.language')}</legend>
      ${app.manifest.locales.available.map((code) => html`<label class="cx-segmented__option" key=${code}>
        <input type="radio" name="g-locale" value=${code} checked=${locale.value === code}
          onChange=${() => app.switchLocale(code)} />
        <span lang=${code}>${autonym(code)}</span>
      </label>`)}
    </fieldset>
  </div>`;
}

function Tokens() {
  const colours = ['bg', 'surface', 'surface-alt', 'surface-raised', 'selected', 'text', 'text-muted',
    'rule', 'border', 'border-strong', 'accent', 'focus', 'danger', 'warning'];
  return html`<${Section} id="tokens">
    <div class="cx-gallery__swatches">
      ${colours.map((c) => html`<div class="cx-swatch" key=${c}>
        <span class=${`cx-swatch__chip cx-swatch__chip--${c}`}></span>
        <code class="cx-swatch__name">--cx-${c}</code>
      </div>`)}
    </div>
    <div class="cx-gallery__type">
      ${['3xl', '2xl', 'xl', 'l', 'm', 's', 'xs'].map((size) => html`<p key=${size}
        class=${`cx-type-sample cx-type-sample--${size}`}>
        <code>--cx-text-${size}</code> ${t('gallery.tokens.sample')}</p>`)}
      <p class="cx-type-sample cx-type-sample--m cx-tabular">
        <code>tabular-nums</code> ${formatNumber(1234567.89)} · ${formatNumber(42)} · ${formatNumber(1000)}</p>
    </div>
    <div class="cx-gallery__row">
      ${[1, 2, 3, 4, 5, 6, 7, 8].map((n) => html`<span key=${n} class="cx-space-sample">
        <span class=${`cx-space-sample__bar cx-space-sample__bar--${n}`}></span>
        <code>${`--cx-space-${n}`}</code></span>`)}
    </div>
  <//>`;
}

function Buttons() {
  const variants = ['primary', 'secondary', 'ghost', 'danger'];
  const [count, setCount] = useState(0);
  return html`<${Section} id="button">
    <div class="cx-gallery__matrix" role="group" aria-label=${t('gallery.section.button')}>
      ${variants.map((v) => html`<div class="cx-gallery__row" key=${v}>
        <${Button} variant=${v} onClick=${() => setCount(count + 1)}>${t(`gallery.button.${v}`)}<//>
        <${Button} variant=${v} class="is-hover">${t('gallery.state.hover')}<//>
        <${Button} variant=${v} class="is-focus">${t('gallery.state.focus')}<//>
        <${Button} variant=${v} disabled>${t('gallery.state.disabled')}<//>
        <${Button} variant=${v} loading>${t('gallery.state.loading')}<//>
        <${Button} variant=${v} icon="download">${t('gallery.button.with_icon')}<//>
      </div>`)}
    </div>
    <p class="cx-gallery__note" role="status">${t('gallery.button.pressed', { count })}</p>
    <${Example} label=${t('gallery.state.long_text')}>
      <div class="cx-gallery__narrow"><${Button} variant="secondary">${t('gallery.long.button')}<//></div>
    <//>
    <${Example} label=${t('gallery.button.icon_buttons')}>
      <div class="cx-gallery__row">
        <${IconButton} icon="copy" label=${t('gallery.button.copy')} />
        <${IconButton} icon="download" label=${t('gallery.button.download')} variant="secondary" />
        <${IconButton} icon="sun" label=${t('gallery.button.toggle')} pressed=${true} variant="secondary" />
        <${IconButton} icon="close" label=${t('common.close')} class="is-hover" />
        <${IconButton} icon="close" label=${t('common.close')} class="is-focus" />
        <${IconButton} icon="close" label=${t('common.close')} disabled />
        <${IconButton} icon="close" label=${t('common.close')} loading />
      </div>
    <//>
  <//>`;
}

function Cards() {
  return html`<${Section} id="card">
    <div class="cx-gallery__grid">
      <${Card} title=${t('gallery.card.title')}>
        <p>${t('gallery.card.body', { kept: 1240, check: 86 })}</p>
      <//>
      <${Card} title=${t('gallery.card.title')}
        actions=${html`<${IconButton} icon="more" label=${t('gallery.card.more')} />`}
        footer=${html`<${Button} size="s">${t('gallery.card.action')}<//>`}>
        <p>${t('gallery.card.body', { kept: 1240, check: 86 })}</p>
      <//>
      <${Card} title=${t('gallery.card.loading')} loading />
      <${Card} title=${t('gallery.long.title')} tone="quiet">
        <p>${t('gallery.long.text')}</p>
      <//>
    </div>
  <//>`;
}

function Statuses() {
  return html`<${Section} id="status">
    <div class="cx-gallery__grid cx-gallery__grid--small">
      ${STATES.map((s) => html`<div class="cx-gallery__cell" key=${s}>
        <${StatusDot} state=${s} />
        <${StatusDot} state=${s} label=${true} />
        <${StatusPill} state=${s} />
      </div>`)}
    </div>
    <${Example} label=${t('gallery.status.detail')}>
      <${StatusPill} state="needs update" detail=${t('reason.input', { subject: 'decisions/keywords.csv' })} />
    <//>
  <//>`;
}

function Tracker() {
  return html`<${Section} id="tracker">
    <div class="cx-gallery__grid">
      <${Example} label=${t('gallery.tracker.full')} wide>
        <${StageTracker} stages=${STAGES} />
      <//>
      <${Example} label=${t('gallery.tracker.compact')}>
        <${StageTracker} stages=${STAGES.slice(0, 5)} compact />
      <//>
    </div>
  <//>`;
}

function Steppers() {
  const [current, setCurrent] = useState('people');
  const order = ['source', 'people', 'check', 'import'];
  const at = order.indexOf(current);
  return html`<${Section} id="stepper">
    <${Example} label=${t('gallery.stepper.clickable')} wide>
      <${Stepper} label=${t('gallery.stepper.label')} onSelect=${setCurrent}
        steps=${order.map((id, i) => ({ id, label: t(`gallery.stepper.${id}`),
          state: i < at ? 'done' : i === at ? 'current' : 'todo' }))} />
    <//>
    <${Example} label=${t('gallery.stepper.error')} wide>
      <${Stepper} label=${t('gallery.stepper.label')}
        steps=${[{ id: 'source', label: t('gallery.stepper.source'), state: 'done' },
          { id: 'people', label: t('gallery.stepper.people'), state: 'error' },
          { id: 'check', label: t('gallery.stepper.check'), state: 'todo' },
          { id: 'import', label: t('gallery.long.step'), state: 'todo' }]} />
    <//>
  <//>`;
}

function TabsDemo() {
  const [tab, setTab] = useState('kept');
  const [manualTab, setManualTab] = useState('kept');
  const tabs = [
    { id: 'kept', label: t('gallery.tabs.kept'), count: formatNumber(1240) },
    { id: 'check', label: t('gallery.tabs.check'), count: formatNumber(86) },
    { id: 'aside', label: t('gallery.tabs.aside'), count: formatNumber(3120) },
    { id: 'off', label: t('gallery.state.disabled'), disabled: true },
  ];
  return html`<${Section} id="tabs">
    <${Example} label=${t('gallery.tabs.automatic')} wide>
      <${Tabs} tabs=${tabs} selected=${tab} onSelect=${setTab} label=${t('gallery.tabs.label')}
        panel=${(id) => html`<p>${t('gallery.tabs.panel', { name: tabs.find((x) => x.id === id).label })}</p>`} />
    <//>
    <${Example} label=${t('gallery.tabs.manual')} wide>
      <${Tabs} tabs=${tabs} selected=${manualTab} onSelect=${setManualTab} manual
        label=${t('gallery.tabs.label_manual')}
        panel=${(id) => html`<p>${t('gallery.tabs.panel', { name: tabs.find((x) => x.id === id).label })}</p>`} />
    <//>
  <//>`;
}

function TableDemo({ ctx }) {
  const rows = useMemo(() => tableRows(), []);
  const [selection, setSelection] = useState(new Set());
  const [opened, setOpened] = useState('');
  const [data, setData] = useState(rows);
  const columns = [
    { id: 'term', label: t('gallery.table.term'), width: 'minmax(14rem, 3fr)', sortable: true },
    { id: 'lang', label: t('gallery.table.lang'), width: '6rem', sortable: true },
    { id: 'people', label: t('gallery.table.people'), width: '8rem', sortable: true, numeric: true },
    { id: 'texts', label: t('gallery.table.texts'), width: '8rem', sortable: true, numeric: true },
    { id: 'state', label: t('gallery.table.state'), width: 'minmax(10rem, 1.5fr)',
      render: (row) => html`<${StatusDot} state=${row.state} label=${true} size="s" />` },
  ];
  const rowMenu = (keys) => [
    { id: 'keep', label: t('gallery.table.keep', { count: keys.length }), icon: 'check' },
    { id: 'exclude', label: t('gallery.table.exclude', { count: keys.length }), icon: 'cross', danger: true },
    { kind: 'separator', id: 'sep' },
    { id: 'copy', label: t('gallery.table.copy'), icon: 'copy' },
  ];
  const small = useMemo(() => rows.slice(0, 3), [rows]);
  return html`<${Section} id="table">
    <div class="cx-gallery__row">
      <${Button} size="s" onClick=${() => setData([{ ...rows[0], id: `new-${Date.now()}`,
        term: t('gallery.table.new_term') }, ...data])}>${t('gallery.table.insert')}<//>
      <${Button} size="s" onClick=${() => setData(data.map((r, i) => (i % 2 ? r : { ...r, people: r.people + 1 })))}>
        ${t('gallery.table.update')}<//>
      <span class="cx-gallery__note" role="status">${opened ? t('gallery.table.opened', { term: opened }) : ''}</span>
    </div>
    <${Table} label=${t('gallery.table.label')} columns=${columns} rows=${data}
      selection=${selection} onSelectionChange=${setSelection} size="l"
      onActivate=${(row) => setOpened(row.term)} rowMenu=${rowMenu}
      onRowMenu=${(item, keys) => ctx.app.toaster.show({ kind: 'info',
        title: t('gallery.table.menu_done', { action: item.label, count: keys.length }) })} />
    <div class="cx-gallery__grid">
      <${Example} label=${t('gallery.state.loading')}>
        <${Table} label=${t('gallery.table.label_loading')} columns=${columns} rows=${[]} loading size="s" />
      <//>
      <${Example} label=${t('gallery.state.empty')}>
        <${Table} label=${t('gallery.table.label_empty')} columns=${columns} rows=${[]} size="s"
          empty=${html`<${EmptyState} title=${t('gallery.table.empty_title')} icon="search"
            action=${{ label: t('gallery.table.empty_action'), onClick: () => setData(rows) }}>
            ${t('gallery.table.empty_text')}<//>`} />
      <//>
      <${Example} label=${t('gallery.state.error')}>
        <${Table} label=${t('gallery.table.label_error')} columns=${columns} rows=${small}
          error=${ERRORS.network} />
      <//>
    </div>
  <//>`;
}

function Menus({ ctx }) {
  const [sortBy, setSortBy] = useState('name');
  const [wrap, setWrap] = useState(true);
  const [last, setLast] = useState('');
  const items = [
    { id: 'open', label: t('gallery.menu.open'), icon: 'external' },
    { id: 'rename', label: t('gallery.menu.rename') },
    { id: 'disabled', label: t('gallery.state.disabled'), disabled: true },
    { kind: 'separator', id: 's1' },
    { kind: 'group', id: 'sort', label: t('gallery.menu.sort'), items: ['name', 'size'].map((k) => ({
      id: `sort:${k}`, kind: 'radio', label: t(`gallery.menu.sort.${k}`), checked: sortBy === k })) },
    { kind: 'separator', id: 's2' },
    { id: 'wrap', kind: 'checkbox', label: t('gallery.menu.wrap'), checked: wrap },
    { id: 'delete', label: t('gallery.menu.delete'), icon: 'cross', danger: true },
  ];
  const onSelect = (item) => {
    if (item.id.startsWith('sort:')) setSortBy(item.id.slice(5));
    else if (item.id === 'wrap') setWrap(!wrap);
    setLast(item.label);
  };
  return html`<${Section} id="menu">
    <div class="cx-gallery__row">
      <${MenuButton} label=${t('gallery.menu.button')} items=${items} onSelect=${onSelect} />
      <${MenuButton} label=${t('gallery.menu.more')} icon="more" iconOnly variant="ghost" items=${items}
        onSelect=${onSelect} />
      <span class="cx-gallery__note" role="status">${last ? t('gallery.menu.chosen', { item: last }) : ''}</span>
    </div>
    <div class="cx-gallery__grid">
      <${Example} label=${t('gallery.menu.open_state')}>
        <${Menu} inline items=${items} label=${t('gallery.menu.static')} onSelect=${onSelect} />
      <//>
      <${Example} label=${t('gallery.menu.context')}>
        <${ContextMenuArea} label=${t('gallery.menu.context')} items=${items.slice(0, 3)}
          onSelect=${(item) => ctx.app.toaster.show({ kind: 'info', title: t('gallery.menu.chosen', { item: item.label }) })}>
          <div class="cx-gallery__target" tabindex="0">${t('gallery.menu.context_hint')}</div>
        <//>
      <//>
    </div>
  <//>`;
}

function Dialogs() {
  const [open, setOpen] = useState(null);
  const [name, setName] = useState('');
  const [answer, setAnswer] = useState('');
  const close = () => setOpen(null);
  return html`<${Section} id="dialog">
    <div class="cx-gallery__row">
      <${Button} onClick=${() => setOpen('dialog')}>${t('gallery.dialog.open')}<//>
      <${Button} onClick=${() => setOpen('confirm')}>${t('gallery.dialog.confirm')}<//>
      <${Button} onClick=${() => setOpen('drawer')}>${t('gallery.dialog.drawer')}<//>
      <span class="cx-gallery__note" role="status">${answer}</span>
    </div>
    <${Dialog} open=${open === 'dialog'} onClose=${close} title=${t('gallery.dialog.title')}
      description=${t('gallery.dialog.description')}
      footer=${html`<${Button} variant="ghost" onClick=${close}>${t('common.cancel')}<//>
        <${Button} variant="primary" onClick=${() => {
          setAnswer(t('gallery.dialog.saved', { name }));
          close();
        }}>${t('common.save')}<//>`}>
      <${FormField} label=${t('gallery.form.name')} help=${t('gallery.form.name_help')} required>
        ${(field) => html`<${Input} ...${field} value=${name} onInput=${(e) => setName(e.currentTarget.value)} />`}
      <//>
    <//>
    <${ConfirmDialog} open=${open === 'confirm'} title=${t('leave.title')} danger
      confirmLabel=${t('leave.confirm')} cancelLabel=${t('leave.stay')}
      onAnswer=${(yes) => {
        setAnswer(t(yes ? 'gallery.dialog.left' : 'gallery.dialog.stayed'));
        close();
      }}>
      <p>${t('leave.text')}</p>
    <//>
    <${Drawer} open=${open === 'drawer'} onClose=${close} title=${t('gallery.dialog.drawer_title')}>
      <p>${t('gallery.long.text')}</p>
      <${Button} onClick=${close}>${t('common.close')}<//>
    <//>
  <//>`;
}

function Toasts({ ctx }) {
  const preview = useMemo(() => {
    const store = createToaster();
    ['info', 'success', 'warning', 'error'].forEach((kind) => store.show({
      kind, id: `preview-${kind}`, timeout: 0, title: t(`gallery.toast.${kind}`),
      message: kind === 'error' ? t('gallery.toast.error_message') : undefined,
    }));
    return store;
  }, [locale.value]);
  const show = (kind) => ctx.app.toaster.show({
    kind, title: t(`gallery.toast.${kind}`),
    message: kind === 'error' ? t('gallery.toast.error_message') : undefined,
    action: kind === 'success' ? { label: t('gallery.toast.undo'), onClick: () => {} } : undefined,
  });
  return html`<${Section} id="toast">
    <div class="cx-gallery__row">
      ${['info', 'success', 'warning', 'error'].map((kind) => html`<${Button} key=${kind}
        onClick=${() => show(kind)}>${t('gallery.toast.show', { kind: t(`toast.kind.${kind}`) })}<//>`)}
    </div>
    <${Example} label=${t('gallery.toast.preview')} wide>
      <${Toaster} toaster=${preview} preview />
    <//>
  <//>`;
}

function Forms() {
  const [text, setText] = useState('');
  const [choice, setChoice] = useState('en');
  const [agree, setAgree] = useState(true);
  return html`<${Section} id="form">
    <div class="cx-gallery__grid">
      <${FormField} label=${t('gallery.form.name')}>
        ${(field) => html`<${Input} ...${field} value=${text} onInput=${(e) => setText(e.currentTarget.value)} />`}
      <//>
      <${FormField} label=${t('gallery.form.title')} help=${t('gallery.form.title_help')} required>
        ${(field) => html`<${Input} ...${field} value="" />`}
      <//>
      <${FormField} label=${t('gallery.form.year')} error=${t('gallery.form.year_error')}>
        ${(field) => html`<${Input} ...${field} type="text" inputmode="numeric" value="20x6" />`}
      <//>
      <${FormField} label=${t('gallery.state.disabled')}>
        ${(field) => html`<${Input} ...${field} value=${t('gallery.form.locked')} disabled />`}
      <//>
      <${FormField} label=${t('gallery.form.language')}>
        ${(field) => html`<${Select} ...${field} value=${choice}
          onChange=${(e) => setChoice(e.currentTarget.value)}
          options=${['en', 'fr', 'pt-BR'].map((c) => ({ value: c, label: autonym(c) }))} />`}
      <//>
      <${FormField} label=${t('gallery.long.label')} help=${t('gallery.long.text')}>
        ${(field) => html`<${Textarea} ...${field} rows=${3} value="" />`}
      <//>
      <div class="cx-field">
        <${Checkbox} label=${t('gallery.form.agree')} checked=${agree}
          onChange=${(e) => setAgree(e.currentTarget.checked)} />
        <${Checkbox} label=${t('gallery.state.disabled')} disabled />
      </div>
      <div class="cx-field">
        <span class="cx-field__label">${t('gallery.form.focus_state')}</span>
        <input class="cx-input is-focus" aria-label=${t('gallery.form.focus_state')} value="" />
      </div>
    </div>
  <//>`;
}

function Empties() {
  return html`<${Section} id="empty">
    <div class="cx-gallery__grid">
      <${EmptyState} icon="file" title=${t('gallery.empty.title')}
        action=${{ label: t('gallery.empty.action'), icon: 'upload', onClick: () => {} }}>
        ${t('gallery.empty.text')}
      <//>
      <${EmptyState} icon="search" title=${t('gallery.empty.search_title')}
        action=${{ label: t('gallery.empty.search_action'), href: '/gallery' }}>
        ${t('gallery.empty.search_text')}
      <//>
    </div>
  <//>`;
}

function Errors() {
  const [dismissed, setDismissed] = useState(false);
  return html`<${Section} id="error">
    <div class="cx-gallery__grid">
      <${ErrorCard} error=${ERRORS.server} onAction=${() => {}} />
      <${ErrorCard} error=${ERRORS.network} onRetry=${() => {}} />
      <${ErrorCard} error=${ERRORS.unexpected} detailsOpen onAction=${() => {}} />
      ${dismissed ? html`<${Button} onClick=${() => setDismissed(false)}>${t('gallery.error.restore')}<//>`
        : html`<${ErrorCard} error=${ERRORS.server} compact onAction=${() => {}}
          onDismiss=${() => setDismissed(true)} />`}
    </div>
  <//>`;
}

function Progress() {
  const [value, setValue] = useState(0.45);
  return html`<${Section} id="progress">
    <div class="cx-gallery__stack">
      <${ProgressBar} value=${0} label=${t('gallery.progress.label', { n: 1 })} showValue />
      <${ProgressBar} value=${value} label=${t('gallery.progress.label', { n: 2 })} showValue
        resetKey="demo" />
      <${ProgressBar} value=${1} label=${t('gallery.progress.label', { n: 3 })} showValue />
      <${ProgressBar} label=${t('gallery.progress.label', { n: 4 })} showValue />
    </div>
    <div class="cx-gallery__row">
      <${Button} size="s" onClick=${() => setValue(Math.min(1, value + 0.1))}>${t('gallery.progress.more')}<//>
      <${Button} size="s" onClick=${() => setValue(Math.max(0, value - 0.3))}>${t('gallery.progress.back')}<//>
      <span class="cx-gallery__note">${t('gallery.progress.note')}</span>
    </div>
  <//>`;
}

function Tooltips() {
  return html`<${Section} id="tooltip">
    <div class="cx-gallery__row cx-gallery__row--tall">
      <${Tooltip} text=${t('gallery.tooltip.text')}>
        <button type="button" class="cx-button cx-button--secondary cx-button--m">
          <span class="cx-button__label">${t('gallery.tooltip.hover')}</span></button>
      <//>
      <${Tooltip} text=${t('gallery.tooltip.text')} open>
        <button type="button" class="cx-button cx-button--secondary cx-button--m">
          <span class="cx-button__label">${t('gallery.tooltip.open')}</span></button>
      <//>
      <span class="cx-gallery__inline">${t('gallery.tooltip.help_label')}
        <${Help} topic=${t('gallery.tooltip.help_topic')}><p>${t('gallery.tooltip.help_text')}</p><//>
      </span>
      <span class="cx-gallery__inline">${t('gallery.tooltip.help_open')}
        <${Help} topic=${t('gallery.tooltip.help_topic_open')} open><p>${t('gallery.tooltip.help_text')}</p><//>
      </span>
    </div>
  <//>`;
}

function Handoff({ ctx }) {
  const [open, setOpen] = useState(null);
  const file = { name: 'handoff-keywords.json', content: JSON.stringify(HANDOFF_BUNDLE, null, 1) };
  const onImport = async () => {
    ctx.app.toaster.show({ kind: 'success', title: t('gallery.handoff.imported') });
  };
  const checked = checkAnswer(HANDOFF_ANSWER);
  return html`<${Section} id="handoff">
    <div class="cx-gallery__row">
      <${Button} icon="download" onClick=${() => setOpen('export')}>${t('gallery.handoff.open')}<//>
      <${Button} onClick=${() => setOpen('import')}>${t('gallery.handoff.open_import')}<//>
      <${Button} onClick=${() => setOpen('review')}>${t('gallery.handoff.open_review')}<//>
    </div>
    ${open ? html`<${AiHandoffDialog} open=${true} key=${open} onClose=${() => setOpen(null)}
      bundle=${HANDOFF_BUNDLE} file=${file} prompt=${HANDOFF_PROMPT}
      initialStep=${open} initialAnswer=${open === 'export' ? '' : HANDOFF_ANSWER}
      initialChecked=${open === 'review' ? checked : null}
      onCheck=${async (text) => checkAnswer(text)} onImport=${onImport} />` : null}
  <//>`;
}

function fakeJobs(list) {
  const jobs = signal(list);
  const dismissed = signal([]);
  const visible = computed(() => jobs.value.filter((j) => !dismissed.value.includes(j.id)));
  const active = computed(() => jobs.value.filter((j) => ['queued', 'running', 'cancelling'].includes(j.state)));
  const headline = computed(() => active.value[0] || visible.value.find((j) => j.state === 'failed') || null);
  return {
    jobs, visible, active, headline,
    watch: () => () => {},
    cancel: (id) => {
      jobs.value = jobs.value.map((j) => (j.id === id ? { ...j, state: 'cancelling' } : j));
    },
    dismiss: (id) => {
      dismissed.value = [...dismissed.value, id];
    },
  };
}

function Activity() {
  const stores = useMemo(() => ({
    running: fakeJobs(JOBS), failed: fakeJobs(JOBS.slice(1)), idle: fakeJobs([]),
  }), []);
  const [open, setOpen] = useState(null);
  return html`<${Section} id="activity">
    <div class="cx-gallery__row">
      ${['running', 'failed', 'idle'].map((k) => html`<${Example} key=${k} label=${t(`gallery.activity.${k}`)}>
        <${ActivityIndicator} jobs=${stores[k]} onOpen=${() => setOpen(k)} />
      <//>`)}
    </div>
    <${ActivityDrawer} open=${Boolean(open)} jobs=${open ? stores[open] : stores.idle}
      onClose=${() => setOpen(null)} />
  <//>`;
}

function Icons() {
  return html`<${Section} id="icons">
    <ul class="cx-gallery__icons">
      ${ICON_NAMES.map((name) => html`<li key=${name}><${Icon} name=${name} size=${20} /><code>${name}</code></li>`)}
    </ul>
  <//>`;
}

function Gallery({ ctx }) {
  const { app } = ctx;
  usePageTitle(t('gallery.title'));
  return html`<div class="cx-page cx-gallery">
    <h1 class="cx-page__title">${t('gallery.title')}</h1>
    <p class="cx-page__lead">${t('gallery.lead')}</p>
    <${Switchers} app=${app} />
    <nav class="cx-gallery__toc" aria-label=${t('gallery.toc')}>
      <ul>${SECTIONS.map((id) => html`<li key=${id}><a href=${`#g-${id}`}>${t(`gallery.section.${id}`)}</a></li>`)}</ul>
    </nav>
    <${Tokens} />
    <${Buttons} />
    <${Cards} />
    <${Statuses} />
    <${Tracker} />
    <${Steppers} />
    <${TabsDemo} />
    <${TableDemo} ctx=${ctx} />
    <${Menus} ctx=${ctx} />
    <${Dialogs} />
    <${Toasts} ctx=${ctx} />
    <${Forms} />
    <${Empties} />
    <${Errors} />
    <${Progress} />
    <${Tooltips} />
    <${Handoff} ctx=${ctx} />
    <${Activity} />
    <${Icons} />
  </div>`;
}

export const page = definePage(Gallery, { styles: ['/static/css/gallery.css'] });
