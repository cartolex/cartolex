// SPDX-License-Identifier: MIT
/**
 * The measures a browser cannot compute (`keywords`, `jaccard`: they need every person's
 * whole vocabulary), asked of a host that has a server (the app: `POST /api/atlas/similarity`
 * and `POST /api/atlas/similar-pairs`, `docs/dev/api.md`), through the source's optional
 * `similarity(body)` and `similarPairs(body)`. The answers come back over the same indexes as
 * the browser's own measures, so the views draw them alike.
 */

/** The measures a server measures, in the app's order. */
export const REMOTE_MEASURES = ['keywords', 'jaccard'];
/** Every measure, in the order of the app's « Similarity » parameter. */
export const ALL_MEASURES = ['space', 'keywords', 'jaccard', 'themes'];

/** The ids of *items* (indexes of people or organisations; theme ids as they are). */
function idsOf(index, kind, items) {
  if (kind === 'theme') return Array.from(items);
  if (kind === 'organisation') return Array.from(items, (i) => index.orgs[i].id);
  return Array.from(items, (i) => index.people[i].person_id);
}

/** The similarity of each of *aItems* to each of *bItems* (null: every one of *bKind*), by the
 * server: a Float32Array of rows × columns (NaN: no place). */
export const remoteCross = async (ctx, measure, aKind, aItems, bKind, bItems) => {
  const { index, source } = ctx;
  const answer = await source.similarity({ measure, a: { kind: aKind, ids: idsOf(index, aKind, aItems) },
    b: { kind: bKind, ids: bItems === null ? null : idsOf(index, bKind, bItems) } });
  if (!answer || answer.error) throw new Error((answer && answer.error && answer.error.code) || 'similarity');
  return answer.values;
};

/** The pairs among *targets* (indexes of *kind*), by the server: `[{a, b, score, texts}]`. */
export const remotePairs = async (ctx, measure, kind, targets, limit, mode) => {
  const { index, source } = ctx;
  const answer = await source.similarPairs({ measure, kind, ids: idsOf(index, kind, targets), limit, mode });
  if (!answer || answer.error) throw new Error((answer && answer.error && answer.error.code) || 'pairs');
  const at = kind === 'organisation' ? index.byOrg : index.byPerson;
  return answer.items.map((p) => ({ a: at.get(p.a), b: at.get(p.b), score: p.similarity, texts: p.texts }))
    .filter((p) => p.a !== undefined && p.b !== undefined);
};
