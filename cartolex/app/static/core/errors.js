// SPDX-License-Identifier: MIT
/**
 * The error model every ErrorCard shows, and the diagnostic a person can copy.
 *
 * The server answers an error with `{"error": {"code", "message", "next":
 * {"label", "action"}}}`. The interface turns it, a failed connection or an
 * unexpected exception into one shape:
 *
 *   { code, params, message, next: {label, action} | null, status, method,
 *     path, requestId, time, technical }
 *
 * `message` and `next.label` are plain words for the person; `technical` holds
 * what a developer needs and is shown folded. A diagnostic never holds project
 * data: no query strings, no identifiers, no request or response bodies.
 */

/** The error model from an API error body (or none) and the HTTP exchange around it. */
export function errorFromResponse(body, { status, method, path, requestId } = {}) {
  const err = body && typeof body === 'object' && body.error && typeof body.error === 'object'
    ? body.error : {};
  const next = err.next && typeof err.next === 'object' && err.next.action
    ? { label: String(err.next.label || ''), action: String(err.next.action) } : null;
  return {
    code: String(err.code || `http_${status || 0}`),
    params: err.params && typeof err.params === 'object' ? err.params : {},
    message: String(err.message || ''),
    next,
    status: status ?? null,
    method: method || null,
    path: path ? redactPath(path) : null,
    requestId: requestId || null,
    time: new Date().toISOString(),
    technical: null,
  };
}

/**
 * The error model of a failed job (`job.error`: `{code, params, message, exception, detail,
 * step, progress}`, or words from an older app): the cause's code and params for the card,
 * and, folded away and in the diagnostic, the exception, its message and the step it was in.
 */
export function jobError(job) {
  const err = job && job.error;
  if (!err) return null;
  if (typeof err === 'string') {
    return errorFromResponse({ error: { code: `job_${job.state}`, message: err } });
  }
  const model = errorFromResponse({ error: err }, { status: err.status });
  const lines = [];
  if (err.exception) lines.push(`${err.exception}: ${err.detail || ''}`.trim());
  if (err.step) {
    const p = err.progress || {};
    const fraction = typeof p.stage_fraction === 'number' ? p.stage_fraction : p.fraction;
    lines.push(`step: ${err.step}${typeof fraction === 'number' ? ` (${Math.round(fraction * 100)} %)` : ''}`);
  }
  if (err.progress && err.progress.code) {
    lines.push(`progress: ${err.progress.code} ${JSON.stringify(err.progress.params || {})}`);
  }
  model.technical = lines.length ? lines.join('\n') : null;
  return withJobFacts(model, job);
}

/**
 * *model* with the facts of the failed *job* a diagnostic needs: its id, the time it
 * failed (not the time the card was drawn) and the traceback the app kept.
 */
export function withJobFacts(model, job) {
  if (!job) return model;
  const err = job.error && typeof job.error === 'object' ? job.error : {};
  if (job.id) model.jobId = job.id;
  if (job.finished_at) model.time = job.finished_at;
  if (err.traceback) model.traceback = String(err.traceback);
  return model;
}

/** The error model of a request that got no answer (server stopped, connection lost). */
export function networkError({ method, path, cause } = {}) {
  return {
    code: 'network',
    params: {},
    message: '',
    next: { label: '', action: 'retry' },
    status: null,
    method: method || null,
    path: path ? redactPath(path) : null,
    requestId: null,
    time: new Date().toISOString(),
    technical: cause ? String(cause.message || cause) : null,
  };
}

/** The error model of an exception thrown by the interface itself. */
export function errorFromException(error, { code = 'unexpected' } = {}) {
  const e = error instanceof Error ? error : new Error(String(error));
  return {
    code,
    params: {},
    message: '',
    next: { label: '', action: 'reload' },
    status: null,
    method: null,
    path: null,
    requestId: null,
    time: new Date().toISOString(),
    technical: `${e.name}: ${e.message}${e.stack ? `\n${e.stack.split('\n').slice(1, 6).join('\n')}` : ''}`,
  };
}

const SAFE_SEGMENT = /^[a-z][a-z0-9_.-]{0,31}$/;

/**
 * A request path without anything that could name project data: the query is
 * dropped and every segment that is not a plain lower-case word becomes « … ».
 */
export function redactPath(path) {
  const bare = String(path).split(/[?#]/)[0];
  return bare
    .split('/')
    .map((seg) => (seg === '' || SAFE_SEGMENT.test(seg) ? seg : '…'))
    .join('/');
}

/**
 * The text « Copy a diagnostic » puts on the clipboard: the application, its
 * build, the page, the error's code and HTTP details, the time (a failed job's:
 * when it failed), the job, the system, and a failed job's traceback (the home
 * folder written `~` by the app). Nothing else from the project.
 */
export function diagnosticText(error, context = {}) {
  const build = context.build && context.build.commit
    ? ` (${context.build.commit.slice(0, 7)}, ${context.build.date || '?'})` : '';
  const lines = [
    `app: ${context.app || 'cartolex'} ${context.version || ''}${build}`.trim(),
    `page: ${context.page || '-'}`,
    `language: ${context.locale || '-'}`,
    `time: ${error.time || new Date().toISOString()}`,
    `code: ${error.code}`,
  ];
  if (error.jobId) lines.push(`job: ${error.jobId}`);
  if (context.platform) lines.push(`system: ${context.platform}`);
  if (error.status !== null && error.status !== undefined) lines.push(`status: ${error.status}`);
  if (error.method || error.path) lines.push(`request: ${error.method || ''} ${error.path || ''}`.trim());
  if (error.requestId) lines.push(`request id: ${error.requestId}`);
  if (context.userAgent) lines.push(`browser: ${context.userAgent}`);
  if (error.technical) lines.push('', error.technical);
  if (error.traceback) lines.push('', error.traceback);
  return lines.join('\n');
}
