"""Generate dashboard data with OHLC bars + trade markers for per-stock charts."""
import json, sys, math
sys.path.insert(0, '.')
from app.services.strategy_builder import StrategyBuilder, leading_liqhunter
import yfinance as yf
import pandas as pd

WHITELIST = ['MOTHERSON', 'HDFCBANK', 'ASIANPAINT', 'HCLTECH', 'KOTAKBANK',
             'LT', 'SUNPHARMA', 'TCS', 'RELIANCE', 'INFY', 'ICICIBANK',
             'BHARTIARTL', 'NTPC', 'TITAN', 'WIPRO']

builder = StrategyBuilder()
builder.select_preset("LiqHunter")
builder.buy_only = False
builder.signal_on_change = False
builder.min_gap_bars = 0
builder.atr_sl_mult = 2.0
builder.trailing_sl = True
builder.sl_pct = 0

stocks = []
for sym in WHITELIST:
    try:
        df = yf.download(f"{sym}.NS", period="180d", interval="1d", progress=False, auto_adjust=True)
        if df.empty or len(df) < 30:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]

        # Ensure numeric
        for col in ['Open','High','Low','Close','Volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        o = [float(x) for x in df['Open']]
        h = [float(x) for x in df['High']]
        l = [float(x) for x in df['Low']]
        c = [float(x) for x in df['Close']]
        v = [int(x) if not (math.isnan(float(x)) or math.isinf(float(x))) else 0 for x in df['Volume']]

        # Live signal
        ld = leading_liqhunter(o, h, l, c, v)
        is_signal = ld["direction"] in ("LONG", "SHORT")

        # Backtest
        bt = builder.backtest(sym, days=180, interval="1d")
        if "error" in bt:
            continue
        trades = bt.get("trades", [])
        signals = bt.get("signals", [])

        last_sig = None
        for sig in reversed(signals):
            if sig['signal'] in ('BUY', 'SELL'):
                last_sig = sig
                break

        # ── Chart data: last 100 OHLC bars (forward-fill NaN) ──
        ts = [int(t.timestamp()) for t in df.index]
        chart_bars = []
        prev = None
        for i in range(max(0, len(ts) - 100), len(ts)):
            bar = {
                "time": ts[i], "open": round(o[i], 2), "high": round(h[i], 2),
                "low": round(l[i], 2), "close": round(c[i], 2), "volume": v[i],
            }
            # Forward-fill any NaN
            for k in ("open","high","low","close","volume"):
                val = bar[k]
                if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                    bar[k] = prev[k] if prev else 0
                elif val is None:
                    bar[k] = prev[k] if prev else 0
            chart_bars.append(bar)
            prev = bar

        # ── Trade markers ──
        trade_markers = []
        for t in trades:
            # Find timestamp for entry and exit bars
            try:
                et = str(t["entry_time"])[:10]
                xt = str(t["exit_time"])[:10]
                entry_ts = int(pd.Timestamp(et).timestamp())
                exit_ts = int(pd.Timestamp(xt).timestamp())
            except Exception:
                continue
            trade_markers.append({
                "entry_time": entry_ts,
                "exit_time": exit_ts,
                "entry_price": round(t["entry_price"], 2),
                "exit_price": round(t["exit_price"], 2),
                "direction": t["direction"],
                "pnl_pct": round(t["pnl_pct"], 2),
                "exit_reason": t["exit_reason"],
                "bars_held": int(t["bars_held"]),
            })

        trade_list = []
        for t in trades[-10:]:
            trade_list.append({
                "entry_time": str(t["entry_time"])[:10],
                "exit_time": str(t["exit_time"])[:10],
                "direction": t["direction"],
                "entry_price": round(t["entry_price"], 2),
                "exit_price": round(t["exit_price"], 2),
                "pnl_pct": round(t["pnl_pct"], 2),
                "exit_reason": t["exit_reason"],
                "bars_held": int(t["bars_held"]),
            })

        stocks.append({
            "symbol": sym,
            "direction": ld["direction"],
            "level": ld.get("level"),
            "last_close": round(c[-1], 2),
            "last_open": round(o[-1], 2),
            "last_high": round(h[-1], 2),
            "last_low": round(l[-1], 2),
            "last_volume": v[-1],
            "last_bar_date": str(df.index[-1])[:10],
            "has_signal": is_signal,
            "bt_total_return": round(bt["total_return"], 2),
            "bt_win_rate": bt["win_rate"],
            "bt_profit_factor": str(bt.get("profit_factor", 0)),
            "bt_total_trades": bt["total_trades"],
            "bt_avg_return": round(bt["avg_return"], 2),
            "bt_expectancy": round(bt["expectancy"], 2),
            "bt_period": "180d",
            "last_signal": {
                "signal": last_sig["signal"] if last_sig else None,
                "price": last_sig["price"] if last_sig else None,
                "date": str(last_sig["timestamp"])[:10] if last_sig else None,
            } if last_sig else None,
            "chart_bars": chart_bars,
            "trade_markers": trade_markers,
            "recent_trades": trade_list,
        })
        print(f"  {sym}: {ld['direction']} | {bt['total_trades']}t {bt['total_return']:+.1f}% | {len(chart_bars)} bars")
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"  {sym}: ERROR - {e}")

def clean(obj):
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else round(float(obj), 2)
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (int,)):
        return obj
    return obj

output = clean({"stocks": stocks, "generated_at": "2026-06-22"})
with open("/Users/piyush/Desktop/trading_news/dashboard_data.json", "w") as f:
    json.dump(output, f, indent=2, allow_nan=False)
print(f"\nSaved {len(stocks)} stocks")
