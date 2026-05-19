# Session Context — Trading Dashboard

## Project
Full-stack trading dashboard (trading_news) with Nifty 100 technical signals + Groq AI explanations, deployed on Render, accessible via Telegram bot.

## Recent Work

### May 18, 2026

**Done**:
1. **Switched LLM**: Google Gemini → Groq (`llama-3.3-70b-versatile`) in `config.py`, `signal_explainer.py`, `requirements.txt`, `.env`
2. **Fixed Groq integration**: `_template_explain()` → `explain()` in `signal_monitor.py:47` and `market_realtime.py:141`
3. **Expanded stock list**: 24 → ~100 (Nifty 50 + Next 50)
4. **Yahoo rate limiting**: Added 2s delay between yfinance calls + custom User-Agent
5. **Fixed curl_cffi conflict**: Removed custom `requests.Session` from yfinance calls — let yfinance manage its own (requires `curl_cffi` on Render)
6. **Cached `/signals` endpoint**: Background loop writes results to `_signal_cache` dict; API reads from cache instead of live-fetching all 123 symbols (was timing out)
7. **Fixed event loop blocking**: Wrapped `yf.Ticker().info` and `yf.download()` in `asyncio.to_thread()` + `asyncio.wait_for(15s)` — prevents yfinance from blocking the server (was causing health check failures)
8. **Cached `/realtime` endpoint**: Same pattern as /signals — `_realtime_cache` populated by bg loop
9. **Removed duplicates**: `itc` and `tcs` were in both Nifty 50 and Next 50 lists (123 unique symbols now)
10. **Health endpoint**: Now returns cache stats (symbols cached, ages, fill %, started_at) for easy debugging
11. **Fixed `_cache_start` scope bug**: Added `global` keyword — Python was shadowing module variable with local assignment
12. **Telegram bot**: `@Signal_alpha267_bot` working (chat_id: 5163568145, polling loop 3s)
13. **Fixed circular import**: `_INDIAN_STOCKS` hardcoded in `telegram_notifier.py`
14. **Pinned yfinance==0.2.65** in requirements.txt
15. **Fixed circular import**: `signal_confirmer.py` imported `_INDIAN_STOCKS` from `signal_monitor.py`, but `signal_monitor.py` already imported `confirm_signal` from `signal_confirmer.py`. Fixed with lazy import inside the function instead of top-level import.
16. **FII/DII card on main dashboard**: Added institutional flow card in `dashboard_live.html` between Market Edge and Signal Log sections. Shows FII net, DII net, combined signal (bullish/bearish/neutral), trend arrows, and "India Only" badge. Fetches from `/api/v1/edge/fiidii` + `/fiidii/trend`, refreshes every 2 min.
17. **Efficiency overhaul (7 fixes)**:
    - **Critical**: Pass pre-fetched 5m prices into `confirm_signal` + skip 1d MTF (saves 4 yfinance calls per confirmed signal)
    - **Critical**: Parallel signal processing — Indian stocks processed in batches of 5 (12min full cycle → ~3min)
    - **Critical**: Rate limiting (1.5s) + 5min TTL cache for `scan_all_stocks` in edge scanner
    - **Medium**: Market-hours awareness — signal loop skips weekends/nights outside 9am-4pm IST (66% fewer useless cycles)
    - **Medium**: News sentiment parallel processing for all 29 stocks; increased news cache TTL 45s→300s
    - **Medium**: `_fetch_dashboard_data` reads from `_signal_cache`/`_realtime_cache` before live-fetching (saves ~15s on Telegram summary)
    - **Low**: Lazy import `docker` in telegram_bot.py (no crash if Docker unavailable)
    - Removed stale `GEMINI_API_KEY` from `.env` (Pydantic v2 validation error)

### May 19, 2026 — Persistence & Reliability

**Done**:
1. **Redeployed service**: Was returning 503 (down). Redeployed via Render hook, restored to healthy.
2. **Persistent signal cache**: Added 3 new SQLite tables (`signal_cache`, `realtime_cache`, `sent_signals`) in `accuracy_tracker.py`. Signal data now survives restarts.
3. **Render persistent disk**: Added 1GB disk mount at `/data` in `render.yaml` + `PERSISTENT_DIR=/data` env var so `signals.db` persists across deploys.
4. **On-startup cache reload**: `signal_monitor.py` now loads `_signal_cache`, `_realtime_cache`, `_CONFIRMED_SENT` from DB on import — API serves cached data immediately, Telegram bot avoids duplicate alerts.
5. **Config**: Added `persistent_dir` to `Settings` with fallback to project root.

**Known Issues** (unchanged):
1. **Groq quota**: 100K tokens/day free tier — exhausted. Only calls LLM for BUY/SELL ≥ 50% confidence. Resets ~24h cycle.
2. **Yahoo rate limit**: 2s per-call delay still applies but parallelism (batch of 5) reduces effective wall-clock time.

### Current State
- **Deployed at**: https://trading-dashboard-e0us.onrender.com/
- **GitHub**: git@github.com:pdewanganadv1-dot/Trading_news.git (main branch)
- **Deploy hook**: POST https://api.render.com/deploy/srv-d8514l3rjlhs73dj5ul0?key=dKh3Te8CRXI
- **Git commit HEAD**: e245d5f

### Key Files
| File | Purpose |
|------|---------|
| `app/config.py` | Settings (groq_api_key, signal_check_interval=600s, persistent_dir) |
| `app/services/signal_monitor.py` | Bg loop, caches, 123 Indian stocks, DB persistence |
| `app/services/market_data_service.py` | yfinance calls in threads with 2s delay + 15s timeout |
| `app/services/signal_explainer.py` | Groq LLM client + template fallback |
| `app/services/telegram_bot.py` | Polling loop, command handlers |
| `app/services/telegram_notifier.py` | Send messages, `_INDIAN_STOCKS` hardcoded list |
| `app/services/accuracy_tracker.py` | SQLite DB — signal history + cache/sent persistence |
| `app/routes/market_realtime.py` | API endpoints (signals + realtime) |
| `app/main.py` | FastAPI entry, lifespan tasks |
| `render.yaml` | Render service config (Docker + persistent disk) |
| `requirements.txt` | Pinned: yfinance==0.2.65, groq>=0.5.0 |

### Environment Variables (set in Render dashboard)
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID=5163568145`
- `GROQ_API_KEY`
- `SYNC=false` (secrets managed manually)

### Tech Stack
- FastAPI + uvicorn
- yfinance (0.2.65) for Indian stock prices
- Groq (llama-3.3-70b-versatile) for signal explanations
- python-telegram-bot (polling)
- async background loops for monitoring
- SQLite + Render persistent disk for data persistence
