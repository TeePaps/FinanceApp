/**
 * Global state management for the Finance App.
 *
 * Provides centralized state that can be accessed across modules.
 */

// Global state object
const state = {
    // Stock list for autocomplete
    stocksList: [],

    // Polling intervals
    screenerInterval: null,
    secInterval: null,
    datasetsInterval: null,

    // Current selections
    currentIndex: 'all',
    currentTab: 'summary',

    // Screener state
    screenerData: [],
    screenerSort: { column: 'price_vs_value', direction: 'asc' },
    screenerFilters: {}
};

/**
 * Set the current index for screener
 * @param {string} indexName - Index name
 */
function setCurrentIndex(indexName) {
    state.currentIndex = indexName;
}

/**
 * Get the current index
 * @returns {string} Current index name
 */
function getCurrentIndex() {
    return state.currentIndex;
}

/**
 * Set screener polling interval
 * @param {number|null} interval - Interval ID or null to clear
 */
function setScreenerInterval(interval) {
    if (state.screenerInterval) {
        clearInterval(state.screenerInterval);
    }
    state.screenerInterval = interval;
}

/**
 * Set SEC polling interval
 * @param {number|null} interval - Interval ID or null to clear
 */
function setSecInterval(interval) {
    if (state.secInterval) {
        clearInterval(state.secInterval);
    }
    state.secInterval = interval;
}

/**
 * Set datasets polling interval
 * @param {number|null} interval - Interval ID or null to clear
 */
function setDatasetsInterval(interval) {
    if (state.datasetsInterval) {
        clearInterval(state.datasetsInterval);
    }
    state.datasetsInterval = interval;
}

/**
 * Clear all polling intervals
 */
function clearAllIntervals() {
    setScreenerInterval(null);
    setSecInterval(null);
    setDatasetsInterval(null);
}

/**
 * Update screener sort state
 * @param {string} column - Column to sort by
 */
function updateScreenerSort(column) {
    if (state.screenerSort.column === column) {
        state.screenerSort.direction = state.screenerSort.direction === 'asc' ? 'desc' : 'asc';
    } else {
        state.screenerSort.column = column;
        state.screenerSort.direction = 'asc';
    }
}

/**
 * Apply screener filter
 * @param {string} field - Field to filter
 * @param {any} value - Filter value
 */
function setScreenerFilter(field, value) {
    if (value === null || value === undefined || value === '') {
        delete state.screenerFilters[field];
    } else {
        state.screenerFilters[field] = value;
    }
}

/**
 * Clear all screener filters
 */
function clearScreenerFilters() {
    state.screenerFilters = {};
}
