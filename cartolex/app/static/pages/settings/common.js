/**
 * What the settings' sections share: loading a resource with its version, a
 * section's frame, a status line, and the words of a refusal.
 */

import { html, useEffect, useState } from '../../core/preact.js';
import { has, t } from '../../core/i18n.js';
import { Card, ErrorCard, Icon } from '../../components/index.js';

/**
 * Read *path* on mount (and on `reload()`): `{data, etag, error, loading, reload, set}`;
 * nothing when *path* is null.
 * @param {object} api the page's API client
 * @param {string} path the route to read
 */
export function useResource(api, path) {
  const [state, setState] = useState({ data: null, etag: null, error: null, loading: true });
  const load = async () => {
    if (!path) return;
    setState((s) => ({ ...s, loading: true }));
    const result = await api.get(path);
    setState(result.ok ? { data: result.data, etag: result.etag, error: null, loading: false }
      : { data: null, etag: null, error: result.error, loading: false });
  };
  useEffect(() => {
    load();
  }, [path]);
  return {
    ...state,
    reload: load,
    set: (data, etag) => setState({ data, etag: etag || state.etag, error: null, loading: false }),
  };
}

/** A card of a section, with its loading and error states. */
export function Block({ title, resource = null, actions = null, children, class: cls = '' }) {
  if (resource && resource.error) {
    return html`<${Card} title=${title} level=${2} class=${cls}>
      <${ErrorCard} error=${resource.error} compact onRetry=${() => resource.reload()} /><//>`;
  }
  return html`<${Card} title=${title} level=${2} actions=${actions} class=${cls}
    loading=${Boolean(resource && !resource.data)}>${resource && !resource.data ? null : children}<//>`;
}

/** A state in words with its shape: done (a check), not done (a dash), a warning. */
export function State({ kind, children }) {
  const icon = kind === 'ok' ? 'check' : kind === 'warning' ? 'warning' : 'dash';
  return html`<span class=${`cx-settings-state cx-settings-state--${kind}`}>
    <${Icon} name=${icon} /><span>${children}</span></span>`;
}

/** The words of a refused request. */
export function refusal(error) {
  if (!error) return t('settings.failed');
  const key = `error.${error.code}.message`;
  if (error.code && has(key)) return t(key, error.params || {});
  return error.message || t('settings.failed');
}

/** The language codes a project may use, with their names in the interface language. */
export function languageLabel(code) {
  try {
    const name = new Intl.DisplayNames([document.documentElement.lang || 'en'], { type: 'language' }).of(code);
    return name ? `${name} (${code})` : code;
  } catch {
    return code;
  }
}
