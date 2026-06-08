// static/js/storage.js
// Centralized localStorage access with key constants and JSON parse safety

const STORAGE_MIGRATION_KEY = 'neatai-storage-migrated-v1';

/** One-time migration from odysseus-* localStorage keys to neatai-*. */
function migrateLegacyStorage() {
  try {
    if (localStorage.getItem(STORAGE_MIGRATION_KEY)) return;
    const toRemove = [];
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (!key) continue;
      let newKey = null;
      if (key.startsWith('odysseus-')) {
        newKey = 'neatai-' + key.slice('odysseus-'.length);
      } else if (key.startsWith('odysseus.')) {
        newKey = 'neatai.' + key.slice('odysseus.'.length);
      }
      if (newKey && localStorage.getItem(newKey) === null) {
        localStorage.setItem(newKey, localStorage.getItem(key));
      }
      if (newKey) toRemove.push(key);
    }
    toRemove.forEach((key) => localStorage.removeItem(key));
    localStorage.setItem(STORAGE_MIGRATION_KEY, '1');
  } catch (e) {
    console.warn('[Storage] Legacy key migration failed:', e.message);
  }
}

migrateLegacyStorage();

// ── Key constants ──
export const KEYS = {
  THEME: 'neatai-theme',
  TOGGLES: 'neatai-toggles',
  SIDEBAR_COLLAPSED: 'sidebar-collapsed',
  SIDEBAR_WIDTH: 'sidebar-width',
  SIDEBAR_SIDE: 'sidebar-side',
  CURRENT_SESSION: 'currentSessionId',
  COMPARE_SAVE: 'compare-save-results',
  COMPARE_CHAT: 'compare-continue-chat',
  COMPARE_BLIND: 'compare-blind',
  COMPARE_RANDOM: 'compare-randomize',
  MODELS_EXPANDED: 'neatai-model-expanded',
  MODEL_ENDPOINTS: 'neatai-model-endpoints',
  MODEL_SELECTED: 'neatai-selected-model',
  SORT_ORDER: 'neatai-sessions-sort',
  CHAT_SEARCH_SCOPE: 'neatai-search-scope',
  INCOGNITO: 'neatai-incognito',
  RAG_ACTIVE: 'neatai-rag-active',
  MCP_ACTIVE: 'neatai-mcp-active',
  SECTION_ORDER: 'sidebar-section-order',
  ADMIN_LAST_TAB: 'admin-last-tab',
  DENSITY: 'neatai-density'
};

/**
 * Safely get and parse a JSON value from localStorage.
 * Returns fallback on any error.
 */
export function getJSON(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return fallback !== undefined ? fallback : null;
    return JSON.parse(raw);
  } catch (e) {
    console.warn('[Storage] Failed to parse key "' + key + '":', e.message);
    return fallback !== undefined ? fallback : null;
  }
}

/**
 * Set a JSON-serialized value in localStorage.
 */
export function setJSON(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (e) {
    console.warn('[Storage] Failed to set key "' + key + '":', e.message);
  }
}

/**
 * Get a raw string value from localStorage.
 */
export function get(key, fallback) {
  try {
    const val = localStorage.getItem(key);
    return val !== null ? val : (fallback !== undefined ? fallback : null);
  } catch (e) {
    return fallback !== undefined ? fallback : null;
  }
}

/**
 * Set a raw string value in localStorage.
 */
export function set(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (e) {
    console.warn('[Storage] Failed to set key "' + key + '":', e.message);
  }
}

/**
 * Remove a key from localStorage.
 */
export function remove(key) {
  try {
    localStorage.removeItem(key);
  } catch (e) {
    // Ignore removal errors
  }
}

// ── Toggle state helpers ──

export function loadToggleState() {
  return getJSON(KEYS.TOGGLES, {});
}

export function saveToggleState(state) {
  setJSON(KEYS.TOGGLES, state);
}

export function getToggle(name, fallback) {
  const state = loadToggleState();
  return state[name] !== undefined ? state[name] : (fallback !== undefined ? fallback : false);
}

export function setToggle(name, value) {
  const state = loadToggleState();
  state[name] = value;
  saveToggleState(state);
}

const Storage = {
  KEYS,
  getJSON,
  setJSON,
  get,
  set,
  remove,
  loadToggleState,
  saveToggleState,
  getToggle,
  setToggle
};

export default Storage;
