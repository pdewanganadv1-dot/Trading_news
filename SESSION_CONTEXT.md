# Session Context — Trading Dashboard

## Project
Full-stack trading dashboard (trading_news) with Nifty 100 technical signals + Groq AI explanations + 6 standalone analysis dashboards, deployed on Render, accessible via Telegram bot `@Signal_alpha267_bot`.

## Recent Work

### May 18, 2026

**Done**:
1. **Switched LLM**: Google Gemini → Groq (`llama-3.3-70b-versatile`) in `config.py`, `signal_explainer.py`, `requirements.txt`, `.env`
2. **Fixed Groq integration**: `_template_explain()` → `explain()` in `signal_monitor.py:47` and `market_realtime.py:141`
3. **Expanded stock list**: 24 → ~100 (Nifty 50 + Next 50)
4. **Yahoo rate limiting**: Added 2s delay between yfinance calls + custom User-Agent
5. **Fixed curl_cffi conflict**: Removed custom `requests.Session` from yfinance calls
6. **Cached `/signals` endpoint**: Background loop writes results to `_signal_cache` dict
7. **Fixed event loop blocking**: Wrapped yfinance calls in `asyncio.to_thread()` + `asyncio.wait_for(15s)`
8. **Cached `/realtime` endpoint**: Same pattern as /signals
9. **Removed duplicates**: 123 unique symbols now
10. **Health endpoint**: Cache stats for debugging
11. **Fixed `_cache_start` scope bug**: Added `global` keyword
12. **Telegram bot**: `@Signal_alpha267_bot` working (chat_id: 5163568145, polling loop 3s)
13. **Fixed circular imports** in telegram_notifier.py, signal_confirmer.py
14. **Pinned yfinance==0.2.65**
15. **FII/DII card on main dashboard**: Institutional flow card in `dashboard_live.html`
16. **Efficiency overhaul (7 fixes)**: Parallel processing, rate limiting, market-hours awareness, caching

### May 19-20, 2026 — Persistence, Standalone Pages, Stock List Consolidation

**Done**:
1. **Redeployed service**: Was 503 — restored via Render hook
2. **Persistent signal cache**: 3 SQLite tables in `accuracy_tracker.py` — survives restarts
3. **Render persistent disk**: 1GB at `/data` — `signals.db` persists across deploys
4. **On-startup cache reload**: `signal_monitor.py` loads from DB on import
5. **Config**: `persistent_dir` with fallback
6. **Consolidated stock lists**: `app/data/stocks.py` = single source of truth (119 Indian + 4 global)
7. **Fixed edge scanner coverage**: Was 24 → now full 119 stocks
8. **Options Chain Analysis** at `/options-chain` — F&O bhavcopy, PCR, max pain, OI distribution, key levels
9. **Insider Trading** at `/insider-trading` — bulk/block deals, top buyers/sellers, net flow
10. **Sector Rotation Board** at `/sector-rotation` — 11 NSE sectoral indices, per-sector stock breakdown
11. **AI Trading Agent** at `/ai-agent` — Groq LLM: news + price/technicals + FII/DII + social sentiment
12. **Strategy Marketplace** at `/strategy-marketplace` — 6 curated strategies + backtest simulator
13. **Congressional Trading** at `/politician-trades` — 11 business group bulk/block deals + FII/DII
14. **8 Telegram bot commands**: `/agent`, `/options`, `/insider`, `/sectors`, `/politicians`, `/strategies`, `/backtest`, `/markets`
15. **Breadth shows WHICH stocks**: `/breadth` lists top 5 above/below SMA20; `/breadth all` for full list
16. **Sentiment shows WHICH stocks**: `/sentiment` lists top 5 bullish/bearish by score
17. **`/stocks` command**: Lists all 119 monitored Indian stocks alphabetically
19. **DhanHQ Broker Integration** (`app/services/dhanhq_service.py`):
    - Live Market Quote API (LTP/OHLC for all 119 stocks in 1 call)
    - Order placement (BUY/SELL via Telegram `/buy`, `/sell`)
    - Dashboard: funds, positions, order book, profile
    - Auto-trading: SCALP scanner auto-places trades when enabled
    - Auto-renew: background loop renews access token every 23h
    - Toggle: `/dhanon` / `/dhanoff` to enable/disable
    - Credentials stored in `.env` (gitignored, never committed)

18. **EMA 200 Bounce Scanner** (`app/services/ema_bounce_scanner.py`):
    - Scans all 119 stocks on 1min timeframe
    - Detects bounces off EMA 200 with S/R confirmation
    - `/scalp` command shows **SCALP BUY** / **SCALP SELL** (separate from regular signals)
    - Auto-scan every 5 min during market hours with push alerts
    - `/scalpbt` — Backtest strategy on 6mo daily data (buy on bounce, target +10%, stop -5%, max 20d hold)
    - `/scalpon` / `/scalpoff` — Toggle SCALP signals on/off (currently **disabled** by default)
    - Target: 10% ROI, intraday or weekend hold

### May 20, 2026 — DhanHQ Fixes & Credential Sync

**Done**:
1. **DhanHQ reads credentials at runtime**: `_headers()` and `_client()` now fallback to `settings.dhan_client_id`/`settings.dhan_access_token` directly instead of relying on import-time cache
2. **Fixed scrip master column name**: Was reading `SYMBOL_NAME` (doesn't exist in CSV) → changed to `SEM_TRADING_SYMBOL` for security ID lookup
3. **Filter security map to NSE EQ only**: Added `NSE` + `E` (equity) segment filter to avoid BSE/derivative symbol collisions
4. **Added `/debug/dhan` endpoint**: Shows client_id, token status, security map state, profile, and funds for diagnosing Dhan issues
5. **Added `get_debug_status()`**: Exports current Dhan connection state for debugging
6. **Fixed Dhan env vars on Render**: User added `DHAN_CLIENT_ID` and `DHAN_ACCESS_TOKEN` env vars in Render dashboard (was incorrectly using `tradin_news`)
7. **DhanHQ fully operational**: `/dhan` dashboard shows funds (₹20,832), profile, `/buy RELIANCE 1` works now (was failing with "Security ID not found")

**Lesson**: Dhan scrip master CSV column `SYMBOL_NAME` doesn't exist — the correct column is `SEM_TRADING_SYMBOL` for the trading symbol. Must also filter to `NSE` + `E` segment only.

### May 21, 2026 — DIY Strategy Builder (Pine Script Port) + 1-min OHLC

**Done**:
1. **`app/services/ohlc_builder.py`** — Aggregates Dhan WebSocket ticks into 1-minute OHLC bars per symbol. Tracks Open/High/Low/Close/Volume. Stores last 200 bars. Singleton `ohlc_builder`.
2. **`app/services/strategy_builder.py`** — Full Python port of TradingView "DIY Custom Strategy Builder [ZP] - v1":
   - **36 leading indicators**: Range Filter, Speedy Range, SuperTrend, HalfTrend, RSI V2, Stochastic V2, CCI V2, Williams %R V2, TSI, TDFI, Fisher V2, Inv Fisher, Coppock, MACD, Awesome Osc, Momentum, ROC, TRIX, Vortex, KAMA, Chandelier, DIY MA, DEMA, TEMA, Laguerre RSI, RSI 3/3/3, LinReg, Swing Index, Rainbow MA, Aroon, PSAR, ZLEMA, HMA, ALMA, JJMA, Tillson T3
   - **23 confirmation filters**: EMA 20/50/100/200, SMA 20/50, Bollinger, Keltner, ADX, ATR Trail, Donchian, MACD, RSI, Stochastic, Volume, Price Action, MFI, OBV, Williams %R, Heikin Ashi, VWAP, Pivot, Divergence
   - **Signal engine**: Leading indicator + confirmations vote → BUY/SELL/HOLD, configurable threshold/expiry/alt mode
   - **Backtest/History**: `backtest()`, `get_signal_history()`, `format_signal_history()` — uses yfinance historical data
   - **SQLite persistence**: Signal cache + active state tracking
3. **`app/services/market_feed.py`** — Now calls `ohlc_builder.process_tick()` on every parsed packet
4. **`app/main.py`** — Added `strategy_builder_loop` (scans all symbols every 3 min, auto-alerts)
5. **Telegram commands**: `/strategy`, `/strategy_config`, `/strategy_leading`, `/strategy_threshold`, `/strategy_expiry`, `/strategy_alt`, `/strategy_bt`, `/strategy_signals`, `/signals_<sym>`
6. **Backtest results** (RELIANCE, Range Filter, daily 1y): 239 signals, 16 trades, 25% win rate, -5.32% return

### Current State
- **Deployed at**: https://trading-dashboard-e0us.onrender.com/
- **Live pages**: `/options-chain`, `/insider-trading`, `/sector-rotation`, `/ai-agent`, `/strategy-marketplace`, `/politician-trades`
- **GitHub**: git@github.com:pdewanganadv1-dot/Trading_news.git (main branch)
- **Deploy hook**: ~~broken (repo private)~~ — manual deploy via Render dashboard
- **Git commit HEAD**: `066b2d2` (strategy builder)
- **Repo**: **Private** on GitHub

### Key Files
| File | Purpose |
|------|---------|
| `app/config.py` | Settings (groq_api_key, signal_check_interval=600s, persistent_dir) |
| `app/data/stocks.py` | Single source of truth for stock lists (INDIAN_STOCKS, MONITORED_SYMBOLS) |
| `app/services/signal_monitor.py` | Bg loop, caches, 123 symbols, DB persistence |
| `app/services/market_data_service.py` | yfinance calls in threads with 2s delay + 15s timeout |
| `app/services/signal_explainer.py` | Groq LLM client + template fallback |
| `app/services/telegram_bot.py` | Polling loop, all command handlers |
| `app/services/telegram_notifier.py` | Send messages |
| `app/services/accuracy_tracker.py` | SQLite DB — signal history + cache/sent persistence |
| `app/services/market_edge_service.py` | Edge scanner, FII/DII, breadth (full 119 stocks) |
| `app/services/ema_bounce_scanner.py` | EMA 200 bounce scanner on 1min chart (SCALP signals) |
| `app/services/dhanhq_service.py` | DhanHQ broker integration: market data, orders, funds, token mgmt |
| `app/services/options_chain_service.py` | F&O bhavcopy → option chain (PCR, max pain, key levels) |
| `app/services/insider_service.py` | NSE bulk/block deals with summary aggregation |
| `app/services/sector_service.py` | Sectoral index performance + industry→sector mapping |
| `app/services/ai_agent_service.py` | Groq-based multi-modal stock analysis |
| `app/services/strategy_marketplace.py` | 6 curated strategies + in-memory backtest simulator |
| `app/services/politician_service.py` | 11 business group bulk/block deals + FII/DII |
| `app/services/ohlc_builder.py` | 1-min OHLC bar builder from Dhan WebSocket tick stream |
| `app/services/strategy_builder.py` | DIY Strategy Builder: 36 leading indicators, 23 confirmation filters, signal engine, backtest |
| `app/routes/market_realtime.py` | API endpoints (signals + realtime) |
| `app/main.py` | FastAPI entry, lifespan tasks, 6 new routers |
| `render.yaml` | Render service config (Docker + persistent disk) |

### Telegram Commands
| Command | Description |
|---------|-------------|
| `summary` | Dashboard overview |
| `edges` | Top 10 stocks by edge score |
| `breadth` | Market breadth with top/bottom stocks list |
| `breadth all` | Full list of stocks above SMA20 |
| `sentiment` | Market sentiment with top bullish/bearish stocks |
| `stocks` | All 119 monitored stocks |
| `fiidii` | FII/DII institutional flow |
| `social <sym>` | StockTwits + Reddit sentiment |
| `/scalp` | SCALP signals — EMA 200 bounces on 1min chart |
| `/scalpbt` | Backtest SCALP strategy on 6mo daily data |
| `/scalpon` / `/scalpoff` | Toggle SCALP signals on/off (default: off) |
| `/dhan` | DhanHQ dashboard (funds, account, data plan) |
| `/dhanon` / `/dhanoff` | Toggle DhanHQ auto-trading (default: off) |
| `/buy <sym> <qty>` | Place BUY order via Dhan |
| `/sell <sym> <qty>` | Place SELL order via Dhan |
| `/agent <sym>` | AI multi-modal analysis |
| `/options <sym>` | Option chain with PCR & max pain |
| `/insider` | Bulk & block deals |
| `/sectors` | Sector rotation performance |
| `/politicians` | Group political trades |
| `/strategies` | Strategy marketplace |
| `/backtest <id>` | Backtest a strategy |
| `/strategy` | DIY Strategy Builder dashboard (active BUY/SELL signals) |
| `/strategy_config` | View current config + all indicators/filters |
| `/strategy_leading <name>` | Set leading indicator (36 options) |
| `/strategy_threshold <N>` | Min confirmations required (default 3) |
| `/strategy_expiry <N>` | Signal expiry in bars (default 5) |
| `/strategy_alt` | Toggle alternate signal mode |
| `/strategy_bt <sym> [days] [1d\|1m]` | Backtest on historical data |
| `/strategy_signals <sym> [days] [1d\|1m]` | Full BUY/SELL signal history |
| `/signals_<sym> [days] [1d\|1m]` | Quick signal history shortcut |

### Tech Stack
- FastAPI + uvicorn (Render Docker, free tier)
- yfinance (0.2.65) for Indian stock prices
- nselib (2.5.1) for NSE data (FII/DII, bulk/block deals, sectoral indices, F&O bhavcopy)
- Groq (llama-3.3-70b-versatile) for AI analysis
- python-telegram-bot (polling via httpx)
- DhanHQ WebSocket feed (9,457 NSE EQ symbols) for live ticks
- SQLite + Render persistent disk (1GB at /data)
- pandas for backtest calculations
- Chart.js for all standalone dashboards
