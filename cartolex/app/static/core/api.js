// SPDX-License-Identifier: MIT
/**
 * The JSON client every screen talks to the server with.
 *
 * - Every call takes an optional `AbortSignal`; a page's calls carry its
 *   navigation's signal, so leaving the page cancels them (see router.js).
 * - State-changing calls (POST, PUT, PATCH, DELETE) send the CSRF header the
 *   manifest names, with the session's token.
 * - A read returns the resource's version (`ETag`); a write that passes it as
 *   `ifMatch` sends `If-Match`, and a write refused because the resource
 *   changed meanwhile (HTTP 412) comes back as `{ok: false, kind: 'stale',
 *   etag}` with the current version, for the « reload and merge » flow.
 *
 * Calls never throw for HTTP or network failures: they resolve to a result.
 *
 *   {ok: true, status, data, etag}
 *   {ok: false, kind: 'stale', status: 412, etag, error, data}
 *   {ok: false, kind: 'http', status, error, data}     any other 4xx/5xx
 *   {ok: false, kind: 'network', status: null, error}   no answer
 *   {ok: false, kind: 'aborted', status: null, error: null}
 *
 * `error` is the ErrorCard model of errors.js.
 */
import { errorFromResponse, networkError } from './errors.js';

const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

/** Read a cookie's value by name (for a CSRF token kept in a cookie). */
export function readCookie(name) {
  if (typeof document === 'undefined') return null;
  for (const part of document.cookie.split(';')) {
    const [key, ...rest] = part.trim().split('=');
    if (key === name) return decodeURIComponent(rest.join('='));
  }
  return null;
}

export class ApiClient {
  /**
   * @param {object} options
   * @param {string} [options.base] prefix of every path ('' for the same origin)
   * @param {string} [options.csrfHeader] the header name (manifest `security.csrf_header`)
   * @param {() => (string|null)} [options.csrfToken] the session's token, read at each call
   * @param {() => string} [options.language] the interface language, sent as Accept-Language
   * @param {typeof fetch} [options.fetch] the fetch function (tests pass a fake)
   */
  constructor({ base = '', csrfHeader = 'X-Cartolex-CSRF', csrfToken = () => null,
    language = () => '', fetch: fetchImpl } = {}) {
    this.base = base;
    this.csrfHeader = csrfHeader;
    this.csrfToken = csrfToken;
    this.language = language;
    this.fetchImpl = fetchImpl || ((...args) => globalThis.fetch(...args));
    /** Number of requests sent (the budgets harness reads it). */
    this.sent = 0;
  }

  /** A client whose every call also carries *signal* (a page's navigation signal). */
  withSignal(signal) {
    const parent = this;
    const bound = Object.create(parent);
    bound.request = (method, path, options = {}) =>
      parent.request(method, path, { ...options, signal: options.signal || signal });
    return bound;
  }

  get(path, options) {
    return this.request('GET', path, options);
  }

  post(path, body, options) {
    return this.request('POST', path, { ...options, body });
  }

  put(path, body, options) {
    return this.request('PUT', path, { ...options, body });
  }

  patch(path, body, options) {
    return this.request('PATCH', path, { ...options, body });
  }

  delete(path, options) {
    return this.request('DELETE', path, options);
  }

  /**
   * Send one request and resolve to a result (never rejects).
   *
   * @param {string} method
   * @param {string} path
   * @param {{body?: any, signal?: AbortSignal, ifMatch?: string, query?: object,
   *          headers?: object, keepalive?: boolean}} [options] `keepalive`: the request
   *          outlives the page (a goodbye sent while it closes)
   */
  async request(method, path, { body, signal, ifMatch, query, headers = {}, keepalive = false } = {}) {
    const url = this.base + path + queryString(query);
    const init = {
      method,
      credentials: 'same-origin',
      headers: { Accept: 'application/json', ...headers },
      signal,
    };
    if (keepalive) init.keepalive = true;
    const language = this.language();
    if (language) init.headers['Accept-Language'] = language;
    if (body !== undefined) {
      if (typeof FormData !== 'undefined' && body instanceof FormData) {
        init.body = body;
      } else {
        init.headers['Content-Type'] = 'application/json';
        init.body = JSON.stringify(body);
      }
    }
    if (UNSAFE.has(method)) {
      const token = this.csrfToken();
      if (token) init.headers[this.csrfHeader] = token;
    }
    if (ifMatch) init.headers['If-Match'] = ifMatch;
    if (signal && signal.aborted) return aborted();
    let response;
    this.sent += 1;
    try {
      response = await this.fetchImpl(url, init);
    } catch (cause) {
      if (signal && signal.aborted) return aborted();
      return { ok: false, kind: 'network', status: null, error: networkError({ method, path, cause }) };
    }
    let data = null;
    try {
      data = await readBody(response);
    } catch (cause) {
      if (signal && signal.aborted) return aborted();
      data = null;
    }
    if (signal && signal.aborted) return aborted();
    const etag = response.headers.get('ETag');
    if (response.ok) return { ok: true, status: response.status, data, etag };
    const error = errorFromResponse(data, {
      status: response.status,
      method,
      path,
      requestId: response.headers.get('X-Request-Id'),
    });
    if (response.status === 412) return { ok: false, kind: 'stale', status: 412, etag, error, data };
    return { ok: false, kind: 'http', status: response.status, error, data };
  }
}

function aborted() {
  return { ok: false, kind: 'aborted', status: null, error: null };
}

async function readBody(response) {
  if (response.status === 204) return null;
  const type = response.headers.get('Content-Type') || '';
  if (type.includes('json')) return response.json();
  // A file to offer (a zip made on demand): its bytes, for downloadFile().
  if (type.includes('zip') || type.includes('octet-stream')) return { blob: await response.blob() };
  const text = await response.text();
  return text ? { text } : null;
}

/** `?a=1&b=x` from an object (undefined and null values left out), or ''. */
export function queryString(query) {
  if (!query) return '';
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) value.forEach((v) => params.append(key, String(v)));
    else params.append(key, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : '';
}
