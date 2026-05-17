from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="", tags=["indicators"])


@router.get("/indicators", response_class=HTMLResponse)
async def get_indicators_dashboard():
    """Trading Indicators Dashboard - Technical Analysis Module"""

    html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Trading Indicators - Technical Analysis</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Space+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }

        :root {
            --bg-primary: #0f0f14;
            --bg-secondary: #18181f;
            --bg-card: #1e1e28;
            --bg-card-hover: #252530;
            --border-color: #2a2a3a;
            --text-primary: #f0f0f5;
            --text-secondary: #8888a0;
            --accent-blue: #4f8cff;
            --accent-cyan: #38bdf8;
            --accent-green: #34d399;
            --accent-red: #f87171;
            --accent-gold: #fbbf24;
            --accent-purple: #a78bfa;
            --gradient-blue: linear-gradient(135deg, #4f8cff, #38bdf8);
            --gradient-green: linear-gradient(135deg, #34d399, #10b981);
            --gradient-red: linear-gradient(135deg, #f87171, #ef4444);
        }

        body {
            font-family: 'Inter', -apple-system, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            background-image:
                radial-gradient(ellipse at 10% 10%, rgba(79, 140, 255, 0.08) 0%, transparent 40%),
                radial-gradient(ellipse at 90% 90%, rgba(167, 139, 250, 0.08) 0%, transparent 40%);
        }

        .header {
            background: linear-gradient(180deg, var(--bg-card) 0%, var(--bg-secondary) 100%);
            padding: 1rem 2rem;
            border-bottom: 1px solid var(--border-color);
            display: flex;
            justify-content: space-between;
            align-items: center;
            position: sticky;
            top: 0;
            z-index: 100;
            box-shadow: 0 4px 16px rgba(0,0,0,0.4);
        }

        .logo { display: flex; align-items: center; gap: 0.75rem; }

        .logo-icon {
            width: 44px;
            height: 44px;
            background: linear-gradient(135deg, #a78bfa, #6366f1);
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 1.1rem;
            font-family: 'Space Grotesk', sans-serif;
            box-shadow: 0 4px 15px rgba(167, 139, 250, 0.4);
        }

        .logo h1 {
            font-size: 1.3rem;
            font-weight: 600;
            font-family: 'Space Grotesk', sans-serif;
            color: var(--text-primary);
        }

        .header-right { display: flex; align-items: center; gap: 1.5rem; }

        .asset-selector {
            display: flex;
            gap: 0.5rem;
            background: var(--bg-secondary);
            padding: 0.3rem;
            border-radius: 10px;
        }

        .asset-btn {
            padding: 0.5rem 1rem;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 0.85rem;
            font-weight: 600;
            transition: all 0.3s;
            background: transparent;
            color: var(--text-secondary);
        }

        .asset-btn:hover { color: var(--text-primary); }
        .asset-btn.active {
            background: var(--accent-purple);
            color: white;
            box-shadow: 0 0 15px rgba(167, 139, 250, 0.4);
        }

        .refresh-btn {
            background: var(--gradient-blue);
            color: white;
            border: none;
            padding: 0.6rem 1.2rem;
            border-radius: 10px;
            cursor: pointer;
            font-size: 0.85rem;
            font-weight: 600;
            transition: all 0.3s;
            box-shadow: 0 4px 15px rgba(79, 140, 255, 0.3);
        }

        .refresh-btn:hover { transform: translateY(-2px); }

        .main-content {
            display: grid;
            grid-template-columns: 1fr 380px;
            gap: 1.5rem;
            padding: 1.5rem;
            max-width: 1600px;
            margin: 0 auto;
        }

        .left-column { display: flex; flex-direction: column; gap: 1.25rem; }

        .card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 20px;
            padding: 1.5rem;
            box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        }

        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1rem;
        }

        .card-title {
            font-size: 0.9rem;
            color: var(--text-primary);
            font-weight: 600;
            font-family: 'Space Grotesk', sans-serif;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        .card-title::before {
            content: '📊';
        }

        .current-symbol {
            font-size: 0.85rem;
            color: var(--accent-purple);
            font-weight: 600;
        }

        /* Chart */
        .chart-wrapper { height: 300px; position: relative; }

        /* Indicator Cards Grid */
        .indicators-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 1rem;
        }

        .indicator-card {
            background: var(--bg-secondary);
            border-radius: 14px;
            padding: 1rem;
            border: 1px solid var(--border-color);
        }

        .indicator-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 0.75rem;
        }

        .indicator-name {
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--text-primary);
            font-family: 'Space Grotesk', sans-serif;
        }

        .indicator-value {
            font-size: 1.25rem;
            font-weight: 700;
            font-family: 'Space Grotesk', sans-serif;
        }

        .indicator-status {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            font-size: 0.75rem;
            margin-top: 0.5rem;
        }

        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
        }

        .status-dot.buy { background: var(--accent-green); box-shadow: 0 0 8px var(--accent-green); }
        .status-dot.sell { background: var(--accent-red); box-shadow: 0 0 8px var(--accent-red); }
        .status-dot.neutral { background: var(--text-secondary); }

        .indicator-mini-chart {
            height: 50px;
            margin-top: 0.5rem;
        }

        /* Signal Summary */
        .signal-box {
            text-align: center;
            padding: 1.5rem;
            border-radius: 16px;
            margin-bottom: 1rem;
        }

        .signal-box.buy {
            background: linear-gradient(135deg, rgba(52, 211, 153, 0.15), rgba(16, 185, 129, 0.05));
            border: 2px solid var(--accent-green);
        }

        .signal-box.sell {
            background: linear-gradient(135deg, rgba(248, 113, 113, 0.15), rgba(239, 68, 68, 0.05));
            border: 2px solid var(--accent-red);
        }

        .signal-box.neutral {
            background: linear-gradient(135deg, rgba(136, 136, 160, 0.15), rgba(100, 116, 139, 0.05));
            border: 2px solid var(--text-secondary);
        }

        .signal-label {
            font-size: 1.5rem;
            font-weight: 700;
            font-family: 'Space Grotesk', sans-serif;
            letter-spacing: 2px;
        }

        .signal-box.buy .signal-label { color: var(--accent-green); }
        .signal-box.sell .signal-label { color: var(--accent-red); }
        .signal-box.neutral .signal-label { color: var(--text-secondary); }

        .signal-strength {
            font-size: 0.8rem;
            color: var(--text-secondary);
            margin-top: 0.5rem;
        }

        /* Indicator Details Table */
        .indicator-table {
            width: 100%;
            border-collapse: collapse;
        }

        .indicator-table th {
            text-align: left;
            padding: 0.75rem;
            font-size: 0.7rem;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 1px;
            border-bottom: 1px solid var(--border-color);
        }

        .indicator-table td {
            padding: 0.75rem;
            font-size: 0.85rem;
            border-bottom: 1px solid var(--border-color);
        }

        .indicator-table tr:last-child td { border-bottom: none; }

        .value-buy { color: var(--accent-green); font-weight: 600; }
        .value-sell { color: var(--accent-red); font-weight: 600; }
        .value-neutral { color: var(--text-secondary); }

        /* SMA/EMA Display */
        .ma-lines {
            display: flex;
            gap: 1rem;
            flex-wrap: wrap;
            margin-top: 0.5rem;
        }

        .ma-line {
            display: flex;
            align-items: center;
            gap: 0.4rem;
            font-size: 0.75rem;
        }

        .ma-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
        }

        .ma-value {
            font-weight: 600;
            font-family: 'Space Grotesk', sans-serif;
        }

        /* RSI Gauge */
        .rsi-gauge {
            height: 12px;
            background: linear-gradient(90deg, var(--accent-green) 0%, var(--accent-gold) 50%, var(--accent-red) 100%);
            border-radius: 6px;
            position: relative;
            margin-top: 0.5rem;
        }

        .rsi-marker {
            position: absolute;
            top: -4px;
            width: 20px;
            height: 20px;
            background: white;
            border-radius: 50%;
            border: 3px solid var(--accent-blue);
            transform: translateX(-50%);
            box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        }

        .rsi-labels {
            display: flex;
            justify-content: space-between;
            font-size: 0.65rem;
            color: var(--text-secondary);
            margin-top: 0.25rem;
        }

        /* MACD */
        .macd-display {
            display: flex;
            gap: 1rem;
            margin-top: 0.5rem;
        }

        .macd-item {
            text-align: center;
            flex: 1;
        }

        .macd-label {
            font-size: 0.65rem;
            color: var(--text-secondary);
            text-transform: uppercase;
        }

        .macd-value {
            font-size: 0.9rem;
            font-weight: 600;
            font-family: 'Space Grotesk', sans-serif;
        }

        /* Bollinger Bands */
        .bb-display {
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }

        .bb-row {
            display: flex;
            justify-content: space-between;
            font-size: 0.8rem;
        }

        .bb-label { color: var(--text-secondary); }
        .bb-value { font-weight: 600; font-family: 'Space Grotesk', sans-serif; }

        .loading {
            text-align: center;
            padding: 2rem;
            color: var(--accent-blue);
        }

        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: var(--bg-secondary); }
        ::-webkit-scrollbar-thumb { background: var(--border-color); border-radius: 3px; }
    </style>
</head>
<body>
    <div class="header">
        <div class="logo">
            <div class="logo-icon">IND</div>
            <h1>Trading Indicators</h1>
        </div>
        <div class="header-right">
            <div class="asset-selector">
                <button class="asset-btn active" onclick="selectAsset('BTC')">BTC</button>
                <button class="asset-btn" onclick="selectAsset('ETH')">ETH</button>
                <button class="asset-btn" onclick="selectAsset('GOLD')">GOLD</button>
                <button class="asset-btn" onclick="selectAsset('SILVER')">SILVER</button>
            </div>
            <button class="refresh-btn" onclick="loadIndicators()">↻ Refresh</button>
        </div>
    </div>

    <div class="main-content">
        <div class="left-column">
            <!-- Price Chart with Indicators -->
            <div class="card">
                <div class="card-header">
                    <span class="card-title">Price Chart</span>
                    <span class="current-symbol" id="currentSymbol">BTC/USD</span>
                </div>
                <div class="chart-wrapper">
                    <canvas id="priceChart"></canvas>
                </div>
            </div>

            <!-- Technical Indicators Grid -->
            <div class="card">
                <div class="card-header">
                    <span class="card-title">Technical Indicators</span>
                </div>
                <div class="indicators-grid" id="indicatorsGrid">
                    <div class="loading">Loading indicators...</div>
                </div>
            </div>
        </div>

        <!-- Right Sidebar -->
        <div class="right-column">
            <!-- Overall Signal -->
            <div class="card">
                <div class="card-header">
                    <span class="card-title">📈 Signal Summary</span>
                </div>
                <div class="signal-box" id="signalBox">
                    <div class="signal-label" id="signalLabel">NEUTRAL</div>
                    <div class="signal-strength" id="signalStrength">Waiting for data...</div>
                </div>

                <table class="indicator-table">
                    <thead>
                        <tr>
                            <th>Indicator</th>
                            <th>Value</th>
                            <th>Signal</th>
                        </tr>
                    </thead>
                    <tbody id="indicatorTable">
                        <tr><td colspan="3" style="text-align: center; color: var(--text-secondary);">Loading...</td></tr>
                    </tbody>
                </table>
            </div>

            <!-- Indicator Details -->
            <div class="card">
                <div class="card-header">
                    <span class="card-title">📋 Indicator Details</span>
                </div>
                <div id="indicatorDetails">
                    <div class="loading">Loading details...</div>
                </div>
            </div>
        </div>
    </div>

    <script>
        // Mock indicator data
        const indicatorData = {
            BTC: {
                price: 67432.50,
                change: 2.34,
                sma: { sma20: 66800, sma50: 65200, sma200: 62000, trend: 'bullish' },
                ema: { ema12: 67200, ema26: 66500, trend: 'bullish' },
                rsi: 68.5,
                macd: { macd: 320, signal: 180, histogram: 140 },
                bb: { upper: 68200, middle: 67000, lower: 65800 },
                trend: 'bullish'
            },
            ETH: {
                price: 3521.80,
                change: 1.85,
                sma: { sma20: 3450, sma50: 3380, sma200: 3200, trend: 'bullish' },
                ema: { ema12: 3510, ema26: 3480, trend: 'bullish' },
                rsi: 62.3,
                macd: { macd: 45, signal: 32, histogram: 13 },
                bb: { upper: 3580, middle: 3520, lower: 3460 },
                trend: 'bullish'
            },
            GOLD: {
                price: 2342.30,
                change: 0.45,
                sma: { sma20: 2335, sma50: 2320, sma200: 2280, trend: 'neutral' },
                ema: { ema12: 2340, ema26: 2335, trend: 'neutral' },
                rsi: 52.8,
                macd: { macd: 8, signal: 6, histogram: 2 },
                bb: { upper: 2360, middle: 2345, lower: 2330 },
                trend: 'neutral'
            },
            SILVER: {
                price: 28.45,
                change: -0.32,
                sma: { sma20: 28.6, sma50: 28.2, sma200: 27.5, trend: 'bearish' },
                ema: { ema12: 28.4, ema26: 28.5, trend: 'bearish' },
                rsi: 42.1,
                macd: { macd: -0.15, signal: -0.08, histogram: -0.07 },
                bb: { upper: 29.0, middle: 28.5, lower: 28.0 },
                trend: 'bearish'
            }
        };

        let currentAsset = 'BTC';
        let priceChart;

        function selectAsset(asset) {
            currentAsset = asset;
            document.querySelectorAll('.asset-btn').forEach(btn => {
                btn.classList.remove('active');
            });
            event.target.closest('.asset-btn').classList.add('active');
            document.getElementById('currentSymbol').textContent = asset + '/USD';
            loadIndicators();
        }

        function loadIndicators() {
            const data = indicatorData[currentAsset];
            updateSignal(data);
            updateIndicatorsGrid(data);
            updateIndicatorTable(data);
            updateIndicatorDetails(data);
            updateChart(data);
        }

        function updateSignal(data) {
            const signalBox = document.getElementById('signalBox');
            const signalLabel = document.getElementById('signalLabel');
            const signalStrength = document.getElementById('signalStrength');

            let signal, strength;
            if (data.trend === 'bullish') {
                signal = 'BUY';
                signalBox.className = 'signal-box buy';
                strength = '3/5 indicators bullish';
            } else if (data.trend === 'bearish') {
                signal = 'SELL';
                signalBox.className = 'signal-box sell';
                strength = '3/5 indicators bearish';
            } else {
                signal = 'NEUTRAL';
                signalBox.className = 'signal-box neutral';
                strength = 'Mixed signals';
            }

            signalLabel.textContent = signal;
            signalStrength.textContent = strength;
        }

        function updateIndicatorsGrid(data) {
            const grid = document.getElementById('indicatorsGrid');

            const indicators = [
                {
                    name: 'SMA (Simple Moving Average)',
                    value: data.sma.sma20.toLocaleString(),
                    status: data.sma.trend,
                    details: `SMA 20: ${data.sma.sma20.toLocaleString()}<br>SMA 50: ${data.sma.sma50.toLocaleString()}<br>SMA 200: ${data.sma.sma200.toLocaleString()}`
                },
                {
                    name: 'EMA (Exponential MA)',
                    value: data.ema.ema12.toLocaleString(),
                    status: data.ema.trend,
                    details: `EMA 12: ${data.ema.ema12.toLocaleString()}<br>EMA 26: ${data.ema.ema26.toLocaleString()}`
                },
                {
                    name: 'RSI (14)',
                    value: data.rsi.toFixed(1),
                    status: data.rsi > 70 ? 'sell' : data.rsi < 30 ? 'buy' : 'neutral',
                    details: `RSI: ${data.rsi.toFixed(1)}`
                },
                {
                    name: 'MACD',
                    value: data.macd.macd.toFixed(2),
                    status: data.macd.histogram > 0 ? 'buy' : 'sell',
                    details: `MACD: ${data.macd.macd.toFixed(2)}<br>Signal: ${data.macd.signal.toFixed(2)}<br>Hist: ${data.macd.histogram.toFixed(2)}`
                },
                {
                    name: 'Bollinger Bands',
                    value: data.bb.middle.toLocaleString(),
                    status: data.price > data.bb.upper ? 'sell' : data.price < data.bb.lower ? 'buy' : 'neutral',
                    details: `Upper: ${data.bb.upper.toLocaleString()}<br>Middle: ${data.bb.middle.toLocaleString()}<br>Lower: ${data.bb.lower.toLocaleString()}`
                },
                {
                    name: 'Trend',
                    value: data.trend.toUpperCase(),
                    status: data.trend,
                    details: `Price: $${data.price.toLocaleString()}<br>Change: ${data.change >= 0 ? '+' : ''}${data.change.toFixed(2)}%`
                }
            ];

            grid.innerHTML = indicators.map(ind => `
                <div class="indicator-card">
                    <div class="indicator-header">
                        <span class="indicator-name">${ind.name}</span>
                    </div>
                    <div class="indicator-value" style="color: ${ind.status === 'buy' ? 'var(--accent-green)' : ind.status === 'sell' ? 'var(--accent-red)' : 'var(--text-primary)'}">${ind.value}</div>
                    <div class="indicator-status">
                        <span class="status-dot ${ind.status}"></span>
                        <span>${ind.status.toUpperCase()}</span>
                    </div>
                </div>
            `).join('');
        }

        function updateIndicatorTable(data) {
            const table = document.getElementById('indicatorTable');

            const rows = [
                { name: 'SMA 20', value: data.sma.sma20.toLocaleString(), signal: data.sma.trend },
                { name: 'EMA 12', value: data.ema.ema12.toLocaleString(), signal: data.ema.trend },
                { name: 'RSI 14', value: data.rsi.toFixed(1), signal: data.rsi > 70 ? 'sell' : data.rsi < 30 ? 'buy' : 'neutral' },
                { name: 'MACD', value: data.macd.macd.toFixed(2), signal: data.macd.histogram > 0 ? 'buy' : 'sell' },
                { name: 'BB Upper', value: data.bb.upper.toLocaleString(), signal: data.price > data.bb.upper ? 'sell' : 'neutral' },
                { name: 'BB Lower', value: data.bb.lower.toLocaleString(), signal: data.price < data.bb.lower ? 'buy' : 'neutral' }
            ];

            table.innerHTML = rows.map(row => `
                <tr>
                    <td>${row.name}</td>
                    <td>${row.value}</td>
                    <td class="value-${row.signal}">${row.signal.toUpperCase()}</td>
                </tr>
            `).join('');
        }

        function updateIndicatorDetails(data) {
            const details = document.getElementById('indicatorDetails');

            details.innerHTML = `
                <div style="margin-bottom: 1rem;">
                    <div style="font-size: 0.75rem; color: var(--text-secondary); margin-bottom: 0.5rem;">RSI GAUGE (14)</div>
                    <div class="rsi-gauge">
                        <div class="rsi-marker" style="left: ${data.rsi}%"></div>
                    </div>
                    <div class="rsi-labels">
                        <span>0 (Oversold)</span>
                        <span>70 (Overbought)</span>
                    </div>
                </div>
                <div style="margin-bottom: 1rem;">
                    <div style="font-size: 0.75rem; color: var(--text-secondary); margin-bottom: 0.5rem;">MOVING AVERAGES</div>
                    <div class="ma-lines">
                        <div class="ma-line">
                            <span class="ma-dot" style="background: #4f8cff;"></span>
                            <span>MA20</span>
                            <span class="ma-value">${data.sma.sma20.toLocaleString()}</span>
                        </div>
                        <div class="ma-line">
                            <span class="ma-dot" style="background: #a78bfa;"></span>
                            <span>MA50</span>
                            <span class="ma-value">${data.sma.sma50.toLocaleString()}</span>
                        </div>
                        <div class="ma-line">
                            <span class="ma-dot" style="background: #34d399;"></span>
                            <span>MA200</span>
                            <span class="ma-value">${data.sma.sma200.toLocaleString()}</span>
                        </div>
                    </div>
                </div>
                <div>
                    <div style="font-size: 0.75rem; color: var(--text-secondary); margin-bottom: 0.5rem;">MACD (12, 26, 9)</div>
                    <div class="macd-display">
                        <div class="macd-item">
                            <div class="macd-label">MACD</div>
                            <div class="macd-value" style="color: ${data.macd.macd > 0 ? 'var(--accent-green)' : 'var(--accent-red)'}">${data.macd.macd.toFixed(2)}</div>
                        </div>
                        <div class="macd-item">
                            <div class="macd-label">Signal</div>
                            <div class="macd-value">${data.macd.signal.toFixed(2)}</div>
                        </div>
                        <div class="macd-item">
                            <div class="macd-label">Hist</div>
                            <div class="macd-value" style="color: ${data.macd.histogram > 0 ? 'var(--accent-green)' : 'var(--accent-red)'}">${data.macd.histogram.toFixed(2)}</div>
                        </div>
                    </div>
                </div>
            `;
        }

        function updateChart(data) {
            const ctx = document.getElementById('priceChart').getContext('2d');

            // Generate mock price data
            const labels = [];
            const priceDataArr = [];
            let price = data.price * 0.95;
            for (let i = 0; i < 20; i++) {
                labels.push('Day ' + (i + 1));
                price += (Math.random() - 0.4) * (data.price * 0.02);
                priceDataArr.push(price);
            }
            priceDataArr[priceDataArr.length - 1] = data.price;

            if (priceChart) {
                priceChart.destroy();
            }

            priceChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'Price',
                        data: priceDataArr,
                        borderColor: '#a78bfa',
                        backgroundColor: 'rgba(167, 139, 250, 0.1)',
                        fill: true,
                        tension: 0.4,
                        pointRadius: 0,
                        pointHoverRadius: 6,
                        pointHoverBackgroundColor: '#a78bfa'
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false }
                    },
                    scales: {
                        x: {
                            grid: { color: 'rgba(255,255,255,0.05)' },
                            ticks: { color: '#8888a0' }
                        },
                        y: {
                            grid: { color: 'rgba(255,255,255,0.05)' },
                            ticks: {
                                color: '#8888a0',
                                callback: function(value) {
                                    return '$' + value.toLocaleString();
                                }
                            }
                        }
                    }
                }
            });
        }

        // Auto-refresh every 30 seconds
        setInterval(loadIndicators, 30000);

        // Initial load
        loadIndicators();
    </script>
</body>
</html>"""

    return HTMLResponse(content=html)