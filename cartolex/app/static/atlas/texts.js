// SPDX-License-Identifier: MIT
/**
 * Which texts the map draws (`tx` in the state): every text (the source's `texts`, a sample
 * of a large corpus), or « of the focus »: a person's or an organisation's own texts, read
 * from the source (`textsOf`: from every text of the project, a sample beyond its limit), and
 * « with the network » those of the people the network's rings reach too; a theme's, the
 * drawn texts most in it (more than half of the keywords found in them under it); a
 * keyword's, the drawn texts that use it. A text in focus keeps the texts of the focus
 * before it; with nothing in focus, every text is drawn.
 */
import { nodesUnder } from './data.js';

/** The most texts of a focus one answer places. */
export const FOCUS_TEXTS = 5000;
/** The focuses whose own texts the source reads. */
const OWN_TEXTS = ['person', 'organisation', 'projected'];
/** The modes of the texts layer, in the order offered. */
export const TEXT_MODES = ['', 'focus', 'network'];

/** The columns of the texts *all* at *rows*, drawn for a focus (`among`: how many were drawn
 * when those were a sample). */
function textRows(all, rows) {
  const pick = (col) => rows.map((i) => all[col][i]);
  return { id: pick('id'), title: pick('title'), year: pick('year'), x: pick('x'), y: pick('y'),
    ...(all.z ? { z: pick('z') } : {}), by: pick('by'),
    terms: pick('terms'), people: pick('people'), total: rows.length, sampled: false, focus: true,
    among: all.sampled ? all.id.length : 0 };
}

/** The drawn texts that belong to a theme (most of their keywords under it) or use a keyword. */
export function textsIn(index, sel, all) {
  const rows = [];
  if (sel.kind === 'keyword') {
    const k = index.byTerm.get(sel.id);
    for (let i = 0; i < all.id.length; i += 1) if (all.terms[i].includes(k)) rows.push(i);
  } else {
    const under = nodesUnder(index, sel.id);
    for (let i = 0; i < all.id.length; i += 1) {
      const terms = all.terms[i];
      let n = 0;
      for (const k of terms) {
        const node = index.keywords[k] && index.keywords[k].node;
        if (node && under.has(node)) n += 1;
      }
      if (terms.length && n * 2 > terms.length) rows.push(i);
    }
  }
  return textRows(all, rows);
}

/**
 * What the texts layer draws, *onChange()* told when an answer arrives. Answers
 * `{drawn(index, state, all), clear(), dispose()}`; `drawn` gives `{texts, sample, pending}`:
 * the texts to draw (null while read), whether the sample of every text is needed.
 */
export function createTextFocus(source, onChange) {
  const answers = new Map();
  let kept = { all: null, key: '', texts: null };
  let context = null;
  let alive = true;
  const read = (key, query) => {
    answers.set(key, null);
    Promise.resolve().then(() => source.textsOf(query))
      .then((data) => (data && data.error ? { error: data.error } : { data }))
      .catch((error) => ({ error: { message: String(error && error.message ? error.message : error) } }))
      .then((r) => {
        if (!alive) return;
        if (r.data) r.data.focus = true;
        answers.set(key, r);
        onChange();
      });
  };
  return {
    drawn(index, state, all) {
      const sel = state.sel;
      if (!sel) context = null;
      else if (sel.kind !== 'text') context = sel;
      const focus = state.tx ? context : null;
      if (!focus) return { texts: all, sample: true, pending: false };
      if (OWN_TEXTS.includes(focus.kind) && source.textsOf) {
        const net = state.tx === 'network' ? state.net : 0;
        const key = `${focus.kind}:${focus.id}:${net}`;
        if (!answers.has(key)) read(key, { kind: focus.kind, id: focus.id, net, limit: FOCUS_TEXTS });
        const r = answers.get(key);
        if (!r) return { texts: null, sample: false, pending: true };
        if (r.error || !r.data || r.data.available === false) return { texts: all, sample: true, pending: false };
        return { texts: r.data, sample: false, pending: false };
      }
      if ((focus.kind === 'theme' && index.nodes.has(focus.id)) || (focus.kind === 'keyword' && index.byTerm.has(focus.id))) {
        if (!all) return { texts: null, sample: true, pending: true };
        const key = `${focus.kind}:${focus.id}`;
        if (kept.all !== all || kept.key !== key) kept = { all, key, texts: textsIn(index, focus, all) };
        return { texts: kept.texts, sample: true, pending: false };
      }
      return { texts: all, sample: true, pending: false };
    },
    clear() {
      answers.clear();
      kept = { all: null, key: '', texts: null };
      context = null;
    },
    dispose() {
      alive = false;
    },
  };
}
