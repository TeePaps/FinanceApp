/**
 * API client for the Finance App.
 *
 * Centralizes all API calls with consistent error handling.
 */

const API_BASE = '/api';

/**
 * Make an API call with error handling
 * @param {string} endpoint - API endpoint
 * @param {Object} options - Fetch options
 * @returns {Promise<Object>} Response data
 */
async function apiCall(endpoint, options = {}) {
    const response = await fetch(`${API_BASE}${endpoint}`, options);
    if (!response.ok) {
        const error = await response.json().catch(() => ({ error: 'Unknown error' }));
        throw new Error(error.error || `API error: ${response.status}`);
    }
    return response.json();
}

/**
 * Make a POST API call
 * @param {string} endpoint - API endpoint
 * @param {Object} data - Request body data
 * @returns {Promise<Object>} Response data
 */
async function apiPost(endpoint, data = {}) {
    return apiCall(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
}

/**
 * Make a PUT API call
 * @param {string} endpoint - API endpoint
 * @param {Object} data - Request body data
 * @returns {Promise<Object>} Response data
 */
async function apiPut(endpoint, data) {
    return apiCall(endpoint, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
}

/**
 * Make a DELETE API call
 * @param {string} endpoint - API endpoint
 * @returns {Promise<Object>} Response data
 */
async function apiDelete(endpoint) {
    return apiCall(endpoint, { method: 'DELETE' });
}

// API object with all endpoints
const api = {
    // Holdings
    getHoldings: () => apiCall('/holdings'),
    getHoldingsAnalysis: () => apiCall('/holdings-analysis'),

    // Transactions
    getTransactions: () => apiCall('/transactions'),
    createTransaction: (data) => apiPost('/transactions', data),
    updateTransaction: (id, data) => apiPut(`/transactions/${id}`, data),
    deleteTransaction: (id) => apiDelete(`/transactions/${id}`),

    // Stocks
    getStocks: () => apiCall('/stocks'),
    addStock: (data) => apiPost('/stocks', data),

    // Summary & Prices
    getSummary: () => apiCall('/summary'),
    getPrices: () => apiCall('/prices'),
    getPerformance: () => apiCall('/performance'),
    getProfitTimeline: (start, end) => {
        let url = '/profit-timeline?';
        if (start) url += `start=${start}&`;
        if (end) url += `end=${end}`;
        return apiCall(url);
    },

    // Screener
    getIndices: () => apiCall('/indices'),
    getScreener: (index = 'all') => apiCall(`/screener?index=${index}`),
    getScreenerProgress: () => apiCall('/screener/progress'),
    startScreener: (index) => apiPost('/screener/start', { index }),
    quickUpdatePrices: (index) => apiPost('/screener/quick-update', { index }),
    smartUpdate: (index) => apiPost('/screener/smart-update', { index }),
    stopScreener: () => apiPost('/screener/stop'),
    updateDividends: (index) => apiPost('/screener/update-dividends', { index }),

    // Recommendations
    getRecommendations: () => apiCall('/recommendations'),

    // Valuation
    getValuation: (ticker) => apiCall(`/valuation/${ticker}`),
    refreshValuation: (ticker) => apiPost(`/valuation/${ticker}/refresh`),
    getSecMetrics: (ticker) => apiCall(`/sec-metrics/${ticker}`),

    // Data Status
    getDataStatus: () => apiCall('/data-status'),
    getEpsRecommendations: () => apiCall('/eps-recommendations'),
    getExcludedTickers: () => apiCall('/excluded-tickers'),
    clearExcludedTickers: () => apiPost('/excluded-tickers/clear'),

    // Refresh
    globalRefresh: () => apiPost('/refresh'),

    // SEC
    getSecStatus: () => apiCall('/sec/status'),
    getSecProgress: () => apiCall('/sec/progress'),
    startSecUpdate: (tickers) => apiPost('/sec/update', { tickers }),
    stopSecUpdate: () => apiPost('/sec/stop'),
    getSecEps: (ticker) => apiCall(`/sec/eps/${ticker}`),
    compareSecEps: (ticker) => apiCall(`/sec/compare/${ticker}`)
};
