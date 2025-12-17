/**
 * Utility functions for the Finance App.
 *
 * Provides common formatting and helper functions used across the app.
 */

/**
 * Format a number as currency (USD)
 * @param {number} amount - The amount to format
 * @returns {string} Formatted currency string
 */
function formatMoney(amount) {
    if (amount === null || amount === undefined) return 'N/A';
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD'
    }).format(amount);
}

/**
 * Format a timestamp as relative time (e.g., "5m", "2h", "3d")
 * @param {string} timestamp - ISO timestamp string
 * @returns {string} Relative time string
 */
function formatTimeAgo(timestamp) {
    if (!timestamp) return '';
    const updateDate = new Date(timestamp);
    const now = new Date();
    const diffMs = now - updateDate;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return 'now';
    if (diffMins < 60) return `${diffMins}m`;
    if (diffHours < 24) return `${diffHours}h`;
    return `${diffDays}d`;
}

/**
 * Format a date string for display
 * @param {string} dateStr - Date string in any format
 * @returns {string} Formatted date string
 */
function formatDate(dateStr) {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    if (isNaN(date.getTime())) return dateStr;
    return date.toLocaleDateString('en-US', {
        month: 'numeric',
        day: 'numeric',
        year: 'numeric'
    });
}

/**
 * Format a number as percentage
 * @param {number} value - The value to format
 * @param {boolean} showSign - Whether to show + sign for positive values
 * @returns {string} Formatted percentage string
 */
function formatPercent(value, showSign = true) {
    if (value === null || value === undefined) return 'N/A';
    const sign = showSign && value > 0 ? '+' : '';
    return `${sign}${value.toFixed(1)}%`;
}

/**
 * Get CSS class for selloff rate severity
 * @param {number} rate - Selloff rate value
 * @returns {string} CSS class name
 */
function getSelloffRateClass(rate) {
    if (rate === null || rate === undefined) return '';
    if (rate >= 3.0) return 'rate-severe';
    if (rate >= 2.0) return 'rate-high';
    if (rate >= 1.5) return 'rate-moderate';
    return 'rate-normal';
}

/**
 * Get CSS class for gain/loss display
 * @param {number} value - Gain/loss value
 * @returns {string} CSS class name
 */
function getGainClass(value) {
    if (value === null || value === undefined) return '';
    if (value > 0) return 'positive';
    if (value < 0) return 'negative';
    return 'neutral';
}

/**
 * Get CSS class for valuation status
 * @param {number} priceVsValue - Price vs value percentage
 * @returns {string} CSS class name
 */
function getValuationClass(priceVsValue) {
    if (priceVsValue === null || priceVsValue === undefined) return '';
    if (priceVsValue <= -20) return 'undervalued';
    if (priceVsValue >= 20) return 'overvalued';
    return 'fair-value';
}

/**
 * Sanitize a string for safe HTML display
 * @param {string} str - String to sanitize
 * @returns {string} Sanitized string
 */
function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

/**
 * Debounce a function
 * @param {Function} func - Function to debounce
 * @param {number} wait - Wait time in milliseconds
 * @returns {Function} Debounced function
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

/**
 * Display the global last updated time in the header
 * @param {string} lastUpdated - ISO timestamp string
 */
function displayGlobalLastUpdated(lastUpdated) {
    const el = document.getElementById('global-last-updated');
    if (!el) return;

    if (!lastUpdated) {
        el.innerHTML = '<span class="update-warning">No price data</span>';
        return;
    }

    const updateDate = new Date(lastUpdated);
    const now = new Date();
    const diffMs = now - updateDate;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    let timeAgo;
    if (diffMins < 1) {
        timeAgo = 'just now';
    } else if (diffMins < 60) {
        timeAgo = `${diffMins}m ago`;
    } else if (diffHours < 24) {
        timeAgo = `${diffHours}h ago`;
    } else {
        timeAgo = `${diffDays}d ago`;
    }

    const isStale = diffHours >= 24;
    const statusClass = isStale ? 'update-stale' : 'update-fresh';

    el.innerHTML = `<span class="${statusClass}">Updated ${timeAgo}</span>`;
    el.title = `Last updated: ${updateDate.toLocaleString()}`;
}
