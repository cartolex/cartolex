// SPDX-License-Identifier: MIT
/**
 * Fixture data for the component gallery: an invented coastal and marine
 * research field (the demo world's), no real person, no real corpus. Terms
 * are data, not interface text: they are not translated.
 */

/** The build's stages in every state, as `GET /api/project/state` sends them. */
export const STAGES = [
  { id: 'corpus.assemble', name: 'gather the texts', area: 'corpus', state: 'up to date', reasons: [] },
  { id: 'keywords.extract', name: 'find keyword candidates', area: 'keywords', state: 'up to date', reasons: [] },
  { id: 'keywords.triage', name: 'AI clean-up', area: 'keywords', state: 'skipped', reasons: [],
    skip_reason: 'the AI clean-up is switched off' },
  { id: 'keywords.build', name: 'build the vocabulary', area: 'keywords', state: 'running', reasons: [],
    progress: { phase: 4, phases: 7, stage: 'keywords.build', stage_fraction: 0.45, fraction: 0.52,
      message: '', elapsed_s: 81, eta_s: 95 } },
  { id: 'themes.space', name: 'place keywords in a common space', area: 'themes', state: 'needs update',
    reasons: [{ kind: 'upstream', subject: 'keywords.build', detail: 'keywords.build needs an update' },
      { kind: 'parameter', subject: 'themes.space.dimensions', detail: 'a parameter changed' }] },
  { id: 'themes.group', name: 'group keywords into topics and themes', area: 'themes', state: 'failed',
    reasons: [], attempt: { outcome: 'failed', error: 'as many topic groups as keywords' } },
  { id: 'themes.apply', name: 'apply your themes', area: 'themes', state: 'needs update',
    reasons: [{ kind: 'input', subject: 'decisions/themes.json', detail: 'decisions/themes.json changed' }] },
  { id: 'map.layout', name: 'draw the map', area: 'map', state: 'never built', reasons: [] },
  { id: 'map.trajectories', name: 'change over time', area: 'map', state: 'never built', reasons: [] },
  { id: 'overlays.position', name: 'place projected people', area: 'map', state: 'skipped', reasons: [],
    skip_reason: 'no projected set' },
];

const TERMS = [
  ['sediment transport', 'en'], ['longshore drift', 'en'], ['dune erosion', 'en'],
  ['beach morphodynamics', 'en'], ['sandbar migration', 'en'], ['tidal inlet dynamics', 'en'],
  ['dérive littorale', 'fr'], ['érosion dunaire', 'fr'], ['transport sédimentaire', 'fr'],
  ['morphodynamique des plages', 'fr'], ['deriva litorânea', 'pt'], ['erosão de dunas', 'pt'],
  ['transporte de sedimentos', 'pt'], ['salt marsh accretion', 'en'], ['estuarine turbidity maximum', 'en'],
  ['plankton bloom phenology', 'en'], ['coastal upwelling', 'en'], ['storm surge modelling', 'en'],
  ['seagrass meadow loss', 'en'], ['herbiers de phanérogames', 'fr'],
];
const STATES = ['up to date', 'needs update', 'never built', 'up to date', 'up to date', 'skipped'];

/** *count* keyword rows (default 10⁵) for the table: deterministic, keyed by `id`. */
export function tableRows(count = 100000) {
  const rows = new Array(count);
  for (let i = 0; i < count; i += 1) {
    const [term, lang] = TERMS[i % TERMS.length];
    const n = Math.floor(i / TERMS.length);
    rows[i] = {
      id: `k${i}`,
      term: n ? `${term} ${n}` : term,
      lang,
      people: ((i * 7919) % 480) + 1,
      texts: ((i * 104729) % 2600) + 1,
      specificity: ((i * 31) % 1000) / 1000,
      state: STATES[i % STATES.length],
    };
  }
  return rows;
}

/** Jobs in every state, as `GET /api/jobs` sends them. */
export const JOBS = [
  { id: 'job-3', kind: 'build', state: 'running', cancellable: true,
    created_at: '2026-09-28T09:58:00Z', started_at: '2026-09-28T09:58:02Z', finished_at: null,
    progress: { phase: 4, phases: 7, stage: 'keywords.build', name: 'build the vocabulary',
      stage_fraction: 0.45, fraction: 0.52, message: '', elapsed_s: 81, eta_s: 95 },
    result: null, error: null },
  { id: 'job-2', kind: 'build', state: 'failed', cancellable: false,
    created_at: '2026-09-28T09:40:00Z', started_at: '2026-09-28T09:40:01Z',
    finished_at: '2026-09-28T09:44:30Z', progress: null, result: null,
    error: { code: 'stage_failed', message: 'The theme grouping could not run with these parameters.',
      next: { label: 'Open the parameters', action: 'settings' } } },
  { id: 'job-1', kind: 'collect', state: 'succeeded', cancellable: false,
    created_at: '2026-09-28T09:00:00Z', started_at: '2026-09-28T09:00:01Z',
    finished_at: '2026-09-28T09:12:00Z', progress: null,
    result: { summary: '', link: '/corpus', ran: ['corpus.assemble'] }, error: null },
  { id: 'job-0', kind: 'build', state: 'cancelled', cancellable: false,
    created_at: '2026-09-28T08:30:00Z', started_at: '2026-09-28T08:30:01Z',
    finished_at: '2026-09-28T08:31:00Z', progress: null, result: { ran: [] }, error: null },
];

/** A large collection from an institution: reading, asking before a large list, paused. */
export const LARGE_JOBS = [
  { id: 'job-13', kind: 'collection', state: 'running', cancellable: true,
    title_code: 'collect_institutions', created_at: '2026-09-28T10:00:00Z',
    started_at: '2026-09-28T10:00:01Z', finished_at: null,
    progress: { phase: 1, phases: 1, stage: 'corpus.institutions', fraction: 0.41,
      stage_fraction: 0.41, code: 'institution_works', eta_s: 3120,
      params: { works: 697400, total: 1702300, pages: 6974, authors: 141020, rate: 322 } },
    result: null, error: null },
  { id: 'job-12', kind: 'collection', state: 'paused', cancellable: false,
    title_code: 'collect_institutions', created_at: '2026-09-28T09:00:00Z',
    started_at: '2026-09-28T09:00:01Z', finished_at: '2026-09-28T09:00:03Z', progress: null,
    result: { outcome: 'paused', action: 'institutions', pause: {
      code: 'collect_size_confirm', checkpoint: 'institution_works-0123456789abcdef',
      params: { works: 100, total: 1702300, pages: 1, requests: 17023, seconds: 5300,
        cost_usd: 1.7, days: 2, keyed: true },
      progress: { works: 100, total: 1702300, pages: 1 }, cause: null } },
    error: null },
  { id: 'job-10', kind: 'collection', state: 'paused', cancellable: false,
    title_code: 'collect_institutions', created_at: '2026-09-28T07:00:00Z',
    started_at: '2026-09-28T07:00:01Z', finished_at: '2026-09-28T07:41:00Z', progress: null,
    result: { outcome: 'paused', action: 'institutions', pause: {
      code: 'collect_budget_paused', checkpoint: 'institution_works-00112233445566778',
      params: { works: 100000, total: 1702300, pages: 1000,
        resets_at: '2026-09-29T00:00:00+00:00', keyed: false },
      progress: { works: 100000, total: 1702300, pages: 1000 }, cause: null } },
    error: null },
  { id: 'job-11', kind: 'collection', state: 'paused', cancellable: false,
    title_code: 'collect_institutions', created_at: '2026-09-28T08:00:00Z',
    started_at: '2026-09-28T08:00:01Z', finished_at: '2026-09-28T08:37:00Z', progress: null,
    result: { outcome: 'paused', action: 'institutions', pause: {
      code: 'collect_paused', checkpoint: 'institution_works-fedcba9876543210',
      params: { works: 99900, total: 1702300, pages: 999 },
      progress: { works: 99900, total: 1702300, pages: 999 },
      cause: { code: 'collect_service_unavailable', exception: 'ServiceUnavailable',
        params: { host: 'api.openalex.org', status: 429, what: 'too many requests' },
        message: 'api.openalex.org gave no usable answer after every attempt (too many requests)',
        detail: 'api.openalex.org: too many requests (status 429); gave up after 5 attempts',
        step: 'corpus.institutions', progress: { stage: 'corpus.institutions', fraction: 0.06 } } } },
    error: null },
];

/** Error models (core/errors.js) of the gallery's ErrorCards. */
export const ERRORS = {
  server: {
    code: 'stage_failed', message: 'The theme grouping could not run with these parameters.',
    next: { label: 'Open the parameters', action: 'settings' }, status: 422, method: 'POST',
    path: '/api/build', requestId: 'req-7f3a', time: '2026-09-28T09:44:30Z', technical: null,
  },
  network: {
    code: 'network', message: '', next: { label: '', action: 'retry' }, status: null,
    method: 'GET', path: '/api/project/state', requestId: null, time: '2026-09-28T09:45:00Z',
    technical: 'TypeError: Failed to fetch',
  },
  unexpected: {
    code: 'unexpected', message: '', next: { label: '', action: 'reload' }, status: null,
    method: null, path: null, requestId: null, time: '2026-09-28T09:46:00Z',
    technical: 'TypeError: cannot read properties of undefined\n    at render (gallery.js:1:1)',
  },
};

/** A small theme tree (themes, topics, keywords) for the TreeView and the Treemap. */
export const THEME_TREE = [
  { id: 's1', name: 'Coastal hazards', topics: [
    { id: 'c1', name: 'Storm surge', keywords: ['storm surge modelling', 'coastal flooding', 'wave setup'] },
    { id: 'c2', name: 'Shoreline erosion', keywords: ['dune erosion', 'érosion dunaire', 'cliff retreat', 'beach morphodynamics'] },
  ] },
  { id: 's2', name: 'Marine ecology', topics: [
    { id: 'c3', name: 'Seagrass meadows', keywords: ['seagrass meadow loss', 'herbiers de phanérogames'] },
    { id: 'c4', name: 'Plankton', keywords: ['plankton bloom phenology', 'coastal upwelling'] },
  ] },
  { id: 's3', name: 'Estuaries and sediments', topics: [
    { id: 'c5', name: 'Sediment transport', keywords: ['sediment transport', 'longshore drift', 'dérive littorale', 'transport sédimentaire', 'sandbar migration'] },
    { id: 'c6', name: 'Estuarine mixing', keywords: ['estuarine turbidity maximum', 'salt marsh accretion', 'tidal inlet dynamics'] },
  ] },
];

/**
 * Points for the MapFrame: *n* points in twelve clusters, deterministic (a
 * small linear congruential generator), with a hue family each.
 */
export function mapPoints(n = 10_000) {
  let seed = 7;
  const random = () => {
    seed = (seed * 1664525 + 1013904223) % 4294967296;
    return seed / 4294967296;
  };
  const x = new Float32Array(n);
  const y = new Float32Array(n);
  const color = new Uint16Array(n);
  const centres = Array.from({ length: 12 }, (_, k) => [Math.cos(k * 0.52) * (3 + (k % 3)), Math.sin(k * 0.52) * (3 + (k % 3))]);
  for (let i = 0; i < n; i += 1) {
    const k = i % 12;
    const r = Math.sqrt(-2 * Math.log(random() + 1e-9)) * 0.7;
    const a = random() * Math.PI * 2;
    x[i] = centres[k][0] + r * Math.cos(a);
    y[i] = centres[k][1] + r * Math.sin(a);
    color[i] = k;
  }
  return { x, y, color, bounds: { xmin: -7, xmax: 7, ymin: -7, ymax: 7 } };
}
