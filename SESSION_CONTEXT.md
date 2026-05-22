# Session Context — Trading Dashboard

## ⚠️ CRITICAL: Dhan Token Expires Daily ⚠️
The Dhan access token (`DHAN_ACCESS_TOKEN`) expires **every 24 hours**. Every time you start a new session, the **first thing** you must do is:
1. Get a new token from Dhan
2. Update it in **both** places:
   - `./.env` (local — `DHAN_ACCESS_TOKEN=...`)
   - **Render API** (quicker than dashboard):
     ```
     curl -X PUT -H "Authorization: Bearer $RENDER_API_KEY" -H "Content-Type: application/json" \
       "https://api.render.com/v1/services/srv-d8514l3rjlhs73dj5ul0/env-vars/DHAN_ACCESS_TOKEN" \
       -d '{"value":"NEW_TOKEN"}'
     ```
   - Or Render dashboard → Environment Variables → `DHAN_ACCESS_TOKEN`
3. Then trigger a Render deploy:
   ```
   curl -X POST -H "Authorization: Bearer $RENDER_API_KEY" \
     "https://api.render.com/v1/services/srv-d8514l3rjlhs73dj5ul0/deploys" \
     -H "Content-Type: application/json" -d '{}'
   ```
   Or via hook: `curl -X POST https://api.render.com/deploy/srv-d8514l3rjlhs73dj5ul0?key=dKh3Te8CRXI`
4. The app also has an `auto_renew_loop()` that tries to renew every 23h, but a manual renewal is still needed if >24h passes without restart.

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
- **Git commit HEAD**: `1886502`
- **Repo**: **Public** on GitHub
- **Render API Key**: `rnd_oOQmH6cdn0LjgNkhpRUUrv2Mw7Pw` (stored locally, never committed)

### Render API (opencode access)
- **Service ID**: `srv-d8514l3rjlhs73dj5ul0`
- **API Key**: `rnd_oOQmH6cdn0LjgNkhpRUUrv2Mw7Pw`
- **API Base**: `https://api.render.com/v1`
- **Deploy via API**: `curl -X POST -H "Authorization: Bearer $RENDER_API_KEY" "https://api.render.com/v1/services/srv-d8514l3rjlhs73dj5ul0/deploys" -H "Content-Type: application/json" -d '{}'`
- **Check deploys**: `curl -H "Authorization: Bearer $RENDER_API_KEY" "https://api.render.com/v1/services/srv-d8514l3rjlhs73dj5ul0/deploys?limit=5"`
- **View env vars**: `curl -H "Authorization: Bearer $RENDER_API_KEY" "https://api.render.com/v1/services/srv-d8514l3rjlhs73dj5ul0/env-vars"`
- **View logs**: `curl -H "Authorization: Bearer $RENDER_API_KEY" "https://api.render.com/v1/services/srv-d8514l3rjlhs73dj5ul0/deploys/DEPLOY_ID/logs"`
- **Update env var**: `curl -X PUT -H "Authorization: Bearer $RENDER_API_KEY" "https://api.render.com/v1/services/srv-d8514l3rjlhs73dj5ul0/env-vars/DHAN_ACCESS_TOKEN" -H "Content-Type: application/json" -d '{"value":"NEW_TOKEN"}'`
- **Deploy hook**: `curl -X POST https://api.render.com/deploy/srv-d8514l3rjlhs73dj5ul0?key=dKh3Te8CRXI`
- **GitHub SSH push**: `GIT_SSH_COMMAND="ssh -i ~/.ssh/github_deploy" git push origin main`

### May 21, 2026 (Evening) — Dhan WebSocket Signals + Expanded Stock List

**Done**:
1. **Stock list expanded**: 119 → **149 Indian stocks** (full Nifty 100 + 22 extras). Fixed 50+ bad symbol names. Sourced from `nifty100.json`.
2. **Dhan OHLC for prices**: Single API call for all stocks bypasses slow yfinance calls.
3. **OHLC builder for signals**: Resamples WebSocket 1-min bars to 5m/15m for technical indicators (RSI, MACD, etc).
4. **yfinance → fallback only**: Used only when Dhan data unavailable (cold start / market closed).
5. **Startup scan**: Runs immediately after deploy, not just during market hours.
6. **Market-closed cache**: Realtime price cached even when 5min data unavailable.

### May 21, 2026 (Evening) — Robustness Fixes

**Done**:
1. **Fixed `persistent_dir` default** (`app/config.py`): Now resolves to `<project>/data/` instead of root. Render env var `PERSISTENT_DIR=/data` still overrides for persistent disk.
2. **Fixed `_cache_start`**: Always set to app startup time (was `""` when cache empty), so health endpoint always shows when the app started.
3. **Fixed market-hours first-run bug** (`app/services/signal_monitor.py`): Signal loop now runs **at least once on every deploy/startup**, regardless of market hours. Ensures cache is populated immediately after deploy. Subsequent iterations respect `_is_market_hours()`.
4. **Corrected market hours**: 9:15 AM–3:30 PM IST (was 9:00–16:00).
5. **Auto-create `data/` dir**: `accuracy_tracker.py` now creates the DB directory on import if missing. Added `data/` to `.gitignore`.
6. **SSH deploy key**: Added `~/.ssh/github_deploy` for pushing to private repo.

### May 21, 2026 (Night) — Batch Backtest + Speedy+ALMA Composite Strategy

**Backtest Results (36 indicators × 133 stocks, 30-day daily)**:
- Ran in 30s (data cached locally), 187 total signals
- **Top by 5-day win rate**: Speedy Range (100%, 3 sigs), ALMA (75%, 4 sigs), DIY MA (67%, 6 sigs)
- Default SuperTrend scored lowest among active indicators: 50% win rate, +2.46% avg P&L
- CSV with timestamps (market close 15:30 IST) sent to Telegram

**Changes**:
1. **`scan_all()` parallelized**: `ThreadPoolExecutor(max_workers=10)` instead of sequential loop (`strategy_builder.py:1592`)
2. **New composite indicator** `leading_speedy_alma()`: Requires both Speedy Range & ALMA to agree on direction. Registered as `"Speedy+ALMA"` in `LEADING_INDICATORS`.
3. **Default leading indicator changed**: `SuperTrend` → `Speedy+ALMA` in `StrategyBuilder.__init__`
4. **Created** `backtest_batch.py`, `send_backtest_report.py` (standalone scripts)
5. **Deployed**: Commit `39155e2` → Render deploy hook triggered

**Key insight**: Daily timeframe backtest shows Speedy+ALMA composite should outperform old SuperTrend default during live market hours.

### May 22, 2026 — Session Recovery + Sanity Fixes + Dhan Order Pipeline

**Part 1 — Recovery & Sanity Fixes**:
1. **Recovered local repo**: Synced 52 commits behind from GitHub (merged origin-https/main)
2. **Switched remote to HTTPS**: SSH had no key access
3. **Made GitHub repo public**: Enabled deploy hook access
4. **Redeployed to Render**: Triggered deploy hook → app back online (HTTP 200, 100% cache)
5. **Fixed dead code** in `app/routes/debug.py:278-302`: Removed unreachable duplicate code block after early return
6. **Updated `.env.example`**: Now lists all required vars (Telegram, Groq, Dhan, PERSISTENT_DIR, etc.)
7. **Updated `docker-compose.yml`**: Passes through `GROQ_API_KEY`, `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN`, `PERSISTENT_DIR`
8. **Updated `render.yaml`**: Added `DHAN_CLIENT_ID` and `DHAN_ACCESS_TOKEN` env vars (sync: false)
9. **Pushed fixes to GitHub**
10. **Configuration & setup**:
    - Configured SSH config (`~/.ssh/config`) pointing to `github_deploy` key
    - Switched remote from HTTPS back to SSH with identity file
    - Got SSH push working: `GIT_SSH_COMMAND="ssh -i ~/.ssh/github_deploy" git push origin main`

**Part 2 — Code Quality Fixes (May 22 afternoon)**:
1. **Removed dead dependencies** from `requirements.txt`: `sqlalchemy`, `asyncpg`, `alembic`, `python-multipart`
2. **Fixed `app/data/market_data.py` stub**: Rewired `routes/market.py` to use real `market_data_service`
3. **Fixed `sentiment.py:151`**: Removed unused walrus operator assignment
4. **Hoisted `INDIAN_STOCKS_SET` import** to top in `signal_confirmer.py`
5. **Fixed WebSocket `ConnectionManager.disconnect()`**: Added `ValueError` catch on removal
6. **Updated `SESSION_CONTEXT.md`**: Added Render API key, commands, and May 22 session

**Part 3 — Dhan Order Pipeline (May 22 evening)**:
1. **Generated new Dhan access token** from user, decoded JWT (exp: 2026-05-23T15:14:29 UTC)
2. **Updated `DHAN_ACCESS_TOKEN` on Render** via API `PUT /v1/services/srv-d8514l3rjlhs73dj5ul0/env-vars/DHAN_ACCESS_TOKEN` (avoids manual dashboard entry)
3. **Fixed AMO order placement**: Added `amoTime` field to `dhanhq_service.place_order()` — Dhan API requires `"PRE_OPEN"` for AMO orders (rejects `null` or `""`)
4. **Successfully placed AMO order**: YESBANK x1 (`orderId: 2252605229284`, status: `TRANSIT`)
5. **Added cancel-order debug endpoint**: `POST /debug/cancel-order/{order_id}`
6. **Successfully cancelled the order**: `orderId: 2252605229284` → `orderStatus: CANCELLED`
7. **Verified full order pipeline**: Place (TRANSIT) → Cancel (CANCELLED) — both work end-to-end**

### May 21, 2026 (Night) — Speedy+ALMA 1m Backtest + Threshold Comparison + Individual Telegram Alerts

**Done**:
1. **7-day 1-minute Speedy+ALMA backtest** (133 stocks, 57s): 121K signals, 41.4% WR 1m — **no predictive edge on 1-minute noise**
2. **Threshold comparison (3/4/5/6)**: Raising threshold doesn't improve WR:
   - Thresh 3: 121K sigs, 41.9% WR, -0.005% avg
   - Thresh 4: 29K sigs, 41.4% WR, -0.006% avg  
   - Thresh 5: 1.7K sigs, 40.6% WR, -0.012% avg
   - Thresh 6: 0 sigs — impossible to get all 6/6
3. **Individual Telegram alerts for Speedy+ALMA**: `strategy_builder_loop()` now sends per-signal alerts via `send_signal_alert()` (deduplicated by symbol+timestamp) in addition to batch summary
4. **Deployed**: Commit `73b8a04` → Render deploy `dep-d87jduhkh4rs73an3g00` (health: 200, 100% cache)

**Key insight**: Speedy+ALMA works on daily data (75% WR, 30d batch) but is random on 1-minute. Composite needs longer timeframe to have edge. Individual alerts active during market hours.

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
| `app/routes/debug.py` | Debug endpoints: Dhan status, IP, place-test, test-amo, cancel-order |
| `app/main.py` | FastAPI entry, lifespan tasks, 6 new routers |
| `render.yaml` | Render service config (Docker + persistent disk) |
| `backtest_batch.py` | Batch backtest: all 36 indicators on 149 stocks with win rate tracking |
| `send_backtest_report.py` | Send backtest CSV + summary to Telegram | |

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
