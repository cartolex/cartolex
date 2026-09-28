/**
 * The dialogs of the operations on the tree: each one asks what it needs
 * (a name, a target, the parts of a split…) and runs the operation, which
 * resolves true or with the words of a refusal shown in the dialog.
 */

import { html } from '../../core/preact.js';
import { locale, t } from '../../core/i18n.js';
import { keywordTargets, lang2, levelName, mergeTargets, nextIds, nodeName, nodeParents } from './model.js';
import {
  AttributionDialog, CreateDialog, InsertLevelDialog, PickNodeDialog, RemoveLevelDialog, RenameDialog,
  RenameLevelDialog, SetAsideDialog, SplitDialog,
} from './dialogs.js';

/**
 * The open operation dialog, if any.
 * @param {object} props
 * @param {object|null} props.dialog what is asked: `{kind, …}`
 * @param {object} props.editor the editor store
 * @param {object} props.ui the page's view state and actions
 * @param {function} props.run run operations
 * @param {function} props.onClose close the dialog
 */
export function OperationDialog({ dialog: d, editor, ui, run, onClose: close }) {
  const eindex = editor.editIndex.value;
  const tree = editor.tree.value;
  const lang = lang2(locale.value);
  if (!d || !eindex || !tree) return null;
  let modal = null;
  if (d.kind === 'rename') {
    modal = html`<${RenameDialog} open node=${eindex.nodes.get(d.id)} onClose=${close}
      levelLabel=${levelName(tree, eindex.level.get(d.id), lang)}
      onSubmit=${(names) => run([{ op: 'rename_node', node_id: d.id, names }])} />`;
  } else if (d.kind === 'rename-level') {
    modal = html`<${RenameLevelDialog} open tree=${tree} level=${d.level} onClose=${close}
      onSubmit=${(names) => run([{ op: 'rename_level', level: d.level, names }])} />`;
  } else if (d.kind === 'move-keywords') {
    const review = d.review ? [{ op: 'set_review', keywords: d.terms, state: 'reviewed' }] : [];
    const aside = d.terms.filter((k) => k in (tree.set_aside || {}));
    modal = html`<${PickNodeDialog} open index=${eindex} candidates=${keywordTargets(eindex, d.terms)}
      title=${t('themes.move.title', { count: d.terms.length, term: d.terms[0] })}
      description=${t('themes.move.lead')} submitLabel=${t('themes.move.submit')} onClose=${close}
      onSubmit=${async (target) => {
        const ops = aside.length ? [{ op: 'put_back', keywords: d.terms, node_id: target }]
          : [{ op: 'move_keywords', keywords: d.terms, node_id: target }];
        const outcome = await run([...ops, ...review]);
        if (outcome === true && d.after) d.after();
        return outcome;
      }} />`;
  } else if (d.kind === 'put-back-into') {
    modal = html`<${PickNodeDialog} open index=${eindex} candidates=${eindex.order}
      title=${t('themes.putback.title', { count: d.terms.length, term: d.terms[0] })}
      description=${t('themes.putback.lead')} submitLabel=${t('themes.putback.submit')} onClose=${close}
      onSubmit=${(target) => run([{ op: 'put_back', keywords: d.terms, node_id: target }])} />`;
  } else if (d.kind === 'move-node') {
    const level = eindex.level.get(d.id);
    modal = html`<${PickNodeDialog} open index=${eindex} candidates=${nodeParents(eindex, d.id)}
      title=${t('themes.movenode.title', { name: nodeName(eindex.nodes.get(d.id), lang) })}
      description=${t('themes.movenode.lead', { level: levelName(tree, level - 1, lang) })}
      submitLabel=${t('themes.move.submit')} onClose=${close}
      onSubmit=${(target) => run([{ op: 'move_node', node_id: d.id, parent: target }])} />`;
  } else if (d.kind === 'merge') {
    modal = html`<${PickNodeDialog} open index=${eindex} candidates=${mergeTargets(eindex, d.id)}
      title=${t('themes.merge_node.title', { name: nodeName(eindex.nodes.get(d.id), lang) })}
      description=${t('themes.merge_node.lead', { name: nodeName(eindex.nodes.get(d.id), lang) })}
      submitLabel=${t('themes.merge_node.submit')} onClose=${close}
      onSubmit=${(target) => run([{ op: 'merge_nodes', source: d.id, target }])} />`;
  } else if (d.kind === 'split') {
    modal = html`<${SplitDialog} open index=${eindex} id=${d.id} onClose=${close}
      onSubmit=${(members, names) => {
        const [newId] = nextIds(tree, 1);
        return run([{ op: 'split_node', node_id: d.id, parts: [{ members, names }], ids: [newId] }]);
      }} />`;
  } else if (d.kind === 'create') {
    modal = html`<${CreateDialog} open index=${eindex} parent=${d.parent} onClose=${close}
      onSubmit=${async (names) => {
        const [newId] = nextIds(tree, 1);
        const outcome = await run([{ op: 'create_node', parent: d.parent, names, node_id: newId }]);
        if (outcome === true) setTimeout(() => ui.openNode(newId), 0);
        return outcome;
      }} />`;
  } else if (d.kind === 'aside') {
    const current = d.terms.length === 1 && tree.set_aside && tree.set_aside[d.terms[0]] ? tree.set_aside[d.terms[0]].reason : '';
    modal = html`<${SetAsideDialog} open terms=${d.terms} current=${current} onClose=${close}
      onSubmit=${async (reason) => {
        const review = d.review ? [{ op: 'set_review', keywords: d.terms, state: 'reviewed' }] : [];
        const outcome = await run([{ op: 'set_aside', keywords: d.terms, reason }, ...review]);
        if (outcome === true && d.after) d.after();
        return outcome;
      }} />`;
  } else if (d.kind === 'attribution') {
    modal = html`<${AttributionDialog} open index=${eindex} terms=${d.terms} onClose=${close}
      onSubmit=${(levels) => run([{ op: 'set_attribution', keywords: d.terms, levels }])} />`;
  } else if (d.kind === 'insert-level') {
    modal = html`<${InsertLevelDialog} open tree=${tree} onClose=${close}
      onSubmit=${(at) => run([{ op: 'insert_level', at }])} />`;
  } else if (d.kind === 'remove-level') {
    modal = html`<${RemoveLevelDialog} open tree=${tree} onClose=${close}
      onSubmit=${(at) => run([{ op: 'remove_level', at }])} />`;
  }
  return modal;
}
