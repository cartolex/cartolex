// SPDX-License-Identifier: MIT
/**
 * The registries extensions add to: pages, slots, facets and layers.
 *
 * - `pages`: routed screens `{id, route, label?, load?|module?|page?, order?,
 *   placement?: 'main'|'settings'|'hidden', area?}`. The manifest's nav
 *   entries are registered here too.
 * - `slots`: named insertion points a screen renders with `<Slot name>`, each
 *   holding ordered contributions `{id, order?, component}`. Known names:
 *   `overview.cards`, `corpus.sources`, `map.layers`, `share.cards`,
 *   `settings.sections`, `header.actions`; any other name works.
 * - `facets`: filters a list screen offers `{id, label, kind, …}`.
 * - `layers`: map layers `{id, label, kind, …}`.
 *
 * Every `add` returns a function that removes what it added. Each registry
 * has a `version` signal that changes on every change, so what renders a
 * registry's content re-renders when an extension adds to it.
 */
import { signal } from './preact.js';

export class Registry {
  /** @param {string} kind a name for messages ('page', 'facet'…) */
  constructor(kind) {
    this.kind = kind;
    this.items = new Map();
    this.version = signal(0);
  }

  /** Add an item with a unique `id`; returns its remover. */
  add(item) {
    if (!item || typeof item.id !== 'string' || !item.id) {
      throw new Error(`${this.kind}: an item needs an id`);
    }
    if (this.items.has(item.id)) throw new Error(`${this.kind} "${item.id}" is already registered`);
    this.items.set(item.id, item);
    this.version.value += 1;
    return () => {
      if (this.items.get(item.id) === item) {
        this.items.delete(item.id);
        this.version.value += 1;
      }
    };
  }

  /** The item with this id, or undefined. */
  get(id) {
    return this.items.get(id);
  }

  /** Every item, by `order` then id. Reading it inside a component subscribes to changes. */
  list() {
    void this.version.value;
    return [...this.items.values()].sort(byOrder);
  }
}

export class SlotRegistry {
  constructor() {
    this.slots = new Map();
    this.version = signal(0);
  }

  /** Add a contribution `{id, order?, component}` to the slot *name*; returns its remover. */
  add(name, contribution) {
    if (!contribution || !contribution.id || typeof contribution.component !== 'function') {
      throw new Error(`slot ${name}: a contribution needs an id and a component`);
    }
    if (!this.slots.has(name)) this.slots.set(name, new Map());
    const slot = this.slots.get(name);
    if (slot.has(contribution.id)) {
      throw new Error(`slot ${name}: "${contribution.id}" is already registered`);
    }
    slot.set(contribution.id, contribution);
    this.version.value += 1;
    return () => {
      if (slot.get(contribution.id) === contribution) {
        slot.delete(contribution.id);
        this.version.value += 1;
      }
    };
  }

  /** The contributions of the slot *name*, by `order` then id. */
  list(name) {
    void this.version.value;
    const slot = this.slots.get(name);
    return slot ? [...slot.values()].sort(byOrder) : [];
  }

  /** The names of every slot that has a contribution. */
  names() {
    void this.version.value;
    return [...this.slots.keys()].filter((n) => this.slots.get(n).size);
  }
}

function byOrder(a, b) {
  return (a.order ?? 1000) - (b.order ?? 1000) || String(a.id).localeCompare(String(b.id));
}

/** A fresh set of registries (one per app; tests make their own). */
export function createRegistries() {
  return {
    pages: new Registry('page'),
    slots: new SlotRegistry(),
    facets: new Registry('facet'),
    layers: new Registry('layer'),
  };
}
