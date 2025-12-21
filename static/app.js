let stocksList = [];

// Ticker autocomplete cache
let tickerCache = [];
let autocompleteSelectedIndex = -1;

// EPS Source name formatting utilities
// Centralizes source name display to avoid scattered hardcoded strings
const EPS_SOURCE_LABELS = {
    'sec': 'SEC EDGAR',
    'sec_edgar': 'SEC EDGAR',
    'yfinance': 'Yahoo Finance',
    'defeatbeta': 'DefeatBeta',
    'fmp': 'FMP'
};

const EPS_SOURCE_SHORT_LABELS = {
    'sec': 'SEC',
    'sec_edgar': 'SEC',
    'yfinance': 'YF',
    'defeatbeta': 'DB',
    'fmp': 'FMP'
};

/**
 * Get human-readable label for an EPS source.
 * @param {string} source - Source identifier (e.g., 'sec', 'yfinance')
 * @param {boolean} short - If true, return abbreviated label
 * @returns {string} Human-readable source label
 */
function formatEpsSource(source, short = false) {
    if (!source) return '-';
    const labels = short ? EPS_SOURCE_SHORT_LABELS : EPS_SOURCE_LABELS;
    return labels[source] || source;
}

// Collapsible section toggle
function toggleCollapsible(sectionId) {
    const section = document.getElementById(sectionId);
    if (section) {
        section.classList.toggle('collapsed');
    }
}

// 10-K Filings Functions
async function load10KFilings(ticker) {
    const dropdown = document.getElementById('tenk-filings-dropdown');
    if (!dropdown) return;

    try {
        const response = await fetch(`/api/sec-filings/${ticker}`);
        const filings = await response.json();

        if (filings && filings.length > 0) {
            dropdown.innerHTML = '<option value="">View 10-K...</option>';
            for (const filing of filings) {
                const label = `FY${filing.fiscal_year}${filing.form_type === '10-K/A' ? ' (Amended)' : ''}`;
                dropdown.innerHTML += `<option value="${filing.document_url}">${label}</option>`;
            }
            dropdown.disabled = false;
        } else {
            dropdown.innerHTML = '<option value="">No 10-K filings</option>';
        }
    } catch (error) {
        console.error('Error loading 10-K filings:', error);
        dropdown.innerHTML = '<option value="">Error loading</option>';
    }
}

function openTenKFiling(url) {
    if (url) {
        window.open(url, '_blank');
        // Reset dropdown after opening
        const dropdown = document.getElementById('tenk-filings-dropdown');
        if (dropdown) dropdown.selectedIndex = 0;
    }
}

// Fuzzy search for ticker autocomplete
function fuzzySearchTickers(query, maxResults = 10) {
    if (!query || query.length < 1) return [];

    const q = query.toLowerCase();
    const results = [];

    for (const item of tickerCache) {
        const ticker = item.ticker.toLowerCase();
        const name = (item.company_name || '').toLowerCase();

        // Exact ticker match - highest priority
        if (ticker === q) {
            results.push({ ...item, score: 100, matchType: 'exact' });
            continue;
        }

        // Ticker starts with query - high priority
        if (ticker.startsWith(q)) {
            results.push({ ...item, score: 90 - ticker.length, matchType: 'ticker-start' });
            continue;
        }

        // Ticker contains query
        if (ticker.includes(q)) {
            results.push({ ...item, score: 70, matchType: 'ticker-contains' });
            continue;
        }

        // Company name starts with query word
        const nameWords = name.split(/\s+/);
        const startsWithWord = nameWords.some(word => word.startsWith(q));
        if (startsWithWord) {
            results.push({ ...item, score: 60, matchType: 'name-word' });
            continue;
        }

        // Company name contains query
        if (name.includes(q)) {
            results.push({ ...item, score: 40, matchType: 'name-contains' });
            continue;
        }
    }

    // Sort by score descending, then by ticker alphabetically
    results.sort((a, b) => {
        if (b.score !== a.score) return b.score - a.score;
        return a.ticker.localeCompare(b.ticker);
    });

    return results.slice(0, maxResults);
}

// Initialize ticker autocomplete
function initTickerAutocomplete() {
    const input = document.getElementById('research-ticker');
    const dropdown = document.getElementById('ticker-autocomplete');
    if (!input || !dropdown) return;

    // Fetch ticker list for autocomplete
    fetch('/api/all-tickers')
        .then(res => res.json())
        .then(data => {
            tickerCache = data.tickers || [];
        })
        .catch(err => console.error('Error loading tickers for autocomplete:', err));

    // Input event - show suggestions
    input.addEventListener('input', function() {
        const query = this.value.trim();
        autocompleteSelectedIndex = -1;

        if (query.length < 1) {
            dropdown.style.display = 'none';
            dropdown.innerHTML = '';
            return;
        }

        const results = fuzzySearchTickers(query);

        if (results.length === 0) {
            dropdown.style.display = 'none';
            dropdown.innerHTML = '';
            return;
        }

        dropdown.innerHTML = results.map((item, idx) => `
            <div class="autocomplete-item" data-ticker="${item.ticker}" data-index="${idx}">
                <span class="autocomplete-ticker">${highlightMatch(item.ticker, query)}</span>
                <span class="autocomplete-name">${highlightMatch(item.company_name || '', query)}</span>
            </div>
        `).join('');

        dropdown.style.display = 'block';

        // Add click handlers
        dropdown.querySelectorAll('.autocomplete-item').forEach(item => {
            item.addEventListener('click', function() {
                selectAutocompleteItem(this.dataset.ticker);
            });
        });
    });

    // Keyboard navigation
    input.addEventListener('keydown', function(e) {
        const items = dropdown.querySelectorAll('.autocomplete-item');
        if (items.length === 0) return;

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            autocompleteSelectedIndex = Math.min(autocompleteSelectedIndex + 1, items.length - 1);
            updateAutocompleteSelection(items);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            autocompleteSelectedIndex = Math.max(autocompleteSelectedIndex - 1, -1);
            updateAutocompleteSelection(items);
        } else if (e.key === 'Enter' && autocompleteSelectedIndex >= 0) {
            e.preventDefault();
            const selectedItem = items[autocompleteSelectedIndex];
            if (selectedItem) {
                selectAutocompleteItem(selectedItem.dataset.ticker);
            }
        } else if (e.key === 'Escape') {
            dropdown.style.display = 'none';
            autocompleteSelectedIndex = -1;
        }
    });

    // Close dropdown when clicking outside
    document.addEventListener('click', function(e) {
        if (!input.contains(e.target) && !dropdown.contains(e.target)) {
            dropdown.style.display = 'none';
            autocompleteSelectedIndex = -1;
        }
    });
}

function highlightMatch(text, query) {
    if (!text || !query) return text;
    const regex = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
    return text.replace(regex, '<mark>$1</mark>');
}

function updateAutocompleteSelection(items) {
    items.forEach((item, idx) => {
        item.classList.toggle('selected', idx === autocompleteSelectedIndex);
    });
    // Scroll selected item into view
    if (autocompleteSelectedIndex >= 0 && items[autocompleteSelectedIndex]) {
        items[autocompleteSelectedIndex].scrollIntoView({ block: 'nearest' });
    }
}

function selectAutocompleteItem(ticker) {
    const input = document.getElementById('research-ticker');
    const dropdown = document.getElementById('ticker-autocomplete');

    input.value = ticker;
    dropdown.style.display = 'none';
    dropdown.innerHTML = '';
    autocompleteSelectedIndex = -1;

    // Automatically run the valuation
    runValuation();
}

document.addEventListener('DOMContentLoaded', function() {
    loadStocks();
    loadHoldings();
    loadSummary();
    setupForms();
    initTheme();
    restoreTabFromHash();
    loadGlobalLastUpdated();
    initTickerAutocomplete();
    loadIndices();  // Populate index dropdowns from API
    updateAllPricesCount();  // Update enabled ticker count for All Prices button
});

// Handle browser back/forward buttons
window.addEventListener('hashchange', function() {
    restoreTabFromHash();
});

// Load and display global last updated time in header
async function loadGlobalLastUpdated() {
    try {
        const response = await fetch('/api/holdings-analysis');
        const data = await response.json();
        displayGlobalLastUpdated(data.last_updated);
    } catch (error) {
        console.error('Error loading last updated:', error);
    }
}

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

// Format timestamp as relative time
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

// Restore tab from URL hash on page load/refresh
function restoreTabFromHash() {
    const hash = window.location.hash.slice(1); // Remove the #
    const validTabs = ['summary', 'holdings', 'profit', 'add', 'research', 'screener', 'recommendations', 'datasets'];
    if (hash && validTabs.includes(hash)) {
        showTab(hash);
    }
}

// Theme toggle
function initTheme() {
    const savedTheme = localStorage.getItem('theme') || 'light';
    document.body.setAttribute('data-theme', savedTheme);
    updateThemeIcon(savedTheme);
}

function toggleTheme() {
    const currentTheme = document.body.getAttribute('data-theme') || 'light';
    const newTheme = currentTheme === 'light' ? 'dark' : 'light';
    document.body.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);
    updateThemeIcon(newTheme);
}

function updateThemeIcon(theme) {
    const icon = document.querySelector('.theme-icon');
    if (icon) {
        icon.textContent = theme === 'dark' ? '☀️' : '🌙';
    }
}

// Tab navigation
function showTab(tabName) {
    // Hide all tabs
    document.querySelectorAll('.tab-content').forEach(tab => {
        tab.classList.remove('active');
    });
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.remove('active');
    });

    // Show selected tab
    document.getElementById(`${tabName}-tab`).classList.add('active');

    // Check if this is a portfolio sub-tab
    const portfolioTabs = ['profit', 'holdings', 'add'];
    if (portfolioTabs.includes(tabName)) {
        // Activate the dropdown button
        const dropdownBtn = document.querySelector('.tab-dropdown-btn');
        if (dropdownBtn) {
            dropdownBtn.classList.add('active');
        }
    } else {
        // Find and activate the correct button
        const tabBtn = document.querySelector(`.tab-btn[onclick*="'${tabName}'"]`);
        if (tabBtn) {
            tabBtn.classList.add('active');
        }
    }

    // Update URL hash for refresh persistence
    window.location.hash = tabName;

    // Refresh data when switching tabs
    if (tabName === 'summary') {
        loadSummary();
    } else if (tabName === 'holdings') {
        loadHoldings();
    } else if (tabName === 'profit') {
        loadProfitTimeline();
    } else if (tabName === 'screener') {
        loadScreener();
    } else if (tabName === 'recommendations') {
        loadRecommendations();
    } else if (tabName === 'datasets') {
        loadDatasets();
    } else if (tabName === 'settings') {
        loadProviderSettings();
    }
}

// Tab dropdown functions
function toggleTabDropdown(event) {
    event.stopPropagation();
    const dropdown = document.getElementById('portfolio-dropdown');
    dropdown.classList.toggle('show');
}

function closeTabDropdown() {
    const dropdown = document.getElementById('portfolio-dropdown');
    if (dropdown) {
        dropdown.classList.remove('show');
    }
}

// Close dropdown when clicking outside
document.addEventListener('click', function(event) {
    if (!event.target.closest('.tab-dropdown')) {
        closeTabDropdown();
    }
});

// Profit Timeline
function setProfitRange(range) {
    const startInput = document.getElementById('profit-start-date');
    const endInput = document.getElementById('profit-end-date');
    const today = new Date();

    switch(range) {
        case 'ytd':
            startInput.value = `${today.getFullYear()}-01-01`;
            endInput.value = '';
            break;
        case '2024':
            startInput.value = '2024-01-01';
            endInput.value = '2024-12-31';
            break;
        case '2023':
            startInput.value = '2023-01-01';
            endInput.value = '2023-12-31';
            break;
        case 'all':
            startInput.value = '';
            endInput.value = '';
            break;
    }
    loadProfitTimeline();
}

async function loadProfitTimeline() {
    const startDate = document.getElementById('profit-start-date').value;
    const endDate = document.getElementById('profit-end-date').value;

    let url = '/api/profit-timeline?';
    if (startDate) url += `start=${startDate}&`;
    if (endDate) url += `end=${endDate}`;

    try {
        const response = await fetch(url);
        const data = await response.json();
        renderProfitTimeline(data);
    } catch (error) {
        console.error('Error loading profit timeline:', error);
    }
}

function renderProfitTimeline(data) {
    const totals = data.totals;
    const dateRange = data.date_range;

    // Totals
    const rangeLabel = dateRange.start === 'all time' ? 'All Time' :
        `${dateRange.start} to ${dateRange.end === 'now' ? 'Now' : dateRange.end}`;

    document.getElementById('profit-totals').innerHTML = `
        <div class="summary-card realized-profit ${totals.profit >= 0 ? 'positive' : 'negative'}">
            <div class="summary-label">Realized Profit (${rangeLabel})</div>
            <div class="summary-value">${formatMoney(totals.profit)}</div>
        </div>
        <div class="summary-card">
            <div class="summary-label">Total Revenue</div>
            <div class="summary-value">${formatMoney(totals.revenue)}</div>
        </div>
        <div class="summary-card">
            <div class="summary-label">Completed Sales</div>
            <div class="summary-value">${totals.sales_count}</div>
        </div>
    `;

    // By Month chart/table
    if (data.by_month.length > 0) {
        let monthHtml = '<h3>Profit by Month</h3><div class="month-chart">';
        const maxProfit = Math.max(...data.by_month.map(m => Math.abs(m.profit)));

        for (const month of data.by_month) {
            const barWidth = maxProfit > 0 ? (Math.abs(month.profit) / maxProfit * 100) : 0;
            const barClass = month.profit >= 0 ? 'bar-positive' : 'bar-negative';
            const monthLabel = new Date(month.month + '-01').toLocaleDateString('en-US', { month: 'short', year: 'numeric' });

            monthHtml += `
                <div class="month-row">
                    <span class="month-label">${monthLabel}</span>
                    <div class="month-bar-container">
                        <div class="month-bar ${barClass}" style="width: ${barWidth}%"></div>
                    </div>
                    <span class="month-value ${month.profit >= 0 ? 'gain-positive' : 'gain-negative'}">${formatMoney(month.profit)}</span>
                </div>
            `;
        }
        monthHtml += '</div>';
        document.getElementById('profit-by-month').innerHTML = monthHtml;
    } else {
        document.getElementById('profit-by-month').innerHTML = '';
    }

    // By Ticker
    if (data.by_ticker.length > 0) {
        let tickerHtml = `
            <h3>Profit by Stock</h3>
            <table class="summary-table">
                <thead>
                    <tr>
                        <th>Ticker</th>
                        <th>Name</th>
                        <th>Shares Sold</th>
                        <th>Revenue</th>
                        <th>Profit</th>
                    </tr>
                </thead>
                <tbody>
        `;

        for (const ticker of data.by_ticker) {
            const profitClass = ticker.profit >= 0 ? 'gain-positive' : 'gain-negative';
            tickerHtml += `
                <tr>
                    <td class="stock-ticker">${ticker.ticker}</td>
                    <td>${ticker.name}</td>
                    <td>${ticker.shares_sold}</td>
                    <td>${formatMoney(ticker.revenue)}</td>
                    <td class="${profitClass}">${formatMoney(ticker.profit)}</td>
                </tr>
            `;
        }

        tickerHtml += '</tbody></table>';
        document.getElementById('profit-by-ticker').innerHTML = tickerHtml;
    } else {
        document.getElementById('profit-by-ticker').innerHTML = '<p class="empty-state">No completed sales in this period</p>';
    }

    // Sales list
    if (data.sales.length > 0) {
        let salesHtml = `
            <h3>Individual Sales</h3>
            <table class="summary-table">
                <thead>
                    <tr>
                        <th>Date</th>
                        <th>Ticker</th>
                        <th>Shares</th>
                        <th>Price</th>
                        <th>Profit</th>
                    </tr>
                </thead>
                <tbody>
        `;

        for (const sale of data.sales) {
            const profitClass = sale.profit >= 0 ? 'gain-positive' : 'gain-negative';
            const dateDisplay = new Date(sale.date).toLocaleDateString('en-US', { month: 'numeric', day: 'numeric', year: 'numeric' });
            salesHtml += `
                <tr>
                    <td>${dateDisplay}</td>
                    <td class="stock-ticker">${sale.ticker}</td>
                    <td>${sale.shares}</td>
                    <td>${formatMoney(sale.price)}</td>
                    <td class="${profitClass}">${formatMoney(sale.profit)}</td>
                </tr>
            `;
        }

        salesHtml += '</tbody></table>';
        document.getElementById('profit-sales-list').innerHTML = salesHtml;
    } else {
        document.getElementById('profit-sales-list').innerHTML = '';
    }
}

// Summary
async function loadSummary() {
    try {
        const [summaryRes, performanceRes] = await Promise.all([
            fetch('/api/summary'),
            fetch('/api/performance')
        ]);
        const summaryData = await summaryRes.json();
        const performanceData = await performanceRes.json();
        renderSummary(summaryData);
        renderPerformance(performanceData);
        loadPrices();
    } catch (error) {
        console.error('Error loading summary:', error);
    }
}

async function loadPrices() {
    const marketTotals = document.getElementById('market-totals');
    const marketPrices = document.getElementById('market-prices');

    // Show loading state
    marketTotals.innerHTML = '<div class="summary-card"><div class="summary-label">Loading prices...</div></div>';

    try {
        const response = await fetch('/api/prices');
        const data = await response.json();
        renderMarketData(data);
    } catch (error) {
        console.error('Error loading prices:', error);
        marketTotals.innerHTML = '<div class="summary-card"><div class="summary-label">Error loading prices</div></div>';
        marketPrices.innerHTML = '';
    }
}

function renderMarketData(data) {
    const totals = data.totals;
    const prices = data.prices;

    // Render totals
    const gainClass = totals.unrealized_gain >= 0 ? 'positive' : 'negative';
    document.getElementById('market-totals').innerHTML = `
        <div class="summary-card market-value">
            <div class="summary-label">Current Market Value</div>
            <div class="summary-value market-price">${formatMoney(totals.current_value)}</div>
        </div>
        <div class="summary-card ${gainClass}">
            <div class="summary-label">Profit/Loss</div>
            <div class="summary-value">${formatMoney(totals.unrealized_gain)}</div>
            <div class="summary-sub ${gainClass}">${totals.unrealized_pct >= 0 ? '+' : ''}${totals.unrealized_pct}%</div>
        </div>
        <div class="summary-card total-invested">
            <div class="summary-label">Total Invested</div>
            <div class="summary-value">${formatMoney(totals.cost_basis)}</div>
        </div>
    `;

    // Render individual stock prices
    const tickers = Object.keys(prices).sort();
    if (tickers.length === 0) {
        document.getElementById('market-prices').innerHTML = '<p class="empty-state">No holdings with prices available</p>';
        return;
    }

    let html = `
        <table class="summary-table">
            <thead>
                <tr>
                    <th>Ticker</th>
                    <th>Name</th>
                    <th>Price</th>
                    <th>Shares</th>
                    <th>Market Value</th>
                    <th>Total Invested</th>
                    <th>Profit/Loss</th>
                    <th>Updated</th>
                </tr>
            </thead>
            <tbody>
    `;

    for (const ticker of tickers) {
        const p = prices[ticker];
        const gainClass = p.unrealized_gain >= 0 ? 'gain-positive' : 'gain-negative';
        const updatedText = p.updated ? formatTimeAgo(p.updated) : '-';
        const updatedTitle = p.updated ? new Date(p.updated).toLocaleString() : '';
        html += `
            <tr>
                <td class="stock-ticker">${ticker}</td>
                <td class="stock-name-cell">${p.name || ''}</td>
                <td class="market-price">${formatMoney(p.price)}</td>
                <td>${p.shares}</td>
                <td class="market-price">${formatMoney(p.current_value)}</td>
                <td>${formatMoney(p.cost_basis)}</td>
                <td class="${gainClass}">
                    ${formatMoney(p.unrealized_gain)}
                    <span class="gain-pct">(${p.unrealized_pct >= 0 ? '+' : ''}${p.unrealized_pct}%)</span>
                </td>
                <td class="updated-cell" title="${updatedTitle}">${updatedText}</td>
            </tr>
        `;
    }

    html += '</tbody></table>';
    document.getElementById('market-prices').innerHTML = html;
}

function renderPerformance(data) {
    const periods = ['ytd', '1y', '2y', '3y', '5y', 'all'];
    let html = '';

    for (const period of periods) {
        const info = data[period];
        if (!info) continue;

        const profitClass = info.profit >= 0 ? 'positive' : 'negative';
        html += `
            <div class="performance-card">
                <div class="period">${info.label}</div>
                <div class="profit ${profitClass}">${formatMoney(info.profit)}</div>
                <div class="details">${info.sales_count} sales</div>
            </div>
        `;
    }

    document.getElementById('performance-periods').innerHTML = html;
}

function formatMoney(amount) {
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD'
    }).format(amount);
}

function renderSummary(data) {
    const totals = data.totals;

    // Render totals grid
    document.getElementById('summary-totals').innerHTML = `
        <div class="summary-card total-invested">
            <div class="summary-label">Total Invested</div>
            <div class="summary-value">${formatMoney(totals.total_invested)}</div>
        </div>
        <div class="summary-card current-holdings">
            <div class="summary-label">Current Holdings (at cost)</div>
            <div class="summary-value">${formatMoney(totals.current_cost_basis)}</div>
        </div>
        <div class="summary-card realized-profit ${totals.realized_profit >= 0 ? 'positive' : 'negative'}">
            <div class="summary-label">Realized Profit</div>
            <div class="summary-value">${formatMoney(totals.realized_profit)}</div>
        </div>
        <div class="summary-card pending clickable" onclick="showPendingSales()">
            <div class="summary-label">Pending Sales</div>
            <div class="summary-value">${formatMoney(totals.pending_value)}</div>
            <div class="summary-sub ${totals.pending_profit >= 0 ? 'positive' : 'negative'}">
                (${formatMoney(totals.pending_profit)} profit if executed)
            </div>
        </div>
        <div class="summary-card cash-returned">
            <div class="summary-label">Cash Returned</div>
            <div class="summary-value">${formatMoney(totals.total_returned)}</div>
            <div class="summary-sub">From completed sales</div>
        </div>
    `;

    // Render by-ticker table
    const stocks = data.by_ticker.filter(t => t.type === 'stock');
    const indexes = data.by_ticker.filter(t => t.type === 'index');

    let html = `
        <table class="summary-table">
            <thead>
                <tr>
                    <th>Ticker</th>
                    <th>Name</th>
                    <th>Shares</th>
                    <th>Avg Cost</th>
                    <th>Cost Basis</th>
                    <th>Realized Profit</th>
                    <th>Pending</th>
                </tr>
            </thead>
            <tbody>
    `;

    for (const stock of stocks) {
        const profitClass = stock.realized_profit >= 0 ? 'gain-positive' : 'gain-negative';
        html += `
            <tr>
                <td class="stock-ticker">${stock.ticker}</td>
                <td>${stock.name}</td>
                <td>${stock.shares_held}</td>
                <td>${formatMoney(stock.avg_buy_price)}</td>
                <td>${formatMoney(stock.current_cost_basis)}</td>
                <td class="${profitClass}">${formatMoney(stock.realized_profit)}</td>
                <td>${stock.pending_value > 0 ? formatMoney(stock.pending_value) : '-'}</td>
            </tr>
        `;
    }

    html += '</tbody></table>';

    if (indexes.length > 0) {
        html += `
            <h4>Index Funds</h4>
            <table class="summary-table">
                <thead>
                    <tr>
                        <th>Ticker</th>
                        <th>Name</th>
                        <th>Shares</th>
                        <th>Avg Cost</th>
                        <th>Cost Basis</th>
                    </tr>
                </thead>
                <tbody>
        `;

        for (const fund of indexes) {
            html += `
                <tr>
                    <td class="stock-ticker">${fund.ticker}</td>
                    <td>${fund.name}</td>
                    <td>${fund.shares_held}</td>
                    <td>${formatMoney(fund.avg_buy_price)}</td>
                    <td>${formatMoney(fund.current_cost_basis)}</td>
                </tr>
            `;
        }

        html += '</tbody></table>';
    }

    document.getElementById('summary-by-ticker').innerHTML = html;
}

async function showPendingSales() {
    try {
        const response = await fetch('/api/holdings');
        const data = await response.json();

        // Collect all pending transactions
        const pendingTxns = [];
        const allHoldings = {...data.stocks, ...data.index_funds};

        for (const [ticker, holding] of Object.entries(allHoldings)) {
            for (const txn of holding.transactions) {
                const status = (txn.status || '').toLowerCase();
                if (status === 'placed' && txn.action === 'sell') {
                    pendingTxns.push({
                        ...txn,
                        ticker: ticker,
                        name: holding.name
                    });
                }
            }
        }

        // Get today's date in YYYY-MM-DD format
        const today = new Date().toISOString().split('T')[0];

        // Show modal with pending sales
        let html = '<div class="modal-overlay" onclick="closeModal(event)">';
        html += '<div class="modal-content" onclick="event.stopPropagation()">';
        html += '<div class="modal-header"><h3>Pending Sales</h3><button class="modal-close" onclick="closeModal()">&times;</button></div>';

        if (pendingTxns.length === 0) {
            html += '<p class="empty-state">No pending sales</p>';
        } else {
            html += `<table class="summary-table">
                <thead>
                    <tr>
                        <th>Ticker</th>
                        <th>Shares</th>
                        <th>Price</th>
                        <th>Value</th>
                        <th>Set Date</th>
                        <th>Confirm Date</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody>`;

            for (const txn of pendingTxns) {
                const price = parseFloat(txn.price) || 0;
                const shares = parseInt(txn.shares) || 0;
                const value = price * shares;
                const dateDisplay = txn.date ? formatDate(txn.date) : '-';
                const txnDate = txn.date || today;

                html += `
                    <tr>
                        <td><span class="stock-ticker">${txn.ticker}</span> ${txn.name}</td>
                        <td>${shares}</td>
                        <td>${formatMoney(price)}</td>
                        <td>${formatMoney(value)}</td>
                        <td>${dateDisplay}</td>
                        <td class="confirm-date-cell">
                            <select id="confirm-date-type-${txn.id}" onchange="toggleCustomDate(${txn.id})">
                                <option value="today">Today</option>
                                ${txn.date ? `<option value="set" selected>Set date (${dateDisplay})</option>` : ''}
                                <option value="custom">Other...</option>
                            </select>
                            <input type="date" id="confirm-date-custom-${txn.id}" value="${txnDate}" style="display: none; margin-top: 5px;">
                        </td>
                        <td>
                            <button class="confirm-btn" onclick="confirmWithDate(${txn.id})">Confirm</button>
                        </td>
                    </tr>
                `;
            }

            html += '</tbody></table>';
        }

        html += '</div></div>';

        // Add modal to page
        const modalDiv = document.createElement('div');
        modalDiv.id = 'pending-modal';
        modalDiv.innerHTML = html;
        document.body.appendChild(modalDiv);

    } catch (error) {
        console.error('Error loading pending sales:', error);
    }
}

function toggleCustomDate(id) {
    const select = document.getElementById(`confirm-date-type-${id}`);
    const customInput = document.getElementById(`confirm-date-custom-${id}`);

    if (select.value === 'custom') {
        customInput.style.display = 'block';
    } else {
        customInput.style.display = 'none';
    }
}

async function confirmWithDate(id) {
    const select = document.getElementById(`confirm-date-type-${id}`);
    const customInput = document.getElementById(`confirm-date-custom-${id}`);

    let date;
    if (select.value === 'today') {
        date = new Date().toISOString().split('T')[0];
    } else if (select.value === 'set') {
        // Keep the existing date
        date = customInput.value;
    } else {
        // Custom date
        date = customInput.value;
    }

    await updateTransaction(id, { status: 'done', date: date });
    closeModal();
    loadSummary();
}

function closeModal(event) {
    if (event && event.target !== event.currentTarget) return;
    const modal = document.getElementById('pending-modal');
    if (modal) modal.remove();
}

async function loadStocks() {
    try {
        const response = await fetch('/api/stocks');
        stocksList = await response.json();
        setupAutocomplete();
    } catch (error) {
        console.error('Error loading stocks:', error);
    }
}

function setupAutocomplete() {
    const tickerInput = document.querySelector('#add-transaction-form input[name="ticker"]');
    if (!tickerInput) return;

    // Create datalist for autocomplete
    let datalist = document.getElementById('ticker-list');
    if (!datalist) {
        datalist = document.createElement('datalist');
        datalist.id = 'ticker-list';
        document.body.appendChild(datalist);
    }

    datalist.innerHTML = stocksList.map(s =>
        `<option value="${s.ticker}">${s.ticker} - ${s.name}</option>`
    ).join('');

    tickerInput.setAttribute('list', 'ticker-list');
}

async function loadHoldings() {
    try {
        // Fetch both regular holdings and analysis data in parallel
        const [holdingsResponse, analysisResponse] = await Promise.all([
            fetch('/api/holdings'),
            fetch('/api/holdings-analysis')
        ]);
        const data = await holdingsResponse.json();
        const analysis = await analysisResponse.json();

        // Merge analysis data (current prices, etc.) into holdings
        const enrichedStocks = mergeHoldingsWithAnalysis(data.stocks, analysis.holdings);
        const enrichedIndex = mergeHoldingsWithAnalysis(data.index_funds, analysis.holdings);

        renderHoldings(enrichedStocks, 'stocks-container');
        renderHoldings(enrichedIndex, 'index-container');

        // Render sell recommendations
        renderSellRecommendations(analysis.sell_recommendations);

        // Display last updated time
        displayHoldingsLastUpdated(analysis.last_updated);

        // Render pending/watchlist items
        const pendingStocks = data.pending_stocks || {};
        const pendingIndex = data.pending_index || {};
        renderHoldings(pendingStocks, 'pending-stocks-container', true);
        renderHoldings(pendingIndex, 'pending-index-container', true);

        // Show/hide pending section based on whether there are any pending items
        const pendingSection = document.getElementById('pending-section');
        const hasPending = Object.keys(pendingStocks).length > 0 || Object.keys(pendingIndex).length > 0;
        pendingSection.style.display = hasPending ? 'block' : 'none';
    } catch (error) {
        console.error('Error loading holdings:', error);
    }
}

function mergeHoldingsWithAnalysis(holdings, analysisData) {
    const merged = {};
    for (const [ticker, holding] of Object.entries(holdings)) {
        const analysis = analysisData[ticker] || {};
        merged[ticker] = {
            ...holding,
            current_price: analysis.current_price,
            estimated_value: analysis.estimated_value,
            price_vs_value: analysis.price_vs_value,
            avg_cost: analysis.avg_cost,
            gain_pct: analysis.gain_pct,
            updated: analysis.updated
        };
    }
    return merged;
}

function displayHoldingsLastUpdated(lastUpdated) {
    const banner = document.getElementById('holdings-last-updated');
    if (!banner) return;

    if (!lastUpdated) {
        banner.innerHTML = '<span class="update-warning">Price data not available - run a data refresh</span>';
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
        timeAgo = `${diffMins} minute${diffMins !== 1 ? 's' : ''} ago`;
    } else if (diffHours < 24) {
        timeAgo = `${diffHours} hour${diffHours !== 1 ? 's' : ''} ago`;
    } else {
        timeAgo = `${diffDays} day${diffDays !== 1 ? 's' : ''} ago`;
    }

    const formattedDate = updateDate.toLocaleString();
    const isStale = diffHours >= 24;
    const statusClass = isStale ? 'update-stale' : 'update-fresh';

    banner.innerHTML = `
        <span class="${statusClass}">
            <strong>Prices:</strong> Updated ${timeAgo}
            <span class="update-timestamp">(${formattedDate})</span>
        </span>
        <button class="refresh-inline-btn" onclick="refreshCurrentData()" title="Refresh price data">Refresh</button>
    `;
}

function renderSellRecommendations(recommendations) {
    const section = document.getElementById('sell-recommendations-section');
    const container = document.getElementById('sell-recommendations-container');

    if (!recommendations || recommendations.length === 0) {
        section.style.display = 'none';
        return;
    }

    section.style.display = 'block';

    container.innerHTML = recommendations.map(rec => {
        const gainClass = rec.gain_pct > 50 ? 'gain-high' : rec.gain_pct > 30 ? 'gain-moderate' : '';
        const valueClass = rec.price_vs_value > 20 ? 'overvalued-high' : rec.price_vs_value > 10 ? 'overvalued-moderate' : '';

        return `
            <div class="sell-recommendation-card">
                <div class="sell-rec-header">
                    <a href="#research" class="sell-rec-ticker" onclick="lookupTicker('${rec.ticker}', event)">${rec.ticker}</a>
                    <span class="sell-rec-name">${rec.name}</span>
                    <span class="sell-rec-shares">${rec.shares} shares</span>
                </div>
                <div class="sell-rec-metrics">
                    <div class="sell-rec-metric">
                        <span class="label">Current</span>
                        <span class="value">$${rec.current_price ? rec.current_price.toFixed(2) : 'N/A'}</span>
                    </div>
                    <div class="sell-rec-metric">
                        <span class="label">Your Cost</span>
                        <span class="value">$${rec.avg_cost ? rec.avg_cost.toFixed(2) : 'N/A'}</span>
                    </div>
                    <div class="sell-rec-metric ${gainClass}">
                        <span class="label">Gain</span>
                        <span class="value">${rec.gain_pct ? (rec.gain_pct > 0 ? '+' : '') + rec.gain_pct.toFixed(0) + '%' : 'N/A'}</span>
                    </div>
                    <div class="sell-rec-metric ${valueClass}">
                        <span class="label">vs Value</span>
                        <span class="value">${rec.price_vs_value ? (rec.price_vs_value > 0 ? '+' : '') + rec.price_vs_value.toFixed(0) + '%' : 'N/A'}</span>
                    </div>
                </div>
                <div class="sell-rec-reasons">
                    ${rec.reasons.map(r => `<span class="reason-badge">${r}</span>`).join('')}
                </div>
            </div>
        `;
    }).join('');
}

function renderHoldings(holdings, containerId, isPending = false) {
    const container = document.getElementById(containerId);

    if (Object.keys(holdings).length === 0) {
        container.innerHTML = isPending ? '' : '<div class="empty-state">No holdings yet</div>';
        return;
    }

    container.innerHTML = Object.values(holdings).map(stock => {
        // Build lots display on separate line
        let lotsHtml = '';
        if (stock.remaining_lots && stock.remaining_lots.length > 0) {
            lotsHtml = '<div class="lots-info">' +
                stock.remaining_lots.map(lot =>
                    `<span class="lot-badge">${lot.shares}@$${lot.price.toFixed(2)}</span>`
                ).join('') + '</div>';
        }

        // For pending items, show pending badge
        const pendingBadge = isPending ? '<span class="pending-badge">PENDING</span>' : '';
        const cardClass = isPending ? 'stock-card pending-card' : 'stock-card';

        // Build current price and gain info
        let priceHtml = '';
        if (stock.current_price && !isPending) {
            const gainClass = stock.gain_pct > 0 ? 'gain-positive' : stock.gain_pct < 0 ? 'gain-negative' : '';
            const gainSign = stock.gain_pct > 0 ? '+' : '';
            const updatedText = stock.updated ? formatTimeAgo(stock.updated) : '';
            priceHtml = `
                <div class="holding-price-info">
                    <span class="current-price">$${stock.current_price.toFixed(2)}</span>
                    ${stock.gain_pct !== null ? `<span class="holding-gain ${gainClass}">${gainSign}${stock.gain_pct.toFixed(1)}%</span>` : ''}
                    ${updatedText ? `<span class="stock-updated" title="${new Date(stock.updated).toLocaleString()}">${updatedText}</span>` : ''}
                </div>
            `;
        }

        return `
        <div class="${cardClass}">
            <div class="stock-header" onclick="toggleTransactions('${stock.ticker}')">
                <div>
                    <span class="stock-ticker">${stock.ticker}</span>
                    <span class="stock-name">${stock.name}</span>
                    ${pendingBadge}
                </div>
                <div class="stock-shares">
                    ${priceHtml}
                    <span class="shares-label"><span class="count">${stock.shares}</span> shares</span>
                    ${lotsHtml}
                </div>
            </div>
            <div class="transactions-container" id="txn-${stock.ticker}">
                <table class="transactions-table">
                    <thead>
                        <tr>
                            <th>Action</th>
                            <th>Shares</th>
                            <th>Price</th>
                            <th>Gain %</th>
                            <th>Date</th>
                            <th>Status</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${stock.transactions.map(txn => renderTransaction(txn)).join('')}
                    </tbody>
                </table>
            </div>
        </div>
    `}).join('');
}

function renderTransaction(txn) {
    const actionClass = txn.action === 'buy' ? 'action-buy' : 'action-sell';
    const actionSymbol = txn.action === 'buy' ? '+' : '-';

    const status = (txn.status || '').toLowerCase();
    let statusBadge = '';
    if (status) {
        const statusClass = `status-${status}`;
        // Show friendly status names based on action + status
        let friendlyStatus = status.toUpperCase();
        if (status === 'watch') {
            friendlyStatus = 'Watch';
        } else if (status === 'placed') {
            friendlyStatus = txn.action === 'buy' ? 'Placed Buy' : 'Placed Sell';
        } else if (status === 'done') {
            friendlyStatus = txn.action === 'buy' ? 'Bought' : 'Sold';
        }
        statusBadge = `<span class="${statusClass}">${friendlyStatus}</span>`;
    }

    // Use computed FIFO gain if available, otherwise fall back to manual gain_pct
    let gainDisplay = '';
    let gainValue = txn.computed_gain_pct !== undefined ? txn.computed_gain_pct : txn.gain_pct;
    if (gainValue) {
        const gainClass = parseFloat(gainValue) >= 0 ? 'gain-positive' : 'gain-negative';
        gainDisplay = `<span class="${gainClass}">${gainValue}%</span>`;
        // Show cost basis tooltip for sells
        if (txn.fifo_cost_basis) {
            gainDisplay += `<span class="cost-basis-hint" title="FIFO cost basis">@$${txn.fifo_cost_basis}</span>`;
        }
    }

    const dateDisplay = txn.date ? formatDate(txn.date) : '';

    // Quick action buttons based on current status
    let quickActions = '';
    if (status === 'placed') {
        quickActions = `<button class="confirm-btn" onclick="confirmTransaction(${txn.id})" title="Confirm executed">Confirm</button>`;
    } else if (status === '' || status === 'watch') {
        quickActions = `<button class="status-btn" onclick="markPlaced(${txn.id})" title="Mark order as placed">Place</button>`;
    }

    return `
        <tr data-id="${txn.id}">
            <td class="${actionClass}">${actionSymbol} ${txn.action.toUpperCase()}</td>
            <td class="editable" data-field="shares" data-value="${txn.shares}">${txn.shares}</td>
            <td class="editable" data-field="price" data-value="${txn.price}">$${parseFloat(txn.price).toFixed(2)}</td>
            <td class="gain-cell" data-field="gain_pct" data-value="${txn.gain_pct || ''}">${gainDisplay || '-'}</td>
            <td class="editable" data-field="date" data-value="${txn.date || ''}">${dateDisplay || '-'}</td>
            <td>${statusBadge}</td>
            <td class="action-buttons">
                ${quickActions}
                <button class="edit-btn" onclick="editTransaction(${txn.id})" title="Edit">Edit</button>
                <button class="delete-btn" onclick="deleteTransaction(${txn.id})" title="Delete">Del</button>
            </td>
        </tr>
    `;
}

function formatDate(dateStr) {
    if (!dateStr) return '';
    const date = new Date(dateStr);
    if (isNaN(date.getTime())) return dateStr;
    return date.toLocaleDateString('en-US', { month: 'numeric', day: 'numeric', year: 'numeric' });
}

function toggleTransactions(ticker) {
    const container = document.getElementById(`txn-${ticker}`);
    container.classList.toggle('open');
}

function setupForms() {
    // Add Transaction Form
    document.getElementById('add-transaction-form').addEventListener('submit', async function(e) {
        e.preventDefault();
        const formData = new FormData(this);
        const data = Object.fromEntries(formData.entries());
        data.ticker = data.ticker.toUpperCase();

        try {
            const response = await fetch('/api/transactions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });

            if (response.ok) {
                this.reset();
                loadHoldings();
                loadSummary();
                loadStocks();
            } else {
                alert('Error adding transaction');
            }
        } catch (error) {
            console.error('Error:', error);
            alert('Error adding transaction');
        }
    });

    // Add Stock Form
    document.getElementById('add-stock-form').addEventListener('submit', async function(e) {
        e.preventDefault();
        const formData = new FormData(this);
        const data = Object.fromEntries(formData.entries());
        data.ticker = data.ticker.toUpperCase();

        try {
            const response = await fetch('/api/stocks', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });

            const result = await response.json();
            if (result.success) {
                this.reset();
                loadStocks();
                alert('Stock added successfully');
            } else {
                alert(result.error || 'Error adding stock');
            }
        } catch (error) {
            console.error('Error:', error);
            alert('Error adding stock');
        }
    });
}

async function deleteTransaction(id) {
    if (!confirm('Are you sure you want to delete this transaction?')) return;

    try {
        const response = await fetch(`/api/transactions/${id}`, {
            method: 'DELETE'
        });

        if (response.ok) {
            loadHoldings();
            loadSummary();
        } else {
            alert('Error deleting transaction');
        }
    } catch (error) {
        console.error('Error:', error);
        alert('Error deleting transaction');
    }
}

async function updateTransaction(id, data) {
    try {
        const response = await fetch(`/api/transactions/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });

        if (response.ok) {
            loadHoldings();
            loadSummary();
            return true;
        }
    } catch (error) {
        console.error('Error:', error);
    }
    return false;
}

async function confirmTransaction(id) {
    await updateTransaction(id, { status: 'done' });
}

async function markPlaced(id) {
    await updateTransaction(id, { status: 'placed' });
}

function editTransaction(id) {
    // Find the row
    const row = document.querySelector(`tr[data-id="${id}"]`);
    if (!row || row.classList.contains('editing')) return;

    row.classList.add('editing');

    // Get current values
    const cells = row.querySelectorAll('td.editable');
    const currentValues = {};

    cells.forEach(cell => {
        const field = cell.dataset.field;
        const value = cell.dataset.value;
        currentValues[field] = value;

        // Replace with input
        if (field === 'date') {
            cell.innerHTML = `<input type="date" value="${value}" class="edit-input">`;
        } else if (field === 'gain_pct') {
            cell.innerHTML = `<input type="number" step="0.1" value="${value}" class="edit-input" placeholder="Gain %">`;
        } else if (field === 'price') {
            cell.innerHTML = `<input type="number" step="0.01" value="${value}" class="edit-input">`;
        } else {
            cell.innerHTML = `<input type="number" value="${value}" class="edit-input">`;
        }
    });

    // Add status dropdown
    const statusCell = row.querySelector('td:nth-child(6)');
    const statusText = statusCell.textContent.trim().toLowerCase();
    // Map friendly names back to status values
    let currentStatus = '';
    if (statusText.includes('watch')) {
        currentStatus = 'watch';
    } else if (statusText.includes('placed')) {
        currentStatus = 'placed';
    } else if (statusText.includes('bought') || statusText.includes('sold') || statusText === 'done') {
        currentStatus = 'done';
    }
    statusCell.innerHTML = `
        <select class="edit-input status-select">
            <option value="watch" ${currentStatus === 'watch' ? 'selected' : ''}>Watch</option>
            <option value="placed" ${currentStatus === 'placed' ? 'selected' : ''}>Placed</option>
            <option value="done" ${currentStatus === 'done' ? 'selected' : ''}>Done</option>
        </select>
    `;

    // Replace action buttons with Save/Cancel
    const actionCell = row.querySelector('td.action-buttons');
    actionCell.innerHTML = `
        <button class="save-btn" onclick="saveTransaction(${id})">Save</button>
        <button class="cancel-btn" onclick="loadHoldings()">Cancel</button>
    `;
}

async function saveTransaction(id) {
    const row = document.querySelector(`tr[data-id="${id}"]`);
    if (!row) return;

    const data = {};

    // Gather values from inputs
    row.querySelectorAll('td.editable input').forEach(input => {
        const field = input.closest('td').dataset.field;
        data[field] = input.value;
    });

    // Get status
    const statusSelect = row.querySelector('.status-select');
    if (statusSelect) {
        data.status = statusSelect.value;
    }

    const success = await updateTransaction(id, data);
    if (!success) {
        alert('Error saving transaction');
    }
}

// Navigate to company lookup for a ticker
function viewCompany(ticker) {
    document.getElementById('research-ticker').value = ticker;

    // Hide all tabs
    document.querySelectorAll('.tab-content').forEach(tab => {
        tab.classList.remove('active');
    });
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.remove('active');
    });

    // Show research tab
    document.getElementById('research-tab').classList.add('active');

    // Find and activate the correct button
    const tabBtn = document.querySelector('.tab-btn[onclick*="research"]');
    if (tabBtn) {
        tabBtn.classList.add('active');
    }

    // Update URL hash
    window.location.hash = 'research';

    // Run the valuation
    runValuation();
}

// Make viewCompany globally accessible
window.viewCompany = viewCompany;

// Helper function for selloff rate styling
function getSelloffRateClass(rate) {
    if (rate === null || rate === undefined) return '';
    if (rate >= 3.0) return 'rate-severe';
    if (rate >= 2.0) return 'rate-high';
    if (rate >= 1.5) return 'rate-moderate';
    return 'rate-normal';
}

// ============================================================================
// Unified Refresh Functions
// ============================================================================

// Toggle refresh dropdown menu
function toggleRefreshMenu() {
    const menu = document.getElementById('refresh-menu');
    menu.classList.toggle('show');
}

// Close dropdown when clicking outside
document.addEventListener('click', function(e) {
    const dropdown = document.querySelector('.refresh-dropdown');
    const menu = document.getElementById('refresh-menu');
    if (dropdown && menu && !dropdown.contains(e.target)) {
        menu.classList.remove('show');
    }
});

// Quick Update - prices only for selected index
async function startQuickUpdate() {
    closeRefreshMenu();
    const index = typeof currentIndex !== 'undefined' ? currentIndex : 'sp500';
    await runScreenerUpdate('/api/screener/quick-update', 'Quick Update', index);
}

// All Prices Update - prices for all enabled indexes
async function startAllPricesUpdate() {
    closeRefreshMenu();
    await runScreenerUpdate('/api/screener/quick-update', 'All Prices', 'all');
}

// Smart Update - missing data + prices
async function startSmartUpdate() {
    closeRefreshMenu();
    await runScreenerUpdate('/api/screener/smart-update', 'Smart Update', 'all');
}

// Full Update - EPS + Dividends + Prices
async function startFullUpdate() {
    closeRefreshMenu();
    await runScreenerUpdate('/api/screener/start', 'Full Update', 'all');
}

// Remove orphan valuations (tickers no longer in any active index)
async function removeOrphans() {
    closeRefreshMenu();

    // First get the count to show in confirmation
    try {
        const countResp = await fetch('/api/orphans');
        const countData = await countResp.json();

        if (countData.count === 0) {
            alert('No orphan valuations found.');
            return;
        }

        const confirmed = confirm(
            `Found ${countData.count} orphan valuations.\n\n` +
            `These are tickers that have valuation data but are no longer members of any tracked index.\n\n` +
            `Remove them?`
        );

        if (!confirmed) return;

        const response = await fetch('/api/orphans/remove', { method: 'POST' });
        const result = await response.json();

        if (result.success) {
            const r = result.removed;
            alert(
                `Removed ${r.orphans_found} orphan valuations.\n\n` +
                `Details:\n` +
                `- Valuations: ${r.valuations_removed}\n` +
                `- EPS history: ${r.eps_removed}\n` +
                `- SEC companies: ${r.sec_companies_removed}\n` +
                `- Ticker index entries: ${r.ticker_indexes_removed}\n` +
                `- Tickers: ${r.tickers_removed}`
            );
            // Refresh current view
            const currentTab = window.location.hash.slice(1) || 'summary';
            showTab(currentTab);
        } else {
            alert('Error removing orphans');
        }
    } catch (error) {
        console.error('Error removing orphans:', error);
        alert('Error removing orphans: ' + error.message);
    }
}

function closeRefreshMenu() {
    const menu = document.getElementById('refresh-menu');
    if (menu) menu.classList.remove('show');
}

// Unified screener update function
async function runScreenerUpdate(endpoint, updateType, index = 'all') {
    const refreshBtn = document.getElementById('refresh-main-btn');
    const currentTab = window.location.hash.slice(1) || 'summary';

    if (refreshBtn) {
        refreshBtn.disabled = true;
        refreshBtn.textContent = `${updateType}...`;
    }

    // Disable menu buttons during update
    const menuButtons = document.querySelectorAll('.refresh-menu button');
    menuButtons.forEach(btn => btn.disabled = true);

    try {
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ index: index })
        });
        const data = await response.json();

        if (data.error) {
            alert('Error: ' + data.error);
            resetRefreshButton();
            return;
        }

        if (data.status === 'started') {
            // Show progress on appropriate tab
            const screenerProgress = document.getElementById('screener-progress');
            const datasetsProgress = document.getElementById('refresh-status');

            if (currentTab === 'datasets' && datasetsProgress) {
                datasetsProgress.style.display = 'block';
            } else if (screenerProgress) {
                screenerProgress.style.display = 'block';
            }

            // Poll for progress
            monitorUpdateProgress(updateType);
        }
    } catch (error) {
        console.error('Error starting update:', error);
        resetRefreshButton();
    }
}

// Monitor update progress
function monitorUpdateProgress(updateType) {
    const progressInterval = setInterval(async () => {
        try {
            const progressResponse = await fetch('/api/screener/progress');
            const progress = await progressResponse.json();

            // Map phase names to display names
            const phaseNames = {
                'eps': 'SEC EPS',
                'dividends': 'Dividends',
                'prices': 'Prices',
                'combining': 'Building',
                'retrying': 'Retrying',
                'valuations': 'Valuations'
            };
            const phase = phaseNames[progress.phase] || progress.phase || 'Processing';
            const pct = progress.total > 0 ? Math.round((progress.current / progress.total) * 100) : 0;
            const statusText = `[${phase}] ${progress.ticker} (${progress.current}/${progress.total})`;

            // Update screener tab progress (Market Analysis)
            const progressFill = document.getElementById('progress-fill');
            const progressText = document.getElementById('progress-text');
            if (progressFill) progressFill.style.width = `${pct}%`;
            if (progressText) progressText.textContent = statusText;

            // Update datasets tab progress (Data Sets)
            const refreshBar = document.getElementById('refresh-progress-bar');
            const refreshPhase = document.getElementById('refresh-phase');
            const refreshTicker = document.getElementById('refresh-ticker');
            const refreshCount = document.getElementById('refresh-count');
            if (refreshBar) refreshBar.style.width = `${pct}%`;
            if (refreshPhase) refreshPhase.textContent = phase;
            if (refreshTicker) refreshTicker.textContent = progress.ticker || '';
            if (refreshCount) refreshCount.textContent = `${progress.current} / ${progress.total} (${pct}%)`;

            if (progress.provider_logs && progress.provider_logs.length > 0) {
                const logContent = document.getElementById('log-content');
                if (logContent) {
                    const recentLogs = progress.provider_logs.slice(-5);
                    logContent.innerHTML = recentLogs
                        .map(log => `<div class="log-line">${log.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</div>`)
                        .join('');
                }
            }

            if (progress.status === 'complete' || progress.status === 'cancelled') {
                clearInterval(progressInterval);
                const screenerProgress = document.getElementById('screener-progress');
                const datasetsProgress = document.getElementById('refresh-status');
                if (screenerProgress) screenerProgress.style.display = 'none';
                if (datasetsProgress) datasetsProgress.style.display = 'none';
                resetRefreshButton();

                // Reload current view
                reloadCurrentView();
            }
        } catch (e) {
            console.error('Error checking progress:', e);
        }
    }, 1000);
}

function resetRefreshButton() {
    const refreshBtn = document.getElementById('refresh-main-btn');
    if (refreshBtn) {
        refreshBtn.disabled = false;
        refreshBtn.textContent = 'Refresh ▾';
    }
    const menuButtons = document.querySelectorAll('.refresh-menu button');
    menuButtons.forEach(btn => btn.disabled = false);
}

function reloadCurrentView() {
    const nowTab = window.location.hash.slice(1) || 'summary';
    if (nowTab === 'screener') {
        loadScreener();
    } else if (nowTab === 'datasets') {
        loadDatasets();
    } else if (nowTab === 'research') {
        runValuation();
    } else if (nowTab === 'recommendations') {
        loadRecommendations();
    } else {
        loadSummary();
    }
}

// Legacy function for backwards compatibility
async function refreshCurrentData() {
    await startFullUpdate();
}

// Refresh research/company lookup data
async function refreshResearchData() {
    const ticker = document.getElementById('research-ticker').value.trim().toUpperCase();
    const resultsDiv = document.getElementById('research-results');

    if (!ticker) {
        resultsDiv.innerHTML = '<p class="empty-state">Enter a ticker symbol first</p>';
        return;
    }

    resultsDiv.innerHTML = `<p class="loading">Refreshing data for ${ticker}...<br><small>Fetching SEC data (if needed) and fresh market data</small></p>`;

    try {
        const response = await fetch(`/api/valuation/${ticker}/refresh`, {
            method: 'POST'
        });
        const data = await response.json();

        if (data.error) {
            resultsDiv.innerHTML = `<p class="error">Error: ${data.error}</p>`;
            return;
        }

        // Show refresh info
        let refreshInfo = '';
        if (data.refresh_info) {
            const ri = data.refresh_info;
            if (ri.sec_fetched) {
                refreshInfo = '<div class="refresh-notice success">SEC EDGAR data fetched for the first time</div>';
            } else if (ri.sec_had_cached) {
                refreshInfo = '<div class="refresh-notice">Using cached SEC data (already have it)</div>';
            }
        }

        renderValuation(data, refreshInfo);
    } catch (error) {
        console.error('Error refreshing data:', error);
        resultsDiv.innerHTML = '<p class="error">Error refreshing data</p>';
    }
}

// Refresh data for a single company (checks for new SEC data + fresh prices)
async function forceRefreshCompany(ticker) {
    const resultsDiv = document.getElementById('research-results');

    if (!ticker) {
        return;
    }

    resultsDiv.innerHTML = `<p class="loading">Refreshing ${ticker}...<br><small>Checking SEC for new filings + fetching current prices</small></p>`;

    try {
        const response = await fetch(`/api/valuation/${ticker}/refresh?force=true`, {
            method: 'POST'
        });
        const data = await response.json();

        if (data.error) {
            resultsDiv.innerHTML = `<p class="error">Error: ${data.error}</p>`;
            return;
        }

        // Build refresh message based on what was found
        let refreshInfo = '';
        const ri = data.refresh_info;
        if (ri) {
            if (ri.new_eps_years > 0) {
                refreshInfo = `<div class="refresh-notice success">Found ${ri.new_eps_years} new EPS year(s) from SEC</div>`;
            } else {
                refreshInfo = '<div class="refresh-notice">Checked SEC (no new data) + refreshed prices</div>';
            }
        }

        renderValuation(data, refreshInfo);
    } catch (error) {
        console.error('Error refreshing data:', error);
        resultsDiv.innerHTML = '<p class="error">Error refreshing data</p>';
    }
}

// Smart screener update - fills in missing tickers
async function smartScreenerUpdate() {
    try {
        const response = await fetch('/api/screener/smart-update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ index: currentIndex })
        });
        const data = await response.json();

        if (data.status === 'started') {
            document.getElementById('screener-quick-btn').style.display = 'none';
            document.getElementById('screener-start-btn').style.display = 'none';
            document.getElementById('screener-stop-btn').style.display = 'inline-block';
            document.getElementById('screener-progress').style.display = 'block';

            screenerInterval = setInterval(checkScreenerProgress, 1000);
        }
    } catch (error) {
        console.error('Error starting smart update:', error);
    }
}

// Research / Valuation
// Navigate to Company Lookup and analyze a specific ticker
function lookupTicker(ticker, event) {
    // Prevent the default link behavior to avoid double history entries
    if (event) event.preventDefault();
    // Set the ticker in the input field
    document.getElementById('research-ticker').value = ticker;
    // Switch to the research tab
    showTab('research');
    // Run the valuation
    runValuation();
}

async function runValuation() {
    const ticker = document.getElementById('research-ticker').value.trim().toUpperCase();
    const resultsDiv = document.getElementById('research-results');

    if (!ticker) {
        resultsDiv.innerHTML = '<p class="empty-state">Enter a ticker symbol</p>';
        return;
    }

    resultsDiv.innerHTML = '<p class="loading">Loading valuation data...</p>';

    try {
        // Fetch valuation and SEC metrics in parallel
        const [valuationResponse, metricsResponse] = await Promise.all([
            fetch(`/api/valuation/${ticker}`),
            fetch(`/api/sec-metrics/${ticker}`)
        ]);

        const data = await valuationResponse.json();
        const metricsData = metricsResponse.ok ? await metricsResponse.json() : null;

        if (data.error) {
            resultsDiv.innerHTML = `<p class="error">Error: ${data.error}</p>`;
            return;
        }

        // Attach metrics to data for rendering
        data.sec_metrics = metricsData;

        renderValuation(data);
    } catch (error) {
        console.error('Error running valuation:', error);
        resultsDiv.innerHTML = '<p class="error">Error fetching valuation data</p>';
    }
}

function renderValuation(data, refreshInfo = '') {
    const resultsDiv = document.getElementById('research-results');

    // Determine if overvalued or undervalued
    let valueAssessment = '';
    let assessmentClass = '';
    if (data.price_vs_value !== null) {
        if (data.price_vs_value > 20) {
            valueAssessment = `Overvalued by ${data.price_vs_value}%`;
            assessmentClass = 'overvalued';
        } else if (data.price_vs_value < -20) {
            valueAssessment = `Undervalued by ${Math.abs(data.price_vs_value)}%`;
            assessmentClass = 'undervalued';
        } else {
            valueAssessment = `Fairly valued (${data.price_vs_value > 0 ? '+' : ''}${data.price_vs_value}%)`;
            assessmentClass = 'fair-value';
        }
    }

    // Check if we have recommended years for valuation
    const hasEnoughYears = data.has_enough_years;
    const minYearsRecommended = data.min_years_recommended || 8;

    // Data source badge, refresh button, and 10-K dropdown
    const sourceLabel = formatEpsSource(data.eps_source);
    const sourceBadge = `<span class="data-source-badge ${data.eps_source}">${sourceLabel}</span>`;
    const refreshBtn = `<button class="refresh-company-btn" onclick="forceRefreshCompany('${data.ticker}')" title="Force refresh all data for this company">
        🔄 Refresh
    </button>`;
    const tenKDropdown = `<select id="tenk-filings-dropdown" class="tenk-dropdown" onchange="openTenKFiling(this.value)" disabled>
        <option value="">View 10-K...</option>
    </select>`;

    // Fetch 10-K filings asynchronously after render
    setTimeout(() => load10KFilings(data.ticker), 0);

    // Note for fewer than recommended years
    let dataWarning = '';
    if (!hasEnoughYears) {
        dataWarning = `<div class="data-warning">Based on ${data.eps_years} years of data (${minYearsRecommended}+ years recommended for reliable valuation)</div>`;
    }

    // Note for cached data (rate limited fallback)
    let cacheNotice = '';
    if (data.from_cache) {
        cacheNotice = `<div class="cache-notice">⚠️ ${data.cache_note || 'Showing cached data due to rate limiting'}</div>`;
    }

    let html = `
        ${refreshInfo}
        <div class="valuation-header">
            <h3>${data.ticker} - ${data.company_name}</h3>
            <div class="valuation-header-controls">
                ${sourceBadge}
                ${refreshBtn}
                ${tenKDropdown}
            </div>
        </div>

        ${cacheNotice}
        ${dataWarning}

        <div class="valuation-summary">
            <div class="valuation-card">
                <div class="valuation-label">Current Price</div>
                <div class="valuation-value market-price">${data.current_price ? formatMoney(data.current_price) : 'N/A'}</div>
            </div>
            <div class="valuation-card">
                <div class="valuation-label">Estimated Value</div>
                <div class="valuation-value">${data.estimated_value ? formatMoney(data.estimated_value) : 'N/A'}</div>
            </div>
            <div class="valuation-card ${assessmentClass}">
                <div class="valuation-label">Assessment</div>
                <div class="valuation-value">${valueAssessment || 'N/A'}</div>
            </div>
        </div>

        <div class="valuation-formula">
            <h4>Formula</h4>
            <p class="formula-text">${data.formula}</p>
            <p class="formula-note">Uses <strong>average</strong> EPS over ${data.eps_years} years plus annual dividend</p>
        </div>

        <div class="eps-summary">
            <div class="eps-stat">
                <span class="eps-label">Avg EPS (used)</span>
                <span class="eps-value highlight">${data.eps_avg ? formatMoney(data.eps_avg) : 'N/A'}</span>
            </div>
            <div class="eps-stat">
                <span class="eps-label">Years of Data</span>
                <span class="eps-value ${hasEnoughYears ? '' : 'insufficient'}">${data.eps_years}</span>
            </div>
        </div>

        <div class="valuation-details">
            <div class="detail-section">
                <h4>EPS History (${data.eps_years} years)</h4>
                <table class="summary-table eps-table">
                    <thead>
                        <tr>
                            <th>Year</th>
                            <th>EPS</th>
                            <th>Type</th>
                            <th>Fiscal Period</th>
                        </tr>
                    </thead>
                    <tbody>
    `;

    for (const eps of data.eps_data) {
        const rowClass = eps.eps <= 0 ? 'negative-eps-row' : '';
        // Get EPS type - support both old format ('basic'/'diluted') and new descriptive labels
        let epsType = eps.eps_type || eps.type || 'Diluted EPS';
        if (epsType === 'basic') epsType = 'Basic EPS';
        else if (epsType === 'diluted') epsType = 'Diluted EPS';
        // Format fiscal period as "Mon YYYY - Mon YYYY"
        let fiscalPeriod = eps.fiscal_period || '';
        if (!fiscalPeriod && eps.period_start && eps.period_end) {
            const formatPeriodDate = (dateStr) => {
                const d = new Date(dateStr);
                const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
                return `${months[d.getMonth()]} ${d.getFullYear()}`;
            };
            fiscalPeriod = `${formatPeriodDate(eps.period_start)} - ${formatPeriodDate(eps.period_end)}`;
        }
        html += `
            <tr class="${rowClass}">
                <td>${eps.year}</td>
                <td>${formatMoney(eps.eps)}</td>
                <td class="eps-type-cell">${epsType}</td>
                <td class="fiscal-period-cell">${fiscalPeriod}</td>
            </tr>
        `;
    }

    html += `
                    </tbody>
                    <tfoot>
                        <tr>
                            <td><strong>Average (used)</strong></td>
                            <td><strong>${data.eps_avg ? formatMoney(data.eps_avg) : 'N/A'}</strong></td>
                            <td colspan="2"></td>
                        </tr>
                    </tfoot>
                </table>
            </div>

            <div class="detail-section">
                <h4>Dividends (Last 12 months)</h4>
    `;

    if (data.dividend_payments && data.dividend_payments.length > 0) {
        html += `
            <table class="summary-table">
                <thead>
                    <tr>
                        <th>Date</th>
                        <th>Amount</th>
                    </tr>
                </thead>
                <tbody>
        `;

        for (const div of data.dividend_payments) {
            html += `
                <tr>
                    <td>${div.date}</td>
                    <td>${formatMoney(div.amount)}</td>
                </tr>
            `;
        }

        html += `
                </tbody>
                <tfoot>
                    <tr>
                        <td><strong>Annual Total</strong></td>
                        <td><strong>${formatMoney(data.annual_dividend)}</strong></td>
                    </tr>
                </tfoot>
            </table>
        `;
    } else {
        html += '<p class="empty-state">No dividends paid</p>';
    }

    html += `
            </div>
        </div>
    `;

    // SEC Metrics Section - Multi-year EPS Matrix (types on Y, years on X)
    if (data.sec_metrics && data.sec_metrics.eps_matrix) {
        const epsMatrix = data.sec_metrics.eps_matrix;
        const years = data.sec_metrics.eps_years || [];
        const epsTypes = Object.keys(epsMatrix);

        if (epsTypes.length > 0 && years.length > 0) {
            html += `
        <div class="metrics-section">
            <h3>SEC Reported EPS - Annual (10-K)</h3>
            <div class="metrics-grid">
                <div class="metric-category">
                    <table class="summary-table sec-matrix-table">
                        <thead>
                            <tr>
                                <th>EPS Type</th>
                                ${years.map(y => `<th>FY${y}</th>`).join('')}
                            </tr>
                        </thead>
                        <tbody>
            `;

            for (const epsType of epsTypes) {
                const yearValues = epsMatrix[epsType];
                html += `<tr><td>${epsType}</td>`;
                for (const year of years) {
                    const val = yearValues[year];
                    if (val !== undefined) {
                        const valueClass = val < 0 ? 'negative-value' : '';
                        html += `<td class="${valueClass}">${formatMoney(val)}</td>`;
                    } else {
                        html += `<td class="no-data">-</td>`;
                    }
                }
                html += `</tr>`;
            }

            html += `
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
            `;
        }

        // Dividend Matrix Section
        const divMatrix = data.sec_metrics.dividend_matrix || {};
        const divYears = data.sec_metrics.dividend_years || [];
        const divTypes = Object.keys(divMatrix);

        if (divTypes.length > 0 && divYears.length > 0) {
            html += `
        <div class="metrics-section">
            <h3>SEC Reported Dividends - Annual (10-K)</h3>
            <div class="metrics-grid">
                <div class="metric-category">
                    <table class="summary-table sec-matrix-table">
                        <thead>
                            <tr>
                                <th>Dividend Type</th>
                                ${divYears.map(y => `<th>FY${y}</th>`).join('')}
                            </tr>
                        </thead>
                        <tbody>
            `;

            for (const divType of divTypes) {
                const yearValues = divMatrix[divType];
                html += `<tr><td>${divType}</td>`;
                for (const year of divYears) {
                    const val = yearValues[year];
                    if (val !== undefined) {
                        html += `<td>${formatMoney(val)}</td>`;
                    } else {
                        html += `<td class="no-data">-</td>`;
                    }
                }
                html += `</tr>`;
            }

            html += `
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
            `;
        }
    }

    // Selloff Metrics Section
    if (data.selloff && data.selloff.day && data.selloff.week && data.selloff.month) {
        const selloff = data.selloff;
        const severityClass = selloff.severity === 'severe' ? 'selloff-severe' :
                             selloff.severity === 'high' ? 'selloff-high' :
                             selloff.severity === 'moderate' ? 'selloff-moderate' : '';
        const severityLabel = selloff.severity === 'severe' ? 'Severe Selling Pressure' :
                             selloff.severity === 'high' ? 'High Selling Pressure' :
                             selloff.severity === 'moderate' ? 'Moderate Selling Pressure' :
                             selloff.severity === 'normal' ? 'Normal Activity' : 'No Significant Selling';

        // Safe number formatting
        const formatRate = (rate) => (rate != null && !isNaN(rate)) ? rate.toFixed(2) + 'x' : 'N/A';
        const formatPct = (pct) => (pct != null && !isNaN(pct)) ? pct.toFixed(2) + '%' : 'N/A';
        const formatVolume = (vol) => (vol != null) ? vol.toLocaleString() : 'N/A';

        html += `
        <div class="selloff-section">
            <h4>Selling Pressure Analysis</h4>
            <div class="selloff-formula">
                <p><strong>Formula:</strong> ${selloff.formula || 'Selloff Rate = (Avg Volume on Down Days) / (Normal Avg Volume)'}</p>
                <p class="formula-note">A selloff rate &gt; 1.0 means volume on down days exceeds the 20-day average volume.</p>
            </div>

            <div class="selloff-summary ${severityClass}">
                <div class="selloff-severity-label">${severityLabel}</div>
                <div class="selloff-avg-volume">Normal Avg Volume: ${formatVolume(selloff.avg_volume)}</div>
            </div>

            <div class="selloff-grid">
                <div class="selloff-card">
                    <div class="selloff-period">Today</div>
                    ${selloff.day.is_down ?
                        `<div class="selloff-rate ${getSelloffRateClass(selloff.day.selloff_rate)}">${formatRate(selloff.day.selloff_rate)}</div>
                         <div class="selloff-detail">Volume: ${formatVolume(selloff.day.volume)}</div>
                         <div class="selloff-detail price-down">Price: ${formatPct(selloff.day.price_change_pct)}</div>` :
                        `<div class="selloff-rate neutral">N/A</div>
                         <div class="selloff-detail">Not a down day</div>
                         <div class="selloff-detail">${selloff.day.price_change_pct != null ? (selloff.day.price_change_pct >= 0 ? '+' : '') + formatPct(selloff.day.price_change_pct) : ''}</div>`
                    }
                </div>
                <div class="selloff-card">
                    <div class="selloff-period">Past Week</div>
                    <div class="selloff-rate ${getSelloffRateClass(selloff.week.selloff_rate)}">${formatRate(selloff.week.selloff_rate)}</div>
                    <div class="selloff-detail">${selloff.week.down_days || 0} of ${selloff.week.total_days || 0} down days</div>
                    <div class="selloff-detail">${(selloff.week.selloff_rate || 0) > 0 ? 'Avg volume on down days vs normal' : 'No down days'}</div>
                </div>
                <div class="selloff-card">
                    <div class="selloff-period">Past Month</div>
                    <div class="selloff-rate ${getSelloffRateClass(selloff.month.selloff_rate)}">${formatRate(selloff.month.selloff_rate)}</div>
                    <div class="selloff-detail">${selloff.month.down_days || 0} of ${selloff.month.total_days || 0} down days</div>
                    <div class="selloff-detail">${(selloff.month.selloff_rate || 0) > 0 ? 'Avg volume on down days vs normal' : 'No down days'}</div>
                </div>
            </div>

            <div class="selloff-legend">
                <span class="legend-item"><span class="legend-color severe"></span> Severe (3x+)</span>
                <span class="legend-item"><span class="legend-color high"></span> High (2-3x)</span>
                <span class="legend-item"><span class="legend-color moderate"></span> Moderate (1.5-2x)</span>
                <span class="legend-item"><span class="legend-color normal"></span> Normal (&lt;1.5x)</span>
            </div>
        </div>
        `;
    }

    resultsDiv.innerHTML = html;
}

// Screener
let screenerInterval = null;
let currentIndex = 'sp500';  // Default, will be updated by loadIndices()
let availableIndices = [];   // Cached index list from API

/**
 * Load available indices from API and populate all index dropdowns.
 * Called on page load to dynamically populate index selectors.
 */
async function loadIndices() {
    try {
        const response = await fetch('/api/indices');
        availableIndices = await response.json();

        // Populate the screener index selector
        const screenerSelect = document.getElementById('index-select');
        if (screenerSelect) {
            screenerSelect.innerHTML = availableIndices.map(idx =>
                `<option value="${idx.id}"${idx.id === 'sp500' ? ' selected' : ''}>${idx.short_name || idx.name}</option>`
            ).join('');
            // Set currentIndex from the selected option
            currentIndex = screenerSelect.value || 'sp500';
        }

        // Populate the Data Sets ticker filter (keep "All Indexes" as first option)
        const tickerFilterSelect = document.getElementById('ticker-filter-index');
        if (tickerFilterSelect) {
            const individualIndices = availableIndices.filter(idx => idx.id !== 'all');
            tickerFilterSelect.innerHTML = '<option value="">All Indexes</option>' +
                individualIndices.map(idx =>
                    `<option value="${idx.id}">${idx.short_name || idx.name}</option>`
                ).join('');
        }

        return availableIndices;
    } catch (error) {
        console.error('Error loading indices:', error);
        // Fallback: keep dropdowns as-is or add minimal options
        return [];
    }
}

function changeIndex(indexName) {
    currentIndex = indexName;
    // Update dropdown to match (in case called programmatically)
    const select = document.getElementById('index-select');
    if (select && select.value !== indexName) {
        select.value = indexName;
    }
    loadScreener();
}

async function loadScreener() {
    try {
        const response = await fetch(`/api/screener?index=${currentIndex}`);
        const data = await response.json();
        renderScreener(data);
        checkScreenerProgress();
        loadSecStatus();
    } catch (error) {
        console.error('Error loading screener:', error);
    }
}

function renderScreener(data) {
    try {
        // Update title with index name
        const indexName = data.index_name || 'S&P 500';
        document.getElementById('screener-title').textContent = `${indexName} Market Analysis`;

    // Update status
    const lastUpdated = document.getElementById('screener-last-updated');
    if (data.last_updated) {
        const date = new Date(data.last_updated);
        lastUpdated.textContent = `Last updated: ${date.toLocaleDateString()} ${date.toLocaleTimeString()}`;
    } else {
        lastUpdated.textContent = `Never updated - click "Full Update" to scan ${indexName}`;
    }

    // Show count with missing warning
    const countEl = document.getElementById('screener-count');
    if (data.missing_count > 0) {
        countEl.innerHTML = `<span class="count-warning">${data.valuations_count} / ${data.total_tickers} stocks analyzed (${data.missing_count} missing - use Refresh Data to fill)</span>`;
    } else {
        countEl.textContent = `(${data.valuations_count} / ${data.total_tickers} stocks analyzed)`;
    }

    // Render summary of undervalued stocks
    const summaryDiv = document.getElementById('screener-summary');
    if (data.undervalued.length > 0) {
        const showCount = Math.min(10, data.undervalued.length);
        let summaryHtml = `
            <div class="screener-highlight">
                <h3>Undervalued Stocks (${data.undervalued.length})</h3>
                <p class="screener-note">Stocks trading more than 20% below estimated value. Showing top ${showCount} most undervalued. Watch for selloff warnings.</p>
                <div class="undervalued-grid">
        `;

        for (const stock of data.undervalued.slice(0, 10)) {
            // Build selloff warning badge
            let selloffBadge = '';
            if (stock.in_selloff) {
                const severityClass = stock.selloff_severity === 'severe' ? 'selloff-severe' :
                                      stock.selloff_severity === 'moderate' ? 'selloff-moderate' : 'selloff-recent';
                const severityText = stock.selloff_severity === 'severe' ? 'Major Selloff' :
                                     stock.selloff_severity === 'moderate' ? 'Selloff' : 'Dropping';
                selloffBadge = `<span class="selloff-badge ${severityClass}">${severityText}</span>`;
            }

            // Build momentum info
            let momentumInfo = '';
            if (stock.off_high_pct !== null) {
                momentumInfo = `<span class="momentum-info">${stock.off_high_pct}% from 52w high</span>`;
            }

            summaryHtml += `
                <div class="undervalued-card ${stock.in_selloff ? 'has-selloff' : ''}" data-ticker="${stock.ticker}">
                    <div class="undervalued-header">
                        <div class="undervalued-ticker">${stock.ticker}</div>
                        ${selloffBadge}
                    </div>
                    <div class="undervalued-name">${stock.company_name}</div>
                    <div class="undervalued-values">
                        <span class="market-price">${formatMoney(stock.current_price)}</span>
                        <span class="undervalued-arrow">→</span>
                        <span class="estimated-value">${formatMoney(stock.estimated_value)}</span>
                    </div>
                    <div class="undervalued-pct gain-positive">${stock.price_vs_value}%</div>
                    ${momentumInfo}
                </div>
            `;
        }

        summaryHtml += '</div></div>';
        summaryDiv.innerHTML = summaryHtml;

        // Attach click handler using event delegation
        const grid = summaryDiv.querySelector('.undervalued-grid');
        if (grid) {
            grid.addEventListener('click', function(e) {
                const card = e.target.closest('.undervalued-card');
                if (card) {
                    const selection = window.getSelection().toString();
                    if (!selection) {
                        viewCompany(card.dataset.ticker);
                    }
                }
            });
        }
    } else if (data.valuations_count > 0) {
        summaryDiv.innerHTML = '<p class="empty-state">No significantly undervalued stocks found</p>';
    } else {
        summaryDiv.innerHTML = '';
    }

    // Render full results table with sorting and filtering
    const resultsDiv = document.getElementById('screener-results');
    if (data.all_valuations.length > 0) {
        // Store data globally for sorting/filtering
        window.screenerData = data.all_valuations;
        window.screenerSort = { column: 'value_pct', direction: 'asc' };
        window.screenerFilters = {};

        renderScreenerTable();
    } else {
        resultsDiv.innerHTML = '';
    }
    } catch (error) {
        console.error('Error in renderScreener:', error);
    }
}

function renderScreenerTable() {
    const resultsDiv = document.getElementById('screener-results');
    let data = [...window.screenerData];

    // Apply filters
    const filters = window.screenerFilters || {};
    if (filters.ticker) {
        data = data.filter(s => s.ticker.toLowerCase().includes(filters.ticker.toLowerCase()));
    }
    if (filters.company) {
        data = data.filter(s => s.company_name.toLowerCase().includes(filters.company.toLowerCase()));
    }
    if (filters.status) {
        if (filters.status === 'undervalued') {
            data = data.filter(s => s.price_vs_value !== null && s.price_vs_value < -10);
        } else if (filters.status === 'overvalued') {
            data = data.filter(s => s.price_vs_value !== null && s.price_vs_value > 10);
        } else if (filters.status === 'selloff') {
            data = data.filter(s => s.in_selloff);
        }
    }

    // Apply sorting
    const sort = window.screenerSort || { column: 'value_pct', direction: 'asc' };
    data.sort((a, b) => {
        let aVal, bVal;
        switch (sort.column) {
            case 'ticker': aVal = a.ticker; bVal = b.ticker; break;
            case 'company': aVal = a.company_name; bVal = b.company_name; break;
            case 'price': aVal = a.current_price || 0; bVal = b.current_price || 0; break;
            case 'value': aVal = a.estimated_value || 0; bVal = b.estimated_value || 0; break;
            case 'value_pct': aVal = a.price_vs_value ?? 999; bVal = b.price_vs_value ?? 999; break;
            case 'dividend': aVal = a.annual_dividend || 0; bVal = b.annual_dividend || 0; break;
            case 'off_high': aVal = a.off_high_pct ?? 0; bVal = b.off_high_pct ?? 0; break;
            case 'change_3m': aVal = a.price_change_3m ?? 0; bVal = b.price_change_3m ?? 0; break;
            case 'updated': aVal = a.updated || ''; bVal = b.updated || ''; break;
            default: aVal = 0; bVal = 0;
        }
        if (typeof aVal === 'string') {
            return sort.direction === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
        }
        return sort.direction === 'asc' ? aVal - bVal : bVal - aVal;
    });

    const sortIcon = (col) => {
        if (sort.column !== col) return '<span class="sort-icon">↕</span>';
        return sort.direction === 'asc' ? '<span class="sort-icon active">↑</span>' : '<span class="sort-icon active">↓</span>';
    };

    let html = `
        <h3>All Analyzed Stocks <span class="result-count">(${data.length} stocks)</span></h3>
        <div class="screener-filters">
            <input type="text" id="filter-ticker" placeholder="Filter ticker..." oninput="applyScreenerFilter('ticker', this.value)">
            <input type="text" id="filter-company" placeholder="Filter company..." oninput="applyScreenerFilter('company', this.value)">
            <select id="filter-status" onchange="applyScreenerFilter('status', this.value)">
                <option value="">All Status</option>
                <option value="undervalued">Undervalued (&lt;-10%)</option>
                <option value="overvalued">Overvalued (&gt;+10%)</option>
                <option value="selloff">In Selloff</option>
            </select>
        </div>
        <table class="summary-table screener-table sortable-table">
            <thead>
                <tr>
                    <th class="sortable" onclick="sortScreener('ticker')">Ticker ${sortIcon('ticker')}</th>
                    <th class="sortable" onclick="sortScreener('company')">Company ${sortIcon('company')}</th>
                    <th class="sortable" onclick="sortScreener('price')">Price ${sortIcon('price')}</th>
                    <th class="sortable" onclick="sortScreener('value')">Est. Value ${sortIcon('value')}</th>
                    <th class="sortable" onclick="sortScreener('value_pct')">Value % ${sortIcon('value_pct')}</th>
                    <th class="sortable" onclick="sortScreener('dividend')">Ann. Div ${sortIcon('dividend')}</th>
                    <th class="sortable" onclick="sortScreener('off_high')">52w High ${sortIcon('off_high')}</th>
                    <th class="sortable" onclick="sortScreener('change_3m')">3M Change ${sortIcon('change_3m')}</th>
                    <th>Status</th>
                    <th class="sortable" onclick="sortScreener('updated')">Updated ${sortIcon('updated')}</th>
                </tr>
            </thead>
            <tbody>
    `;

    for (const stock of data) {
        // Value % (under/over valued)
        let valuePct = stock.price_vs_value;
        let valuePctClass = '';
        let valuePctDisplay = 'N/A';
        if (valuePct !== null) {
            if (valuePct < -20) valuePctClass = 'gain-positive strong';
            else if (valuePct < -10) valuePctClass = 'gain-positive';
            else if (valuePct > 20) valuePctClass = 'gain-negative strong';
            else if (valuePct > 10) valuePctClass = 'gain-negative';
            valuePctDisplay = (valuePct > 0 ? '+' : '') + valuePct.toFixed(1) + '%';
        }

        // Off 52w high
        const offHighClass = stock.off_high_pct < -30 ? 'gain-negative strong' :
                            stock.off_high_pct < -20 ? 'gain-negative' : '';
        const offHighDisplay = stock.off_high_pct !== null ? stock.off_high_pct + '%' : '-';

        // 3 month change
        const change3mClass = stock.price_change_3m < -15 ? 'gain-negative' :
                             stock.price_change_3m > 0 ? 'gain-positive' : '';
        const change3mDisplay = stock.price_change_3m !== null ? (stock.price_change_3m > 0 ? '+' : '') + stock.price_change_3m + '%' : '-';

        // Annual dividend
        const annDivDisplay = stock.annual_dividend ? '$' + stock.annual_dividend.toFixed(2) : '-';

        // Selloff status
        let statusBadge = '';
        if (stock.in_selloff) {
            const severityClass = stock.selloff_severity === 'severe' ? 'selloff-severe' :
                                  stock.selloff_severity === 'moderate' ? 'selloff-moderate' : 'selloff-recent';
            const severityText = stock.selloff_severity === 'severe' ? 'Major Selloff' :
                                 stock.selloff_severity === 'moderate' ? 'Selloff' : 'Dropping';
            statusBadge = `<span class="selloff-badge ${severityClass}">${severityText}</span>`;
        } else {
            statusBadge = '<span class="status-ok">OK</span>';
        }

        const updatedText = stock.updated ? formatTimeAgo(stock.updated) : '-';
        const updatedTitle = stock.updated ? new Date(stock.updated).toLocaleString() : '';

        html += `
            <tr class="clickable-row ${stock.in_selloff ? 'has-selloff-row' : ''}" data-ticker="${stock.ticker}">
                <td class="stock-ticker">${stock.ticker}</td>
                <td class="stock-name-cell">${stock.company_name}</td>
                <td class="market-price">${formatMoney(stock.current_price)}</td>
                <td>${formatMoney(stock.estimated_value)}</td>
                <td class="${valuePctClass}">${valuePctDisplay}</td>
                <td>${annDivDisplay}</td>
                <td class="${offHighClass}">${offHighDisplay}</td>
                <td class="${change3mClass}">${change3mDisplay}</td>
                <td>${statusBadge}</td>
                <td class="updated-cell" title="${updatedTitle}">${updatedText}</td>
            </tr>
        `;
    }

    html += '</tbody></table>';
    resultsDiv.innerHTML = html;

    // Attach click handler using event delegation at table level
    const table = resultsDiv.querySelector('.screener-table');
    if (table) {
        table.addEventListener('click', function(e) {
            // Find the clicked row
            const row = e.target.closest('.clickable-row');
            if (row) {
                // Only navigate if user didn't select text
                const selection = window.getSelection().toString();
                if (!selection) {
                    viewCompany(row.dataset.ticker);
                }
            }
        });
    }

    // Restore filter values
    if (filters.ticker) document.getElementById('filter-ticker').value = filters.ticker;
    if (filters.company) document.getElementById('filter-company').value = filters.company;
    if (filters.status) document.getElementById('filter-status').value = filters.status;
}

function sortScreener(column) {
    const sort = window.screenerSort || { column: 'value_pct', direction: 'asc' };
    if (sort.column === column) {
        sort.direction = sort.direction === 'asc' ? 'desc' : 'asc';
    } else {
        sort.column = column;
        sort.direction = 'asc';
    }
    window.screenerSort = sort;
    renderScreenerTable();
}

function applyScreenerFilter(field, value) {
    window.screenerFilters = window.screenerFilters || {};
    window.screenerFilters[field] = value;
    renderScreenerTable();
}

function showScreenerError(message) {
    const statusEl = document.getElementById('screener-last-updated');
    if (statusEl) {
        statusEl.innerHTML = `<span style="color: #e74c3c;">Error: ${message}</span>`;
    }
    // Also show in progress text area
    const progressText = document.getElementById('progress-text');
    if (progressText) {
        progressText.textContent = `Error: ${message}`;
    }
}

async function quickUpdatePrices() {
    try {
        const response = await fetch('/api/screener/quick-update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ index: currentIndex })
        });
        const data = await response.json();

        if (data.error) {
            showScreenerError(data.error);
            return;
        }

        if (data.status === 'started') {
            hideScreenerButtons();
            document.getElementById('screener-stop-btn').style.display = 'inline-block';
            document.getElementById('screener-progress').style.display = 'block';

            screenerInterval = setInterval(checkScreenerProgress, 1000);
        } else {
            showScreenerError('Unexpected response: ' + JSON.stringify(data));
        }
    } catch (error) {
        console.error('Error starting quick update:', error);
        showScreenerError('Network error: ' + error.message);
    }
}

async function smartUpdateScreener() {
    try {
        const response = await fetch('/api/screener/smart-update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ index: currentIndex })
        });
        const data = await response.json();

        if (data.error) {
            showScreenerError(data.error);
            return;
        }

        if (data.status === 'started') {
            hideScreenerButtons();
            document.getElementById('screener-stop-btn').style.display = 'inline-block';
            document.getElementById('screener-progress').style.display = 'block';

            screenerInterval = setInterval(checkScreenerProgress, 1000);
        } else {
            showScreenerError('Unexpected response: ' + JSON.stringify(data));
        }
    } catch (error) {
        console.error('Error starting smart update:', error);
        showScreenerError('Network error: ' + error.message);
    }
}

async function startScreener() {
    try {
        const response = await fetch('/api/screener/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ index: currentIndex })
        });
        const data = await response.json();

        if (data.error) {
            showScreenerError(data.error);
            return;
        }

        if (data.status === 'started') {
            hideScreenerButtons();
            document.getElementById('screener-stop-btn').style.display = 'inline-block';
            document.getElementById('screener-progress').style.display = 'block';

            screenerInterval = setInterval(checkScreenerProgress, 1000);
        } else {
            showScreenerError('Unexpected response: ' + JSON.stringify(data));
        }
    } catch (error) {
        console.error('Error starting screener:', error);
        showScreenerError('Network error: ' + error.message);
    }
}

function hideScreenerButtons() {
    const quickBtn = document.getElementById('screener-quick-btn');
    const smartBtn = document.getElementById('screener-smart-btn');
    const startBtn = document.getElementById('screener-start-btn');
    if (quickBtn) quickBtn.style.display = 'none';
    if (smartBtn) smartBtn.style.display = 'none';
    if (startBtn) startBtn.style.display = 'none';
}

function showScreenerButtons() {
    const quickBtn = document.getElementById('screener-quick-btn');
    const smartBtn = document.getElementById('screener-smart-btn');
    const startBtn = document.getElementById('screener-start-btn');
    if (quickBtn) quickBtn.style.display = 'inline-block';
    if (smartBtn) smartBtn.style.display = 'inline-block';
    if (startBtn) startBtn.style.display = 'inline-block';
}

async function stopScreener() {
    try {
        await fetch('/api/screener/stop', { method: 'POST' });
    } catch (error) {
        console.error('Error stopping screener:', error);
    }
}

async function updateDividends() {
    try {
        const response = await fetch('/api/screener/update-dividends', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ index: currentIndex })
        });
        const data = await response.json();

        if (data.status === 'started') {
            document.getElementById('screener-start-btn').style.display = 'none';
            document.getElementById('screener-dividends-btn').style.display = 'none';
            document.getElementById('screener-stop-btn').style.display = 'inline-block';
            document.getElementById('screener-progress').style.display = 'block';

            // Start polling for progress
            screenerInterval = setInterval(checkScreenerProgress, 1000);
        }
    } catch (error) {
        console.error('Error updating dividends:', error);
    }
}

async function checkScreenerProgress() {
    try {
        const response = await fetch('/api/screener/progress');
        const progress = await response.json();

        if (progress.status === 'running') {
            document.getElementById('screener-quick-btn').style.display = 'none';
            document.getElementById('screener-start-btn').style.display = 'none';
            document.getElementById('screener-stop-btn').style.display = 'inline-block';
            document.getElementById('screener-progress').style.display = 'block';

            const pct = progress.total > 0 ? (progress.current / progress.total * 100) : 0;
            document.getElementById('progress-fill').style.width = pct + '%';

            // Show phase info if available
            let phaseText = '';
            if (progress.phase === 'prices') {
                phaseText = '[Downloading prices] ';
            } else if (progress.phase === 'eps') {
                phaseText = '[Fetching EPS] ';
            } else if (progress.phase === 'combining') {
                phaseText = '[Building valuations] ';
            } else if (progress.phase === 'missing') {
                phaseText = '[Fetching new] ';
            }

            // Show rate limiting warning if present
            let rateLimitText = '';
            if (progress.rate_limited && progress.rate_limited > 0) {
                rateLimitText = ` ⚠️ ${progress.rate_limited} rate limited`;
            }

            document.getElementById('progress-text').textContent =
                `${phaseText}${progress.current} / ${progress.total} (${progress.ticker})${rateLimitText}`;

            if (!screenerInterval) {
                screenerInterval = setInterval(checkScreenerProgress, 1000);
            }
        } else {
            showScreenerButtons();
            document.getElementById('screener-stop-btn').style.display = 'none';
            document.getElementById('screener-progress').style.display = 'none';

            if (screenerInterval) {
                clearInterval(screenerInterval);
                screenerInterval = null;
            }

            if (progress.status === 'complete') {
                loadScreener();
            }
        }
    } catch (error) {
        console.error('Error checking progress:', error);
    }
}

// SEC Data Management
let secInterval = null;

async function loadSecStatus() {
    try {
        const response = await fetch('/api/sec/status');
        const data = await response.json();
        renderSecStatus(data);

        // If update is running, start polling
        if (data.update.status === 'running') {
            if (!secInterval) {
                secInterval = setInterval(checkSecProgress, 1000);
            }
            document.getElementById('sec-update-btn').style.display = 'none';
            document.getElementById('sec-stop-btn').style.display = 'inline-block';
            document.getElementById('sec-progress').style.display = 'block';
        }
    } catch (error) {
        console.error('Error loading SEC status:', error);
    }
}

function renderSecStatus(data) {
    const cache = data.cache;

    // CIK mapping status
    const cikStatus = document.getElementById('sec-cik-status');
    if (cache.cik_mapping.count > 0) {
        const updated = cache.cik_mapping.updated ?
            new Date(cache.cik_mapping.updated).toLocaleDateString() : 'Unknown';
        cikStatus.textContent = `CIK Mapping: ${cache.cik_mapping.count.toLocaleString()} tickers (${updated})`;
    } else {
        cikStatus.textContent = 'CIK Mapping: Not loaded';
    }

    // Companies status
    const companiesStatus = document.getElementById('sec-companies-status');
    if (cache.companies.count > 0) {
        const updated = cache.companies.last_full_update ?
            new Date(cache.companies.last_full_update).toLocaleDateString() : 'Never';
        companiesStatus.textContent = `EPS Data: ${cache.companies.count} companies (Last full update: ${updated})`;
    } else {
        companiesStatus.textContent = 'EPS Data: No data cached';
    }
}

async function startSecUpdate() {
    try {
        const response = await fetch('/api/sec/update', { method: 'POST' });
        const data = await response.json();

        if (data.status === 'started') {
            document.getElementById('sec-update-btn').style.display = 'none';
            document.getElementById('sec-stop-btn').style.display = 'inline-block';
            document.getElementById('sec-progress').style.display = 'block';

            secInterval = setInterval(checkSecProgress, 1000);
        }
    } catch (error) {
        console.error('Error starting SEC update:', error);
    }
}

async function stopSecUpdate() {
    try {
        await fetch('/api/sec/stop', { method: 'POST' });
    } catch (error) {
        console.error('Error stopping SEC update:', error);
    }
}

async function checkSecProgress() {
    try {
        const response = await fetch('/api/sec/progress');
        const progress = await response.json();

        if (progress.status === 'running') {
            const pct = progress.total > 0 ? (progress.current / progress.total * 100) : 0;
            document.getElementById('sec-progress-fill').style.width = pct + '%';
            document.getElementById('sec-progress-text').textContent =
                `${progress.current} / ${progress.total} (${progress.ticker})`;
        } else {
            document.getElementById('sec-update-btn').style.display = 'inline-block';
            document.getElementById('sec-stop-btn').style.display = 'none';
            document.getElementById('sec-progress').style.display = 'none';

            if (secInterval) {
                clearInterval(secInterval);
                secInterval = null;
            }

            if (progress.status === 'complete') {
                loadSecStatus();
            }
        }
    } catch (error) {
        console.error('Error checking SEC progress:', error);
    }
}

// Recommendations Tab
async function loadRecommendations() {
    const loadingEl = document.getElementById('recommendations-loading');
    const listEl = document.getElementById('recommendations-list');

    if (loadingEl) loadingEl.style.display = 'block';
    if (listEl) listEl.innerHTML = '';

    try {
        const response = await fetch('/api/recommendations');
        const data = await response.json();

        if (loadingEl) loadingEl.style.display = 'none';

        if (data.error) {
            listEl.innerHTML = `<p class="error-message">${data.error}</p>`;
            return;
        }

        if (!data.recommendations || data.recommendations.length === 0) {
            listEl.innerHTML = '<p class="no-data">No recommendations available. Run a data refresh first.</p>';
            return;
        }

        let html = `
            <div class="recommendations-header">
                <span class="analyzed-count">Analyzed ${data.total_analyzed} stocks</span>
                <div class="index-legend">
                    <span class="legend-item legend-dow">Dow 30</span>
                    <span class="legend-item legend-sp500">S&P 500</span>
                    <span class="legend-item legend-nasdaq">NASDAQ</span>
                </div>
            </div>
        `;

        data.recommendations.forEach((stock, index) => {
            const rank = index + 1;
            const priceClass = stock.price_vs_value < -20 ? 'very-undervalued' :
                              stock.price_vs_value < 0 ? 'undervalued' : 'overvalued';
            const selloffClass = stock.in_selloff ? `selloff-${stock.selloff_severity}` : '';

            // Determine index class for color coding (priority: Dow > S&P 500 > NASDAQ)
            const indexesLower = (stock.indexes || []).map(i => i.toLowerCase());
            let indexClass = '';
            if (indexesLower.some(i => i.includes('dow') || i.includes('djia'))) {
                indexClass = 'index-dow';
            } else if (indexesLower.some(i => i.includes('sp500') || i.includes('s&p 500'))) {
                indexClass = 'index-sp500';
            } else if (indexesLower.some(i => i.includes('nasdaq'))) {
                indexClass = 'index-nasdaq';
            }

            const updatedText = stock.updated ? formatTimeAgo(stock.updated) : '';

            // Create colored index badges
            const indexBadges = (stock.indexes || []).map(idx => {
                const idxLower = idx.toLowerCase();
                let badgeClass = 'index-badge';
                if (idxLower.includes('dow') || idxLower.includes('djia')) {
                    badgeClass += ' index-badge-dow';
                } else if (idxLower.includes('sp500') || idxLower.includes('s&p 500')) {
                    badgeClass += ' index-badge-sp500';
                } else if (idxLower.includes('nasdaq')) {
                    badgeClass += ' index-badge-nasdaq';
                }
                return `<span class="${badgeClass}">${idx}</span>`;
            }).join('');

            html += `
                <div class="recommendation-card ${selloffClass}">
                    <div class="recommendation-rank">#${rank}</div>
                    <div class="recommendation-main">
                        <div class="recommendation-header">
                            <a href="#research" class="recommendation-ticker" onclick="lookupTicker('${stock.ticker}', event)">${stock.ticker}</a>
                            <span class="recommendation-name">${stock.company_name}</span>
                            ${indexBadges ? `<span class="recommendation-indexes">${indexBadges}</span>` : ''}
                            ${updatedText ? `<span class="stock-updated" title="${new Date(stock.updated).toLocaleString()}">${updatedText}</span>` : ''}
                            <span class="recommendation-score">Score: ${stock.score}</span>
                        </div>
                        <div class="recommendation-metrics">
                            <div class="metric">
                                <span class="metric-label">Price</span>
                                <span class="metric-value">$${stock.current_price.toFixed(2)}</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">Est. Value</span>
                                <span class="metric-value">$${stock.estimated_value ? stock.estimated_value.toFixed(2) : 'N/A'}</span>
                            </div>
                            <div class="metric ${priceClass}">
                                <span class="metric-label">vs Value</span>
                                <span class="metric-value">${stock.price_vs_value > 0 ? '+' : ''}${stock.price_vs_value.toFixed(0)}%</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">Annual Div</span>
                                <span class="metric-value">$${stock.annual_dividend.toFixed(2)}/yr <small>(${stock.dividend_yield.toFixed(1)}% yield)</small></span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">Off High</span>
                                <span class="metric-value">${stock.off_high_pct ? stock.off_high_pct.toFixed(0) : 0}%</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">EPS Years</span>
                                <span class="metric-value">${stock.eps_years}</span>
                            </div>
                        </div>
                        <div class="recommendation-reasons">
                            <strong>Why recommended:</strong>
                            <ul>
                                ${stock.reasons.map(r => `<li>${r}</li>`).join('')}
                            </ul>
                        </div>
                    </div>
                </div>
            `;
        });

        // Add criteria explanation at the bottom
        html += `
            <div class="criteria-explanation">
                <h4>Scoring Criteria</h4>
                <ul>
                    <li><strong>Undervaluation:</strong> ${data.criteria.undervaluation}</li>
                    <li><strong>Dividend:</strong> ${data.criteria.dividend}</li>
                    <li><strong>Selloff:</strong> ${data.criteria.selloff}</li>
                </ul>
            </div>
        `;

        listEl.innerHTML = html;

    } catch (error) {
        console.error('Error loading recommendations:', error);
        if (loadingEl) loadingEl.style.display = 'none';
        if (listEl) listEl.innerHTML = '<p class="error-message">Failed to load recommendations</p>';
    }
}

// Data Sets Tab
let datasetsInterval = null;

async function loadDatasets() {
    try {
        // Load data status and recommendations in parallel
        const [statusResponse, recommendationsResponse] = await Promise.all([
            fetch('/api/data-status'),
            fetch('/api/eps-recommendations')
        ]);
        const data = await statusResponse.json();
        const recommendations = await recommendationsResponse.json();

        // Update SEC stats using consolidated data
        document.getElementById('sec-company-count').textContent = data.sec.companies_cached;
        document.getElementById('sec-cik-count').textContent = data.sec.cik_mappings.toLocaleString();

        if (data.sec.cik_updated) {
            const cikDate = new Date(data.sec.cik_updated);
            document.getElementById('sec-cik-updated').textContent = cikDate.toLocaleDateString();
        } else {
            document.getElementById('sec-cik-updated').textContent = 'Never';
        }

        // Show SEC availability breakdown
        const secAvail = data.sec.companies_cached || 0;
        const secUnavail = data.sec.sec_unavailable || 0;
        const secUnknown = data.sec.sec_unknown || 0;
        const totalTickers = data.consolidated?.total_tickers || (secAvail + secUnavail + secUnknown);

        if (totalTickers > 0) {
            const availPct = Math.round((secAvail / totalTickers) * 100);
            document.getElementById('sec-freshness').textContent =
                `${availPct}% have SEC EPS (${secAvail}/${totalTickers})`;
        } else {
            document.getElementById('sec-freshness').textContent = 'No data';
        }

        // Render index cards in compact grid
        const indexCardsDiv = document.getElementById('index-cards');
        let indexHtml = '<h3>Market Indexes</h3><div class="index-cards-grid">';

        for (const idx of data.indices) {
            const coverageClass = idx.coverage_pct >= 90 ? 'good' :
                                  idx.coverage_pct >= 50 ? 'partial' : 'low';

            indexHtml += `
                <div class="index-card-compact">
                    <div class="index-card-header">
                        <span class="index-name">${idx.short_name}</span>
                        <span class="coverage-badge ${coverageClass}">${idx.coverage_pct}%</span>
                    </div>
                    <div class="index-card-stats">
                        <span class="index-stat">${idx.valuations_count}/${idx.total_tickers}</span>
                        <span class="index-stat-label">tickers</span>
                    </div>
                </div>
            `;
        }
        indexHtml += '</div>';
        indexCardsDiv.innerHTML = indexHtml;

        // Render refresh summary if available
        renderRefreshSummary(data.refresh_summary, data.excluded_tickers);

        // Render EPS recommendations
        renderEpsRecommendations(recommendations);

        // Load all tickers table
        loadAllTickersTable();

        // Handle refresh status
        if (data.refresh.running) {
            showRefreshProgress(data.refresh.progress);
            if (!datasetsInterval) {
                datasetsInterval = setInterval(pollDatasetRefresh, 1000);
            }
        } else {
            document.getElementById('refresh-status').style.display = 'none';
            if (datasetsInterval) {
                clearInterval(datasetsInterval);
                datasetsInterval = null;
            }
        }

    } catch (error) {
        console.error('Error loading datasets:', error);
    }
}

function renderRefreshSummary(summary, excludedInfo) {
    const detailsDiv = document.getElementById('datasets-details');
    const inlineRefresh = document.getElementById('data-refresh-inline');

    // Render inline refresh button in SEC EDGAR card
    if (inlineRefresh) {
        const excludedCount = excludedInfo?.count || 0;
        const pendingCount = excludedInfo?.pending_failures || 0;
        let inlineHtml = `
            <button class="btn btn-primary btn-sm" onclick="startGlobalRefresh()">Update Prices</button>
        `;
        if (excludedCount > 0 || pendingCount > 0) {
            inlineHtml += `<div class="excluded-info-compact">`;
            if (excludedCount > 0) {
                inlineHtml += `<span class="excluded-count-sm">${excludedCount} excluded</span>`;
            }
            if (pendingCount > 0) {
                inlineHtml += `<span class="pending-count-sm">${pendingCount} pending</span>`;
            }
            inlineHtml += `<button class="btn-link-sm" onclick="clearExcludedTickers()">Reset</button>`;
            inlineHtml += `</div>`;
        }
        inlineRefresh.innerHTML = inlineHtml;
    }

    if (!detailsDiv) return;

    let html = '';

    if (summary) {
        const lastRefresh = summary.last_refresh ? new Date(summary.last_refresh).toLocaleString() : 'Never';
        const totalTickers = summary.total_tickers || 0;
        const fullData = summary.full_data || 0;
        const noEps = summary.no_eps_data || 0;
        const noPrice = summary.no_price_data || 0;
        const excludedCount = summary.excluded_count || 0;

        html += `
            <div class="dataset-card refresh-summary-card">
                <h3>Last Refresh Summary</h3>
                <p class="refresh-time">Last refreshed: ${lastRefresh}</p>
                <div class="summary-breakdown">
                    <div class="breakdown-item success">
                        <span class="breakdown-count">${fullData}</span>
                        <span class="breakdown-label">Full data (price + EPS)</span>
                    </div>
                    <div class="breakdown-item warning">
                        <span class="breakdown-count">${noEps}</span>
                        <span class="breakdown-label">Price only (no SEC EPS available)</span>
                    </div>
                    <div class="breakdown-item error">
                        <span class="breakdown-count">${noPrice}</span>
                        <span class="breakdown-label">No price data (new unavailable)</span>
                    </div>
        `;

        // Show excluded count if any were skipped
        if (excludedCount > 0) {
            html += `
                    <div class="breakdown-item skipped">
                        <span class="breakdown-count">${excludedCount}</span>
                        <span class="breakdown-label">Skipped (previously unavailable)</span>
                    </div>
            `;
        }

        html += `
                </div>
        `;

        // Show sample of tickers with no price
        if (summary.no_price_tickers && summary.no_price_tickers.length > 0) {
            const sample = summary.no_price_tickers.slice(0, 20).join(', ');
            const more = summary.no_price_data > 20 ? ` ... and ${summary.no_price_data - 20} more` : '';
            html += `
                <div class="breakdown-detail">
                    <strong>New unavailable:</strong> <span class="ticker-list">${sample}${more}</span>
                </div>
            `;
        }

        html += '</div>';
    }

    detailsDiv.innerHTML = html;
}

async function clearExcludedTickers() {
    if (!confirm('This will clear the excluded tickers list. The next refresh will re-check all tickers, which may take longer. Continue?')) {
        return;
    }

    try {
        const response = await fetch('/api/excluded-tickers/clear', { method: 'POST' });
        const data = await response.json();
        alert(data.message);
        loadDatasets(); // Reload to update the display
    } catch (error) {
        console.error('Error clearing excluded tickers:', error);
        alert('Failed to clear excluded tickers');
    }
}

async function startGlobalRefresh() {
    const updateBtn = document.querySelector('.update-price-btn');
    if (updateBtn) {
        updateBtn.disabled = true;
        updateBtn.textContent = 'Updating...';
    }

    try {
        const response = await fetch('/api/refresh', { method: 'POST' });
        const data = await response.json();

        if (data.error) {
            alert('Error: ' + data.error);
            if (updateBtn) {
                updateBtn.disabled = false;
                updateBtn.textContent = 'Update Price Data';
            }
            return;
        }

        if (data.status === 'started') {
            // Show progress on datasets tab
            const datasetsProgress = document.getElementById('refresh-status');
            if (datasetsProgress) {
                datasetsProgress.style.display = 'block';
            }

            // Start polling for progress
            if (!datasetsInterval) {
                datasetsInterval = setInterval(pollDatasetRefresh, 1000);
            }
        }
    } catch (error) {
        console.error('Error starting refresh:', error);
        alert('Failed to start data refresh');
        if (updateBtn) {
            updateBtn.disabled = false;
            updateBtn.textContent = 'Update Price Data';
        }
    }
}

function showRefreshProgress(progress) {
    const statusDiv = document.getElementById('refresh-status');
    statusDiv.style.display = 'block';

    const pct = progress.total > 0 ? Math.round((progress.current / progress.total) * 100) : 0;
    document.getElementById('refresh-progress-bar').style.width = `${pct}%`;

    const phaseText = progress.phase === 'sec_data' ? 'Fetching SEC Data' :
                      progress.phase === 'prices' ? 'Downloading Prices' :
                      progress.phase === 'valuations' ? 'Calculating Valuations' :
                      progress.phase === 'saving' ? 'Saving Data' : 'Initializing';

    document.getElementById('refresh-phase').textContent = phaseText;
    document.getElementById('refresh-ticker').textContent = progress.ticker || '';
    document.getElementById('refresh-count').textContent = `${progress.current} / ${progress.total} (${pct}%)`;
}

async function pollDatasetRefresh() {
    try {
        const response = await fetch('/api/screener/progress');
        const progress = await response.json();

        if (progress.status === 'running') {
            showRefreshProgress(progress);
        } else {
            document.getElementById('refresh-status').style.display = 'none';
            if (datasetsInterval) {
                clearInterval(datasetsInterval);
                datasetsInterval = null;
            }
            // Reload data to show updated stats
            loadDatasets();
        }
    } catch (error) {
        console.error('Error polling refresh:', error);
    }
}

async function stopRefresh() {
    try {
        await fetch('/api/screener/stop', { method: 'POST' });
    } catch (error) {
        console.error('Error stopping refresh:', error);
    }
}

function renderEpsRecommendations(recommendations) {
    const summaryDiv = document.getElementById('eps-recommendations-summary');
    const listDiv = document.getElementById('eps-recommendations-list');
    const statusBadge = document.getElementById('eps-status-badge');
    const epsSection = document.getElementById('eps-section');

    if (!summaryDiv || !listDiv) return;

    // Render summary
    const needsUpdate = recommendations.needs_update_count || 0;
    const recentlyUpdated = recommendations.recently_updated_count || 0;
    const total = recommendations.total_cached || 0;
    const totalMissing = recommendations.total_missing || 0;
    const totalUnavailable = recommendations.total_unavailable || 0;

    let summaryClass = 'recommendations-ok';
    let summaryIcon = '✓';
    let summaryText = 'All SEC data is up to date';
    let badgeClass = 'status-ok';
    let badgeText = 'Up to date';

    if (totalMissing > 0) {
        summaryClass = 'recommendations-high';
        summaryIcon = '⚠';
        summaryText = `${totalMissing} ticker${totalMissing > 1 ? 's' : ''} not yet fetched`;
        badgeClass = 'status-warning';
        badgeText = `${totalMissing} missing`;
    } else if (needsUpdate > 0) {
        summaryClass = needsUpdate > 10 ? 'recommendations-high' : 'recommendations-medium';
        summaryIcon = needsUpdate > 10 ? '⚠' : '○';
        summaryText = `${needsUpdate} ticker${needsUpdate > 1 ? 's' : ''} may have new 10-K filings available`;
        badgeClass = needsUpdate > 10 ? 'status-warning' : 'status-info';
        badgeText = `${needsUpdate} updates`;
    }

    // Update the header badge
    if (statusBadge) {
        statusBadge.className = `status-badge ${badgeClass}`;
        statusBadge.textContent = badgeText;
    }

    // Auto-collapse if all OK
    if (epsSection && summaryClass === 'recommendations-ok') {
        epsSection.classList.add('collapsed');
    }

    summaryDiv.innerHTML = `
        <div class="recommendations-badge ${summaryClass}">
            <span class="badge-icon">${summaryIcon}</span>
            <span class="badge-text">${summaryText}</span>
        </div>
        <div class="recommendations-stats">
            <span class="stat">SEC EPS: ${recentlyUpdated}</span>
            <span class="stat">Needs Update: ${needsUpdate}</span>
            <span class="stat">Not Fetched: ${totalMissing}</span>
            <span class="stat" title="SEC has no EPS data for these - uses yfinance">SEC N/A: ${totalUnavailable}</span>
        </div>
    `;

    // Render list of recommendations
    if (recommendations.top_updates && recommendations.top_updates.length > 0) {
        let listHtml = `
            <table class="summary-table recommendations-table">
                <thead>
                    <tr>
                        <th>Ticker</th>
                        <th>Company</th>
                        <th>Latest FY</th>
                        <th>Next FY End</th>
                        <th>Expected Filing</th>
                        <th>Priority</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody>
        `;

        for (const rec of recommendations.top_updates) {
            const priorityClass = rec.priority === 'high' ? 'priority-high' :
                                  rec.priority === 'medium' ? 'priority-medium' : 'priority-normal';
            const priorityLabel = rec.priority === 'high' ? 'High' :
                                  rec.priority === 'medium' ? 'Medium' : 'Normal';

            listHtml += `
                <tr class="clickable-row" data-ticker="${rec.ticker}">
                    <td class="stock-ticker">${rec.ticker}</td>
                    <td class="stock-name-cell">${rec.company_name || ''}</td>
                    <td>FY${rec.latest_fy || '-'}</td>
                    <td>${rec.next_fy_end || '-'}</td>
                    <td>${rec.expected_filing || '-'}</td>
                    <td><span class="priority-badge ${priorityClass}">${priorityLabel}</span></td>
                    <td>
                        <button class="update-ticker-btn" onclick="event.stopPropagation(); updateTickerEps('${rec.ticker}')">Update</button>
                    </td>
                </tr>
            `;
        }

        listHtml += '</tbody></table>';

        if (recommendations.needs_update_count > 20) {
            listHtml += `<p class="more-notice">Showing top 20 of ${recommendations.needs_update_count} tickers needing updates</p>`;
        }

        listDiv.innerHTML = listHtml;

        // Add click handler for rows
        const table = listDiv.querySelector('.recommendations-table');
        if (table) {
            table.addEventListener('click', function(e) {
                const row = e.target.closest('.clickable-row');
                if (row && !e.target.closest('button')) {
                    viewCompany(row.dataset.ticker);
                }
            });
        }
    } else {
        let listHtml = '';
        let hasContent = false;

        // Show missing tickers (not yet fetched)
        if (recommendations.missing_by_index && Object.keys(recommendations.missing_by_index).length > 0) {
            hasContent = true;
            listHtml += '<h4>Not Yet Fetched</h4>';
            listHtml += '<p class="section-desc">These tickers haven\'t been fetched yet. Use Refresh Data to fetch them.</p>';

            for (const [indexId, indexData] of Object.entries(recommendations.missing_by_index)) {
                listHtml += `
                    <div class="missing-index-section">
                        <div class="missing-index-header">
                            <span class="index-name">${indexData.short_name}</span>
                            <span class="missing-count">${indexData.missing_count} of ${indexData.total_count} not fetched</span>
                        </div>
                        <div class="missing-tickers">
                            ${indexData.missing_tickers.map(t => `<span class="missing-ticker" onclick="viewCompany('${t}')">${t}</span>`).join('')}
                            ${indexData.missing_count > 50 ? `<span class="more-tickers">+${indexData.missing_count - 50} more</span>` : ''}
                        </div>
                    </div>
                `;
            }
        }

        // Show SEC unavailable tickers (uses yfinance)
        if (recommendations.unavailable_by_index && Object.keys(recommendations.unavailable_by_index).length > 0) {
            hasContent = true;
            listHtml += '<h4>SEC EPS Data Not Available</h4>';
            listHtml += '<p class="section-desc">These companies don\'t have EPS in SEC XBRL format. Valuations use yfinance data instead.</p>';

            for (const [indexId, indexData] of Object.entries(recommendations.unavailable_by_index)) {
                listHtml += `
                    <div class="unavailable-index-section">
                        <div class="missing-index-header">
                            <span class="index-name">${indexData.short_name}</span>
                            <span class="unavailable-count">${indexData.unavailable_count} use yfinance</span>
                        </div>
                        <div class="unavailable-tickers">
                            ${indexData.unavailable_tickers.map(t => `<span class="unavailable-ticker" onclick="viewCompany('${t}')">${t}</span>`).join('')}
                            ${indexData.unavailable_count > 50 ? `<span class="more-tickers">+${indexData.unavailable_count - 50} more</span>` : ''}
                        </div>
                    </div>
                `;
            }
        }

        if (hasContent) {
            listDiv.innerHTML = listHtml;
        } else {
            listDiv.innerHTML = '<p class="empty-state">All tickers are up to date</p>';
        }
    }
}

async function updateTickerEps(ticker) {
    const btn = event.target;
    btn.disabled = true;
    btn.textContent = 'Updating...';

    try {
        // Force refresh SEC data for this ticker
        const response = await fetch(`/api/valuation/${ticker}/refresh`, { method: 'POST' });
        const data = await response.json();

        if (data.error) {
            btn.textContent = 'Error';
            setTimeout(() => {
                btn.disabled = false;
                btn.textContent = 'Update';
            }, 2000);
        } else {
            btn.textContent = 'Done!';
            btn.classList.add('success');
            // Reload recommendations after a moment
            setTimeout(() => {
                loadDatasets();
            }, 1000);
        }
    } catch (error) {
        console.error('Error updating ticker:', error);
        btn.textContent = 'Error';
        setTimeout(() => {
            btn.disabled = false;
            btn.textContent = 'Update';
        }, 2000);
    }
}

// All Tickers Table
let allTickersData = [];
let allTickersSortColumn = 'ticker';
let allTickersSortAsc = true;
let allTickersCurrentPage = 1;
let allTickersPageSize = 50;
let allTickersFilteredData = [];

async function loadAllTickersTable() {
    try {
        const response = await fetch('/api/all-tickers');
        const data = await response.json();
        allTickersData = data.tickers || [];

        // Set up event listeners
        setupAllTickersControls();

        // Render the table
        renderAllTickersTable();
    } catch (error) {
        console.error('Error loading all tickers:', error);
        const tbody = document.getElementById('all-tickers-body');
        if (tbody) {
            tbody.innerHTML = '<tr><td colspan="9" class="error">Error loading tickers</td></tr>';
        }
    }
}

function setupAllTickersControls() {
    // Search input
    const searchInput = document.getElementById('ticker-search');
    if (searchInput) {
        searchInput.removeEventListener('input', handleTickerSearch);
        searchInput.addEventListener('input', handleTickerSearch);
    }

    // Index filter
    const indexFilter = document.getElementById('ticker-filter-index');
    if (indexFilter) {
        indexFilter.removeEventListener('change', handleTickerFilterChange);
        indexFilter.addEventListener('change', handleTickerFilterChange);
    }

    // Source filter
    const sourceFilter = document.getElementById('ticker-filter-source');
    if (sourceFilter) {
        sourceFilter.removeEventListener('change', handleTickerFilterChange);
        sourceFilter.addEventListener('change', handleTickerFilterChange);
    }

    // Updated filter
    const updatedFilter = document.getElementById('ticker-filter-updated');
    if (updatedFilter) {
        updatedFilter.removeEventListener('change', handleTickerFilterChange);
        updatedFilter.addEventListener('change', handleTickerFilterChange);
    }

    // Page size selector
    const pageSizeSelect = document.getElementById('ticker-page-size');
    if (pageSizeSelect) {
        pageSizeSelect.removeEventListener('change', handleTickerPageSizeChange);
        pageSizeSelect.addEventListener('change', handleTickerPageSizeChange);
    }

    // Sortable headers
    const headers = document.querySelectorAll('#all-tickers-table th.sortable');
    headers.forEach(th => {
        th.removeEventListener('click', handleTickerSort);
        th.addEventListener('click', handleTickerSort);
    });
}

function handleTickerFilterChange() {
    allTickersCurrentPage = 1; // Reset to first page on filter change
    renderAllTickersTable();
}

function handleTickerPageSizeChange(e) {
    allTickersPageSize = parseInt(e.target.value) || 50;
    allTickersCurrentPage = 1; // Reset to first page
    renderAllTickersTable();
}

function tickerPrevPage() {
    if (allTickersCurrentPage > 1) {
        allTickersCurrentPage--;
        renderAllTickersTable();
    }
}

function tickerNextPage() {
    const totalPages = Math.ceil(allTickersFilteredData.length / allTickersPageSize);
    if (allTickersCurrentPage < totalPages) {
        allTickersCurrentPage++;
        renderAllTickersTable();
    }
}

function handleTickerSearch() {
    allTickersCurrentPage = 1; // Reset to first page on search
    renderAllTickersTable();
}

function handleTickerSort(e) {
    const th = e.target.closest('th[data-sort]');
    if (!th) return;
    const column = th.dataset.sort;
    if (column === allTickersSortColumn) {
        allTickersSortAsc = !allTickersSortAsc;
    } else {
        allTickersSortColumn = column;
        allTickersSortAsc = true;
    }

    // Reset to page 1 when sort changes
    allTickersCurrentPage = 1;

    // Update header indicators
    document.querySelectorAll('#all-tickers-table th.sortable').forEach(header => {
        header.classList.remove('sorted-asc', 'sorted-desc');
    });
    th.classList.add(allTickersSortAsc ? 'sorted-asc' : 'sorted-desc');

    renderAllTickersTable();
}

function renderAllTickersTable() {
    const tbody = document.getElementById('all-tickers-body');
    const countSpan = document.getElementById('ticker-count');
    const pageInfo = document.getElementById('ticker-page-info');
    const prevBtn = document.getElementById('ticker-prev-btn');
    const nextBtn = document.getElementById('ticker-next-btn');
    if (!tbody) return;

    // Get filter values
    const searchValue = (document.getElementById('ticker-search')?.value || '').toLowerCase();
    const indexFilter = document.getElementById('ticker-filter-index')?.value || '';
    const sourceFilter = document.getElementById('ticker-filter-source')?.value || '';
    const updatedFilter = document.getElementById('ticker-filter-updated')?.value || '';

    // Calculate time thresholds for updated filter (showing OLD/stale data)
    const now = new Date();
    const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const weekAgo = new Date(now - 7 * 24 * 60 * 60 * 1000);
    const monthAgo = new Date(now - 30 * 24 * 60 * 60 * 1000);
    const threeMonthsAgo = new Date(now - 90 * 24 * 60 * 60 * 1000);

    // Filter data
    allTickersFilteredData = allTickersData.filter(t => {
        // Search filter
        if (searchValue) {
            const matchesTicker = t.ticker.toLowerCase().includes(searchValue);
            const matchesName = (t.company_name || '').toLowerCase().includes(searchValue);
            if (!matchesTicker && !matchesName) return false;
        }

        // Index filter
        if (indexFilter && !(t.indexes || []).includes(indexFilter)) return false;

        // Source filter
        if (sourceFilter && t.eps_source !== sourceFilter) return false;

        // Updated filter - shows STALE data (older than threshold)
        if (updatedFilter) {
            const updated = t.valuation_updated ? new Date(t.valuation_updated) : null;
            switch (updatedFilter) {
                case 'not-today':
                    // Show items NOT updated today (older than today or never)
                    if (updated && updated >= todayStart) return false;
                    break;
                case 'older-week':
                    // Show items older than 1 week or never updated
                    if (updated && updated >= weekAgo) return false;
                    break;
                case 'older-month':
                    // Show items older than 1 month or never updated
                    if (updated && updated >= monthAgo) return false;
                    break;
                case 'older-3months':
                    // Show items older than 3 months or never updated
                    if (updated && updated >= threeMonthsAgo) return false;
                    break;
                case 'never':
                    // Show only items that were never updated
                    if (updated) return false;
                    break;
            }
        }

        return true;
    });

    // Sort data
    allTickersFilteredData.sort((a, b) => {
        let aVal = a[allTickersSortColumn];
        let bVal = b[allTickersSortColumn];

        // Handle nulls - for date sorting, nulls go to end
        if (allTickersSortColumn === 'valuation_updated') {
            if (!aVal && !bVal) return 0;
            if (!aVal) return allTickersSortAsc ? 1 : -1;  // nulls last when ascending
            if (!bVal) return allTickersSortAsc ? -1 : 1;
            aVal = new Date(aVal).getTime();
            bVal = new Date(bVal).getTime();
        } else {
            // Handle nulls for other columns
            if (aVal === null || aVal === undefined) aVal = '';
            if (bVal === null || bVal === undefined) bVal = '';

            // Numeric sort for these columns
            if (['current_price', 'eps_avg', 'eps_years', 'estimated_value', 'price_vs_value'].includes(allTickersSortColumn)) {
                aVal = parseFloat(aVal) || 0;
                bVal = parseFloat(bVal) || 0;
            }
        }

        if (aVal < bVal) return allTickersSortAsc ? -1 : 1;
        if (aVal > bVal) return allTickersSortAsc ? 1 : -1;
        return 0;
    });

    // Calculate pagination
    const totalItems = allTickersFilteredData.length;
    const totalPages = Math.max(1, Math.ceil(totalItems / allTickersPageSize));

    // Ensure current page is valid
    if (allTickersCurrentPage > totalPages) {
        allTickersCurrentPage = totalPages;
    }

    const startIndex = (allTickersCurrentPage - 1) * allTickersPageSize;
    const endIndex = Math.min(startIndex + allTickersPageSize, totalItems);
    const displayData = allTickersFilteredData.slice(startIndex, endIndex);

    // Update count and pagination info
    if (countSpan) {
        if (totalItems === 0) {
            countSpan.textContent = `0 of ${allTickersData.length} tickers`;
        } else {
            countSpan.textContent = `${startIndex + 1}-${endIndex} of ${totalItems} tickers`;
        }
    }

    if (pageInfo) {
        pageInfo.textContent = `Page ${allTickersCurrentPage} of ${totalPages}`;
    }

    // Update pagination button states
    if (prevBtn) {
        prevBtn.disabled = allTickersCurrentPage <= 1;
    }
    if (nextBtn) {
        nextBtn.disabled = allTickersCurrentPage >= totalPages;
    }

    if (displayData.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" class="empty">No tickers match your filters</td></tr>';
        return;
    }

    let html = '';
    for (const t of displayData) {
        const priceClass = t.current_price ? '' : 'no-data';
        const epsClass = (t.eps_source === 'sec' || t.eps_source === 'sec_edgar') ? 'source-sec' : 'source-yf';
        const vsValueClass = t.price_vs_value > 0 ? 'overvalued' : t.price_vs_value < -20 ? 'undervalued' : '';

        // Format updated time
        let updatedStr = '-';
        if (t.valuation_updated) {
            const updated = new Date(t.valuation_updated);
            const now = new Date();
            const diffMs = now - updated;
            const diffHrs = Math.floor(diffMs / (1000 * 60 * 60));
            const diffDays = Math.floor(diffHrs / 24);

            if (diffDays > 0) {
                updatedStr = `${diffDays}d ago`;
            } else if (diffHrs > 0) {
                updatedStr = `${diffHrs}h ago`;
            } else {
                const diffMins = Math.floor(diffMs / (1000 * 60));
                updatedStr = `${diffMins}m ago`;
            }
        }

        html += `
            <tr class="ticker-row" onclick="viewCompany('${t.ticker}')">
                <td class="ticker-col">${t.ticker}</td>
                <td class="company-col" title="${t.company_name || ''}">${(t.company_name || '').substring(0, 25)}${(t.company_name || '').length > 25 ? '...' : ''}</td>
                <td class="${priceClass}">${t.current_price ? '$' + t.current_price.toFixed(2) : '-'}</td>
                <td>${t.eps_avg ? t.eps_avg.toFixed(2) : '-'}</td>
                <td>${t.eps_years || '-'}</td>
                <td class="${epsClass}">${formatEpsSource(t.eps_source, true)}</td>
                <td>${t.estimated_value ? '$' + t.estimated_value.toFixed(0) : '-'}</td>
                <td class="${vsValueClass}">${t.price_vs_value !== null ? (t.price_vs_value > 0 ? '+' : '') + t.price_vs_value.toFixed(0) + '%' : '-'}</td>
                <td class="updated-col" title="${t.valuation_updated || ''}">${updatedStr}</td>
            </tr>
        `;
    }

    tbody.innerHTML = html;
}

// =====================
// Provider Settings
// =====================

let providerConfig = null;

async function loadProviderSettings() {
    try {
        const response = await fetch('/api/providers/config');
        providerConfig = await response.json();

        renderProviderStatus();
        renderProviderOrder();
        renderApiKeyStatus();
        renderCacheSettings();
        loadCacheStats();
        loadIndexSettings();  // Load index toggle settings
    } catch (error) {
        console.error('Error loading provider settings:', error);
    }
}

function renderProviderStatus() {
    const container = document.getElementById('provider-list');
    if (!providerConfig || !providerConfig.available_providers) {
        container.innerHTML = '<span class="error">Failed to load providers</span>';
        return;
    }

    let html = '<div class="provider-grid">';
    for (const provider of providerConfig.available_providers) {
        const statusClass = provider.available ? 'status-available' : 'status-unavailable';
        const enabledClass = provider.enabled ? '' : 'provider-disabled';
        const statusText = !provider.available ? 'Not Configured' : (provider.enabled ? 'Enabled' : 'Disabled');
        const dataTypes = provider.data_types.join(', ');
        const batchBadge = provider.supports_batch ? '<span class="badge badge-batch">Batch</span>' : '';
        const toggleChecked = provider.enabled ? 'checked' : '';
        const toggleDisabled = !provider.available ? 'disabled' : '';

        html += `
            <div class="provider-item ${statusClass} ${enabledClass}">
                <div class="provider-header">
                    <div class="provider-name">${provider.display_name} ${batchBadge}</div>
                    <label class="toggle-switch">
                        <input type="checkbox" ${toggleChecked} ${toggleDisabled}
                            onchange="toggleProvider('${provider.name}', this.checked)">
                        <span class="toggle-slider"></span>
                    </label>
                </div>
                <div class="provider-types">Data: ${dataTypes}</div>
                <div class="provider-status-text">${statusText}</div>
            </div>
        `;
    }
    html += '</div>';
    container.innerHTML = html;
}

async function toggleProvider(providerName, enabled) {
    try {
        const response = await fetch('/api/providers/toggle', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({provider: providerName, enabled: enabled})
        });

        const result = await response.json();
        if (result.status === 'ok') {
            showNotification(result.message, 'success');
            // Refresh the provider config
            await loadProviderSettings();
        } else {
            showNotification('Failed: ' + result.message, 'error');
            // Revert the checkbox
            await loadProviderSettings();
        }
    } catch (error) {
        showNotification('Error: ' + error.message, 'error');
        await loadProviderSettings();
    }
}

function renderProviderOrder() {
    renderProviderOrderList('price', providerConfig.price_providers, 'price-provider-order');
    renderProviderOrderList('eps', providerConfig.eps_providers, 'eps-provider-order');
    renderProviderOrderList('dividend', providerConfig.dividend_providers, 'dividend-provider-order');
}

function renderProviderOrderList(dataType, providers, elementId) {
    const container = document.getElementById(elementId);
    if (!container) return;

    // Get all providers that support this data type
    const availableForType = providerConfig.available_providers
        .filter(p => p.data_types.includes(dataType))
        .map(p => p.name);

    // Add any configured providers not in available list
    const allProviders = [...new Set([...providers, ...availableForType])];

    let html = '';
    for (const providerName of allProviders) {
        const provider = providerConfig.available_providers.find(p => p.name === providerName);
        const displayName = provider ? provider.display_name : providerName;
        const isAvailable = provider && provider.available;
        const statusClass = isAvailable ? '' : 'provider-unavailable';
        const priority = providers.indexOf(providerName) + 1;

        html += `
            <li class="provider-order-item ${statusClass}" data-provider="${providerName}" draggable="true">
                <span class="drag-handle">☰</span>
                <span class="provider-priority">${priority > 0 ? priority : '-'}</span>
                <span class="provider-name">${displayName}</span>
                ${!isAvailable ? '<span class="provider-note">(not configured)</span>' : ''}
            </li>
        `;
    }
    container.innerHTML = html;

    // Add drag-and-drop event listeners
    const items = container.querySelectorAll('.provider-order-item');
    items.forEach(item => {
        item.addEventListener('dragstart', handleDragStart);
        item.addEventListener('dragover', handleDragOver);
        item.addEventListener('drop', handleDrop);
        item.addEventListener('dragend', handleDragEnd);
    });
}

let draggedItem = null;

function handleDragStart(e) {
    draggedItem = this;
    this.classList.add('dragging');
}

function handleDragOver(e) {
    e.preventDefault();
    const target = e.target.closest('.provider-order-item');
    if (target && target !== draggedItem) {
        const parent = target.parentNode;
        const children = Array.from(parent.children);
        const draggedIndex = children.indexOf(draggedItem);
        const targetIndex = children.indexOf(target);

        if (draggedIndex < targetIndex) {
            parent.insertBefore(draggedItem, target.nextSibling);
        } else {
            parent.insertBefore(draggedItem, target);
        }
    }
}

function handleDrop(e) {
    e.preventDefault();
}

function handleDragEnd() {
    this.classList.remove('dragging');
    draggedItem = null;
    updateProviderPriorities();
}

function updateProviderPriorities() {
    // Update priority numbers after drag
    document.querySelectorAll('.provider-order-list').forEach(list => {
        const items = list.querySelectorAll('.provider-order-item');
        items.forEach((item, index) => {
            item.querySelector('.provider-priority').textContent = index + 1;
        });
    });
}


// ============================================
// Index Settings Functions
// ============================================

let indexSettings = [];  // Cache of index settings

async function loadIndexSettings() {
    try {
        const response = await fetch('/api/indexes/settings');
        indexSettings = await response.json();
        renderIndexToggles();
    } catch (error) {
        console.error('Error loading index settings:', error);
        document.getElementById('index-toggles').innerHTML =
            '<span class="error">Error loading indexes</span>';
    }
}

function renderIndexToggles() {
    const container = document.getElementById('index-toggles');
    if (!container) return;

    // Filter out 'all' since it's a virtual index
    const indexes = indexSettings.filter(idx => idx.name !== 'all');

    if (indexes.length === 0) {
        container.innerHTML = '<span class="no-data">No indexes configured</span>';
        return;
    }

    container.innerHTML = indexes.map(idx => `
        <label class="index-toggle-item">
            <input type="checkbox"
                   data-index="${idx.name}"
                   ${idx.enabled ? 'checked' : ''}
                   onchange="onIndexToggle('${idx.name}', this.checked)">
            <span class="index-toggle-name">${idx.display_name || idx.name}</span>
            <span class="index-toggle-short">(${idx.short_name || idx.name})</span>
        </label>
    `).join('');
}

function onIndexToggle(indexName, enabled) {
    // Update local cache
    const idx = indexSettings.find(i => i.name === indexName);
    if (idx) idx.enabled = enabled ? 1 : 0;
}

function selectAllIndexes(enabled) {
    const checkboxes = document.querySelectorAll('#index-toggles input[type="checkbox"]');
    checkboxes.forEach(cb => {
        cb.checked = enabled;
        const indexName = cb.dataset.index;
        onIndexToggle(indexName, enabled);
    });
}

async function saveIndexSettings() {
    // Build settings object from current state
    const settings = {};
    indexSettings.filter(idx => idx.name !== 'all').forEach(idx => {
        settings[idx.name] = idx.enabled === 1;
    });

    try {
        const response = await fetch('/api/indexes/settings', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(settings)
        });

        const result = await response.json();
        if (result.success) {
            // Build message with ticker stats
            let message = 'Index settings saved.';
            if (result.tickers_disabled > 0) {
                message += ` ${result.tickers_disabled} tickers disabled.`;
            }
            if (result.tickers_enabled > 0) {
                message += ` ${result.tickers_enabled} tickers enabled.`;
            }
            showNotification(message, 'success');
            // Reload index dropdowns to reflect changes
            loadIndices();
            // Update All Prices button count
            updateAllPricesCount();
        } else {
            showNotification('Failed to save: ' + result.error, 'error');
        }
    } catch (error) {
        showNotification('Error saving index settings: ' + error.message, 'error');
    }
}

// Update the All Prices button with actual enabled ticker count
async function updateAllPricesCount() {
    const countEl = document.getElementById('all-prices-count');
    if (!countEl) return;

    try {
        const response = await fetch('/api/indexes/enabled-ticker-count');
        const data = await response.json();
        countEl.textContent = `(${data.count.toLocaleString()})`;
        // Update tooltip with index info
        const btn = document.getElementById('all-prices-btn');
        if (btn && data.enabled_indexes) {
            btn.title = `Update prices for ${data.enabled_indexes.length} enabled indexes (${data.count.toLocaleString()} tickers)`;
        }
    } catch (error) {
        console.error('Error fetching enabled ticker count:', error);
        countEl.textContent = '(?)';
    }
}


async function saveProviderOrder() {
    const priceOrder = getProviderOrder('price-provider-order');
    const epsOrder = getProviderOrder('eps-provider-order');
    const dividendOrder = getProviderOrder('dividend-provider-order');

    try {
        const response = await fetch('/api/providers/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                price_providers: priceOrder,
                eps_providers: epsOrder,
                dividend_providers: dividendOrder
            })
        });

        const result = await response.json();
        if (result.status === 'ok') {
            showNotification('Provider order saved successfully', 'success');
        } else {
            showNotification('Failed to save: ' + result.message, 'error');
        }
    } catch (error) {
        showNotification('Error saving provider order: ' + error.message, 'error');
    }
}

function getProviderOrder(elementId) {
    const container = document.getElementById(elementId);
    const items = container.querySelectorAll('.provider-order-item');
    return Array.from(items).map(item => item.dataset.provider);
}

function renderApiKeyStatus() {
    const fmpStatus = document.getElementById('fmp-key-status');
    const alpacaStatus = document.getElementById('alpaca-key-status');
    const alpacaEndpointInput = document.getElementById('alpaca-api-endpoint');

    if (fmpStatus) {
        fmpStatus.textContent = providerConfig.has_fmp_key ? '✓ Configured' : '✗ Not configured';
        fmpStatus.className = 'api-key-status ' + (providerConfig.has_fmp_key ? 'status-ok' : 'status-missing');
    }

    if (alpacaStatus) {
        let statusText = providerConfig.has_alpaca_key ? '✓ Configured' : '✗ Not configured';
        if (providerConfig.has_alpaca_key && providerConfig.alpaca_endpoint) {
            statusText += ' (custom endpoint)';
        }
        alpacaStatus.textContent = statusText;
        alpacaStatus.className = 'api-key-status ' + (providerConfig.has_alpaca_key ? 'status-ok' : 'status-missing');
    }

    // Populate the endpoint field with current value
    if (alpacaEndpointInput && providerConfig.alpaca_endpoint) {
        alpacaEndpointInput.value = providerConfig.alpaca_endpoint;
    }
}

async function saveFmpApiKey() {
    const apiKey = document.getElementById('fmp-api-key').value.trim();
    if (!apiKey) {
        showNotification('Please enter an API key', 'error');
        return;
    }

    try {
        const response = await fetch('/api/providers/api-key', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                provider: 'fmp',
                api_key: apiKey
            })
        });

        const result = await response.json();
        if (result.status === 'ok') {
            showNotification(result.message, 'success');
            document.getElementById('fmp-api-key').value = '';
            loadProviderSettings();  // Refresh status
        } else {
            showNotification('Failed: ' + result.message, 'error');
        }
    } catch (error) {
        showNotification('Error: ' + error.message, 'error');
    }
}

async function saveAlpacaCredentials() {
    const apiKey = document.getElementById('alpaca-api-key').value.trim();
    const apiSecret = document.getElementById('alpaca-api-secret').value.trim();
    const apiEndpoint = document.getElementById('alpaca-api-endpoint').value.trim();

    if (!apiKey || !apiSecret) {
        showNotification('Please enter both API key and secret', 'error');
        return;
    }

    try {
        const response = await fetch('/api/providers/api-key', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                provider: 'alpaca',
                api_key: apiKey,
                api_secret: apiSecret,
                api_endpoint: apiEndpoint || null
            })
        });

        const result = await response.json();
        if (result.status === 'ok') {
            showNotification(result.message, 'success');
            document.getElementById('alpaca-api-key').value = '';
            document.getElementById('alpaca-api-secret').value = '';
            // Don't clear endpoint - keep showing it
            loadProviderSettings();  // Refresh status
        } else {
            showNotification('Failed: ' + result.message, 'error');
        }
    } catch (error) {
        showNotification('Error: ' + error.message, 'error');
    }
}

async function testProvider(providerName) {
    showNotification(`Testing ${providerName}...`, 'info');

    try {
        const response = await fetch(`/api/providers/test/${providerName}`);
        const result = await response.json();

        if (result.status === 'ok') {
            showNotification(result.message, 'success');
        } else {
            showNotification('Test failed: ' + result.message, 'error');
        }
    } catch (error) {
        showNotification('Error testing provider: ' + error.message, 'error');
    }
}

function renderCacheSettings() {
    const priceCacheInput = document.getElementById('price-cache-seconds');
    const preferBatchInput = document.getElementById('prefer-batch');

    if (priceCacheInput && providerConfig) {
        priceCacheInput.value = providerConfig.price_cache_seconds || 300;
    }

    if (preferBatchInput && providerConfig) {
        preferBatchInput.checked = providerConfig.prefer_batch !== false;
    }
}

async function saveCacheSettings() {
    const priceCacheSeconds = parseInt(document.getElementById('price-cache-seconds').value);
    const preferBatch = document.getElementById('prefer-batch').checked;

    try {
        const response = await fetch('/api/providers/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                price_cache_seconds: priceCacheSeconds,
                prefer_batch: preferBatch
            })
        });

        const result = await response.json();
        if (result.status === 'ok') {
            showNotification('Cache settings saved', 'success');
        } else {
            showNotification('Failed: ' + result.message, 'error');
        }
    } catch (error) {
        showNotification('Error: ' + error.message, 'error');
    }
}

async function clearProviderCache() {
    if (!confirm('Are you sure you want to clear all cached data?')) {
        return;
    }

    try {
        const response = await fetch('/api/providers/cache/clear', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({})
        });

        const result = await response.json();
        if (result.status === 'ok') {
            showNotification('Cache cleared', 'success');
            loadCacheStats();
        } else {
            showNotification('Failed: ' + result.message, 'error');
        }
    } catch (error) {
        showNotification('Error: ' + error.message, 'error');
    }
}

async function loadCacheStats() {
    const container = document.getElementById('cache-stats-content');

    try {
        const response = await fetch('/api/providers/cache/stats');
        const stats = await response.json();

        let html = `
            <div class="cache-stat-row">
                <span class="cache-stat-label">Total Entries:</span>
                <span class="cache-stat-value">${stats.total_entries}</span>
            </div>
        `;

        // By type
        for (const [type, count] of Object.entries(stats.by_type || {})) {
            html += `
                <div class="cache-stat-row">
                    <span class="cache-stat-label">${type}:</span>
                    <span class="cache-stat-value">${count}</span>
                </div>
            `;
        }

        // By source
        if (Object.keys(stats.sources || {}).length > 0) {
            html += '<div class="cache-stat-header">By Source:</div>';
            for (const [source, count] of Object.entries(stats.sources)) {
                html += `
                    <div class="cache-stat-row">
                        <span class="cache-stat-label">${source}:</span>
                        <span class="cache-stat-value">${count}</span>
                    </div>
                `;
            }
        }

        container.innerHTML = html;
    } catch (error) {
        container.innerHTML = '<span class="error">Failed to load cache stats</span>';
    }
}

function showNotification(message, type = 'info') {
    // Check if there's an existing notification system
    const existingNotify = document.querySelector('.notification');
    if (existingNotify) {
        existingNotify.remove();
    }

    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.textContent = message;
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 12px 20px;
        border-radius: 4px;
        z-index: 1000;
        animation: fadeIn 0.3s ease;
        ${type === 'success' ? 'background: #10b981; color: white;' : ''}
        ${type === 'error' ? 'background: #ef4444; color: white;' : ''}
        ${type === 'info' ? 'background: #3b82f6; color: white;' : ''}
    `;

    document.body.appendChild(notification);

    setTimeout(() => {
        notification.style.animation = 'fadeOut 0.3s ease';
        setTimeout(() => notification.remove(), 300);
    }, 3000);
}
