// SPDX-License-Identifier: MIT
/**
 * The one door to the vendored libraries: Preact, its hooks, signals and htm.
 *
 * Every module of the interface imports from here, never from `vendor/`
 * directly, so a library update touches one file. `html` is htm bound to
 * Preact's `h`: templates are tagged template literals, parsed at run time
 * without `eval` (the Content-Security-Policy forbids it).
 */
import {
  Component,
  Fragment,
  cloneElement,
  createContext,
  createRef,
  h,
  isValidElement,
  options,
  render,
  toChildArray,
} from '../vendor/preact/preact.module.js';
import {
  useCallback,
  useContext,
  useEffect,
  useErrorBoundary,
  useId,
  useLayoutEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from '../vendor/preact/hooks.module.js';
import {
  Signal,
  batch,
  computed,
  effect,
  signal,
  untracked,
  useComputed,
  useSignal,
  useSignalEffect,
} from '../vendor/signals/signals.module.js';
import htm from '../vendor/htm/htm.module.js';

/** Tagged template for Preact elements: html`<p class="x">${text}</p>`. */
export const html = htm.bind(h);

export {
  Component,
  Fragment,
  Signal,
  batch,
  cloneElement,
  computed,
  createContext,
  createRef,
  effect,
  h,
  isValidElement,
  options,
  render,
  signal,
  toChildArray,
  untracked,
  useCallback,
  useComputed,
  useContext,
  useEffect,
  useErrorBoundary,
  useId,
  useLayoutEffect,
  useMemo,
  useReducer,
  useRef,
  useSignal,
  useSignalEffect,
  useState,
};
