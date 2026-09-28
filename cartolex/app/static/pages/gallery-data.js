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

/** A handoff bundle (`cartolex-handoff/0`) of twelve terms. */
export const HANDOFF_BUNDLE = {
  format: 'cartolex-handoff/0',
  domain: 'Coastal and marine sciences',
  description: 'An invented research community studying coasts, estuaries and the open sea.',
  items: TERMS.slice(0, 12).map(([term, lang], i) => ({
    term, lang, band: 'to check', reason: 'common modifier', people: 12 + i * 3, texts: 40 + i * 11,
    specificity: 0.9 - i * 0.02, forms: [term], inside: [], usage: [],
  })),
};

/** The bundle as the text a person pastes into an assistant. */
export const HANDOFF_PROMPT = [
  'You are helping to build the keyword list of a map of the research field',
  `"${HANDOFF_BUNDLE.domain}". Field described by its owner: ${HANDOFF_BUNDLE.description}`,
  'For each numbered term, answer on one line, in order, with the triage codes:',
  '  C <lang> <term>=<canonical English form>   a concept, M a method, O an object of study',
  '  N <term>   a person, place or institution;  G too generic;  F a fragment or not a term',
  '',
  ...HANDOFF_BUNDLE.items.map((it, i) => `${i + 1}. ${it.term} [${it.lang}] — ${it.people} people, ${it.texts} texts`),
].join('\n');

/** An answer in the triage's line format, as an assistant would give it. */
export const HANDOFF_ANSWER = [
  'C en sediment transport=sediment transport',
  'C en longshore drift=longshore drift',
  'O en dune erosion=dune erosion',
  'C en beach morphodynamics=beach morphodynamics',
  'G sandbar migration',
  'M en tidal inlet dynamics=tidal inlet dynamics',
  'C fr dérive littorale=longshore drift',
  'C fr érosion dunaire=dune erosion',
  'this line is not an answer',
].join('\n');

const LINE = /^\s*(?:\d+\.\s*)?([CMONGKF])\s+(?:([a-z]{2})\s+)?(.+?)\s*$/;

/**
 * Check an answer against the bundle (the gallery's stand-in for the
 * server's check): how many terms it keeps, sets aside, leaves unanswered.
 */
export function checkAnswer(text, bundle = HANDOFF_BUNDLE) {
  const known = new Map(bundle.items.map((it) => [it.term.toLowerCase(), it.term]));
  const answers = new Map();
  let notUnderstood = 0;
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    const m = LINE.exec(line);
    const term = m ? m[3].split('=')[0].trim().toLowerCase() : null;
    if (!m || !known.has(term)) {
      notUnderstood += 1;
      continue;
    }
    answers.set(known.get(term), m[1]);
  }
  const codes = [...answers.values()];
  const kept = codes.filter((c) => 'CMO'.includes(c)).length;
  return {
    ok: answers.size > 0,
    summary: {
      answered: answers.size,
      kept,
      setAside: answers.size - kept,
      unanswered: bundle.items.length - answers.size,
      notUnderstood,
    },
  };
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
      next: { label: 'Open the parameters', action: 'open:/settings' } } },
  { id: 'job-1', kind: 'collect', state: 'succeeded', cancellable: false,
    created_at: '2026-09-28T09:00:00Z', started_at: '2026-09-28T09:00:01Z',
    finished_at: '2026-09-28T09:12:00Z', progress: null,
    result: { summary: '', link: '/corpus', ran: ['corpus.assemble'] }, error: null },
  { id: 'job-0', kind: 'build', state: 'cancelled', cancellable: false,
    created_at: '2026-09-28T08:30:00Z', started_at: '2026-09-28T08:30:01Z',
    finished_at: '2026-09-28T08:31:00Z', progress: null, result: { ran: [] }, error: null },
];

/** Error models (core/errors.js) of the gallery's ErrorCards. */
export const ERRORS = {
  server: {
    code: 'stage_failed', message: 'The theme grouping could not run with these parameters.',
    next: { label: 'Open the parameters', action: 'open:/settings' }, status: 422, method: 'POST',
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
