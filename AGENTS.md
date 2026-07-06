# Options Trading Backtest & Live Signals

## Goal
Build a profitable NSE F&O options trading strategy with rigorous backtesting, live signals via DhanHQ, decay-aware exits, PnL visualization, and risk controls — now with live BUY-only Telegram alerts every 5 min.

## Constraints
- **Market**: NSE F&O (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY + stocks)
- **Historical data**: `nselib` (Mar–Jun 2026, single-expiry snapshot ~100 rows/day via `build_daily_snapshot`)
- **Live data**: DhanHQ API (falls back to nselib prior-day close when Dhan unavailable)
- **Pricing**: Daily close only — no intraday tick data; signals fire at ~3:30 PM EOD
- **Trading style**: Option buying only (no shorting/selling) — BUY_CE and BUY_PE signals only
- **Symbols**: 4 indices (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY) + 50 F&O stocks (RELIANCE, TCS, INFY, HDFCBANK, ICICIBANK, SBIN, BAJFINANCE, BHARTIARTL, KOTAKBANK, LT, etc.)

## Strategy Hierarchy

### MEGA (default, 42 trades)
- Combines short_premium + ultra_selective + fii + atr into one score
- Only BUY signals pass through (SELL filtered out in signal service)
- Top 2 signals per day, max 1 per (strike, option_type, expiry) — duplicate prevention
- 42 trades, 69% win, ₹+100,980 PnL, PF 1.92, DD 32% (qty=50, hold=3, decay=70, loss=20K)
- FII bias fallback: when FII data unavailable, uses PCR as sentiment proxy (PCR>1.0 → bullish, PCR<1.0 → bearish)

### Others available
- short_premium, ultra_selective, fii_filtered, atr_breakout, combined

## Key Architecture

### Backtest Engine (`backtest_engine.py`)
```
BacktestConfig:
  symbol, instrument, from_date, to_date, period_days
  strategy_name, max_positions_per_day (2)
  trailing_stop_pct (20), fixed_target_pct (40), stop_loss_pct (0)
  max_hold_days (3), decay_stop_pct (70)
  entry_type: "close" | "next_open"
  lot_size (50), trade_qty (0 = sig.qty * lot_size)
  max_loss_per_trade (20000)
```

### Exit Logic (in order checked)
1. Trailing stop (20% from trough for shorts, 20% from peak for longs)
2. Stop loss (disabled by default)
3. Fixed target (40%)
4. Max hold (3 days)
5. Decay stop (70% — long-only, exits when option loses >70% value)
6. Max loss per trade (₹20,000 — checks unrealized PnL)
7. Expiry

### Duplicate Prevention
- Before opening, checks `(strike, option_type, expiry)` against open trades
- Same trade can't be entered twice while first is still open
- Live signal service also dedup against position_tracker open positions

### Entry Types
- **close** (default): signal → entered same day at today's close
- **next_open**: signal → queued in `pending_signals` → entered next trading day (slippage ~7.8%)

### Live Signal Service (`options_signal_service.py`)
- Loads nselib history, fetches Dhan option chain, runs MEGA strategy
- Filters to only BUY signals (`if not sig.action.startswith("BUY"): continue`)
- `dhan_source.renew_token()` calls Dhan /RenewToken API, stores new token in env
- Graceful degradation: returns `dhan_available: false` when Dhan down
- Response includes: data_source, dhan_available, underlying, new_signals, open_positions

### Background Scanner (`options_signal_scanner.py`)
- Scans every **5 min** during market hours (Mon-Fri 9:15-15:30 IST) across **54 symbols**:
  - 4 indices: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY
  - 50 F&O stocks: RELIANCE, TCS, INFY, HDFCBANK, ICICIBANK, SBIN, BAJFINANCE, etc.
- Checks every **60s** outside hours to catch market open
- Uses MEGA strategy with BUY-only filter per symbol
- **Deduplication**: MD5 hash of (symbol, action, strike, option_type, expiry, price) — never re-sends same signal
- Sends formatted Telegram message with all new signals across symbols
- Each symbol has its own history cache (`_snapshots_map`, `_fiidii_map`)
- Stock options use `OPTSTK` instrument (nselib) + `NSE_FNO` segment (Dhan)
- Index options use `OPTIDX` instrument + `IDX_I` segment

### Position Tracker (`position_tracker.py`)
- JSON-persistent (`/tmp/position_tracker.json`)
- Fields: entry_date, strike, option_type, expiry, entry_price, qty, highest/lowest
- open() rejects duplicate (symbol, strike, option_type)
- Update logic: trailing stop, target, stop loss, max hold, expiry exit

### Routes (`options_trading.py`)
- `GET /api/options/signals` — generate signals (dry run, no auto-open)
- `GET /api/options/signals/refresh` — force refresh historical data
- `POST /api/options/positions/open` — open a position from signal params
- `POST /api/options/positions/close` — close by id
- `GET /api/options/positions` — list open/closed
- `GET /api/options/backtest` — full backtest
- Signals endpoint never auto-opens — user clicks "Open" per signal on dashboard

### Dashboard (`options_trading.html`)
- Signals tab: Run Signals, signal list with underlying/source status, Open button per trade
- PnL equity curve (Chart.js line chart)
- Controls: strategy dropdown, entry_type select, max_loss input, trade_qty input

## Historical Context & Decisions
- **FII persistently bearish** since Sep 2025 — all testing is in a bear market
- **PCR reversed** — during downtrends PCR drops (retail buys calls at dip), low PCR is bearish for puts
- **PCR as FII proxy** — when FII data unavailable, PCR>1.0 → bullish, PCR<1.0 → bearish (strategies.py)
- **Single expiry filter before CE/PE merge** avoids cartesian product (6000+ rows → 100-136)
- **Duplicate dedup** removed phantom trades that inflated PnL by ~28%
- **Dhan token** expired June 4, replaced manually; `renew_token()` calls Dhan API
- **BUY-only filter** in signal service — SELL signals filtered out for user who only buys options
- **Telegram scanner** runs every 5 min during market hours, dedup by signal hash

## Relevant Files
- `app/services/options_backtest/strategies.py` — Signal, TradeResult, 4 strategies with PCR fallback for FII
- `app/services/options_backtest/data_loader.py` — build_daily_snapshot(), NaN guard, ATM fields; supports OPTIDX + OPTSTK
- `app/services/options_backtest/backtest_engine.py` — run(), exit logic, BacktestConfig
- `app/services/position_tracker.py` — JSON position manager
- `app/services/options_signal_service.py` — live signals + Dhan interaction; BUY-only filter; per-symbol snapshots cache
- `app/services/options_trading/dhan_source.py` — Dhan client with renew_token API call; FNO_INDICES + FNO_STOCKS; resolve_fno_symbol()
- `app/services/options_signal_scanner.py` — background Telegram scanner (5 min, 54 symbols)
- `app/services/telegram_notifier.py` — send Telegram messages
- `app/routes/options_trading.py` — all API endpoints (signals, positions, backtest, open)
- `app/templates/options_trading.html` — dashboard with Signals tab + Open buttons
- `app/main.py` — lifespan wires all background tasks including scanner

---

## Liquidity Sweep Strategy (Equity, not Options)

Separate from options trading. Tests stop-hunt sweeps on Nifty 149 stocks.

### Concept
- **Sell-side liquidity**: Price breaks below swing low/session low, triggers stop losses, closes back above → BUY
- **Buy-side liquidity**: Price breaks above swing high/session high, closes back below → SELL

### Key Backtest Results (June 24, 2026)

**Daily swing-level sweeps are noise** (8-12% WR) — sweep & rejection happen inside 1 candle.

**Prior-day session H/L sweeps work better**:

| Approach | WR | PF | RR |
|----------|----|----|----|
| Session sweep, next-open entry | 22.5% | **2.85** | **9.83** |
| Session + EMA50 + Vol + BOS | 21.9% | **1.59** | 5.67 |

### Best Live Preset
`"Liq Sweep+Composite"` in `strategy_builder.py`: leading=Liq Sweep, confirmations=[Volume, Price Action, Market Structure, BOS/CHoCH], threshold=2

### Scripts
- `backtest_liquidity_optimize.py` — 18 confirmation combo sweep test
- `backtest_liquidity_mtf.py` — Multi-timeframe: session H/L + next-open entry
- `liquidity_sweep_backtest.py` — 1-min sweep on 149 stocks
- `liquidity_sweep_daily.py` — Daily sweep on 149 stocks
- `app/services/market_structure.py` — JIT-compiled sweep/BOS/FVG/OB detection
- See `SESSION_CONTEXT.md` for full details

