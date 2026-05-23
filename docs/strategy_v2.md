# Strategy V2 — Direction-Flip + Cooldown + Pattern Gating

## TODO

- [ ] Improve win rate beyond 50.8% (try tighter SL 0.5-1.0%, different cooldown 30/45/90 bars, Speedy+ALMA composite)
- [ ] Run 1-min backtest with ZLEMA+PSAR-Conf + different cooldowns (30, 45, 90, 120 bars) to find optimal trade frequency
- [ ] Run 1-min backtest with fixed SL 0.5-1.0% on ZLEMA+PSAR-Conf (PF was 0.91 at 0.5% SL, might give better risk-adjusted returns)
- [ ] Run 1-min backtest with Speedy+ALMA composite leading indicator (was original best before direction-flip)
- [ ] Deploy best config to Render (currently ZLEMA+PSAR-Conf, 1.5% fixed SL, 60-bar cooldown)
- [ ] Monitor live Telegram signals for quality
- [ ] Consider: reduce confirmation threshold from 2→1 for ZLEMA+PSAR-Conf? (fewer gates = more trades but potentially better entries)
- [ ] Consider: add trailing stop on top of fixed SL (trailing once in profit)
- [ ] Consider: dynamic SL based on ATR instead of fixed %

---

## Current State (May 23 2026)

### Problem
Original strategy generated **6,952,687** signals for 19 stocks over 9 trading days. Leading indicators were never NEUTRAL, confirmations almost always passed, and there was no cooldown — signal fired every single bar.

### Solution Applied
1. **Direction-Flip Mode** (`signal_on_change = True`): Only emit BUY when leading indicator flips SHORT→LONG, SELL when LONG→SHORT. Eliminates ~99% of noise.
2. **Cooldown** (`min_gap_bars = 60`): Minimum 60 1-min bars (~1 hour) between signals per symbol. Targets ~3-4 trades/stock/day.
3. **Candle Pattern Gate**: Reject signal if no engulfing/pin bar/inside bar AND no volume surge. Keeps only high-probability setups.
4. **4 Pattern Detectors**: `detect_engulfing()`, `detect_pin_bar()`, `detect_inside_bar()`, `detect_volume_confirmation()`.

### Results After Fix
- **109 total trades** across 10 stocks over 7 days (1-min data, May 13-21) — ~2/stock/day
- **264 BUY/SELL signals** across all stocks — ~3-5/stock/day

### Sweep Results (5 Presets × 8 SL Settings = 40 combinations)

| Rank | Preset | SL | Trades | WR | Avg P&L | PF |
|------|--------|----|--------|----|---------|----|
| 1 | ZLEMA+PSAR-Conf | fixed 1.5% | 132 | **50.8%** | -0.04% | 0.88 |
| 2 | ZLEMA+PSAR-Conf | trail 1.5% | 132 | **50.8%** | -0.04% | 0.88 |
| 3 | ZLEMA+PSAR-Conf | trail 1.0% | 138 | **50.7%** | -0.05% | 0.85 |
| 4 | ZLEMA+PSAR-Conf | fixed 1.0% | 137 | **50.4%** | -0.05% | 0.85 |
| 5 | ZLEMA+PSAR-Conf | fixed 2.0% | 131 | **50.4%** | -0.04% | 0.87 |
| 6 | ZLEMA-Optimized | fixed 1.5% | 133 | **48.9%** | -0.04% | 0.87 |
| 7 | ZLEMA-Optimized | trail 1.0% | 139 | **48.9%** | -0.05% | 0.84 |
| 8 | ZLEMA+PSAR-Conf | fixed 0.5% | 150 | **48.7%** | -0.03% | **0.91** |
| — | PSAR-Default | fixed 1.5% | 98 | 41.8% | -0.25% | 0.50 |
| — | PSAR-Light | trail 1.0% | 109 | 33.9% | -0.27% | 0.46 |

### Key Findings
- **ZLEMA leads outperform PSAR** on 1-min data by ~10 percentage points WR
- **Fixed SL slightly better than trailing** (or equal) at same percentage
- **0.5% SL gives best PF (0.91)** but lower WR (48.7%)
- **1.5% SL gives best WR (50.8%)** with acceptable PF (0.88)
- PSAR-based presets max out at 41.8% WR — significantly worse

### Current Live Config (Render)
- **Preset**: `ZLEMA+PSAR-Conf` (ZLEMA leading, 9 confirmations, threshold 2)
- **SL**: 1.5% fixed (not trailing)
- **Direction-Flip**: ON
- **Cooldown**: 60 bars
- **Candle Pattern + Volume Gate**: ON
- **Buy+SELL**: Both enabled

### Telegram Commands
| Command | Purpose |
|---------|---------|
| `/strategy_gap <N>` | Set cooldown in minutes (default 60) |
| `/strategy_flip` | Toggle direction-change-only mode |
| `/strategy_preset <name>` | Switch preset |
| `/strategy_sl <pct>` | Set stop-loss % |
| `/strategy_trailing` | Toggle trailing SL |
| `/strategy_config` | Show current config |
| `/strategy_bt <SYM> <days> <interval>` | Backtest any stock |

### Relevant Files
- `app/services/strategy_builder.py`: Core engine with direction-flip, cooldown, patterns
- `app/services/telegram_bot.py`: `/strategy_gap`, `/strategy_flip` command handlers
- `backtest_1m_full.py`: Vectorized 1-min backtest (also updated with flip+cooldown)
- `data/backtest_reports/`: All sweep reports and per-stock signal files
- `data/strategy_signals.db`: Live signal DB (178 rows)
- `docs/strategy_v2.md`: This file
