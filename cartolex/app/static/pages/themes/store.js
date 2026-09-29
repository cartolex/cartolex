/**
 * The theme editor's state: the tree being edited, its undo and redo lists,
 * the autosaved draft, and the calls that load, change, save and apply it.
 *
 * **The tree** is changed only by the server's operations
 * (`POST /api/themes/ops`): each change is one entry of the undo list, named
 * by the operations' descriptions, holding the operations and a patch (what
 * changed), so undo and redo need no call and cost little memory.
 *
 * **The draft** is kept in the browser's local storage, per project, on every
 * change: the tree, the version of the saved tree it started from, and the
 * undo list with its operations. It survives a reload and a crash of the tab
 * or the browser, and it is personal: it never reaches the project (shared,
 * versioned, perhaps synced) before someone saves. When the saved tree has
 * changed meanwhile, the draft's operations are applied again to the newer
 * tree (« reload and merge »), and those that no longer apply are listed.
 */

import { batch, computed, signal } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { applyPatch, diffTree, indexTree, optimistic, sameTree } from './model.js';
import { entryLabel, namesFor } from './labels.js';

export const DRAFT_PREFIX = 'cartolex.themes-draft/1:';
/** The most bytes a draft may take in local storage (the undo patches are dropped first). */
const DRAFT_LIMIT = 4_000_000;

function storage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** The draft saved for *projectId*, or null. */
export function readDraft(projectId) {
  const store = storage();
  if (!store || !projectId) return null;
  try {
    const raw = store.getItem(DRAFT_PREFIX + projectId);
    const draft = raw ? JSON.parse(raw) : null;
    return draft && draft.v === 1 && draft.tree ? draft : null;
  } catch {
    return null;
  }
}

function writeDraft(projectId, draft) {
  const store = storage();
  if (!store || !projectId) return false;
  const key = DRAFT_PREFIX + projectId;
  try {
    if (!draft) {
      store.removeItem(key);
      return true;
    }
    let text = JSON.stringify(draft);
    if (text.length > DRAFT_LIMIT) {
      // Keep what « reload and merge » needs: the operations, not the patches.
      text = JSON.stringify({ ...draft, past: draft.past.map((e) => ({ ...e, patch: null })), future: [] });
    }
    store.setItem(key, text);
    return true;
  } catch {
    return false;
  }
}

/**
 * The editor of one page visit.
 * @param {object} options
 * @param {object} options.api the page's API client (calls dropped once the page is left)
 * @param {string|null} options.projectId the open project (the draft's key)
 * @param {(message: string) => void} options.announce a polite live-region message
 */
export function createEditor({ api, projectId, announce = () => {} }) {
  const loading = signal(true);
  const error = signal(null);
  const info = signal(null); // the last GET /api/themes, without its tree
  const base = signal(null); // {tree, version, source}
  const tree = signal(null);
  const past = signal([]);
  const future = signal([]);
  const usage = signal({});
  const busy = signal(false);
  const restored = signal(null); // {at, stale, count}
  const viewing = signal(null); // a version shown read-only: {id, tree, label}
  const preview = signal(null); // AI proposals previewed: {tree, accepted}
  const lastSaved = signal(null); // {removed, action}
  const opError = signal(null);

  /** The tree the views show: a version being read, a preview, or the one being edited. */
  const shown = computed(() => (viewing.value && viewing.value.tree)
    || (preview.value && preview.value.tree) || tree.value);
  const index = computed(() => (shown.value ? indexTree(shown.value, usage.value) : null));
  const editIndex = computed(() => {
    if (!tree.value) return null;
    return shown.value === tree.value ? index.value : indexTree(tree.value, usage.value);
  });
  const readOnly = computed(() => Boolean(viewing.value || preview.value));
  const dirty = computed(() => Boolean(tree.value && base.value && !sameTree(tree.value, base.value.tree)));
  /** The length of the undo list when the tree was last saved (or loaded). */
  const savedMark = signal(0);
  /** How many steps separate the tree being edited from the saved one. */
  const unsaved = computed(() => (dirty.value ? Math.max(1, Math.abs(past.value.length - savedMark.value)) : 0));

  let chain = Promise.resolve();
  const serial = (fn) => {
    const next = chain.then(fn, fn);
    chain = next.catch(() => {});
    return next;
  };

  // The draft is written right after the change is on screen (a few milliseconds later),
  // and at once when the page is left or hidden.
  let pending = 0;
  function persist() {
    if (pending) return;
    pending = setTimeout(flush, 0);
  }
  function flush() {
    clearTimeout(pending);
    pending = 0;
    if (!tree.value || !base.value) return;
    if (!dirty.value) {
      writeDraft(projectId, null);
      return;
    }
    writeDraft(projectId, {
      v: 1,
      base_version: base.value.version,
      source: base.value.source,
      saved_at: new Date().toISOString(),
      tree: tree.value,
      past: past.value,
      future: future.value,
      saved_mark: savedMark.value,
    });
  }

  function setInfo(data) {
    const { tree: _t, ...rest } = data;
    info.value = rest;
  }

  /** Read the tree and the keywords' usage; restore a draft when there is one. */
  async function load() {
    loading.value = true;
    error.value = null;
    // The keywords' usage sizes the treemap; the tree shows without it (by keyword counts)
    // and takes it when it comes, so a cold start of the app does not hold the page.
    api.get('/api/themes/usage').then((used) => {
      if (used.ok) usage.value = used.data.terms || {};
      return used;
    });
    const themes = await api.get('/api/themes');
    if (!themes.ok) {
      batch(() => {
        error.value = themes.error;
        loading.value = false;
      });
      return false;
    }
    const data = themes.data;
    batch(() => {
      setInfo(data);
      if (data.tree) {
        base.value = { tree: data.tree, version: themes.etag || data.version, source: data.source };
        tree.value = data.tree;
      } else {
        base.value = null;
        tree.value = null;
      }
      past.value = [];
      future.value = [];
      savedMark.value = 0;
      loading.value = false;
    });
    if (data.tree) {
      const draft = readDraft(projectId);
      if (draft && !sameTree(draft.tree, data.tree)) {
        const count = Math.max(1, (draft.past || []).length - (draft.saved_mark || 0));
        if (draft.base_version === base.value.version) {
          batch(() => {
            tree.value = draft.tree;
            past.value = (draft.past || []).filter((e) => e.patch);
            future.value = (draft.future || []).filter((e) => e.patch);
            savedMark.value = Math.min(draft.saved_mark || 0, past.value.length);
            restored.value = { at: draft.saved_at, stale: false, count };
          });
        } else {
          restored.value = { at: draft.saved_at, stale: true, count, draft };
        }
      } else if (draft) {
        writeDraft(projectId, null);
      }
    }
    return true;
  }

  /** Read the tree's state again (after an apply, a rebase elsewhere) without losing edits. */
  async function refreshInfo() {
    const themes = await api.get('/api/themes');
    if (themes.ok) setInfo(themes.data);
    return themes;
  }

  function record(before, after, ops, descriptions, label, labelKey = null) {
    const patch = diffTree(before, after);
    if (!Object.keys(patch.whole).length && !Object.keys(patch.dicts).length) return false;
    const names = namesFor(descriptions, before);
    const namesAfter = namesFor(descriptions, after);
    batch(() => {
      past.value = [...past.value, { label, labelKey, descriptions, names, namesAfter, ops, patch, at: Date.now() }];
      future.value = [];
      tree.value = after;
      opError.value = null;
    });
    persist();
    return true;
  }

  /**
   * Apply *ops* to the tree being edited; resolves to `{ok, steps}`. A refused
   * operation changes nothing and sets `opError` (shown by the page).
   */
  function run(ops, { label, labelKey = null, lenient = false } = {}) {
    return serial(async () => {
      if (!tree.value || readOnly.value) return { ok: false };
      busy.value = true;
      const before = tree.value;
      // The change shows at once where it can; the server's tree follows and is the one kept.
      const guess = lenient ? null : optimistic(before, ops);
      if (guess) tree.value = guess;
      const result = await api.post('/api/themes/ops', { tree: before, ops, lenient });
      busy.value = false;
      if (!result.ok) {
        if (guess) tree.value = before;
        opError.value = result.error;
        return { ok: false, error: result.error };
      }
      const { steps } = result.data;
      const done = steps.filter((s) => s.description);
      const descriptions = done.map((s) => s.description);
      const kept = ops.filter((_, i) => steps[i] && steps[i].description);
      const name = label || descriptions.join('; ');
      const changed = record(before, result.data.tree, kept, descriptions, name, labelKey);
      if (!changed && guess) tree.value = before;
      if (changed) announce(t('themes.announce.done', { what: entryLabel(past.value[past.value.length - 1]) }));
      return { ok: true, steps, changed };
    });
  }

  function undo() {
    return serial(async () => {
      const list = past.value;
      if (!list.length || readOnly.value) return false;
      const entry = list[list.length - 1];
      batch(() => {
        tree.value = applyPatch(tree.value, entry.patch, true);
        past.value = list.slice(0, -1);
        future.value = [...future.value, entry];
      });
      persist();
      announce(t('themes.announce.undone', { what: entryLabel(entry) }));
      return true;
    });
  }

  function redo() {
    return serial(async () => {
      const list = future.value;
      if (!list.length || readOnly.value) return false;
      const entry = list[list.length - 1];
      batch(() => {
        tree.value = applyPatch(tree.value, entry.patch);
        future.value = list.slice(0, -1);
        past.value = [...past.value, entry];
      });
      persist();
      announce(t('themes.announce.redone', { what: entryLabel(entry) }));
      return true;
    });
  }

  /** Forget the edits: back to the saved tree (or the proposal). */
  function discard() {
    batch(() => {
      if (base.value) tree.value = base.value.tree;
      past.value = [];
      future.value = [];
      savedMark.value = 0;
      restored.value = null;
      preview.value = null;
    });
    writeDraft(projectId, null);
  }

  /** The action name of a save: the undo list's descriptions since the last save. */
  function actionName() {
    const names = past.value.slice(Math.min(savedMark.value, past.value.length)).map((e) => e.label).filter(Boolean);
    if (!names.length) return base.value && base.value.source === 'draft' ? 'save the proposal' : 'save';
    const text = names.join('; ');
    return text.length <= 200 ? text : `${names.length} changes: ${text}`.slice(0, 199) + '…';
  }

  /**
   * Save the tree as a new version (`If-Match`: the version it started from), named
   * `action` when given, else after the steps since the last save.
   * Resolves to `{ok}`, `{ok: false, stale: true}` on a 412, or the error.
   */
  function save({ action = null } = {}) {
    return serial(async () => {
      if (!tree.value) return { ok: false };
      busy.value = true;
      const before = tree.value;
      const result = await api.put('/api/themes', { tree: before, action: action || actionName() },
        { ifMatch: base.value.version });
      busy.value = false;
      if (!result.ok) {
        if (result.kind === 'stale') return { ok: false, stale: true, error: result.error };
        return { ok: false, error: result.error };
      }
      const saved = result.data;
      batch(() => {
        if (saved.removed.length) {
          // The save removed empty nodes: that is a step of its own, undone like the others.
          const patch = diffTree(before, saved.tree);
          const description = `remove empty ${saved.removed.length === 1 ? 'node' : 'nodes'} ${saved.removed.join(', ')}`;
          past.value = [...past.value, { label: description, descriptions: [description],
            names: namesFor([description], before), ops: [{ op: 'prune_empty' }], patch, at: Date.now() }];
        }
        tree.value = saved.tree;
        savedMark.value = past.value.length;
        base.value = { tree: saved.tree, version: result.etag || saved.version, source: 'saved' };
        lastSaved.value = { removed: saved.removed, action: saved.action, written: saved.written };
        restored.value = null;
      });
      persist();
      refreshInfo();
      return { ok: true, saved };
    });
  }

  /**
   * Apply the operations of *entries* (undo entries, in order) again to the
   * newest saved tree; the ones that no longer apply are returned.
   */
  async function reloadAndMerge(given = null) {
    // Only the steps since the last save: the saved version holds the others already.
    const entries = given || past.value.slice(Math.min(savedMark.value, past.value.length));
    return serial(async () => {
      const themes = await api.get('/api/themes');
      if (!themes.ok) return { ok: false, error: themes.error };
      const newest = themes.data.tree;
      const ops = [];
      const owner = [];
      entries.forEach((entry, i) => {
        for (const op of entry.ops || []) {
          ops.push(op);
          owner.push(i);
        }
      });
      let merged = newest;
      const refused = [];
      if (ops.length && newest) {
        const result = await api.post('/api/themes/ops', { tree: newest, ops, lenient: true });
        if (!result.ok) return { ok: false, error: result.error };
        merged = result.data.tree;
        result.data.steps.forEach((step, k) => {
          if (step.refused) refused.push({ entry: entries[owner[k]], reason: step.refused });
        });
      }
      batch(() => {
        setInfo(themes.data);
        base.value = newest ? { tree: newest, version: themes.etag || themes.data.version,
          source: themes.data.source } : null;
        tree.value = merged;
        const patch = merged && newest ? diffTree(newest, merged) : null;
        past.value = patch && (Object.keys(patch.whole).length || Object.keys(patch.dicts).length)
          ? [{ label: `reload and merge ${entries.length} changes`, descriptions: [],
            labelKey: { key: 'themes.merge.entry', params: { count: entries.length } },
            ops: entries.flatMap((e) => e.ops || []), patch, at: Date.now() }] : [];
        future.value = [];
        savedMark.value = 0;
        restored.value = null;
      });
      persist();
      return { ok: true, refused, applied: ops.length - refused.length, total: ops.length };
    });
  }

  /** Make the tree saved elsewhere the base, and drop the edits (after a restore or a rebase). */
  function adopt(data, etag) {
    batch(() => {
      setInfo(data);
      base.value = data.tree ? { tree: data.tree, version: etag || data.version, source: data.source || 'saved' } : null;
      tree.value = data.tree;
      past.value = [];
      future.value = [];
      savedMark.value = 0;
      restored.value = null;
      viewing.value = null;
      preview.value = null;
    });
    writeDraft(projectId, null);
  }

  return {
    loading, error, info, base, tree, past, future, usage, busy, restored, viewing, preview,
    lastSaved, opError, shown, index, editIndex, readOnly, dirty, unsaved, savedMark,
    load, refreshInfo, run, undo, redo, discard, save, reloadAndMerge, adopt, persist, flush, actionName,
  };
}
