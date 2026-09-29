/**
 * The actions on the tree: selection, operations and menus. Every action is
 * reachable from a menu and the keyboard; drag and drop runs the same
 * operations. The actions live on the page's view state (`ui`), so that
 * every panel calls the same ones.
 */

import { batch, computed, signal } from '../../core/preact.js';
import { locale, t } from '../../core/i18n.js';
import { placeOps } from './fit.js';
import { dropOperation, isEmpty, lang2, levelName, mergeTargets, nodeParents, pathOf } from './model.js';

/** A refused operation or request, in words. */
export function refusal(error) {
  if (!error) return t('themes.refused.unknown');
  const detail = error.params && error.params.detail;
  return detail ? t('themes.refused', { detail }) : (error.message || t('themes.refused.unknown'));
}

/** The page's view state: what is open, selected, shown, and the actions on it. */
export function createUi(editor) {
  const expanded = signal(new Set());
  const treeSel = signal(new Set());
  const active = signal(null);
  const leftTab = signal('outline');
  const centreTab = signal('treemap');
  const zoom = signal(null);
  const mapHover = signal(null);
  const person = signal(null);
  const focus = computed(() => {
    if (person.value) return { kind: 'person', id: person.value };
    const keys = [...treeSel.value];
    const nodes = keys.filter((k) => k.startsWith('n:'));
    const terms = keys.filter((k) => /^[kacb]:/.test(k)).map((k) => k.slice(2));
    if (nodes.length === 1 && !terms.length) return { kind: 'node', id: nodes[0].slice(2) };
    if (terms.length) return { kind: 'keywords', terms };
    const a = active.value;
    if (a && a.startsWith('n:')) return { kind: 'node', id: a.slice(2) };
    return null;
  });
  return {
    expanded, treeSel, active, leftTab, centreTab, zoom, mapHover, person, focus,
    outlineRef: { current: null }, panelRef: { current: null }, mapFrame: null, lastSearchMs: 0,
  };
}

/**
 * Put the actions on `ui`.
 * @param {object} deps
 * @param {object} deps.editor the editor store
 * @param {object} deps.ui the page's view state
 * @param {function} deps.setDialog open a dialog
 * @param {function} deps.toast show a toast
 * @returns {{run: function, runOrToast: function, focusSearch: function}}
 */
export function installActions({ editor, ui, setDialog, toast }) {
  // ── selection ──
  const reveal = (id) => {
    const index = editor.index.value;
    if (!index || !index.nodes.has(id)) return;
    const path = pathOf(index, id).slice(0, -1);
    if (path.some((p) => !ui.expanded.value.has(p))) {
      ui.expanded.value = new Set([...ui.expanded.value, ...path]);
    }
  };
  ui.setActive = (key) => {
    ui.active.value = key;
  };
  ui.select = (keys) => {
    batch(() => {
      ui.person.value = null;
      ui.treeSel.value = keys;
    });
  };
  ui.openNode = (id, { from } = {}) => {
    reveal(id);
    batch(() => {
      ui.leftTab.value = 'outline';
      ui.person.value = null;
      ui.active.value = `n:${id}`;
      ui.treeSel.value = new Set([`n:${id}`]);
    });
    if (!from && ui.outlineRef.current) ui.outlineRef.current.focus({ preventScroll: true });
  };
  ui.openKeyword = (term) => {
    const tree = editor.index.value.tree;
    if (tree.keywords[term] !== undefined) {
      reveal(tree.keywords[term]);
      ui.expanded.value = new Set([...ui.expanded.value, tree.keywords[term]]);
      batch(() => {
        ui.leftTab.value = 'outline';
        ui.person.value = null;
        ui.active.value = `k:${term}`;
        ui.treeSel.value = new Set([`k:${term}`]);
      });
    } else {
      batch(() => {
        ui.leftTab.value = 'aside';
        ui.person.value = null;
        ui.active.value = `a:${term}`;
        ui.treeSel.value = new Set([`a:${term}`]);
      });
    }
  };
  ui.open = () => {
    if (ui.panelRef.current) ui.panelRef.current.focus();
  };
  ui.pickOnMap = (hit, scene) => {
    if (!hit) return;
    if (hit.layer === 'keywords') ui.openKeyword(scene.layers[0].items[hit.index].term);
    else {
      const p = scene.layers[1].items[hit.index];
      batch(() => {
        ui.treeSel.value = new Set();
        ui.person.value = p.person_id;
      });
    }
  };
  ui.expandAll = () => {
    const index = editor.index.value;
    ui.expanded.value = new Set(index.order.filter((id) => (index.children.get(id) || []).length));
  };
  ui.collapseAll = () => {
    ui.expanded.value = new Set();
  };
  const focusSearch = () => {
    const el = document.getElementById('cx-themes-search');
    if (el) el.focus();
  };

  // ── operations ──
  const run = async (ops, options) => {
    const result = await editor.run(ops, options);
    if (!result.ok && result.error) return refusal(result.error);
    return result.ok;
  };
  const runOrToast = async (ops, options) => {
    const outcome = await run(ops, options);
    if (outcome !== true) toast({ kind: 'warning', title: t('themes.refused.title'), message: outcome || '' });
    return outcome;
  };
  const keysToTerms = (keys) => keys.filter((k) => /^[kacb]:/.test(k)).map((k) => k.slice(2));
  const idle = () => !editor.readOnly.value;

  ui.rename = (id) => idle() && setDialog({ kind: 'rename', id });
  ui.renameRow = (row) => {
    if (row.kind === 'node') ui.rename(row.id);
  };
  ui.moveKeywords = (terms, extra = {}) => idle() && setDialog({ kind: 'move-keywords', terms, ...extra });
  ui.canMoveNode = (id) => nodeParents(editor.index.value, id).length > 0;
  ui.moveNode = (id) => idle() && setDialog({ kind: 'move-node', id });
  ui.merge = (id) => idle() && setDialog({ kind: 'merge', id });
  ui.split = (id) => idle() && setDialog({ kind: 'split', id });
  ui.create = (parent) => idle() && setDialog({ kind: 'create', parent });
  ui.setAside = (terms, extra = {}) => idle() && setDialog({ kind: 'aside', terms, ...extra });
  ui.attribution = (terms) => idle() && setDialog({ kind: 'attribution', terms });
  ui.putBack = (terms) => {
    const tree = editor.index.value.tree;
    const lost = terms.filter((k) => !tree.set_aside[k].from || !editor.index.value.nodes.has(tree.set_aside[k].from));
    if (lost.length) {
      setDialog({ kind: 'put-back-into', terms });
      return;
    }
    runOrToast([{ op: 'put_back', keywords: terms }]);
  };
  ui.putBackInto = (terms) => idle() && setDialog({ kind: 'put-back-into', terms });
  ui.placeAt = (term, node) => {
    if (idle()) runOrToast(placeOps(editor.index.value.tree, term, node));
  };
  ui.runOrToast = runOrToast;
  ui.accept = (terms) => runOrToast([{ op: 'set_review', keywords: terms, state: 'reviewed' }]);
  ui.deleteNode = (id) => {
    if (!isEmpty(editor.index.value, id)) {
      toast({ kind: 'warning', title: t('themes.delete.not_empty') });
      return;
    }
    runOrToast([{ op: 'delete_node', node_id: id }]);
  };
  ui.deleteKeys = (keys) => {
    if (!idle()) return;
    const nodes = keys.filter((k) => k.startsWith('n:'));
    if (nodes.length === 1) ui.deleteNode(nodes[0].slice(2));
    else {
      const terms = keys.filter((k) => k.startsWith('k:')).map((k) => k.slice(2));
      if (terms.length) ui.setAside(terms);
    }
  };
  ui.dragFor = (keys) => {
    const nodes = keys.filter((k) => k.startsWith('n:'));
    const terms = keysToTerms(keys);
    if (terms.length) return { kind: 'keywords', terms };
    if (nodes.length === 1) return { kind: 'node', id: nodes[0].slice(2) };
    return null;
  };
  ui.canDrop = (target, data) => !editor.readOnly.value && Boolean(dropOperation(editor.index.value, data, target));
  ui.drop = (target, data) => {
    const ops = dropOperation(editor.index.value, data, target);
    if (ops) runOrToast(ops);
  };
  // ── menus ──
  ui.nodeMenu = (id, { panel = false } = {}) => {
    const index = editor.index.value;
    const level = index.level.get(id);
    const empty = isEmpty(index, id);
    const ro = editor.readOnly.value;
    const items = [];
    if (!panel) items.push({ id: 'open', label: t('themes.action.open'), hint: t('themes.key.enter') });
    items.push(
      { id: 'rename', label: t('themes.action.rename_more'), hint: t('themes.key.f2'), disabled: ro },
      { id: 'move-node', label: t('themes.action.move_more'), disabled: ro || !nodeParents(index, id).length },
      { id: 'merge', label: t('themes.action.merge_more'), disabled: ro || !mergeTargets(index, id).length },
      { id: 'split', label: t('themes.action.split_more'), disabled: ro || (index.children.get(id).length + index.keywordsOn.get(id).length) < 2 },
      { kind: 'separator', id: 'sep1' },
      { id: 'create-in', label: t('themes.action.create_in', { level: levelName(index.tree, level + 1, lang2(locale.value)) }),
        disabled: ro || level >= index.depth },
      { id: 'create-beside', label: t('themes.action.create_beside'), disabled: ro },
      { id: 'zoom', label: t('themes.action.zoom'), disabled: !(index.children.get(id) || []).length },
      { kind: 'separator', id: 'sep2' },
      { id: 'delete', label: empty ? t('themes.action.delete') : t('themes.action.delete_not_empty'),
        danger: true, disabled: ro || !empty, hint: empty ? t('themes.key.delete') : undefined },
    );
    return items;
  };
  ui.keywordMenu = (terms) => {
    const tree = editor.index.value.tree;
    const ro = editor.readOnly.value;
    const aside = terms.filter((k) => tree.set_aside && k in tree.set_aside);
    const placed = terms.filter((k) => tree.keywords[k] !== undefined);
    const checking = terms.filter((k) => (tree.review || {})[k] === 'to_check');
    const items = [];
    if (placed.length) {
      items.push(
        { id: 'move-keywords', label: t('themes.action.move_more'), disabled: ro },
        { id: 'aside', label: t('themes.action.set_aside_more'), hint: t('themes.key.delete'), disabled: ro },
        { id: 'attribution', label: t('themes.action.attribution_more'), disabled: ro },
      );
    }
    if (aside.length) {
      items.push(
        { id: 'put-back', label: t('themes.action.put_back'), disabled: ro },
        { id: 'put-back-into', label: t('themes.action.put_back_into'), disabled: ro },
        { id: 'aside', label: t('themes.action.reason_more'), disabled: ro },
      );
    }
    if (checking.length) items.push({ id: 'accept', label: t('themes.action.accept'), disabled: ro });
    else if (terms.some((k) => (tree.review || {})[k] === 'reviewed')) {
      items.push({ id: 'uncheck', label: t('themes.action.to_check'), disabled: ro });
    }
    if (placed.length === 1 && terms.length === 1) {
      items.push({ kind: 'separator', id: 'sep' }, { id: 'show-node', label: t('themes.action.show_node') });
    }
    return items;
  };
  ui.menuFor = (keys) => {
    const nodes = keys.filter((k) => k.startsWith('n:'));
    const terms = keysToTerms(keys);
    if (terms.length) return ui.keywordMenu(terms);
    if (nodes.length) return ui.nodeMenu(nodes[0].slice(2));
    return [];
  };
  ui.onMenu = (item, keys) => {
    const index = editor.index.value;
    const nodes = keys.filter((k) => k.startsWith('n:')).map((k) => k.slice(2));
    const terms = keysToTerms(keys);
    const id = nodes[0];
    switch (item.id) {
      case 'open': ui.open(); break;
      case 'rename': ui.rename(id); break;
      case 'move-node': ui.moveNode(id); break;
      case 'merge': ui.merge(id); break;
      case 'split': ui.split(id); break;
      case 'create-in': ui.create(id); break;
      case 'create-beside': ui.create(index.nodes.get(id).parent); break;
      case 'zoom':
        ui.zoom.value = id;
        ui.centreTab.value = 'treemap';
        break;
      case 'delete': ui.deleteNode(id); break;
      case 'move-keywords': ui.moveKeywords(terms.filter((k) => index.tree.keywords[k] !== undefined)); break;
      case 'aside': ui.setAside(terms); break;
      case 'attribution': ui.attribution(terms.filter((k) => index.tree.keywords[k] !== undefined)); break;
      case 'put-back': ui.putBack(terms.filter((k) => k in (index.tree.set_aside || {}))); break;
      case 'put-back-into': ui.putBackInto(terms.filter((k) => k in (index.tree.set_aside || {}))); break;
      case 'accept': ui.accept(terms.filter((k) => (index.tree.review || {})[k] === 'to_check')); break;
      case 'uncheck': runOrToast([{ op: 'set_review', keywords: terms, state: 'to_check' }]); break;
      case 'show-node': ui.openNode(index.tree.keywords[terms[0]]); break;
      default: break;
    }
  };

  return { run, runOrToast, focusSearch };
}
