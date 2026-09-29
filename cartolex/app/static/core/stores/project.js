// SPDX-License-Identifier: MIT
/**
 * The project's state: the six-state validity of every build stage, grouped
 * by area (corpus, keywords, themes, map, share…).
 *
 * The last state read is cached in the browser per project, so status dots
 * render at once when the app opens, then the store refreshes it from
 * `GET /api/project/state`. `fresh` says whether the state shown came from
 * the server during this visit.
 */
import { computed, signal } from '../preact.js';
import { summaryState } from '../states.js';

const CACHE_PREFIX = 'cartolex.state/1:';

/**
 * @param {object} options
 * @param {object} options.api the ApiClient
 * @param {string|null} options.projectId the open project's id (the cache key)
 * @param {Storage|null} [options.storage]
 */
export function createProjectStore({ api, projectId, storage = safeStorage() }) {
  const cacheKey = projectId ? CACHE_PREFIX + projectId : null;
  const state = signal(readCache(storage, cacheKey));
  const fresh = signal(false);
  const loading = signal(false);
  const error = signal(null);

  /** Stages by id. */
  const stages = computed(() => {
    const map = new Map();
    for (const stage of (state.value && state.value.stages) || []) map.set(stage.id, stage);
    return map;
  });

  /** Each area's summary state, from the API's `areas` or computed from its stages. */
  const areas = computed(() => {
    const out = new Map();
    const data = state.value;
    if (!data) return out;
    const list = data.areas || [];
    for (const area of list) {
      const own = (area.stages || []).map((id) => stages.value.get(id)).filter(Boolean);
      out.set(area.id, {
        ...area,
        state: area.state || summaryState(own.map((s) => s.state)),
        stageList: own,
      });
    }
    if (!list.length) {
      const grouped = new Map();
      for (const stage of data.stages || []) {
        const id = stage.area || stage.id.split('.')[0];
        if (!grouped.has(id)) grouped.set(id, []);
        grouped.get(id).push(stage);
      }
      for (const [id, own] of grouped) {
        out.set(id, { id, stages: own.map((s) => s.id), state: summaryState(own.map((s) => s.state)),
          stageList: own });
      }
    }
    return out;
  });

  let pending = null;
  /** Read the state from the server; concurrent calls share one request. */
  const refresh = () => {
    // No project open: there is no state to read (the start screen opens one).
    if (!projectId) return Promise.resolve({ ok: false, kind: 'no-project' });
    if (pending) return pending;
    loading.value = true;
    pending = api.get('/api/project/state').then((result) => {
      pending = null;
      loading.value = false;
      if (result.ok) {
        state.value = result.data;
        fresh.value = true;
        error.value = null;
        writeCache(storage, cacheKey, result.data);
      } else if (result.kind !== 'aborted') {
        error.value = result.error;
      }
      return result;
    });
    return pending;
  };

  return { state, stages, areas, fresh, loading, error, refresh };
}

function readCache(storage, key) {
  if (!storage || !key) return null;
  try {
    const raw = storage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writeCache(storage, key, data) {
  if (!storage || !key) return;
  try {
    storage.setItem(key, JSON.stringify(data));
  } catch {
    // The cache is a convenience; a full storage only means no instant dots next time.
  }
}

function safeStorage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}
