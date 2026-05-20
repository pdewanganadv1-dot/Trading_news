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

### Current State
- **Deployed at**: https://trading-dashboard-e0us.onrender.com/
- **Live pages**: `/options-chain`, `/insider-trading`, `/sector-rotation`, `/ai-agent`, `/strategy-marketplace`, `/politician-trades`
- **GitHub**: git@github.com:pdewanganadv1-dot/Trading_news.git (main branch)
- **Deploy hook**: POST https://api.render.com/deploy/srv-d8514l3rjlhs73dj5ul0?key=dKh3Te8CRXI
- **Git commit HEAD**: (latest — see git log)
- **Repo**: Make sure it's **private** on GitHub (Settings → General → Danger Zone → Change visibility)

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
| `app/routes/market_realtime.py` | API endpoints (signals + realtime) |
| `app/main.py` | FastAPI entry, lifespan tasks, 6 new routers |
| `render.yaml` | Render service config (Docker + persistent disk) |

### Telegram Commands
| `/dhan` | DhanHQ dashboard (funds, account, data plan) |
| `/dhanon` / `/dhanoff` | Toggle DhanHQ auto-trading |
| `/buy <sym> <qty>` | Place BUY order via Dhan |
| `/sell <sym> <qty>` | Place SELL order via Dhan |
| Command | Description |
|---------|-------------|
| `summary` | Dashboard overview |
| `edges` | Top 10 stocks by edge score |
| `breadth` | Market breadth with top/bottom stocks list |
| `breadth all` | Full list of stocks above SMA20 |
| `sentiment` | Market sentiment with top bullish/bearish stocks |
| `stocks` | All 119 monitored stocks |
| `/scalp` | SCALP signals — EMA 200 bounces on 1min chart |
| `/scalpbt` | Backtest SCALP strategy on 6mo daily data |
| `/scalpon` / `/scalpoff` | Toggle SCALP signals on/off (default: off) |
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
| `fiidii` | FII/DII institutional flow |
| `social <sym>` | StockTwits + Reddit sentiment |

### Tech Stack
- FastAPI + uvicorn (Render Docker, free tier)
- yfinance (0.2.65) for Indian stock prices
- nselib (2.5.1) for NSE data (FII/DII, bulk/block deals, sectoral indices, F&O bhavcopy)
- Groq (llama-3.3-70b-versatile) for AI analysis
- python-telegram-bot (polling via httpx)
- SQLite + Render persistent disk (1GB at /data)
- Chart.js for all standalone dashboards
