// SPDX-License-Identifier: MIT
/**
 * Full screen for a part of a page (the atlas's body, the theme editor's centre): the
 * browser's Fullscreen API when it allows it, else the part fixed over the whole window.
 * Escape leaves it (the browser's own key in true full screen; ours in the fallback, unless
 * a dialog or a menu is open), and a button inside the part does too. Leaving the page
 * leaves full screen.
 */
import { html, useEffect, useRef, useState } from '../../core/preact.js';
import { t } from '../../core/i18n.js';
import { IconButton } from '../../components/index.js';

/**
 * @returns {{ref: {current: HTMLElement|null}, on: boolean, enter: Function, leave: Function,
 *   toggle: Function, className: string}} put `ref` on the part and `className` on its class.
 */
export function useFullscreen() {
  const ref = useRef(null);
  const [mode, setMode] = useState(''); // '' | 'native' | 'fixed'
  useEffect(() => {
    const onChange = () => {
      const el = ref.current;
      if (document.fullscreenElement && document.fullscreenElement === el) setMode('native');
      else setMode((m) => (m === 'native' ? '' : m));
    };
    document.addEventListener('fullscreenchange', onChange);
    return () => {
      document.removeEventListener('fullscreenchange', onChange);
      if (document.fullscreenElement && document.fullscreenElement === ref.current) {
        document.exitFullscreen().catch(() => {});
      }
    };
  }, []);
  // The fallback: Escape leaves it, unless a dialog or a menu is open (they take Escape first).
  useEffect(() => {
    if (mode !== 'fixed') return undefined;
    const onKey = (event) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      if (document.querySelector('dialog[open], [role=menu], [role=listbox]:not([hidden])')) return;
      setMode('');
    };
    document.addEventListener('keydown', onKey);
    document.documentElement.classList.add('cx-has-fullscreen');
    return () => {
      document.removeEventListener('keydown', onKey);
      document.documentElement.classList.remove('cx-has-fullscreen');
    };
  }, [mode]);
  const enter = () => {
    const el = ref.current;
    if (!el) return;
    if (document.fullscreenEnabled && el.requestFullscreen) {
      el.requestFullscreen().then(() => setMode('native')).catch(() => setMode('fixed'));
    } else setMode('fixed');
  };
  const leave = () => {
    if (mode === 'native' && document.fullscreenElement) document.exitFullscreen().catch(() => {});
    setMode('');
  };
  return {
    ref,
    on: mode !== '',
    enter,
    leave,
    toggle: () => (mode ? leave() : enter()),
    className: mode ? `is-fullscreen is-fullscreen--${mode}` : '',
  };
}

/** The button that enters full screen, or leaves it (it stays inside the part). */
export function FullscreenButton({ fs, size = 's' }) {
  return html`<${IconButton} icon=${fs.on ? 'shrink' : 'expand'} size=${size}
    label=${fs.on ? t('fullscreen.leave') : t('fullscreen.enter')} onClick=${fs.toggle} />`;
}
