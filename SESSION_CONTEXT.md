# Session Context — Trading Dashboard

## Project
Full-stack trading dashboard (trading_news) with Nifty 100 technical signals + Groq AI explanations, deployed on Render, accessible via Telegram bot.

## Recent Work (May 18, 2026)

### Done
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
11. **Telegram bot**: `@Signal_alpha267_bot` working (chat_id: 5163568145, polling loop 3s)
12. **Fixed circular import**: `_INDIAN_STOCKS` hardcoded in `telegram_notifier.py`
13. **Pinned yfinance==0.2.65** in requirements.txt

### Current State
- **Deployed at**: https://trading-dashboard-e0us.onrender.com/
- **GitHub**: git@github.com:pdewanganadv1-dot/Trading_news.git (main branch)
- **Deploy hook**: POST https://api.render.com/deploy/srv-d8514l3rjlhs73dj5ul0?key=dKh3Te8CRXI
- **Git commit HEAD**: 488ea1b

### Latest Session (May 18, 2026)

**Done**:
1. **Fixed circular import**: `signal_confirmer.py` imported `_INDIAN_STOCKS` from `signal_monitor.py`, but `signal_monitor.py` already imported `confirm_signal` from `signal_confirmer.py`. Fixed with lazy import inside the function instead of top-level import.
2. **FII/DII card on main dashboard**: Added institutional flow card in `dashboard_live.html` between Market Edge and Signal Log sections. Shows FII net, DII net, combined signal (bullish/bearish/neutral), trend arrows, and "India Only" badge. Fetches from `/api/v1/edge/fiidii` + `/fiidii/trend`, refreshes every 2 min.

### Known Issues
1. **Groq quota**: 100K tokens/day free tier — exhausted. Only calls LLM for BUY/SELL ≥ 50% confidence. Resets ~24h cycle.
2. **Yahoo rate limit**: 2s sequential delay makes cache fill slow (~8 min for 123 stocks). Acceptable since bg loop runs every 600s.

### Key Files
| File | Purpose |
|------|---------|
| `app/config.py` | Settings (groq_api_key, signal_check_interval=600s) |
| `app/services/signal_monitor.py` | Bg loop, _signal_cache, _realtime_cache, _INDIAN_STOCKS (123 symbols) |
| `app/services/market_data_service.py` | yfinance calls in threads with 2s delay + 15s timeout |
| `app/services/signal_explainer.py` | Groq LLM client + template fallback |
| `app/services/telegram_bot.py` | Polling loop, command handlers |
| `app/services/telegram_notifier.py` | Send messages |
| `app/routes/market_realtime.py` | API endpoints (signals + realtime) |
| `app/main.py` | FastAPI entry, lifespan tasks |
| `render.yaml` | Render service config (Docker) |
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
