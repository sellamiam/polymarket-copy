// Application State
let activeTab = 'dashboard';
let refreshTimer = 30;
let timerInterval = null;
let chart = null;
let leaderboardPeriod = 'WEEK';

// DOM Elements
const navItems = document.querySelectorAll('.nav-item');
const tabContents = document.querySelectorAll('.tab-content');
const pageTitle = document.getElementById('page-title');
const pageSubtitle = document.getElementById('page-subtitle');
const refreshTimerEl = document.getElementById('refresh-timer');
const btnForceRefresh = document.getElementById('btn-force-refresh');
const btnToggleBot = document.getElementById('btn-toggle-bot');
const btnResetSim = document.getElementById('btn-reset-sim');
const btnSyncWhales = document.getElementById('btn-sync-whales');
const botStatusDot = document.querySelector('.id-status-dot');
const botStatusText = document.querySelector('.id-status-text');

// Form Sizing Inputs
const selectSizingType = document.getElementById('trader-sizing-type');
const labelSizingValue = document.getElementById('label-sizing-value');
const inputSizingValue = document.getElementById('trader-sizing-value');

// Quick Sizing Modal Inputs
const quickModal = document.getElementById('modal-quick-follow');
const quickTraderName = document.getElementById('quick-trader-name');
const quickTraderAddress = document.getElementById('quick-trader-address');
const selectQuickSizingType = document.getElementById('quick-sizing-type');
const labelQuickSizingValue = document.getElementById('label-quick-sizing-value');
const inputQuickSizingValue = document.getElementById('quick-sizing-value');

// Init Hook
document.addEventListener('DOMContentLoaded', () => {
    // Start Polling Timer
    startRefreshTimer();
    
    // Tab switching event binding
    navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const tabName = item.getAttribute('data-tab');
            switchTab(tabName);
        });
    });

    // Control Listeners
    btnForceRefresh.addEventListener('click', forceRefreshData);
    btnToggleBot.addEventListener('click', toggleBotStatus);
    btnResetSim.addEventListener('click', resetSimulationAccount);
    btnSyncWhales.addEventListener('click', syncWhalesFromLeaderboard);

    // Mobile sidebar toggle
    const btnMobileMenu = document.getElementById('btn-mobile-menu');
    const sidebarOverlay = document.getElementById('sidebar-overlay');
    const appContainer = document.querySelector('.app-container');
    
    btnMobileMenu.addEventListener('click', () => {
        appContainer.classList.toggle('sidebar-open');
    });
    sidebarOverlay.addEventListener('click', () => {
        appContainer.classList.remove('sidebar-open');
    });
    // Close sidebar when a nav item is clicked on mobile
    navItems.forEach(item => {
        item.addEventListener('click', () => {
            appContainer.classList.remove('sidebar-open');
        });
    });

    // Form Submits
    document.getElementById('form-settings').addEventListener('submit', saveGlobalSettings);
    document.getElementById('form-follow-trader').addEventListener('submit', followNewTrader);
    document.getElementById('form-quick-follow').addEventListener('submit', submitQuickFollow);

    // Modal close elements
    document.querySelector('.btn-close-modal').addEventListener('click', () => {
        quickModal.classList.remove('active');
    });

    // Sizing change event bindings to update help labels dynamically
    selectSizingType.addEventListener('change', () => {
        updateSizingLabels(selectSizingType.value, labelSizingValue, inputSizingValue);
    });
    selectQuickSizingType.addEventListener('change', () => {
        updateSizingLabels(selectQuickSizingType.value, labelQuickSizingValue, inputQuickSizingValue);
    });

    // Leaderboard Period Triggers
    document.querySelectorAll('.period-selectors button').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.period-selectors button').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            leaderboardPeriod = btn.getAttribute('data-period');
            fetchLeaderboard();
        });
    });

    document.getElementById('btn-clear-logs').addEventListener('click', () => {
        document.getElementById('console-logs').innerHTML = '<div class="log-entry">[Console cleared by user]</div>';
    });

    // Fetch Initial Data
    fetchData();
});

// Timer Management
function startRefreshTimer() {
    if (timerInterval) clearInterval(timerInterval);
    refreshTimer = 30;
    refreshTimerEl.textContent = refreshTimer;
    timerInterval = setInterval(() => {
        refreshTimer--;
        refreshTimerEl.textContent = refreshTimer;
        if (refreshTimer <= 0) {
            refreshTimer = 30;
            fetchData();
        }
    }, 1000);
}

function forceRefreshData() {
    startRefreshTimer();
    fetchData();
}

// Tab Swapping Logic
function switchTab(tabName) {
    activeTab = tabName;
    
    // Update Menu Selection
    navItems.forEach(item => {
        if (item.getAttribute('data-tab') === tabName) {
            item.classList.add('active');
        } else {
            item.classList.remove('active');
        }
    });

    // Update Contents Visible
    tabContents.forEach(content => {
        if (content.id === `tab-${tabName}`) {
            content.classList.add('active');
        } else {
            content.classList.remove('active');
        }
    });

    // Update Header Text dynamically
    const meta = {
        dashboard: { title: 'Dashboard Overview', subtitle: 'Track simulated performance of cloned Polymarket bets.' },
        traders: { title: 'Monitored Whales', subtitle: 'Wallets you are following and copying trade structures.' },
        leaderboard: { title: 'Polymarket Leaderboard', subtitle: 'Dynamic ranking of top profit generators on Polymarket.' },
        history: { title: 'Trade Simulation History', subtitle: 'Audit log of all completed virtual buy/sell execution matches.' },
        logs: { title: 'Background Engine Logs', subtitle: 'Live terminal stdout logs of the API polling cycle.' },
        settings: { title: 'Simulation Parameters', subtitle: 'Configure starting capital, intervals, execution mode, and slippage.' }
    };

    if (meta[tabName]) {
        pageTitle.textContent = meta[tabName].title;
        pageSubtitle.textContent = meta[tabName].subtitle;
    }

    // Trigger sub-tab updates
    if (tabName === 'leaderboard') {
        fetchLeaderboard();
    }
}

// Formatting Helper
function formatUSD(num) {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(num);
}

// Dynamic Sizing Fields
function updateSizingLabels(type, labelEl, inputEl) {
    if (type === 'fixed') {
        labelEl.textContent = 'Allocation Size (USDC)';
        inputEl.value = '100';
        inputEl.min = '1';
        inputEl.step = '1';
    } else if (type === 'multiplier') {
        labelEl.textContent = 'Trade Multiplier (x)';
        inputEl.value = '1.0';
        inputEl.min = '0.01';
        inputEl.step = '0.01';
    } else if (type === 'proportional') {
        labelEl.textContent = 'Portfolio Allocation Share (%)';
        inputEl.value = '2';
        inputEl.min = '0.1';
        inputEl.step = '0.1';
    }
}

// REST API calls
async function fetchData() {
    try {
        const res = await fetch('/api/state');
        if (!res.ok) throw new Error('API query failed.');
        const data = await res.json();
        renderDashboard(data);
    } catch (e) {
        console.error('Error fetching data: ', e);
    }
}

function renderDashboard(data) {
    const config = data.config;
    const state = data.state;
    const holdingsValue = data.holdings_value;
    const totalEquity = data.total_equity;

    // Render Bot Status
    if (config.simulation_active) {
        botStatusDot.classList.add('active');
        botStatusText.textContent = 'SIMULATOR RUNNING';
        btnToggleBot.className = 'btn btn-secondary btn-block text-danger';
        btnToggleBot.innerHTML = '<i class="fa-solid fa-pause"></i> Stop Simulation';
    } else {
        botStatusDot.classList.remove('active');
        botStatusText.textContent = 'SIMULATOR PAUSED';
        btnToggleBot.className = 'btn btn-primary btn-block';
        btnToggleBot.innerHTML = '<i class="fa-solid fa-play"></i> Start Simulation';
    }

    // Render Metrics
    document.getElementById('val-equity').textContent = formatUSD(totalEquity);
    document.getElementById('val-cash').textContent = formatUSD(state.cash_usdc);
    document.getElementById('val-holdings').textContent = formatUSD(holdingsValue);
    
    const countPositions = Object.keys(state.positions).length;
    document.getElementById('val-open-positions').textContent = `${countPositions} Active Positions`;

    // Profit Calculations
    const startingCapital = config.starting_capital;
    const pnlUsd = totalEquity - startingCapital;
    const pnlPct = (pnlUsd / startingCapital) * 100;

    const pnlUsdEl = document.getElementById('val-pnl-usd');
    const pnlPctEl = document.getElementById('val-pnl-pct');

    pnlUsdEl.textContent = (pnlUsd >= 0 ? '+' : '') + formatUSD(pnlUsd);
    pnlPctEl.textContent = (pnlPct >= 0 ? '+' : '') + pnlPct.toFixed(2) + '% Net Profit';

    if (pnlUsd >= 0) {
        pnlUsdEl.className = 'metric-value pnl-green';
        pnlPctEl.className = 'metric-subtext pnl-green';
    } else {
        pnlUsdEl.className = 'metric-value pnl-red';
        pnlPctEl.className = 'metric-subtext pnl-red';
    }

    // Render Positions Table
    renderPositionsTable(state.positions);

    // Render Followed Traders List
    renderFollowedTraders(config.followed_traders, state.whale_positions);

    // Render History Table
    renderHistoryTable(state.trades);

    // Render Console Logs
    renderLogsConsole(state.logs);

    // Update settings form default values if not currently focused
    const setCapital = document.getElementById('settings-capital');
    if (document.activeElement !== setCapital) {
        setCapital.value = config.starting_capital;
        // Lock starting capital if trades exist
        if (state.trades.length > 0) {
            setCapital.disabled = true;
        } else {
            setCapital.disabled = false;
        }
    }
    const setPrep = document.getElementById('settings-interval');
    if (document.activeElement !== setPrep) setPrep.value = config.poll_interval_seconds;
    const setMode = document.getElementById('settings-execution-mode');
    if (document.activeElement !== setMode) setMode.value = config.execution_mode;
    const setSlip = document.getElementById('settings-slippage');
    if (document.activeElement !== setSlip) setSlip.value = config.slippage_bps;
    const setMinPrice = document.getElementById('settings-min-price');
    if (document.activeElement !== setMinPrice) setMinPrice.value = config.min_copy_price;
    const setMaxPrice = document.getElementById('settings-max-price');
    if (document.activeElement !== setMaxPrice) setMaxPrice.value = config.max_copy_price;
    const setCopyBest = document.getElementById('settings-copy-only-best');
    if (document.activeElement !== setCopyBest) setCopyBest.checked = config.copy_only_best_wins || false;
    const setMinBestScore = document.getElementById('settings-min-best-score');
    if (document.activeElement !== setMinBestScore) setMinBestScore.value = config.min_best_bet_score || 65;
    const setMaxDays = document.getElementById('settings-max-days');
    if (document.activeElement !== setMaxDays) setMaxDays.value = config.max_days_to_resolution || 7;
    const setExcludeSports = document.getElementById('settings-exclude-sports');
    if (document.activeElement !== setExcludeSports) setExcludeSports.checked = config.exclude_sports_bets !== false;
    const setExcludeCrypto = document.getElementById('settings-exclude-crypto');
    if (document.activeElement !== setExcludeCrypto) setExcludeCrypto.checked = config.exclude_crypto_bets !== false;
    const setNichePriority = document.getElementById('settings-niche-priority');
    if (document.activeElement !== setNichePriority) setNichePriority.checked = config.niche_priority_active || false;
    const setDynamicSizing = document.getElementById('settings-dynamic-sizing');
    if (document.activeElement !== setDynamicSizing) setDynamicSizing.checked = config.dynamic_sizing_active || false;

    // Render Chart
    if (state.portfolio_value_history && state.portfolio_value_history.length > 0) {
        drawChart(state.portfolio_value_history);
    }
}

function renderPositionsTable(positions) {
    const tbody = document.querySelector('#table-positions tbody');
    tbody.innerHTML = '';

    const list = Object.values(positions);
    if (list.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">No open positions. Select a whale and start the simulation.</td></tr>';
        return;
    }

    list.forEach(pos => {
        const costBasis = pos.avg_price * pos.quantity;
        const currentVal = pos.current_price * pos.quantity;
        const unrealizedPnl = currentVal - costBasis;
        const unrealizedPnlPct = costBasis > 0 ? (unrealizedPnl / costBasis) * 100 : 0.0;
        
        const badgeClass = pos.outcome.toUpperCase() === 'YES' ? 'badge-yes' : 'badge-no';
        const pnlClass = unrealizedPnl >= 0 ? 'pnl-green' : 'pnl-red';
        const sign = unrealizedPnl >= 0 ? '+' : '';

        // Win probability & score layout
        const winProb = pos.win_probability ? pos.win_probability.toFixed(1) + '%' : (pos.avg_price * 100).toFixed(0) + '%';
        const bestBetScore = pos.best_bet_score || 0;
        let scoreHTML = `<div style="font-weight: 500;">${winProb}</div>`;
        if (bestBetScore > 0) {
            scoreHTML += `<div style="font-size: 0.75rem; color: var(--color-text-secondary); margin-top: 0.15rem;">Score: ${bestBetScore}</div>`;
            if (bestBetScore >= 70) {
                scoreHTML += `<div style="margin-top: 0.2rem;"><span class="badge-best-pick"><i class="fa-solid fa-star"></i> Best Pick</span></div>`;
            }
        } else {
            scoreHTML += `<div style="font-size: 0.75rem; color: var(--color-text-muted); margin-top: 0.15rem;">Score: -</div>`;
        }

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>
                <div style="font-weight: 600;">${pos.market_title}</div>
                <div style="margin-top: 0.25rem;">
                    <span class="outcome-badge ${badgeClass}">${pos.outcome}</span>
                    <a href="https://polymarket.com/event/${pos.market_slug}" target="_blank" style="color: var(--color-primary); font-size: 0.75rem; text-decoration: none; margin-left: 0.5rem;"><i class="fa-solid fa-up-right-from-square"></i> view market</a>
                </div>
            </td>
            <td>${scoreHTML}</td>
            <td>${pos.quantity.toFixed(2)}</td>
            <td>${pos.avg_price.toFixed(3)} USDC</td>
            <td>${pos.current_price.toFixed(3)} USDC</td>
            <td>${formatUSD(costBasis)}</td>
            <td class="${pnlClass} font-weight-bold">${sign}${formatUSD(unrealizedPnl)} (${sign}${unrealizedPnlPct.toFixed(2)}%)</td>
        `;
        tbody.appendChild(tr);
    });
}

function renderFollowedTraders(followed, whalePositions) {
    const container = document.getElementById('list-followed-traders');
    container.innerHTML = '';

    if (followed.length === 0) {
        container.innerHTML = '<div class="text-center text-muted py-4">No followed traders. Follow one above or from the leaderboard.</div>';
        return;
    }

    followed.forEach(trader => {
        const addrClean = trader.address.toLowerCase();
        
        // Count open position counts we are tracking for this whale
        let holdingsText = 'No open positions tracked';
        const holdings = whalePositions[addrClean];
        if (holdings) {
            const activeKeys = Object.entries(holdings).filter(([_, qty]) => qty > 0);
            if (activeKeys.length > 0) {
                holdingsText = `Tracking ${activeKeys.length} positions`;
            }
        }

        // Format allocation text
        let sizeText = '';
        if (trader.sizing_type === 'fixed') {
            sizeText = `Fixed: ${formatUSD(trader.sizing_value)} per trade`;
        } else if (trader.sizing_type === 'multiplier') {
            sizeText = `Multiplier: ${trader.sizing_value}x whale size`;
        } else if (trader.sizing_type === 'proportional') {
            sizeText = `Share: ${(trader.sizing_value).toFixed(1)}% of portfolio value`;
        }

        const div = document.createElement('div');
        div.className = 'trader-card';
        div.innerHTML = `
            <div class="trader-info">
                <div class="trader-name-row">
                    <span class="trader-name">${trader.name}</span>
                    <span class="badge ${trader.enabled ? 'badge-yes' : 'badge-no'}" style="font-size: 0.65rem; padding: 0.1rem 0.3rem;">${trader.enabled ? 'ACTIVE' : 'MUTED'}</span>
                </div>
                <span class="trader-addr">${trader.address}</span>
                <span class="trader-alloc"><i class="fa-solid fa-coins"></i> ${sizeText} &bull; <i class="fa-solid fa-compass"></i> ${holdingsText}</span>
            </div>
            <div class="trader-actions">
                <label class="switch">
                    <input type="checkbox" ${trader.enabled ? 'checked' : ''} onchange="toggleTraderActive('${trader.address}', this.checked)">
                    <span class="slider"></span>
                </label>
                <button class="btn btn-secondary btn-icon text-danger" onclick="unfollowTrader('${trader.address}')" title="Unfollow">
                    <i class="fa-solid fa-trash-can"></i>
                </button>
            </div>
        `;
        container.appendChild(div);
    });
}

function renderHistoryTable(trades) {
    const tbody = document.querySelector('#table-history tbody');
    tbody.innerHTML = '';

    if (trades.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" class="text-center text-muted">No historical trades found.</td></tr>';
        return;
    }

    // Sort showing newest first
    const sortedTrades = [...trades].sort((a, b) => b.timestamp - a.timestamp);

    sortedTrades.forEach(trade => {
        const d = new Date(trade.timestamp * 1000);
        const dateStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) + ' ' + d.toLocaleDateString([], { month: 'short', day: 'numeric' });
        
        let typeBadge = '';
        if (trade.type === 'BUY') {
            typeBadge = '<span class="outcome-badge badge-yes">BUY</span>';
        } else if (trade.type === 'SELL') {
            typeBadge = '<span class="outcome-badge badge-no">SELL</span>';
        } else {
            typeBadge = '<span class="outcome-badge" style="background-color: rgba(99, 102, 241, 0.1); color: #818cf8; border: 1px solid rgba(99,102,241,0.2);">RESOLVE</span>';
        }

        const sign = trade.realized_pnl >= 0 ? '+' : '';
        const pnlClass = trade.realized_pnl >= 0 ? 'pnl-green' : 'pnl-red';
        const pnlText = trade.type === 'BUY' ? '-' : `${sign}${formatUSD(trade.realized_pnl)}`;

        // Score display
        let scoreHTML = '<span class="text-muted">-</span>';
        if (trade.type === 'BUY') {
            const winProb = trade.win_probability ? trade.win_probability.toFixed(1) + '%' : (trade.price * 100).toFixed(0) + '%';
            const bestBetScore = trade.best_bet_score || 0;
            scoreHTML = `<div style="font-weight: 500;">${winProb}</div>`;
            if (bestBetScore > 0) {
                scoreHTML += `<div style="font-size: 0.75rem; color: var(--color-text-secondary); margin-top: 0.15rem;">Score: ${bestBetScore}</div>`;
                if (bestBetScore >= 70) {
                    scoreHTML += `<div style="margin-top: 0.2rem;"><span class="badge-best-pick"><i class="fa-solid fa-star"></i> Best Pick</span></div>`;
                }
            }
        }

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><span class="text-muted" style="font-size: 0.75rem;">${dateStr}</span></td>
            <td>
                <span class="font-weight-bold" style="font-size: 0.8rem;">${trade.trader_name}</span>
                ${trade.trader_address !== 'resolution' ? `<div style="font-family: var(--font-mono); font-size: 0.65rem; color: var(--color-text-muted);">${trade.trader_address.substring(0,6)}...${trade.trader_address.substring(38)}</div>` : ''}
            </td>
            <td>
                <div style="font-weight: 500;">${trade.market_title}</div>
                <div style="font-size: 0.75rem; color: var(--color-text-muted); margin-top: 0.15rem;">Outcome: <strong>${trade.outcome}</strong></div>
            </td>
            <td>${typeBadge}</td>
            <td>${scoreHTML}</td>
            <td>${trade.quantity.toFixed(2)}</td>
            <td>${trade.price.toFixed(3)} USDC</td>
            <td>${formatUSD(trade.usdc_size)}</td>
            <td class="${trade.type !== 'BUY' ? pnlClass : ''}">${pnlText}</td>
        `;
        tbody.appendChild(tr);
    });
}

function renderLogsConsole(logs) {
    const consoleLogs = document.getElementById('console-logs');
    if (!logs || logs.length === 0) {
        consoleLogs.innerHTML = '<div class="log-entry">[No log entries found]</div>';
        return;
    }

    consoleLogs.innerHTML = '';
    logs.forEach(log => {
        const d = new Date(log.timestamp * 1000);
        const timeStr = d.toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
        
        const div = document.createElement('div');
        div.className = 'log-entry';
        div.innerHTML = `<span class="log-time">[${timeStr}]</span> ${escapeHTML(log.message)}`;
        consoleLogs.appendChild(div);
    });

    // Auto Scroll
    const consoleWrapper = document.querySelector('.console-body-wrapper');
    consoleWrapper.scrollTop = consoleWrapper.scrollHeight;
}

function escapeHTML(str) {
    return str.replace(/[&<>'"]/g, 
        tag => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[tag] || tag)
    );
}

// Chart drawing logic
function drawChart(history) {
    const ctx = document.getElementById('equityChart').getContext('2d');
    
    // Sort chronologically
    const sortedHistory = [...history].sort((a, b) => a.timestamp - b.timestamp);
    
    const labels = sortedHistory.map(h => {
        const d = new Date(h.timestamp * 1000);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    });
    
    const dataPoints = sortedHistory.map(h => h.total_equity);
    
    if (chart) {
        chart.data.labels = labels;
        chart.data.datasets[0].data = dataPoints;
        chart.update();
        return;
    }
    
    const gradient = ctx.createLinearGradient(0, 0, 0, 260);
    gradient.addColorStop(0, 'rgba(99, 102, 241, 0.35)');
    gradient.addColorStop(1, 'rgba(99, 102, 241, 0.00)');
    
    chart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'Simulated Equity',
                data: dataPoints,
                borderColor: '#6366f1',
                borderWidth: 2.5,
                backgroundColor: gradient,
                fill: true,
                tension: 0.2,
                pointBackgroundColor: '#6366f1',
                pointBorderColor: '#ffffff',
                pointHoverRadius: 6,
                pointRadius: 1.5
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: '#0f172a',
                    titleColor: '#f8fafc',
                    bodyColor: '#94a3b8',
                    borderColor: 'rgba(255, 255, 255, 0.1)',
                    borderWidth: 1,
                    displayColors: false,
                    callbacks: {
                        label: context => `Value: $${context.raw.toFixed(2)} USDC`
                    }
                }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(255, 255, 255, 0.03)' },
                    ticks: { color: '#64748b', maxTicksLimit: 6, autoSkip: true }
                },
                y: {
                    grid: { color: 'rgba(255, 255, 255, 0.03)' },
                    ticks: {
                        color: '#64748b',
                        callback: value => `$${value}`
                    }
                }
            }
        }
    });
}

// Controller Actions
async function toggleBotStatus() {
    try {
        const stateRes = await fetch('/api/state');
        const data = await stateRes.json();
        const active = data.config.simulation_active;
        const endpoint = active ? '/api/control/stop' : '/api/control/start';
        
        const res = await fetch(endpoint, { method: 'POST' });
        if (res.ok) {
            forceRefreshData();
        }
    } catch (e) {
        console.error('Error toggling bot:', e);
    }
}

async function resetSimulationAccount() {
    if (!confirm('Are you sure you want to completely reset the simulation? This will wipe your simulated trade history, active positions, and restore your cash to the starting capital settings.')) {
        return;
    }
    
    try {
        const res = await fetch('/api/control/reset', { method: 'POST' });
        if (res.ok) {
            if (chart) {
                chart.destroy();
                chart = null;
            }
            forceRefreshData();
        }
    } catch (e) {
        console.error('Error resetting simulation:', e);
    }
}

async function syncWhalesFromLeaderboard() {
    btnSyncWhales.disabled = true;
    const oldHTML = btnSyncWhales.innerHTML;
    btnSyncWhales.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Syncing...';
    
    try {
        const res = await fetch('/api/traders/sync-leaderboard', { method: 'POST' });
        if (res.ok) {
            const data = await res.json();
            alert(data.message || 'Successfully synced top whales from the weekly leaderboard!');
            forceRefreshData();
        } else {
            const data = await res.json();
            alert(`Sync failed: ${data.detail || 'Unknown error'}`);
        }
    } catch (err) {
        console.error('Error syncing whales:', err);
        alert('Sync failed. Please check backend status.');
    } finally {
        btnSyncWhales.disabled = false;
        btnSyncWhales.innerHTML = oldHTML;
    }
}

async function saveGlobalSettings(e) {
    e.preventDefault();
    const capital = parseFloat(document.getElementById('settings-capital').value);
    const interval = parseInt(document.getElementById('settings-interval').value);
    const mode = document.getElementById('settings-execution-mode').value;
    const slippage = parseFloat(document.getElementById('settings-slippage').value);
    const minPrice = parseFloat(document.getElementById('settings-min-price').value);
    const maxPrice = parseFloat(document.getElementById('settings-max-price').value);
    const copyOnlyBest = document.getElementById('settings-copy-only-best').checked;
    const minBestScore = parseInt(document.getElementById('settings-min-best-score').value);
    const maxDays = parseInt(document.getElementById('settings-max-days').value);
    const excludeSports = document.getElementById('settings-exclude-sports').checked;
    const excludeCrypto = document.getElementById('settings-exclude-crypto').checked;
    const nichePriority = document.getElementById('settings-niche-priority').checked;
    const dynamicSizing = document.getElementById('settings-dynamic-sizing').checked;

    try {
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                starting_capital: capital,
                poll_interval_seconds: interval,
                execution_mode: mode,
                slippage_bps: slippage,
                min_copy_price: minPrice,
                max_copy_price: maxPrice,
                copy_only_best_wins: copyOnlyBest,
                min_best_bet_score: minBestScore,
                max_days_to_resolution: maxDays,
                exclude_sports_bets: excludeSports,
                exclude_crypto_bets: excludeCrypto,
                niche_priority_active: nichePriority,
                dynamic_sizing_active: dynamicSizing
            })
        });
        if (res.ok) {
            alert('Global settings saved successfully!');
            forceRefreshData();
        } else {
            const data = await res.json();
            alert(`Failed: ${data.detail || 'Unknown error'}`);
        }
    } catch (err) {
        console.error('Error saving settings:', err);
    }
}

async function followNewTrader(e) {
    e.preventDefault();
    const address = document.getElementById('trader-address').value;
    const name = document.getElementById('trader-name').value;
    const sizingType = selectSizingType.value;
    const sizingValue = parseFloat(inputSizingValue.value);

    try {
        const res = await fetch('/api/traders/follow', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                address: address,
                name: name,
                sizing_type: sizingType,
                sizing_value: sizingValue
            })
        });

        if (res.ok) {
            document.getElementById('form-follow-trader').reset();
            updateSizingLabels('fixed', labelSizingValue, inputSizingValue);
            forceRefreshData();
            alert(`Started copying: ${name}`);
        } else {
            const data = await res.json();
            alert(`Error: ${data.detail || 'Check address format.'}`);
        }
    } catch (err) {
        console.error('Error adding followed trader:', err);
    }
}

async function toggleTraderActive(address, enabled) {
    try {
        const res = await fetch('/api/traders/toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                address: address,
                enabled: enabled
            })
        });
        if (res.ok) {
            fetchData();
        }
    } catch (e) {
        console.error('Error toggling trader active status:', e);
    }
}

async function unfollowTrader(address) {
    if (!confirm('Are you sure you want to stop following this trader? Existing open positions will NOT be closed, but new trades from this trader will not be copied.')) {
        return;
    }
    
    try {
        const res = await fetch('/api/traders/unfollow', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ address: address })
        });
        if (res.ok) {
            forceRefreshData();
        }
    } catch (e) {
        console.error('Error unfollowing trader:', e);
    }
}

// Leaderboard operations
async function fetchLeaderboard() {
    const tbody = document.querySelector('#table-leaderboard tbody');
    tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted"><i class="fa-solid fa-spinner fa-spin"></i> Fetching live rankings from Polymarket...</td></tr>';

    try {
        const res = await fetch(`/api/leaderboard?timePeriod=${leaderboardPeriod}`);
        if (!res.ok) throw new Error('Failed to fetch leaderboard');
        const list = await res.json();
        
        tbody.innerHTML = '';
        if (list.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">No records found.</td></tr>';
            return;
        }

        list.forEach(item => {
            const tr = document.createElement('tr');
            
            // Format username
            let nameHTML = `<span class="font-weight-bold">${escapeHTML(item.userName || 'Anonymous')}</span>`;
            if (item.xUsername) {
                nameHTML += ` <a href="https://x.com/${item.xUsername}" target="_blank" style="color: #38bdf8; text-decoration:none; margin-left: 0.25rem;"><i class="fa-brands fa-x-twitter"></i></a>`;
            }
            nameHTML += `<div style="font-family: var(--font-mono); font-size: 0.65rem; color: var(--color-text-muted); margin-top: 0.15rem;">${item.proxyWallet}</div>`;

            const safeName = (item.userName || item.proxyWallet.substring(0,8)).replace(/'/g, "\\'");

            tr.innerHTML = `
                <td><span class="rank-badge ${parseInt(item.rank) <= 3 ? 'text-gold' : ''}" style="font-weight: 700; font-size: 1rem;">#${item.rank}</span></td>
                <td>${nameHTML}</td>
                <td class="text-right">${formatUSD(item.vol)}</td>
                <td class="text-right pnl-green font-weight-bold">+${formatUSD(item.pnl)}</td>
                <td class="text-center">
                    <button class="btn btn-secondary btn-small" onclick="openQuickFollow('${item.proxyWallet}', '${escapeHTML(safeName)}')">
                        <i class="fa-solid fa-user-plus"></i> Copy
                    </button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) {
        console.error('Error rendering leaderboard:', e);
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-danger"><i class="fa-solid fa-triangle-exclamation"></i> Error loading leaderboard. Polymarket API might be throttled or down.</td></tr>';
    }
}

// Quick Follow modal logic
window.openQuickFollow = function(address, name) {
    quickTraderAddress.value = address;
    quickTraderName.textContent = name;
    
    // Set default sizing
    selectQuickSizingType.value = 'fixed';
    updateSizingLabels('fixed', labelQuickSizingValue, inputQuickSizingValue);
    
    quickModal.classList.add('active');
};

async function submitQuickFollow(e) {
    e.preventDefault();
    const address = quickTraderAddress.value;
    const name = quickTraderName.textContent;
    const sizingType = selectQuickSizingType.value;
    const sizingValue = parseFloat(inputQuickSizingValue.value);

    try {
        const res = await fetch('/api/traders/follow', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                address: address,
                name: name,
                sizing_type: sizingType,
                sizing_value: sizingValue
            })
        });

        if (res.ok) {
            quickModal.classList.remove('active');
            forceRefreshData();
            alert(`Now copy trading: ${name}`);
            switchTab('traders');
        } else {
            const data = await res.json();
            alert(`Error: ${data.detail || 'Could not save follow configuration.'}`);
        }
    } catch (err) {
        console.error('Error submitting quick follow:', err);
    }
}
