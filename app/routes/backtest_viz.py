import os, pickle, json, math
from datetime import datetime, timedelta
from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
import pandas as pd
from app.data.stocks import INDIAN_STOCKS

router = APIRouter(tags=["backtest-viz"])

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "ohlc_180_cache")


# ─── Helpers ──────────────────────────────────────────────────────────

def ema(arr, period):
    if len(arr) < period: return [arr[-1]]*len(arr)
    k = 2/(period+1); r = [arr[0]]
    for v in arr[1:]: r.append(v*k + r[-1]*(1-k))
    return r

def sma(arr, period):
    return [sum(arr[max(0,i-period+1):i+1])/min(period,i+1) for i in range(len(arr))]

def swing_highs(h, left=3, right=3):
    n=len(h); r=[0.0]*n
    for i in range(left, n-right):
        if all(h[j]<h[i] for j in range(i-left,i) if j>=0) and all(h[j]<h[i] for j in range(i+1,i+right+1) if j<n): r[i]=1.0
    return r

def swing_lows(l, left=3, right=3):
    n=len(l); r=[0.0]*n
    for i in range(left, n-right):
        if all(l[j]>l[i] for j in range(i-left,i) if j>=0) and all(l[j]>l[i] for j in range(i+1,i+right+1) if j<n): r[i]=1.0
    return r

def prev_swing(i, arr):
    for j in range(i-1,-1,-1):
        if j<len(arr) and arr[j]==1.0: return j
    return None

def fair_value_gap(h, l):
    n=len(l); r=[0.0]*n
    for i in range(2,n):
        if l[i]>h[i-2]: r[i]=1
        elif h[i]<l[i-2]: r[i]=-1
    return r

def detect_bos(i, h, l, sh, sl):
    sh_idx=[j for j in range(i) if sh[j]==1]
    sl_idx=[j for j in range(i) if sl[j]==1]
    if len(sh_idx)<2 or len(sl_idx)<2: return 0
    h1=h[sh_idx[-2]]; h2=h[sh_idx[-1]]; l1=l[sl_idx[-2]]; l2=l[sl_idx[-1]]
    if h2>h1 and l2>l1 and h[i]>h2: return 1
    if h2<h1 and l2<l1 and l[i]<l2: return -1
    return 0


# ─── Strategy Variants ───────────────────────────────────────────────

STRATEGIES = {
    "v1_session":       {"session": True, "swing": False, "ema": False, "vol": False, "bos": False, "next": False},
    "v3_session_ema":   {"session": True, "swing": False, "ema": True,  "vol": False, "bos": False, "next": False},
    "v5_session_ema_vol": {"session": True, "swing": False, "ema": True, "vol": True,  "bos": False, "next": False},
    "v7_full":          {"session": True, "swing": False, "ema": True,  "vol": True,  "bos": True,  "next": False},
    "v9_session_next":  {"session": True, "swing": False, "ema": False, "vol": False, "bos": False, "next": True},
}


def run_backtest(symbol, params):
    path = os.path.join(CACHE_DIR, f"{symbol}.pkl")
    if not os.path.exists(path): return None
    with open(path, "rb") as f: df = pickle.load(f)
    if isinstance(df.columns, pd.MultiIndex): df.columns = [c[0] for c in df.columns]
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    if len(df) < 50: return None

    opens = [float(x) for x in df["Open"]]
    highs = [float(x) for x in df["High"]]
    lows = [float(x) for x in df["Low"]]
    closes = [float(x) for x in df["Close"]]
    volumes = [float(x) for x in df["Volume"]]
    dates = list(df.index)

    use_session = params.get("session", True)
    use_swing = params.get("swing", False)
    need_ema = params.get("ema", False)
    need_vol = params.get("vol", False)
    need_bos = params.get("bos", False)
    entry_next = params.get("next", False)

    sh = swing_highs(highs, 3, 3)
    sl = swing_lows(lows, 3, 3)
    e50 = ema(closes, 50)
    vol_avg = sma(volumes, 20)
    fvg = fair_value_gap(highs, lows)

    trades = []; entries = set()

    for i in range(60, len(closes)):
        if i in entries: continue
        ci = closes[i]; hi = highs[i]; li = lows[i]; oi = opens[i]

        # Confirmations
        if need_ema and (i >= len(e50) or ci <= e50[i]): continue
        if need_vol and (i < 20 or volumes[i] < 1.5 * vol_avg[i]): continue
        if need_bos and detect_bos(i, highs, lows, sh, sl) == 0: continue

        levels = []
        if use_session and i >= 1:
            levels.append(("session_high", highs[i-1], "SELL"))
            levels.append(("session_low", lows[i-1], "BUY"))
        if use_swing:
            sh_i = prev_swing(i, sh)
            sl_i = prev_swing(i, sl)
            if sh_i is not None and i - sh_i >= 20:
                levels.append(("swing_high", highs[sh_i], "SELL"))
            if sl_i is not None and i - sl_i >= 20:
                levels.append(("swing_low", lows[sl_i], "BUY"))

        for lname, level, exp_dir in levels:
            if i in entries: break

            if exp_dir == "BUY" and li < level and ci > level:
                ep = oi if entry_next else ci
                stop = min(li, level - (hi-li)*0.1)
                if abs(ep-stop)/ep*100 > 8.0: continue
                tgt = None
                for j in range(i, max(i-120, -1), -1):
                    if j < len(sh) and sh[j]==1.0 and highs[j] > ep: tgt=highs[j]; break
                if tgt is None: tgt = ep + (ep-stop)*10
                rr = (tgt-ep)/(ep-stop) if ep != stop else 0
                if rr < 1.5: continue
                t = _sim_trade(i, "BUY", ep, stop, tgt, closes, highs, lows, dates,
                    {"symbol": symbol.upper(), "approach": f"{lname}_buy"})
                if t: trades.append(t); entries.add(i)

            elif exp_dir == "SELL" and hi > level and ci < level:
                ep = oi if entry_next else ci
                stop = max(hi, level + (hi-li)*0.1)
                if abs(ep-stop)/ep*100 > 8.0: continue
                tgt = None
                for j in range(i, max(i-120, -1), -1):
                    if j < len(sl) and sl[j]==1.0 and lows[j] < ep: tgt=lows[j]; break
                if tgt is None: tgt = ep - (stop-ep)*10
                rr = (ep-tgt)/(stop-ep) if stop != ep else 0
                if rr < 1.5: continue
                t = _sim_trade(i, "SELL", ep, stop, tgt, closes, highs, lows, dates,
                    {"symbol": symbol.upper(), "approach": f"{lname}_sell"})
                if t: trades.append(t); entries.add(i)

    # Build OHLC response
    ohlc = []
    for i, dt in enumerate(dates):
        ohlc.append({
            "time": int(dt.timestamp()),
            "open": round(opens[i], 2),
            "high": round(highs[i], 2),
            "low": round(lows[i], 2),
            "close": round(closes[i], 2),
            "volume": int(volumes[i])
        })

    # Build equity curve
    equity = []
    running = 0.0
    for t in sorted(trades, key=lambda x: x["entry_ts"]):
        running += t["pnl_pct"]
        equity.append({"time": t["entry_ts"], "value": round(running, 2)})

    return {"ohlc": ohlc, "trades": trades, "equity": equity}


def _sim_trade(entry_idx, direction, entry, stop, target, closes, highs, lows, dates, extra):
    be = False; cs = stop
    for j in range(entry_idx+1, len(closes)):
        ch, cl = highs[j], lows[j]
        if not be:
            if direction=="BUY" and ch >= entry+(entry-stop): be=True; cs=entry
            elif direction=="SELL" and cl <= entry-(stop-entry): be=True; cs=entry
        if direction=="BUY" and cl <= cs:
            return _trade_result(j, cs, "stop", be, entry_idx, entry, dates, extra, closes, highs, lows)
        if direction=="SELL" and ch >= cs:
            return _trade_result(j, cs, "stop", be, entry_idx, entry, dates, extra, closes, highs, lows)
        if direction=="BUY" and ch >= target:
            return _trade_result(j, target, "target", be, entry_idx, entry, dates, extra, closes, highs, lows)
        if direction=="SELL" and cl <= target:
            return _trade_result(j, target, "target", be, entry_idx, entry, dates, extra, closes, highs, lows)
    final = closes[-1]
    pnl = ((final-entry)/entry*100) if direction=="BUY" else ((entry-final)/entry*100)
    return _trade_result(len(closes)-1, round(final,2), "expired", be, entry_idx, entry, dates, extra, closes, highs, lows)


def _trade_result(exit_idx, exit_price, reason, be, entry_idx, entry, dates, extra, closes, highs, lows):
    pnl = ((exit_price-entry)/entry*100) if extra.get("direction","BUY")=="BUY" else ((entry-exit_price)/entry*100)
    rr = (exit_price-entry)/(1) if extra.get("direction","BUY")=="BUY" else (entry-exit_price)/(1)  # placeholder
    entry_ts = int(dates[entry_idx].timestamp()) if hasattr(dates[entry_idx],"timestamp") else 0
    exit_ts = int(dates[exit_idx].timestamp()) if hasattr(dates[exit_idx],"timestamp") else 0
    return {
        "entry_time": dates[entry_idx].strftime("%Y-%m-%d"),
        "exit_time": dates[exit_idx].strftime("%Y-%m-%d"),
        "entry_ts": entry_ts, "exit_ts": exit_ts,
        "direction": extra.get("direction","BUY"),
        "entry_price": round(entry, 2),
        "exit_price": round(exit_price, 2),
        "pnl_pct": round(pnl, 2),
        "stop_loss": round(extra.get("stop",0),2),
        "target": round(extra.get("target",0),2),
        "bars_held": exit_idx - entry_idx,
        "exit_reason": reason,
        "be_triggered": be,
        "approach": extra.get("approach",""),
        "symbol": extra.get("symbol",""),
    }


# ─── Routes ──────────────────────────────────────────────────────────

@router.get("/backtest-viz")
async def get_backtest_viz_page():
    path = os.path.join(os.path.dirname(__file__), "../templates/backtest_viz.html")
    if os.path.exists(path):
        return FileResponse(path)
    return {"error": "Template not found"}


@router.get("/api/backtest-viz/stocks")
async def list_stocks():
    available = []
    for s in INDIAN_STOCKS:
        p = os.path.join(CACHE_DIR, f"{s}.pkl")
        if os.path.exists(p):
            available.append(s.upper())
    return {"stocks": sorted(available), "total": len(available)}


@router.get("/api/backtest-viz/backtest/{symbol}")
async def get_backtest(
    symbol: str,
    strategy: str = Query("v7_full"),
):
    s = symbol.lower().strip()
    path = os.path.join(CACHE_DIR, f"{s}.pkl")
    if not os.path.exists(path):
        return {"error": f"No cached data for {symbol}"}
    params = STRATEGIES.get(strategy)
    if not params:
        return {"error": f"Unknown strategy: {strategy}"}
    result = run_backtest(s, params)
    if result is None:
        return {"error": f"Backtest failed for {symbol}"}

    trades = result["trades"]
    total = len(trades)
    winners = [t for t in trades if t["pnl_pct"] > 0]
    summary = {
        "symbol": symbol.upper(),
        "strategy": strategy,
        "total_trades": total,
        "win_rate": round(len(winners)/total*100, 1) if total else 0,
        "total_return": round(sum(t["pnl_pct"] for t in trades), 2),
        "avg_pnl": round(sum(t["pnl_pct"] for t in trades)/total, 2) if total else 0,
        "avg_bars": round(sum(t["bars_held"] for t in trades)/total, 1) if total else 0,
    }
    return {"summary": summary, "ohlc": result["ohlc"], "trades": trades, "equity": result["equity"]}
