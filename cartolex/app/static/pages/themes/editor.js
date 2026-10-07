/**
 * The theme editor: three views of one tree (outline, treemap, map) kept in
 * sync, a side panel, and every action from a menu and the keyboard (drag and
 * drop is a shortcut). Nothing is ever lost: undo and redo, an autosaved
 * draft, a guard before leaving, « reload and merge » when the saved tree
 * changed, versions to compare and restore. Above them, the « Tune » panel of
 * the space and the grouping (`pages/tune/`). An address may name what to open:
 * `?node=<id>` or `?keyword=<term>` (a link from the map or the keywords).
 */

import { batch, html, useEffect, useMemo, useState } from '../../core/preact.js';
import { formatDate, locale, t } from '../../core/i18n.js';
import { usePage, usePageTitle } from '../../core/page.js';
import { runtime } from '../../core/runtime.js';
import { errorFromResponse, jobError } from '../../core/errors.js';
import { ACTIVE } from '../../core/stores/jobs.js';
import {
  Button, ConfirmDialog, EmptyState, ErrorCard, Icon, IconButton, MenuButton, ProgressBar, messageOf,
} from '../../components/index.js';
import { lang2, levelName } from './model.js';
import { entryLabel } from './labels.js';
import { createEditor } from './store.js';
import { CompareDialog, MergeReportDialog } from './dialogs.js';
import { OutlinePane } from './outline.js';
import { CentrePane } from './centre.js';
import { SidePanel } from './panel.js';
import { VersionsDrawer } from './versions.js';
import { ThemeCopilotDialog } from './copilot.js';
import { OperationDialog } from './operations.js';
import { createUi, installActions, refusal } from './actions.js';
import { createFit } from './fit.js';
import { createLevels } from './levels.js';
import { TunePanel } from '../tune/panel.js';

function Banner({ tone = 'info', icon, children, actions }) {
  return html`<div class=${`cx-themes-banner cx-themes-banner--${tone}`} role="status">
    <span class="cx-themes-banner__icon" aria-hidden="true"><${Icon} name=${icon || (tone === 'warning' ? 'warning' : 'info')} /></span>
    <div class="cx-themes-banner__text">
      ${tone === 'warning' ? html`<span class="cx-themes-banner__word">${t('toast.kind.warning')}</span> ` : null}
      ${children}
    </div>
    ${actions ? html`<div class="cx-themes-banner__actions">${actions}</div>` : null}
  </div>`;
}

/** The theme editor page's content. */
export function ThemesEditor() {
  const ctx = usePage();
  const { app } = ctx;
  usePageTitle(t('nav.themes'));
  const projectId = app.manifest.project && app.manifest.project.open ? app.manifest.project.id : null;
  const [announcement, setAnnouncement] = useState('');
  const editor = useMemo(() => createEditor({
    api: ctx.api, projectId, announce: (text) => setAnnouncement(text),
  }), []);
  const ui = useMemo(() => {
    const u = createUi(editor);
    u.fit = createFit({ api: ctx.api });
    u.levels = createLevels({ api: ctx.api });
    return u;
  }, []);
  const [atlas, setAtlas] = useState(null);
  const [atlasError, setAtlasError] = useState(null);
  const [dialog, setDialog] = useState(null);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [copilot, setCopilot] = useState(null); // {resume}
  const [leave, setLeave] = useState(null);
  const [applyJob, setApplyJob] = useState(null);
  const [applyError, setApplyError] = useState(null);
  const [report, setReport] = useState(null);
  const [kept, setKept] = useState(null); // an apply kept the tree over a pending proposal
  const lang = lang2(locale.value);
  const toast = (item) => app.toaster.show(item);

  // The saved tree changed on the server (an apply's rebase or keep): follow it when nothing is unsaved.
  const followSaved = () => editor.refreshInfo().then((themes) => {
    if (themes.ok && themes.etag && editor.base.value && themes.etag !== editor.base.value.version
      && !editor.dirty.value) editor.adopt(themes.data, themes.etag);
  });

  const loadAtlas = async () => {
    setAtlasError(null);
    const result = await ctx.api.get('/api/atlas');
    if (result.ok) setAtlas(result.data);
    else setAtlasError(result.error);
  };

  // Ready when the tree is on screen: deferred now, during the first render (an effect runs
  // after the router has already marked the page ready).
  const done = useMemo(() => ctx.deferReady(), []);
  useEffect(() => {
    editor.load().then(() => {
      const index = editor.editIndex.value;
      if (index && index.depth > 1) ui.expanded.value = new Set(index.tops);
      done(); // the tree is rendered (signals render synchronously)
      // `?copilot=1` (a build waiting for the copilot) opens its dialog once the tree is here.
      if (ctx.query && ctx.query.get('copilot') === '1' && !editor.readOnly.value) setCopilot({});
      openFromAddress();
      // The map's data comes next: after the tree, not competing with it.
      setTimeout(loadAtlas, 0);
    });
    ctx.guard({
      dirty: () => editor.dirty.value,
      confirm: () => new Promise((resolve) => setLeave({ resolve })),
    });
    const onKey = (event) => {
      if (document.querySelector('dialog[open]')) return;
      const target = event.target;
      const typing = target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable);
      const mod = event.ctrlKey || event.metaKey;
      if (mod && !event.altKey && (event.key === 'z' || event.key === 'Z') && !typing) {
        if (event.shiftKey) editor.redo();
        else editor.undo();
      } else if (mod && (event.key === 'y' || event.key === 'Y') && !typing) {
        editor.redo();
      } else if (mod && (event.key === 's' || event.key === 'S')) {
        save();
      } else if (event.key === '/' && !typing && !mod) {
        focusSearch();
      } else {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
    };
    // In the capture phase: the page's keys come before the widgets' own (a tree's type-ahead).
    document.addEventListener('keydown', onKey, true);
    // The draft is written when the tab is hidden or closed, whatever is pending.
    const onHide = () => editor.flush();
    window.addEventListener('pagehide', onHide);
    document.addEventListener('visibilitychange', onHide);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      window.removeEventListener('pagehide', onHide);
      document.removeEventListener('visibilitychange', onHide);
      editor.flush();
    };
  }, []);

  // The apply job: follow it with the jobs store; refresh the map when it ends.
  const jobs = app.stores.jobs.jobs.value;
  useEffect(() => {
    if (!applyJob) return;
    const job = jobs.find((j) => j.id === applyJob.id);
    if (!job || ACTIVE.has(job.state)) return;
    setApplyJob(null);
    if (job.state === 'succeeded') {
      loadAtlas();
      // The apply rebases the saved tree first when the vocabulary changed: follow it.
      followSaved();
      toast({ kind: 'success', title: t('themes.apply.done') });
    } else {
      const failed = job.result && job.result.failed;
      const cause = jobError(job);
      setApplyError(failed ? {
        code: failed.code, params: failed.params || {}, message: failed.message,
        next: { label: '', action: 'report' }, status: null, method: null, path: null, requestId: null,
        time: job.finished_at || new Date().toISOString(), technical: cause ? cause.technical : null,
      } : { ...(cause || errorFromResponse({ error: { code: 'job_failed' } })),
        next: { label: '', action: 'report' } });
    }
  }, [jobs, applyJob]);

  const { run, focusSearch } = installActions({ editor, ui, setDialog, toast });
  // `?node=<id>` or `?keyword=<term>`: the node or the keyword selected, its panel open.
  function openFromAddress() {
    const index = editor.index.value;
    const node = ctx.query && ctx.query.get('node');
    const keyword = ctx.query && ctx.query.get('keyword');
    if (!index || (!node && !keyword)) return;
    if (node && index.nodes.has(node)) {
      ui.openNode(node, { from: 'address' });
      return;
    }
    const tree = index.tree;
    const wanted = (keyword || node || '').trim().toLowerCase();
    const known = [...Object.keys(tree.keywords || {}), ...Object.keys(tree.set_aside || {})];
    const term = known.find((k) => k === keyword) || known.find((k) => k.toLowerCase() === wanted);
    if (term) ui.openKeyword(term);
    else toast({ kind: 'warning', title: t('themes.address.missing', { name: keyword || node }) });
  }
  ui.openCopilot = () => setCopilot({});

  // ── saving, applying, versions ──
  async function save({ quiet = false, action = null } = {}) {
    if (!editor.tree.value) return false;
    const result = await editor.save({ action });
    if (result.stale) {
      setDialog({ kind: 'stale' });
      return false;
    }
    if (!result.ok) {
      if (result.error) toast({ kind: 'error', title: t('themes.save.failed'), message: refusal(result.error) });
      return false;
    }
    const { saved } = result;
    if (!quiet || saved.removed.length) {
      toast({
        kind: 'success',
        title: saved.written ? t('themes.save.done') : t('themes.save.same'),
        message: saved.removed.length ? t('themes.save.removed', { count: saved.removed.length,
          names: saved.removed.join(', ') }) : undefined,
      });
    }
    return true;
  }
  ui.saveAndApply = async () => {
    if (editor.dirty.value || (editor.base.value && editor.base.value.source === 'draft')) {
      if (!(await save({ quiet: true }))) return;
    }
    setApplyError(null);
    const result = await ctx.api.post('/api/themes/apply', {});
    if (!result.ok) {
      setApplyError(result.error);
      return;
    }
    setApplyJob(result.data.job);
    app.stores.jobs.refresh();
    // A new grouping nobody answered is kept over, as a version of its own: say so, and follow it.
    setKept(result.data.kept || null);
    if (result.data.kept) followSaved();
  };
  const merge = async () => {
    setDialog(null);
    const outcome = await editor.reloadAndMerge();
    if (!outcome.ok) {
      toast({ kind: 'error', title: t('themes.merge.failed'), message: refusal(outcome.error) });
      return;
    }
    if (outcome.refused.length) setReport(outcome);
    else toast({ kind: 'success', title: t('themes.merge.done', { count: outcome.applied }) });
  };
  const needClean = () => {
    if (!editor.dirty.value) return true;
    toast({ kind: 'warning', title: t('themes.clean.first') });
    return false;
  };
  const openVersion = async (v) => {
    const result = await ctx.api.get(`/api/themes/versions/${encodeURIComponent(v.id)}`);
    if (!result.ok) {
      toast({ kind: 'error', title: t('themes.versions.failed'), message: refusal(result.error) });
      return;
    }
    setVersionsOpen(false);
    batch(() => {
      editor.preview.value = null;
      editor.viewing.value = { id: v.id, tree: result.data.tree, at: v.made_at, current: v.id === 'current' };
    });
  };
  const compareVersion = async (v, tree = null) => {
    const version = tree || (await ctx.api.get(`/api/themes/versions/${encodeURIComponent(v.id)}`));
    const before = tree || (version.ok ? version.data.tree : null);
    if (!before) return;
    setDialog({ kind: 'compare', title: t('themes.compare.version_title', { when: v.made_at ? formatDate(v.made_at, 'datetime') : v.id }),
      description: t('themes.compare.version_lead'), before, after: editor.base.value.tree, version: v });
    const result = await ctx.api.post('/api/themes/compare', { before, after: editor.base.value.tree });
    setDialog((d) => (d && d.kind === 'compare' ? { ...d, result: result.ok ? result.data : null } : d));
  };
  const restoreVersion = async (v) => {
    if (!needClean()) return;
    const result = await ctx.api.post(`/api/themes/versions/${encodeURIComponent(v.id)}/restore`, {},
      { ifMatch: editor.base.value.version });
    if (!result.ok) {
      toast({ kind: 'error', title: t('themes.versions.restore_failed'), message: refusal(result.error) });
      return;
    }
    setDialog(null);
    setVersionsOpen(false);
    const themes = await ctx.api.get('/api/themes');
    if (themes.ok) editor.adopt(themes.data, themes.etag);
    toast({ kind: 'success', title: t('themes.versions.restored', { when: v.made_at ? formatDate(v.made_at, 'datetime') : v.id }) });
  };
  const rebase = async () => {
    if (!needClean()) return;
    const result = await ctx.api.post('/api/themes/rebase', {}, { ifMatch: editor.base.value.version });
    if (!result.ok) {
      toast({ kind: 'error', title: t('themes.rebase.failed'), message: refusal(result.error) });
      return;
    }
    const themes = await ctx.api.get('/api/themes');
    if (themes.ok) editor.adopt(themes.data, themes.etag);
    toast({ kind: 'success', title: t('themes.rebase.done', { count: result.data.to_check }) });
    if (result.data.to_check) ui.leftTab.value = 'check';
  };
  const compareProposal = async () => {
    const draft = await ctx.api.get('/api/themes/draft');
    if (!draft.ok) {
      toast({ kind: 'error', title: t('themes.proposal.failed'), message: refusal(draft.error) });
      return;
    }
    setDialog({ kind: 'proposal', run: draft.data.run, before: editor.base.value.tree, after: draft.data.tree });
    const result = await ctx.api.post('/api/themes/compare', { before: editor.base.value.tree, after: draft.data.tree });
    setDialog((d) => (d && d.kind === 'proposal' ? { ...d, result: result.ok ? result.data : null } : d));
  };
  const decideProposal = async (decision, run) => {
    if (!needClean()) return;
    const result = await ctx.api.post('/api/themes/proposal', { decision, run }, { ifMatch: editor.base.value.version });
    if (!result.ok) {
      toast({ kind: 'error', title: t('themes.proposal.failed'), message: refusal(result.error) });
      return;
    }
    setDialog(null);
    const themes = await ctx.api.get('/api/themes');
    if (themes.ok) editor.adopt(themes.data, themes.etag);
    toast({ kind: 'success', title: decision === 'adopt' ? t('themes.proposal.adopted') : t('themes.proposal.kept') });
  };

  // ── AI proposals ──
  // The accepted changes in order: one that carries its tree (a restructuring) is put in place
  // as one step, and holds the changes before it; the others' operations follow it.
  const planOf = (proposal, accepted) => {
    let start = null;
    let ops = [];
    proposal.items.forEach((it, i) => {
      if (!accepted.has(i)) return;
      if (it.tree) {
        start = it.tree;
        ops = [];
      } else ops.push(...(it.ops || [it.op]));
    });
    return { start, ops };
  };
  // At most MAX_OPS operations per request (the API's bound).
  const MAX_OPS = 500;
  const inParts = (ops) => Array.from({ length: Math.ceil(ops.length / MAX_OPS) },
    (_, k) => ops.slice(k * MAX_OPS, (k + 1) * MAX_OPS));
  const previewProposal = async (proposal, accepted) => {
    const { start, ops } = planOf(proposal, accepted);
    let current = start || editor.tree.value;
    let refused = 0;
    for (const part of inParts(ops)) {
      const result = await ctx.api.post('/api/themes/ops', { tree: current, ops: part, lenient: true });
      if (!result.ok) {
        toast({ kind: 'error', title: t('themes.ai.failed'), message: refusal(result.error) });
        return;
      }
      current = result.data.tree;
      refused += result.data.steps.filter((s) => s.refused).length;
    }
    setCopilot(null);
    editor.preview.value = { tree: current, proposal, accepted, refused };
  };
  const applyProposal = async (proposal, accepted, label = null) => {
    const { start, ops } = planOf(proposal, accepted);
    const name = label || `apply ${accepted.size} AI proposals`;
    editor.preview.value = null;
    let changed = start ? await editor.replace(start, { label: name,
      labelKey: { key: 'themes.ai.restructure_entry' } }) : false;
    let refused = 0;
    for (const part of inParts(ops)) {
      const result = await editor.run(part, { lenient: true, label: name,
        labelKey: { key: 'themes.ai.entry', params: { count: part.length } } });
      if (!result.ok) {
        toast({ kind: 'error', title: t('themes.ai.failed'), message: refusal(result.error) });
        return { ok: false, changed };
      }
      refused += result.steps.filter((s) => s.refused).length;
      changed = changed || result.changed;
    }
    const count = accepted.size;
    toast({ kind: 'success', title: t('themes.ai.applied', { count: count - refused }),
      message: refused ? t('themes.ai.applied_refused', { count: refused }) : undefined });
    return { ok: true, changed };
  };
  // A copilot's accepted changes are saved at once, as a version of their own.
  const applyCopilot = async (proposal, accepted) => {
    // The version's action names the copilot (ai-copilot), and the other unsaved edits it holds.
    const others = editor.unsaved.value;
    const label = `ai-copilot: apply ${accepted.size} ${accepted.size === 1 ? 'change' : 'changes'} of the AI copilot`;
    const result = await applyProposal(proposal, accepted, label);
    setCopilot(null);
    if (result && result.ok && result.changed) {
      await save({ quiet: true,
        action: others ? `${label}, with ${others} other unsaved ${others === 1 ? 'edit' : 'edits'}` : label });
    }
  };

  // ── rendering ──
  if (editor.loading.value) {
    return html`<div class="cx-page cx-themes">
      <h1 class="cx-page__title">${t('nav.themes')}</h1>
      <p class="cx-themes-empty-line" aria-busy="true">${t('common.loading')}</p>
    </div>`;
  }
  if (editor.error.value) {
    return html`<div class="cx-page">
      <h1 class="cx-page__title">${t('nav.themes')}</h1>
      <${ErrorCard} error=${editor.error.value} onRetry=${() => editor.load()} live />
    </div>`;
  }
  const info = editor.info.value || {};
  if (!editor.tree.value) {
    const next = info.empty && info.empty.next;
    return html`<div class="cx-page">
      <h1 class="cx-page__title">${t('nav.themes')}</h1>
      <${TunePanel} ctx=${ctx} id="themes" />
      <${EmptyState} icon="file" level=${2} title=${t('themes.none.title')}
        action=${next ? { label: t('themes.none.action'), href: '/build' } : null}>${t('themes.none.text')}<//>
    </div>`;
  }

  const index = editor.index.value;
  const tree = editor.tree.value;
  const base = editor.base.value;
  const past = editor.past.value;
  const future = editor.future.value;
  const dirty = editor.dirty.value;
  const viewing = editor.viewing.value;
  const preview = editor.preview.value;
  const readOnly = editor.readOnly.value;
  const restored = editor.restored.value;
  const running = applyJob ? jobs.find((j) => j.id === applyJob.id) : null;
  const undoLabel = past.length ? t('themes.undo', { what: entryLabel(past[past.length - 1]) }) : t('themes.undo.none');
  const redoLabel = future.length ? t('themes.redo', { what: entryLabel(future[future.length - 1]) }) : t('themes.redo.none');
  const levelsMenu = [
    ...tree.levels.map((_, i) => ({ id: `rename-level:${i + 1}`, label: t('themes.levels.rename', { name: levelName(tree, i + 1, lang), level: i + 1 }) })),
    { kind: 'separator', id: 'sep' },
    { id: 'insert-level', label: t('themes.levels.insert'), disabled: tree.depth >= 4 },
    { id: 'remove-level', label: t('themes.levels.remove'), disabled: tree.depth <= 1, danger: true },
  ];
  const moreMenu = [
    { id: 'versions', label: t('themes.more.versions') },
    { id: 'new-top', label: t('themes.more.new_top', { level: levelName(tree, 1, lang) }) },
    { kind: 'separator', id: 'sep' },
    { id: 'discard', label: t('themes.more.discard'), danger: true, disabled: !dirty },
  ];
  const onToolbarMenu = (item) => {
    if (item.id.startsWith('rename-level:')) setDialog({ kind: 'rename-level', level: Number(item.id.split(':')[1]) });
    else if (item.id === 'insert-level') setDialog({ kind: 'insert-level' });
    else if (item.id === 'remove-level') setDialog({ kind: 'remove-level' });
    else if (item.id === 'versions') setVersionsOpen(true);
    else if (item.id === 'new-top') ui.create(null);
    else if (item.id === 'discard') setDialog({ kind: 'discard' });
  };

  const banners = [];
  if (viewing) {
    banners.push(html`<${Banner} key="viewing" icon="file" actions=${html`
      ${viewing.current ? null : html`<${Button} size="s" onClick=${() => compareVersion({ id: viewing.id, made_at: viewing.at }, viewing.tree)}>
        ${t('themes.versions.compare')}<//>
      <${Button} size="s" onClick=${() => restoreVersion({ id: viewing.id, made_at: viewing.at })}>${t('themes.versions.restore')}<//>`}
      <${Button} size="s" variant="primary" onClick=${() => {
        editor.viewing.value = null;
      }}>${t('themes.viewing.back')}<//>`}>
      ${t('themes.viewing.text', { when: viewing.at ? formatDate(viewing.at, 'datetime') : viewing.id })}<//>`);
  }
  if (preview) {
    banners.push(html`<${Banner} key="preview" icon="info" actions=${html`
      <${Button} size="s" variant="primary" onClick=${() => applyCopilot(preview.proposal, preview.accepted)}>
        ${t('copilot.apply_save', { count: preview.accepted.size })}<//>
      <${Button} size="s" onClick=${() => {
        setCopilot({ resume: { proposal: preview.proposal, accepted: preview.accepted } });
        editor.preview.value = null;
      }}>${t('themes.preview.back')}<//>
      <${Button} size="s" variant="ghost" onClick=${() => {
        editor.preview.value = null;
      }}>${t('themes.preview.stop')}<//>`}>
      ${t('themes.preview.text', { count: preview.accepted.size, refused: preview.refused })}<//>`);
  }
  if (restored && !restored.stale) {
    banners.push(html`<${Banner} key="restored" icon="check" actions=${html`<${Button} size="s"
      onClick=${() => editor.discard()}>${t('themes.restored.discard')}<//>`}>
      ${t('themes.restored.text', { count: restored.count, when: formatDate(restored.at, 'datetime') })}<//>`);
  }
  if (restored && restored.stale) {
    banners.push(html`<${Banner} key="stale-draft" tone="warning" actions=${html`
      <${Button} size="s" variant="primary" onClick=${async () => {
        const entries = (restored.draft.past || []).slice(restored.draft.saved_mark || 0);
        const outcome = await editor.reloadAndMerge(entries);
        if (outcome.ok && outcome.refused.length) setReport(outcome);
        else if (outcome.ok) toast({ kind: 'success', title: t('themes.merge.done', { count: outcome.applied }) });
      }}>${t('themes.stale.merge')}<//>
      <${Button} size="s" onClick=${() => editor.discard()}>${t('themes.restored.discard')}<//>`}>
      ${t('themes.restored.stale', { count: restored.count, when: formatDate(restored.at, 'datetime') })}<//>`);
  }
  if (base && base.source === 'draft') {
    banners.push(html`<${Banner} key="draft">${t('themes.source.draft')}<//>`);
  }
  // what the last grouping says of a tree of this depth (fewer levels than asked)
  for (const note of info.notes || []) {
    banners.push(html`<${Banner} key=${`note-${note.code}`}>${messageOf(note)}<//>`);
  }
  if (info.based_on_current === false) {
    banners.push(html`<${Banner} key="vocabulary" tone="warning" actions=${base && base.source === 'saved'
      ? html`<${Button} size="s" onClick=${rebase} softDisabled=${dirty}>${t('themes.vocabulary.rebase')}<//>` : null}>
      ${t('themes.vocabulary.text', { missing: info.missing_count || 0, extra: info.extra_count || 0 })}
      ${dirty ? html` <span class="cx-themes-banner__note">${t('themes.clean.first')}</span>` : null}<//>`);
  }
  if (kept) {
    banners.push(html`<${Banner} key="kept" icon="check" actions=${html`<${Button} size="s" variant="ghost"
      onClick=${() => setKept(null)}>${t('common.dismiss')}<//>`}>${t('themes.apply.kept')}<//>`);
  }
  // a proposal adopted as the draft (the playground) waits for the draft's save, not for a question
  const adopted = info.proposal && tree.based_on && tree.based_on.run === info.proposal.run;
  if (info.proposal && info.proposal.pending && !adopted) {
    banners.push(html`<${Banner} key="proposal" actions=${html`<${Button} size="s" onClick=${compareProposal}>
      ${t('themes.proposal.compare')}<//>`}>${t('themes.proposal.text')}<//>`);
  }
  if (index && index.toCheck.length && ui.leftTab.value !== 'check') {
    banners.push(html`<${Banner} key="check" actions=${html`<${Button} size="s" onClick=${() => {
      ui.leftTab.value = 'check';
      const first = index.toCheck[0];
      ui.active.value = `c:${first}`;
      ui.treeSel.value = new Set([`c:${first}`]);
    }}>${t('themes.check.review')}<//>`}>${t('themes.check.banner', { count: index.toCheck.length })}<//>`);
  }
  if (running) {
    const pct = running.progress && typeof running.progress.fraction === 'number' ? running.progress.fraction : null;
    banners.push(html`<${Banner} key="apply" icon="activity" actions=${html`<${Button} size="s" variant="ghost"
      onClick=${() => runtime.openActivity()}>${t('themes.apply.activity')}<//>`}>
      ${t('themes.apply.running', { stage: running.progress && running.progress.name ? running.progress.name : '' })}
      <${ProgressBar} value=${pct === null ? undefined : pct} label=${t('themes.apply.progress')} /><//>`);
  }

  const d = dialog;
  const close = () => setDialog(null);
  let modal = null;
  if (d && index) {
    if (d.kind === 'compare') {
      const names = new Map([...d.before.nodes, ...d.after.nodes].map((n) => [n.id, n]));
      modal = html`<${CompareDialog} open title=${d.title} description=${d.description} result=${d.result}
        names=${names} onClose=${close} footer=${html`<${Button} onClick=${close}>${t('common.close')}<//>
          <${Button} variant="primary" onClick=${() => restoreVersion(d.version)}>${t('themes.versions.restore')}<//>`} />`;
    } else if (d.kind === 'proposal') {
      const names = new Map([...d.before.nodes, ...d.after.nodes].map((n) => [n.id, n]));
      modal = html`<${CompareDialog} open title=${t('themes.proposal.title')} description=${t('themes.proposal.lead')}
        result=${d.result} names=${names} onClose=${close} footer=${html`
          <${Button} variant="ghost" onClick=${close}>${t('common.cancel')}<//>
          <${Button} onClick=${() => decideProposal('keep', d.run)}>${t('themes.proposal.keep')}<//>
          <${Button} variant="primary" onClick=${() => decideProposal('adopt', d.run)}>${t('themes.proposal.adopt')}<//>`} />`;
    } else if (d.kind === 'stale') {
      modal = html`<${ConfirmDialog} open title=${t('themes.stale.title')} confirmLabel=${t('themes.stale.merge')}
        cancelLabel=${t('common.cancel')} onAnswer=${(yes) => (yes ? merge() : close())}>
        <p>${t('themes.stale.text', { count: editor.unsaved.value })}</p><//>`;
    } else if (d.kind === 'discard') {
      modal = html`<${ConfirmDialog} open danger title=${t('themes.discard.title')} confirmLabel=${t('themes.discard.confirm')}
        onAnswer=${(yes) => {
          if (yes) editor.discard();
          close();
        }}><p>${t('themes.discard.text', { count: editor.unsaved.value })}</p><//>`;
    } else {
      modal = html`<${OperationDialog} dialog=${d} editor=${editor} ui=${ui} run=${run} onClose=${close} />`;
    }
  }

  const playing = ui.centreTab.value === 'playground';
  const status = viewing ? t('themes.status.viewing') : preview ? t('themes.status.preview')
    : dirty ? t('themes.status.unsaved', { count: editor.unsaved.value })
      : base.source === 'draft' ? t('themes.status.proposal') : t('themes.status.saved');

  return html`<div class=${`cx-page cx-themes ${readOnly ? 'is-read-only' : ''}`}>
    <header class="cx-themes__head">
      <div class="cx-themes__titles">
        <h1 class="cx-page__title">${t('nav.themes')}</h1>
        <p class="cx-themes__status">
          <span class=${`cx-themes-state ${dirty ? 'is-dirty' : ''}`} aria-hidden="true"></span>
          <span>${status}</span>
          ${editor.busy.value ? html`<span class="cx-spinner cx-themes__busy" aria-hidden="true"></span>` : null}
          ${tree.saved && !dirty ? html`<span class="cx-themes__saved">${t('themes.status.saved_at', { when: formatDate(tree.saved.at, 'datetime') })}</span>` : null}
          <span class="cx-themes__levels">${tree.levels.map((_, i) => levelName(tree, i + 1, lang)).join(' › ')}</span>
        </p>
      </div>
      <div class="cx-themes__toolbar" role="toolbar" aria-label=${t('themes.toolbar')}>
        <${IconButton} icon="undo" label=${undoLabel} disabled=${!past.length || readOnly}
          onClick=${() => editor.undo()} class="cx-themes__undo" aria-keyshortcuts="Control+Z" />
        <${IconButton} icon="redo" label=${redoLabel} disabled=${!future.length || readOnly}
          onClick=${() => editor.redo()} class="cx-themes__redo" aria-keyshortcuts="Control+Shift+Z" />
        <${Button} class="cx-themes__ai" disabled=${readOnly} onClick=${() => setCopilot({})}
          aria-haspopup="dialog">${t('themes.curate_ai')}<//>
        <${MenuButton} label=${t('themes.levels')} items=${levelsMenu} onSelect=${onToolbarMenu} variant="ghost" />
        <${MenuButton} label=${t('themes.more')} items=${moreMenu} onSelect=${onToolbarMenu} variant="ghost" />
        <${Button} variant=${dirty ? 'primary' : 'secondary'}
          disabled=${readOnly || (!dirty && base.source !== 'draft')} onClick=${() => save()}
          aria-keyshortcuts="Control+S">${base.source === 'draft' && !dirty ? t('themes.save.proposal') : t('common.save')}<//>
        <${Button} disabled=${readOnly || Boolean(running)} loading=${Boolean(running)}
          onClick=${() => ui.saveAndApply()}>${t('themes.save_apply')}<//>
      </div>
    </header>
    <${TunePanel} ctx=${ctx} id="themes" />
    ${banners.length || applyError ? html`<div class="cx-themes__banners">
      ${banners}
      ${applyError ? html`<${ErrorCard} error=${applyError} compact onDismiss=${() => setApplyError(null)}
        onRetry=${() => ui.saveAndApply()} />` : null}
    </div>` : null}
    <div class=${`cx-themes__body ${playing ? 'is-playground' : ''}`}>
      ${playing ? null : html`<${OutlinePane} editor=${editor} ui=${ui} />`}
      <${CentrePane} ctx=${ctx} editor=${editor} ui=${ui} atlas=${atlas} atlasError=${atlasError} onRetryAtlas=${loadAtlas} />
      ${playing ? null : html`<${SidePanel} editor=${editor} ui=${ui} atlas=${atlas} api=${ctx.api} />`}
    </div>
    <p class="cx-visually-hidden" aria-live="polite" aria-atomic="true">${announcement}</p>
    ${modal}
    <${MergeReportDialog} open=${Boolean(report)} report=${report} onClose=${() => setReport(null)} />
    <${VersionsDrawer} open=${versionsOpen} api=${ctx.api} editor=${editor} onClose=${() => setVersionsOpen(false)}
      onOpenVersion=${openVersion} onCompare=${(v) => compareVersion(v)} onRestore=${restoreVersion} />
    ${copilot ? html`<${ThemeCopilotDialog} api=${ctx.api} editor=${editor} resume=${copilot.resume || null}
      onClose=${() => setCopilot(null)} onPreview=${previewProposal}
      onApply=${applyCopilot} />` : null}
    <${ConfirmDialog} open=${Boolean(leave)} title=${t('themes.leave.title')} confirmLabel=${t('themes.leave.confirm')}
      cancelLabel=${t('leave.stay')} onAnswer=${(yes) => {
        const answer = leave;
        setLeave(null);
        if (answer) answer.resolve(yes);
      }}>
      <p>${t('themes.leave.text', { count: editor.unsaved.value })}</p>
    <//>
  </div>`;
}
