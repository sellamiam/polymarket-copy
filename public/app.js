// Application State
let activeTab = 'dashboard';
let refreshTimer = 30;
let timerInterval = null;
let leaderboardPeriod = 'WEEK';
let currentChartView = 'winrate';
let cachedWinRateHistory = [];
let cachedPortfolioHistory = [];

// Optional API token for mutating routes (localStorage or ?token= query)
function getApiToken() {
    try {
        const params = new URLSearchParams(window.location.search);
        const q = params.get('token');
        if (q) {
            localStorage.setItem('polycopy_api_token', q);
            return q;
        }
        return localStorage.getItem('polycopy_api_token') || '';
    } catch (e) {
        return '';
    }
}

function apiHeaders(extra = {}) {
    const headers = { ...extra };
    const token = getApiToken();
    if (token) headers['X-API-Token'] = token;
    return headers;
}

async function apiFetch(url, options = {}) {
    const opts = { ...options };
    opts.headers = apiHeaders(opts.headers || {});
    if (opts.body && !opts.headers['Content-Type']) {
        opts.headers['Content-Type'] = 'application/json';
    }
    return fetch(url, opts);
}

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

    // Chart View Toggles
    const btnChartWinrate = document.getElementById('btn-chart-winrate');
    const btnChartEquity = document.getElementById('btn-chart-equity');
    if (btnChartWinrate && btnChartEquity) {
        btnChartWinrate.addEventListener('click', () => {
            btnChartWinrate.classList.add('active');
            btnChartEquity.classList.remove('active');
            currentChartView = 'winrate';
            renderPerformanceChart();
        });
        btnChartEquity.addEventListener('click', () => {
            btnChartEquity.classList.add('active');
            btnChartWinrate.classList.remove('active');
            currentChartView = 'equity';
            renderPerformanceChart();
        });
    }

    window.addEventListener('resize', () => {
        if (activeTab === 'dashboard') {
            renderPerformanceChart();
        }
    });

    // Fetch Initial Data
    fetchData();
    fetchTraders();
    fetchHistory();
    fetchLogs();
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
    if (activeTab === 'traders') {
        fetchTraders();
    } else if (activeTab === 'history') {
        fetchHistory();
    } else if (activeTab === 'logs') {
        fetchLogs();
    }
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
    } else if (tabName === 'traders') {
        fetchTraders();
    } else if (tabName === 'history') {
        fetchHistory();
    } else if (tabName === 'logs') {
        fetchLogs();
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

async function fetchTraders() {
    try {
        const res = await fetch('/api/traders');
        if (!res.ok) throw new Error('Failed to fetch followed traders.');
        const data = await res.json();
        renderFollowedTraders(data.followed_traders || [], data.whale_positions || {});
    } catch (e) {
        console.error('Error fetching traders:', e);
    }
}

async function fetchHistory() {
    try {
        const res = await fetch('/api/history');
        if (!res.ok) throw new Error('Failed to fetch history.');
        const data = await res.json();
        renderHistoryTable(data.trades || []);
    } catch (e) {
        console.error('Error fetching history:', e);
    }
}

async function fetchLogs() {
    try {
        const res = await fetch('/api/logs');
        if (!res.ok) throw new Error('Failed to fetch logs.');
        const data = await res.json();
        renderLogsConsole(data.logs || []);
    } catch (e) {
        console.error('Error fetching logs:', e);
    }
}

function renderDashboard(data) {
    const config = data.config;
    const state = data.state;
    const holdingsValue = data.holdings_value;
    const totalEquity = data.total_equity;

    // Render Bot Status
    window.currentSimulationActive = Boolean(config && config.simulation_active);
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

    // Render Win Rate Metrics
    const winRate = state.win_rate !== undefined ? state.win_rate : 0.0;
    const resolvedCount = state.resolved_count !== undefined ? state.resolved_count : 0;
    const winsCount = state.wins_count !== undefined ? state.wins_count : 0;
    const lossesCount = Math.max(0, resolvedCount - winsCount);

    const winRateEl = document.getElementById('val-win-rate');
    const resolvedCountEl = document.getElementById('val-resolved-count');
    if (winRateEl) winRateEl.textContent = `${winRate.toFixed(1)}%`;
    if (resolvedCountEl) resolvedCountEl.textContent = `${winsCount} wins / ${resolvedCount} resolved`;

    const chartStatRate = document.getElementById('chart-stat-rate');
    const chartStatWl = document.getElementById('chart-stat-wl');
    const chartStatEdge = document.getElementById('chart-stat-edge');
    const chartStatCount = document.getElementById('chart-stat-count');
    if (chartStatRate) chartStatRate.textContent = `${winRate.toFixed(1)}%`;
    if (chartStatWl) chartStatWl.textContent = `${winsCount}W / ${lossesCount}L`;
    if (chartStatCount) chartStatCount.textContent = `${resolvedCount} resolved`;
    if (chartStatEdge) {
        const edge = winRate - 50.0;
        chartStatEdge.textContent = (edge >= 0 ? '+' : '') + edge.toFixed(1) + '%';
        chartStatEdge.className = 'stat-item-val ' + (edge >= 0 ? 'pnl-green' : 'pnl-red');
    }

    // Cache history and render full-width performance plot
    cachedWinRateHistory = state.win_rate_history || [];
    cachedPortfolioHistory = state.portfolio_value_history || [];
    renderPerformanceChart();

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

    // Update settings form default values if not currently focused
    const setCapital = document.getElementById('settings-capital');
    if (document.activeElement !== setCapital) {
        setCapital.value = config.starting_capital;
        // Lock starting capital if trades exist
        if (state.has_trades || (state.trades && state.trades.length > 0)) {
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
    if (document.activeElement !== setMaxDays) setMaxDays.value = config.max_days_to_resolution || 90;
    const setMaxHolding = document.getElementById('settings-max-holding-hours');
    if (document.activeElement !== setMaxHolding) setMaxHolding.value = config.max_holding_hours !== undefined ? config.max_holding_hours : 24;
    const setTakeProfit = document.getElementById('settings-take-profit-pct');
    if (document.activeElement !== setTakeProfit) setTakeProfit.value = config.take_profit_pct !== undefined ? config.take_profit_pct : 15.0;
    const setStopLoss = document.getElementById('settings-stop-loss-pct');
    if (document.activeElement !== setStopLoss) setStopLoss.value = config.stop_loss_pct !== undefined ? config.stop_loss_pct : 5.0;
    const setMinWhaleSize = document.getElementById('settings-min-whale-trade-size');
    if (document.activeElement !== setMinWhaleSize) setMinWhaleSize.value = config.min_whale_trade_size !== undefined ? config.min_whale_trade_size : 500.0;
    const setMaxExposure = document.getElementById('settings-max-market-exposure');
    if (document.activeElement !== setMaxExposure) setMaxExposure.value = config.max_market_exposure !== undefined ? config.max_market_exposure : 500.0;
    const setMinLiquidity = document.getElementById('settings-min-liquidity');
    if (document.activeElement !== setMinLiquidity) setMinLiquidity.value = config.min_market_liquidity !== undefined ? config.min_market_liquidity : 5000;
    const setMinVolume = document.getElementById('settings-min-volume');
    if (document.activeElement !== setMinVolume) setMinVolume.value = config.min_market_volume !== undefined ? config.min_market_volume : 20000;
    const setExcludeSports = document.getElementById('settings-exclude-sports');
    if (document.activeElement !== setExcludeSports) setExcludeSports.checked = config.exclude_sports_bets !== false;
    const setExcludeCrypto = document.getElementById('settings-exclude-crypto');
    if (document.activeElement !== setExcludeCrypto) setExcludeCrypto.checked = config.exclude_crypto_bets !== false;
    const setExcludeWeather = document.getElementById('settings-exclude-weather');
    if (document.activeElement !== setExcludeWeather) setExcludeWeather.checked = config.exclude_weather_bets !== false;
    const setNichePriority = document.getElementById('settings-niche-priority');
    if (document.activeElement !== setNichePriority) setNichePriority.checked = config.niche_priority_active || false;
    const setDynamicSizing = document.getElementById('settings-dynamic-sizing');
    if (document.activeElement !== setDynamicSizing) setDynamicSizing.checked = config.dynamic_sizing_active || false;
    const setEnableValuePlays = document.getElementById('settings-value-plays');
    if (document.activeElement !== setEnableValuePlays) setEnableValuePlays.checked = config.enable_value_plays !== false;

}

function renderPositionsTable(positions) {
    const tbody = document.querySelector('#table-positions tbody');
    tbody.innerHTML = '';

    const list = Object.values(positions);
    if (list.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted">No open positions. Select a whale and start the simulation.</td></tr>';
        return;
    }

    list.forEach(pos => {
        const costBasis = pos.avg_price * pos.quantity;
        const currentPrice = (pos.current_price !== undefined && pos.current_price !== null && pos.current_price > 0)
            ? pos.current_price
            : (pos.bid_price || pos.mid_price || pos.avg_price);
        const currentVal = currentPrice * pos.quantity;
        const unrealizedPnl = currentVal - costBasis;
        const unrealizedPnlPct = costBasis > 0 ? (unrealizedPnl / costBasis) * 100 : 0.0;
        
        const badgeClass = pos.outcome.toUpperCase() === 'YES' ? 'badge-yes' : 'badge-no';
        const pnlClass = unrealizedPnl > 0.001 ? 'pnl-green' : (unrealizedPnl < -0.001 ? 'pnl-red' : '');
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

        const safeTitle = (pos.market_title || 'Position').replace(/"/g, '&quot;');
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
            <td>${currentPrice.toFixed(3)} USDC</td>
            <td>${formatUSD(costBasis)}</td>
            <td class="${pnlClass} font-weight-bold">${sign}${formatUSD(unrealizedPnl)} (${sign}${unrealizedPnlPct.toFixed(2)}%)</td>
            <td>
                <button class="btn btn-sm btn-outline-danger btn-close-pos" data-token="${pos.token_id}" data-title="${safeTitle}" style="font-size: 0.75rem; padding: 0.25rem 0.6rem; border-radius: 4px; border: 1px solid #ef4444; background: transparent; color: #ef4444; cursor: pointer; font-weight: 500;" title="Liquidate position">
                    Close
                </button>
            </td>
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


// Controller Actions
async function toggleBotStatus() {
    const currentlyActive = Boolean(window.currentSimulationActive);
    const targetEndpoint = currentlyActive ? '/api/control/stop' : '/api/control/start';
    const actionLabel = currentlyActive ? 'Stopping' : 'Starting';

    // Immediate visual feedback to user
    btnToggleBot.disabled = true;
    btnToggleBot.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> ${actionLabel}...`;

    try {
        const res = await apiFetch(targetEndpoint, { method: 'POST' });
        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            alert(`Error: ${errData.detail || errData.error || 'Failed to update simulation state'}`);
        } else {
            window.currentSimulationActive = !currentlyActive;
            if (window.currentSimulationActive) {
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
        }
        forceRefreshData();
    } catch (e) {
        console.error('Error toggling bot:', e);
        alert('Network error while toggling simulation.');
    } finally {
        btnToggleBot.disabled = false;
    }
}

async function resetSimulationAccount() {
    if (!confirm(
        'Archive the ledger and start a fresh simulation?\n\n' +
        'The current SQLite ledger will be archived (not silently deleted). ' +
        'You must confirm with the phrase RESET.'
    )) {
        return;
    }
    const phrase = prompt('Type RESET to confirm archive + re-init:');
    if ((phrase || '').trim().toUpperCase() !== 'RESET') {
        alert('Reset cancelled.');
        return;
    }
    try {
        const res = await apiFetch('/api/control/reset', {
            method: 'POST',
            body: JSON.stringify({ confirm: true, confirm_phrase: 'RESET' }),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || 'Reset failed');
        }
        const data = await res.json();
        alert(data.message + (data.archive_path ? `\nArchive: ${data.archive_path}` : ''));
        forceRefreshData();
    } catch (e) {
        console.error('Error resetting simulation:', e);
        alert('Reset failed: ' + e.message);
    }
}

async function syncWhalesFromLeaderboard() {
    btnSyncWhales.disabled = true;
    const oldHTML = btnSyncWhales.innerHTML;
    btnSyncWhales.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Syncing...';
    
    try {
        const res = await apiFetch('/api/traders/sync-leaderboard', { method: 'POST' });
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
    const maxHolding = parseInt(document.getElementById('settings-max-holding-hours').value);
    const takeProfit = parseFloat(document.getElementById('settings-take-profit-pct').value);
    const stopLoss = parseFloat(document.getElementById('settings-stop-loss-pct').value);
    const minWhaleSize = parseFloat(document.getElementById('settings-min-whale-trade-size').value);
    const maxExposure = parseFloat(document.getElementById('settings-max-market-exposure').value);
    const minLiquidity = parseFloat(document.getElementById('settings-min-liquidity').value);
    const minVolume = parseFloat(document.getElementById('settings-min-volume').value);
    const excludeSports = document.getElementById('settings-exclude-sports').checked;
    const excludeCrypto = document.getElementById('settings-exclude-crypto').checked;
    const excludeWeather = document.getElementById('settings-exclude-weather').checked;
    const nichePriority = document.getElementById('settings-niche-priority').checked;
    const dynamicSizing = document.getElementById('settings-dynamic-sizing').checked;
    const enableValuePlays = document.getElementById('settings-value-plays').checked;

    try {
        const res = await apiFetch('/api/settings', {
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
                max_holding_hours: maxHolding,
                take_profit_pct: takeProfit,
                stop_loss_pct: stopLoss,
                min_whale_trade_size: minWhaleSize,
                max_market_exposure: maxExposure,
                min_market_liquidity: minLiquidity,
                min_market_volume: minVolume,
                exclude_sports_bets: excludeSports,
                exclude_crypto_bets: excludeCrypto,
                exclude_weather_bets: excludeWeather,
                niche_priority_active: nichePriority,
                dynamic_sizing_active: dynamicSizing,
                enable_value_plays: enableValuePlays
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
        const res = await apiFetch('/api/traders/follow', {
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
        const res = await apiFetch('/api/traders/toggle', {
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
        const res = await apiFetch('/api/traders/unfollow', {
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
        const res = await apiFetch('/api/traders/follow', {
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

// Manual Position Close Listener
document.addEventListener('DOMContentLoaded', () => {

    const tablePositions = document.querySelector('#table-positions tbody');
    if (tablePositions) {
        tablePositions.addEventListener('click', async (e) => {
            const btn = e.target.closest('.btn-close-pos');
            if (!btn) return;
            const token = btn.getAttribute('data-token');
            const title = btn.getAttribute('data-title') || 'this position';
            if (!confirm(`Liquidate open position: "${title}"?`)) {
                return;
            }
            btn.disabled = true;
            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
            try {
                const res = await apiFetch('/api/positions/close', {
                    method: 'POST',
                    body: JSON.stringify({ token_id: token, reason: 'MANUAL_CLOSE' })
                });
                const data = await res.json();
                if (res.ok && data.status === 'success') {
                    alert(`Position successfully liquidated! Proceeds credited to cash.`);
                    forceRefreshData();
                } else {
                    alert(`Failed to close position: ${data.detail || 'Unknown error'}`);
                    btn.disabled = false;
                    btn.innerHTML = 'Close';
                }
            } catch (err) {
                console.error('Error closing position:', err);
                alert('Network error while liquidating position.');
                btn.disabled = false;
                btn.innerHTML = 'Close';
            }
        });
});

// ==========================================
// Full-Width Performance & Win Rate Chart
// ==========================================
function renderPerformanceChart() {
    const svg = document.getElementById('win-rate-svg');
    const tooltip = document.getElementById('chart-tooltip');
    if (!svg) return;

    const width = 1000;
    const height = 240;
    const padding = { top: 25, right: 35, bottom: 35, left: 55 };
    const plotW = width - padding.left - padding.right;
    const plotH = height - padding.top - padding.bottom;

    if (currentChartView === 'winrate') {
        renderWinRatePlot(svg, tooltip, width, height, padding, plotW, plotH);
    } else {
        renderEquityPlot(svg, tooltip, width, height, padding, plotW, plotH);
    }
}

function renderWinRatePlot(svg, tooltip, width, height, padding, plotW, plotH) {
    const data = cachedWinRateHistory || [];
    if (data.length === 0) {
        svg.innerHTML = `
            <text x="${width / 2}" y="${height / 2}" text-anchor="middle" fill="#64748b" font-family="var(--font-sans)" font-size="14" font-weight="500">
                Simulation running. Win rate plot will render as positions resolve.
            </text>
        `;
        if (tooltip) tooltip.style.display = 'none';
        return;
    }

    const N = data.length;
    const getX = (idx) => N === 1 ? padding.left + plotW / 2 : padding.left + (idx / (N - 1)) * plotW;
    const getY = (val) => padding.top + plotH * (1.0 - Math.max(0, Math.min(100, val)) / 100.0);

    const points = data.map((d, i) => ({
        x: getX(i),
        y: getY(d.win_rate),
        raw: d,
        index: i
    }));

    // Grid lines for 0%, 25%, 50%, 75%, 100%
    const gridLevels = [0, 25, 50, 75, 100];
    let gridSvg = '';
    gridLevels.forEach(level => {
        const yVal = getY(level);
        const isTarget = level === 50;
        gridSvg += `
            <line x1="${padding.left}" y1="${yVal}" x2="${width - padding.right}" y2="${yVal}" 
                  stroke="${isTarget ? '#f59e0b' : 'rgba(255, 255, 255, 0.06)'}" 
                  stroke-width="${isTarget ? '1.5' : '1'}" 
                  stroke-dasharray="${isTarget ? '5,4' : '3,3'}" 
                  stroke-opacity="${isTarget ? '0.75' : '1'}" />
            <text x="${padding.left - 10}" y="${yVal + 4}" text-anchor="end" fill="${isTarget ? '#f59e0b' : '#64748b'}" font-size="11" font-family="var(--font-mono)" font-weight="${isTarget ? '700' : '500'}">
                ${level}%
            </text>
        `;
        if (isTarget) {
            gridSvg += `
                <text x="${padding.left + 8}" y="${yVal - 6}" fill="#f59e0b" font-size="10.5" font-family="var(--font-sans)" font-weight="700" letter-spacing="0.5">
                    50% TARGET BENCHMARK
                </text>
            `;
        }
    });

    // Build line path and area polygon
    let pathD = '';
    let areaPoints = '';
    const baselineY = getY(0);

    if (N === 1) {
        pathD = `M ${points[0].x - 25} ${points[0].y} L ${points[0].x + 25} ${points[0].y}`;
        areaPoints = `${points[0].x - 25},${baselineY} ${points[0].x - 25},${points[0].y} ${points[0].x + 25},${points[0].y} ${points[0].x + 25},${baselineY}`;
    } else {
        pathD = `M ${points[0].x} ${points[0].y}`;
        for (let i = 1; i < N; i++) {
            const prev = points[i - 1];
            const curr = points[i];
            const cpX = (prev.x + curr.x) / 2;
            pathD += ` C ${cpX} ${prev.y}, ${cpX} ${curr.y}, ${curr.x} ${curr.y}`;
        }

        areaPoints = `${points[0].x},${baselineY} `;
        for (let i = 0; i < N; i++) {
            areaPoints += `${points[i].x},${points[i].y} `;
        }
        areaPoints += `${points[N - 1].x},${baselineY}`;
    }

    // Points circles
    let circlesSvg = '';
    const dotRadius = N > 40 ? 3 : 4;
    points.forEach((p, i) => {
        const dotColor = p.raw.is_win ? '#10b981' : '#ef4444';
        circlesSvg += `
            <circle cx="${p.x}" cy="${p.y}" r="${dotRadius}" fill="${dotColor}" stroke="#090e17" stroke-width="1.5" class="chart-point" data-idx="${i}" />
        `;
    });

    svg.innerHTML = `
        <defs>
            <linearGradient id="wrAreaGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="#6366f1" stop-opacity="0.45" />
                <stop offset="65%" stop-color="#06b6d4" stop-opacity="0.15" />
                <stop offset="100%" stop-color="#06b6d4" stop-opacity="0.0" />
            </linearGradient>
            <linearGradient id="wrLineGrad" x1="0" y1="0" x2="1" y2="0">
                <stop offset="0%" stop-color="#818cf8" />
                <stop offset="50%" stop-color="#06b6d4" />
                <stop offset="100%" stop-color="#10b981" />
            </linearGradient>
            <filter id="wrGlow" x="-20%" y="-20%" width="140%" height="140%">
                <feGaussianBlur stdDeviation="3" result="blur" />
                <feComposite in="SourceGraphic" in2="blur" operator="over" />
            </filter>
        </defs>

        <!-- Grid -->
        ${gridSvg}

        <!-- Area Fill -->
        <polygon points="${areaPoints}" fill="url(#wrAreaGrad)" />

        <!-- Line Path with Glow -->
        <path d="${pathD}" fill="none" stroke="url(#wrLineGrad)" stroke-width="5" stroke-opacity="0.3" filter="url(#wrGlow)" stroke-linecap="round" stroke-linejoin="round" />
        <path d="${pathD}" fill="none" stroke="url(#wrLineGrad)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />

        <!-- Dots -->
        ${circlesSvg}

        <!-- Interactive Crosshair (hidden by default) -->
        <line id="svg-crosshair" x1="0" y1="${padding.top}" x2="0" y2="${padding.top + plotH}" stroke="rgba(255, 255, 255, 0.4)" stroke-dasharray="3,3" stroke-width="1.5" style="display: none;" />
        <circle id="svg-hover-halo" cx="0" cy="0" r="9" fill="none" stroke="#6366f1" stroke-width="2" stroke-opacity="0.8" style="display: none;" />
        <circle id="svg-hover-point" cx="0" cy="0" r="5" fill="#fff" stroke="#090e17" stroke-width="2" style="display: none;" />

        <!-- Full Transparent Mouse Overlay -->
        <rect id="svg-overlay" x="${padding.left}" y="${padding.top}" width="${plotW}" height="${plotH}" fill="transparent" style="cursor: crosshair;" />
    `;

    setupChartInteractions(svg, tooltip, points, width, height, 'winrate');
}

function renderEquityPlot(svg, tooltip, width, height, padding, plotW, plotH) {
    const data = cachedPortfolioHistory || [];
    if (data.length === 0) {
        svg.innerHTML = `
            <text x="${width / 2}" y="${height / 2}" text-anchor="middle" fill="#64748b" font-family="var(--font-sans)" font-size="14" font-weight="500">
                Recording portfolio history... Check back shortly.
            </text>
        `;
        if (tooltip) tooltip.style.display = 'none';
        return;
    }

    const N = data.length;
    const equities = data.map(d => Number(d.total_equity || d.cash || 10000));
    const minVal = Math.min(...equities, 9500);
    const maxVal = Math.max(...equities, 10200);
    const range = Math.max(100, maxVal - minVal);
    const yPad = range * 0.1;
    const yMin = minVal - yPad;
    const yMax = maxVal + yPad;

    const getX = (idx) => N === 1 ? padding.left + plotW / 2 : padding.left + (idx / (N - 1)) * plotW;
    const getY = (val) => padding.top + plotH * (1.0 - (val - yMin) / (yMax - yMin));

    const points = data.map((d, i) => ({
        x: getX(i),
        y: getY(Number(d.total_equity || d.cash || 10000)),
        raw: d,
        index: i
    }));

    // Baseline at $10,000
    const baselineY = getY(10000);
    const gridSvg = `
        <line x1="${padding.left}" y1="${baselineY}" x2="${width - padding.right}" y2="${baselineY}" 
              stroke="#6366f1" stroke-width="1.5" stroke-dasharray="5,4" stroke-opacity="0.75" />
        <text x="${padding.left - 10}" y="${baselineY + 4}" text-anchor="end" fill="#818cf8" font-size="11" font-family="var(--font-mono)" font-weight="600">
            $10.0k
        </text>
        <text x="${padding.left + 8}" y="${baselineY - 6}" fill="#818cf8" font-size="10.5" font-family="var(--font-sans)" font-weight="700">
            $10,000 STARTING CAPITAL
        </text>
    `;

    // Path
    let pathD = `M ${points[0].x} ${points[0].y}`;
    let areaPoints = `${points[0].x},${padding.top + plotH} `;
    for (let i = 1; i < N; i++) {
        const prev = points[i - 1];
        const curr = points[i];
        const cpX = (prev.x + curr.x) / 2;
        pathD += ` C ${cpX} ${prev.y}, ${cpX} ${curr.y}, ${curr.x} ${curr.y}`;
    }
    for (let i = 0; i < N; i++) {
        areaPoints += `${points[i].x},${points[i].y} `;
    }
    areaPoints += `${points[N - 1].x},${padding.top + plotH}`;

    svg.innerHTML = `
        <defs>
            <linearGradient id="eqAreaGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="#10b981" stop-opacity="0.35" />
                <stop offset="100%" stop-color="#10b981" stop-opacity="0.0" />
            </linearGradient>
            <linearGradient id="eqLineGrad" x1="0" y1="0" x2="1" y2="0">
                <stop offset="0%" stop-color="#38bdf8" />
                <stop offset="100%" stop-color="#10b981" />
            </linearGradient>
        </defs>
        ${gridSvg}
        <polygon points="${areaPoints}" fill="url(#eqAreaGrad)" />
        <path d="${pathD}" fill="none" stroke="url(#eqLineGrad)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />
        <line id="svg-crosshair" x1="0" y1="${padding.top}" x2="0" y2="${padding.top + plotH}" stroke="rgba(255, 255, 255, 0.4)" stroke-dasharray="3,3" stroke-width="1.5" style="display: none;" />
        <circle id="svg-hover-halo" cx="0" cy="0" r="9" fill="none" stroke="#10b981" stroke-width="2" stroke-opacity="0.8" style="display: none;" />
        <circle id="svg-hover-point" cx="0" cy="0" r="5" fill="#fff" stroke="#090e17" stroke-width="2" style="display: none;" />
        <rect id="svg-overlay" x="${padding.left}" y="${padding.top}" width="${plotW}" height="${plotH}" fill="transparent" style="cursor: crosshair;" />
    `;

    setupChartInteractions(svg, tooltip, points, width, height, 'equity');
}

function setupChartInteractions(svg, tooltip, points, width, height, mode) {
    const overlay = svg.querySelector('#svg-overlay');
    const crosshair = svg.querySelector('#svg-crosshair');
    const halo = svg.querySelector('#svg-hover-halo');
    const dot = svg.querySelector('#svg-hover-point');
    if (!overlay || !tooltip || points.length === 0) return;

    overlay.addEventListener('mousemove', (e) => {
        const rect = svg.getBoundingClientRect();
        const svgX = ((e.clientX - rect.left) / rect.width) * width;

        let closest = points[0];
        let minDist = Math.abs(points[0].x - svgX);
        for (let i = 1; i < points.length; i++) {
            const dist = Math.abs(points[i].x - svgX);
            if (dist < minDist) {
                minDist = dist;
                closest = points[i];
            }
        }

        crosshair.setAttribute('x1', closest.x);
        crosshair.setAttribute('x2', closest.x);
        crosshair.style.display = 'block';

        halo.setAttribute('cx', closest.x);
        halo.setAttribute('cy', closest.y);
        halo.style.display = 'block';

        dot.setAttribute('cx', closest.x);
        dot.setAttribute('cy', closest.y);
        dot.style.display = 'block';

        const pointPixelX = (closest.x / width) * rect.width;
        const pointPixelY = (closest.y / height) * rect.height;

        let leftPos = pointPixelX + 15;
        if (pointPixelX > rect.width * 0.65) {
            leftPos = pointPixelX - 265;
        }
        tooltip.style.left = `${Math.max(10, leftPos)}px`;
        tooltip.style.top = `${Math.max(5, pointPixelY - 35)}px`;
        tooltip.style.display = 'block';

        if (mode === 'winrate') {
            const p = closest.raw;
            const dateStr = p.timestamp ? new Date(p.timestamp * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
            const pnlFormatted = (p.realized_pnl >= 0 ? '+' : '') + formatUSD(p.realized_pnl);
            const pnlColor = p.is_win ? '#10b981' : '#ef4444';
            const outcomeBadge = p.outcome ? `<span style="background: rgba(56, 189, 248, 0.15); color: #38bdf8; padding: 2px 6px; border-radius: 4px; font-weight: 600;">${p.outcome}</span>` : '';

            tooltip.innerHTML = `
                <div style="font-weight: 700; color: #fff; margin-bottom: 4px; display: flex; justify-content: space-between; align-items: center;">
                    <span>Trade #${p.trade_num}</span>
                    <span style="font-size: 0.72rem; color: #94a3b8; font-weight: 400;">${dateStr}</span>
                </div>
                <div style="color: #cbd5e1; font-size: 0.75rem; margin-bottom: 6px; white-space: normal; line-height: 1.3;">
                    ${escapeHtml(p.market_title)}
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                    <span style="color: #94a3b8; font-size: 0.73rem;">Outcome:</span>
                    ${outcomeBadge}
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px;">
                    <span style="color: #94a3b8; font-size: 0.73rem;">Realized PnL:</span>
                    <span style="font-weight: 700; color: ${pnlColor}; font-size: 0.8rem;">
                        ${pnlFormatted} (${p.is_win ? 'WIN' : 'LOSS'})
                    </span>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center; padding-top: 5px; border-top: 1px solid rgba(255,255,255,0.1);">
                    <span style="color: #94a3b8; font-size: 0.75rem;">Win Rate at Exit:</span>
                    <span style="font-weight: 800; color: #818cf8; font-family: var(--font-mono); font-size: 0.85rem;">
                        ${p.win_rate.toFixed(1)}% (${p.wins_count}/${p.total_resolved})
                    </span>
                </div>
            `;
        } else {
            const p = closest.raw;
            const dateStr = p.timestamp ? new Date(p.timestamp * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
            const eq = Number(p.total_equity || p.cash || 10000);
            const netPnl = eq - 10000;
            const pnlColor = netPnl >= 0 ? '#10b981' : '#ef4444';

            tooltip.innerHTML = `
                <div style="font-weight: 700; color: #fff; margin-bottom: 4px;">
                    ${dateStr}
                </div>
                <div style="display: flex; justify-content: space-between; gap: 15px; margin-bottom: 3px;">
                    <span style="color: #94a3b8;">Total Equity:</span>
                    <span style="font-weight: 700; color: #fff; font-family: var(--font-mono);">${formatUSD(eq)}</span>
                </div>
                <div style="display: flex; justify-content: space-between; gap: 15px; margin-bottom: 3px;">
                    <span style="color: #94a3b8;">Net Profit:</span>
                    <span style="font-weight: 700; color: ${pnlColor}; font-family: var(--font-mono);">${(netPnl >= 0 ? '+' : '') + formatUSD(netPnl)}</span>
                </div>
            `;
        }
    });

    overlay.addEventListener('mouseleave', () => {
        crosshair.style.display = 'none';
        halo.style.display = 'none';
        dot.style.display = 'none';
        tooltip.style.display = 'none';
    });
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

