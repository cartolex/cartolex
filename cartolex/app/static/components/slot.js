// SPDX-License-Identifier: MIT
/**
 * Slot: renders what extensions contributed to a named insertion point, each
 * contribution inside its own error boundary: a contribution that throws
 * shows a compact ErrorCard, and the rest of the screen keeps working.
 */
import { Component, html } from '../core/preact.js';
import { errorFromException } from '../core/errors.js';
import { ErrorCard } from './error-card.js';

/** Catches a render error of its children and shows an ErrorCard instead. */
export class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  componentDidCatch(error) {
    this.setState({ error: errorFromException(error, { code: 'component_failed' }) });
  }

  render({ children }, { error }) {
    if (error) {
      return html`<${ErrorCard} error=${error} compact
        onRetry=${() => this.setState({ error: null })} />`;
    }
    return children;
  }
}

/**
 * @param {object} props
 * @param {object} props.slots the slot registry
 * @param {string} props.name the slot's name (`overview.cards`)
 * @param {object} [props.context] props given to every contribution
 * @param {string} [props.class] a class for the wrapper
 */
export function Slot({ slots, name, context = {}, class: cls = '' }) {
  const items = slots.list(name);
  if (!items.length) return null;
  return html`<div class=${`cx-slot ${cls}`} data-slot=${name}>
    ${items.map((item) => html`<${ErrorBoundary} key=${item.id}>
      <${item.component} ...${context} />
    <//>`)}
  </div>`;
}
