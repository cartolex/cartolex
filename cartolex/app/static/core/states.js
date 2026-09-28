// SPDX-License-Identifier: MIT
/**
 * The six states of a build stage (`cartolex.build.validity.StageState`) and
 * how several states sum up into one (an area's).
 *
 * The API sends states as the build's words (« up to date »); the interface
 * uses keys (`up_to_date`).
 */

export const STATES = ['up_to_date', 'needs_update', 'never_built', 'running', 'failed', 'skipped'];

/** The key of a state given as the API's words or as a key. */
export function stateKey(state) {
  const key = String(state || 'never_built').trim().toLowerCase().replace(/[\s-]+/g, '_');
  return STATES.includes(key) ? key : 'never_built';
}

/**
 * The summary state of several stages (an area): running before failed before
 * needs update; all skipped is skipped; nothing built is never built; some
 * built and some not is needs update.
 */
export function summaryState(states) {
  const keys = states.map(stateKey);
  if (!keys.length) return 'never_built';
  if (keys.includes('running')) return 'running';
  if (keys.includes('failed')) return 'failed';
  if (keys.includes('needs_update')) return 'needs_update';
  if (keys.every((k) => k === 'skipped')) return 'skipped';
  const built = keys.filter((k) => k === 'up_to_date').length;
  const never = keys.filter((k) => k === 'never_built').length;
  if (never && !built) return 'never_built';
  if (never) return 'needs_update';
  return 'up_to_date';
}

/** The area a page's status dot sums up, when the page's id is not an area's. */
export const PAGE_AREAS = { people: 'corpus' };

/** The area of a page entry (its `area`, the mapping above, or its own id). */
export function areaOfPage(entry) {
  return entry.area || PAGE_AREAS[entry.id] || entry.id;
}
