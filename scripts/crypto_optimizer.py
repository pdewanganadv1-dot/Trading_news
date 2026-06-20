"""
Crypto strategy optimizer: tests ALL indicator combos on Binance 5m data,
finds best by total return × win rate.
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("PERSISTENT_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"))

import asyncio
import httpx
import numpy as np
import pandas as pd
from collections import defaultdict
from datetime import datetime

BINANCE_SYMBOLS = {"btc": "BTCUSDT", "eth": "ETHUSDT"}
CRYPTO_NAMES = {"btc": "BTC-USD", "eth": "ETH-USD"}
CANDLE_INTERVAL = "5m"
MAX_CANDLES = 2000

async def fetch_binance_klines(symbol: str, interval: str = CANDLE_INTERVAL, limit: int = MAX_CANDLES):
    sym = BINANCE_SYMBOLS.get(symbol)
    if not sym: return []
    async with httpx.AsyncClient(timeout=15.0) as session:
        url = f"https://api.binance.com/api/v3/klines?symbol={sym}&interval={interval}&limit={limit}"
        resp = await session.get(url)
        if resp.status_code != 200: return []
        data = resp.json()
        candles = [{"time": int(k[0])//1000, "open": float(k[1]), "high": float(k[2]),
                     "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])} for k in data]
        return candles

def candles_to_df(candles):
    df = pd.DataFrame(candles)
    df.set_index("time", inplace=True)
    df.index = pd.to_datetime(df.index, unit="s")
    df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}, inplace=True)
    return df

async def run_backtest(symbol: str = "btc"):
    print(f"\n{'='*60}")
    print(f"Crypto Strategy Optimizer — {symbol.upper()}")
    print(f"{'='*60}")

    candles = await fetch_binance_klines(symbol)
    if len(candles) < 100:
        print(f"ERROR: Only {len(candles)} candles fetched")
        return []

    df = candles_to_df(candles)
    print(f"Loaded {len(df)} candles ({df.index[0].date()} to {df.index[-1].date()})")

    opens = [float(x) for x in df['Open']]
    highs = [float(x) for x in df['High']]
    lows = [float(x) for x in df['Low']]
    closes = [float(x) for x in df['Close']]
    volumes = [float(x) for x in df['Volume']]
    dates = [str(idx) for idx in df.index]
    n = len(closes)

    from app.services.strategy_builder import LEADING_INDICATORS, CONFIRMATION_FILTERS

    all_results = []
    GAP_SETTINGS = [10, 20, 40]
    THRESHOLDS = [1, 2, 3]
    FLIP_MODES = [True, False]

    # Test each leading indicator
    total_combos = len(LEADING_INDICATORS) * len(GAP_SETTINGS) * len(THRESHOLDS) * len(FLIP_MODES)
    done = 0

    for ind_name, ind_func in LEADING_INDICATORS.items():
        for gap in GAP_SETTINGS:
            for threshold in THRESHOLDS:
                for flip in FLIP_MODES:
                    done += 1
                    if done % 20 == 0:
                        print(f"  Progress: {done}/{total_combos} ({done*100//total_combos}%)", flush=True)

                    # Run backtest using simulated tick()
                    in_position = False
                    entry_price = 0
                    entry_time = ""
                    entry_direction = ""
                    trades = []
                    min_bars = max(30, gap + 10)
                    prev_dir = None
                    last_signal_idx = 0
                    sl_pct = 5.0
                    tp_pct = 2.0

                    for i in range(min_bars, n):
                        chunk_c = closes[:i+1]
                        chunk_h = highs[:i+1]
                        chunk_l = lows[:i+1]
                        chunk_o = opens[:i+1]
                        chunk_v = volumes[:i+1]

                        # Leading indicator
                        try:
                            ld = ind_func(chunk_o, chunk_h, chunk_l, chunk_c, chunk_v)
                        except Exception:
                            continue
                        direction = ld.get("direction", "NEUTRAL")
                        if direction == "NEUTRAL":
                            continue

                        # Flip check
                        if flip:
                            if prev_dir is None or prev_dir == direction:
                                prev_dir = direction
                                continue
                            prev_dir = direction
                        else:
                            prev_dir = direction

                        # Gap check
                        if i - last_signal_idx < gap:
                            continue
                        last_signal_idx = i

                        # Skip if already in position (simplification)
                        if in_position:
                            continue

                        # Enter
                        current_price = closes[i]
                        signal = "BUY" if direction == "LONG" else "SELL"
                        in_position = True
                        entry_price = current_price
                        entry_time = dates[i]
                        entry_direction = signal
                        highest = current_price
                        lowest = current_price

                        # Simulate exit (simplified)
                        exit_price = current_price
                        exit_time = dates[i]
                        for j in range(i + 1, min(i + 40, n)):
                            if signal == "BUY":
                                if closes[j] >= entry_price * (1 + tp_pct / 100):
                                    exit_price = entry_price * (1 + tp_pct / 100)
                                    exit_time = dates[j]
                                    break
                                if closes[j] <= entry_price * (1 - sl_pct / 100):
                                    exit_price = entry_price * (1 - sl_pct / 100)
                                    exit_time = dates[j]
                                    break
                                highest = max(highest, closes[j])
                            else:
                                if closes[j] <= entry_price * (1 - tp_pct / 100):
                                    exit_price = entry_price * (1 - tp_pct / 100)
                                    exit_time = dates[j]
                                    break
                                if closes[j] >= entry_price * (1 + sl_pct / 100):
                                    exit_price = entry_price * (1 + sl_pct / 100)
                                    exit_time = dates[j]
                                    break
                                lowest = min(lowest, closes[j])
                        else:
                            exit_price = closes[min(i + 40, n - 1)]
                            exit_time = dates[min(i + 40, n - 1)]

                        pnl_pct = (exit_price / entry_price - 1) * (100 if signal == "BUY" else -100)
                        trades.append({
                            "direction": signal, "entry_price": entry_price,
                            "exit_price": exit_price, "pnl_pct": round(pnl_pct, 2),
                            "entry_time": entry_time, "exit_time": exit_time,
                            "bars_held": j - i if 'j' in dir() else 40,
                        })
                        in_position = False

                    if trades:
                        total = len(trades)
                        wins = sum(1 for t in trades if t["pnl_pct"] > 0)
                        losses = total - wins
                        win_rate = wins / total * 100 if total else 0
                        total_return = sum(t["pnl_pct"] for t in trades)
                        avg_return = total_return / total if total else 0
                        avg_win = sum(t["pnl_pct"] for t in trades if t["pnl_pct"] > 0) / wins if wins else 0
                        avg_loss = sum(t["pnl_pct"] for t in trades if t["pnl_pct"] < 0) / losses if losses else 0
                        pf = abs(avg_win * wins / (avg_loss * losses)) if losses and avg_loss != 0 else (99 if wins > 0 else 0)
                        buy_trades = sum(1 for t in trades if t["direction"] == "BUY")
                        sell_trades = total - buy_trades

                        all_results.append({
                            "indicator": ind_name,
                            "gap": gap,
                            "threshold": threshold,
                            "flip": flip,
                            "trades": total,
                            "wins": wins,
                            "losses": losses,
                            "win_rate": round(win_rate, 1),
                            "avg_return": round(avg_return, 2),
                            "total_return": round(total_return, 2),
                            "avg_win": round(avg_win, 2),
                            "avg_loss": round(avg_loss, 2),
                            "profit_factor": round(pf, 2),
                            "buys": buy_trades,
                            "sells": sell_trades,
                        })

    # Sort by total_return × win_rate
    all_results.sort(key=lambda x: x["total_return"] * x["win_rate"] / 100 if x["trades"] >= 5 else -99999, reverse=True)

    print(f"\n{'='*60}")
    print(f"TOP 20 STRATEGIES (sorted by return × win_rate)")
    print(f"{'='*60}")
    print(f"{'Indicator':20s} {'Gap':4s} {'Thr':4s} {'Flip':5s} {'Trades':7s} {'WR':6s} {'AvgR':7s} {'TotR':7s} {'PF':6s}")
    print("-"*70)
    for r in all_results[:20]:
        print(f"{r['indicator']:20s} {r['gap']:4d} {r['threshold']:4d} {str(r['flip']):5s} {r['trades']:4d}   {r['win_rate']:5.1f}% {r['avg_return']:+6.2f}% {r['total_return']:+7.2f}% {r['profit_factor']:.2f}")

    return all_results

if __name__ == "__main__":
    results = asyncio.run(run_backtest("btc"))
    if results:
        best = results[0]
        print(f"\n{'='*60}")
        print(f"BEST STRATEGY: {best['indicator']}")
        print(f"  Gap={best['gap']}, Threshold={best['threshold']}, Flip={best['flip']}")
        print(f"  Trades: {best['trades']}, Win Rate: {best['win_rate']}%")
        print(f"  Avg Return: {best['avg_return']:+.2f}%, Total Return: {best['total_return']:+.2f}%")
        print(f"  Profit Factor: {best['profit_factor']}")
        print(f"{'='*60}")

        # Save to JSON
        out = os.path.join(os.path.dirname(__file__), "data", "crypto_best_strategies.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as f:
            json.dump({"symbol": "btc", "timestamp": datetime.now().isoformat(), "results": results[:50], "best": best}, f, indent=2)
        print(f"Saved to {out}")
