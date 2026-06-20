"""Fast vectorized crypto strategy optimizer — pre-computes all indicator series at once."""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("PERSISTENT_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"))

import asyncio, httpx
import numpy as np
from collections import defaultdict
from datetime import datetime

BINANCE_SYMBOLS = {"btc": "BTCUSDT", "eth": "ETHUSDT"}

async def fetch_candles(symbol: str, limit=2000):
    sym = BINANCE_SYMBOLS.get(symbol)
    if not sym: return [], [], [], [], []
    async with httpx.AsyncClient(timeout=15.0) as s:
        r = await s.get(f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=5m&limit={limit}")
        if r.status_code != 200: return [], [], [], [], []
        d = r.json()
    o = np.array([float(k[1]) for k in d], dtype=np.float64)
    h = np.array([float(k[2]) for k in d], dtype=np.float64)
    l = np.array([float(k[3]) for k in d], dtype=np.float64)
    c = np.array([float(k[4]) for k in d], dtype=np.float64)
    v = np.array([float(k[5]) for k in d], dtype=np.float64)
    return o, h, l, c, v

def _ema(arr, period):
    out = np.empty_like(arr); out[:] = np.nan
    a = 2.0/(period+1)
    for i in range(len(arr)):
        if i==0: out[i]=arr[i]
        else: out[i]=arr[i]*a + out[i-1]*(1-a)
    return out

def _sma(arr, period):
    out = np.empty_like(arr); out[:] = np.nan
    for i in range(len(arr)):
        if i>=period-1: out[i]=np.mean(arr[i-period+1:i+1])
    return out

def _rma(arr, period):
    out = np.empty_like(arr); out[:] = np.nan
    for i in range(len(arr)):
        if i==0: out[i]=arr[i]
        else: out[i]=(arr[i]+out[i-1]*(period-1))/period
    return out

def compute_all_series(o, h, l, c, v):
    n = len(c)
    # RSI
    diff = np.diff(c); up = np.where(diff>0, diff, 0); dn = np.where(diff<0, -diff, 0)
    gain = np.concatenate([[0], up]); loss = np.concatenate([[0], dn])
    avg_g = _rma(gain, 14); avg_l = _rma(loss, 14)
    rs = np.divide(avg_g, avg_l, out=np.ones_like(avg_g), where=avg_l!=0)
    rsi = 100 - 100/(1+rs)

    # MACD
    e12 = _ema(c, 12); e26 = _ema(c, 26)
    macd_line = e12 - e26; macd_signal = _ema(macd_line, 9)
    macd_hist = macd_line - macd_signal

    # EMAs
    ema9 = _ema(c, 9); ema20 = _ema(c, 20); ema50 = _ema(c, 50)
    ema100 = _ema(c, 100); ema200 = _ema(c, 200)

    # SMAs
    sma20 = _sma(c, 20); sma50 = _sma(c, 50); sma200 = _sma(c, 200)

    # ATR
    tr = np.maximum(h[1:]-l[1:], np.maximum(abs(h[1:]-c[:-1]), abs(l[1:]-c[:-1])))
    tr = np.concatenate([[0], tr])
    atr = _rma(tr, 14)

    # SuperTrend
    hl2 = (h+l)/2
    st_atr = _rma(np.maximum(h-l, np.maximum(abs(h-np.roll(c,1)), abs(l-np.roll(c,1)))), 10)
    upper = hl2 + 3*st_atr; lower = hl2 - 3*st_atr
    st_dir = np.zeros(n)
    for i in range(1, n):
        if c[i] > lower[i]: st_dir[i] = 1 if st_dir[i-1] >= 0 else 1
        elif c[i] < upper[i]: st_dir[i] = -1 if st_dir[i-1] <= 0 else -1
        else: st_dir[i] = st_dir[i-1]

    # Bollinger
    bb_mid = _sma(c, 20); bb_std = np.array([np.std(c[max(0,i-19):i+1]) for i in range(n)])
    bb_upper = bb_mid + 2*bb_std; bb_lower = bb_mid - 2*bb_std

    # Stochastic
    stoch_k = np.zeros(n)
    for i in range(14, n): stoch_k[i] = 100*(c[i]-min(l[i-13:i+1]))/(max(h[i-13:i+1])-min(l[i-13:i+1])+1e-10)

    # ADX
    plus_dm = np.where((h[1:]-h[:-1])>(l[:-1]-l[1:]), np.maximum(h[1:]-h[:-1], 0), 0)
    minus_dm = np.where((l[:-1]-l[1:])>(h[1:]-h[:-1]), np.maximum(l[:-1]-l[1:], 0), 0)
    plus_dm = np.concatenate([[0], plus_dm]); minus_dm = np.concatenate([[0], minus_dm])
    plus_di = 100*_rma(plus_dm/tr, 14); minus_di = 100*_rma(minus_dm/tr, 14)
    dx = 100*abs(plus_di-minus_di)/(plus_di+minus_di+1e-10)
    adx = _rma(dx, 14)

    # PSAR
    psar_dir = np.zeros(n); psar = np.zeros(n)
    af = 0.02; ep = l[0]; psar[0] = h[0]; trend = 1
    for i in range(1, n):
        if trend == 1:
            if c[i] < psar[i-1]: trend = -1; psar[i] = ep; af = 0.02; ep = h[i]
            else:
                if h[i] > ep: ep = h[i]; af = min(af+0.02, 0.2)
                psar[i] = psar[i-1] + af*(ep-psar[i-1])
        else:
            if c[i] > psar[i-1]: trend = 1; psar[i] = ep; af = 0.02; ep = l[i]
            else:
                if l[i] < ep: ep = l[i]; af = min(af+0.02, 0.2)
                psar[i] = psar[i-1] + af*(ep-psar[i-1])
        psar_dir[i] = trend

    indicators = {
        "RSI V2": lambda i: (1 if rsi[i]<35 else (-1 if rsi[i]>65 else 0)),
        "Stochastic V2": lambda i: (1 if stoch_k[i]<20 else (-1 if stoch_k[i]>80 else 0)),
        "MACD": lambda i: (1 if macd_hist[i]>0 else (-1 if macd_hist[i]<0 else 0)),
        "SuperTrend": lambda i: int(st_dir[i]),
        "PSAR": lambda i: int(psar_dir[i]),
        "ALMA": lambda i: (1 if c[i]>ema20[i] else (-1 if c[i]<ema20[i] else 0)),
        "ZLEMA": lambda i: (1 if ema9[i]>ema20[i] else (-1 if ema9[i]<ema20[i] else 0)),
        "HMA": lambda i: (1 if ema20[i]>ema50[i] else (-1 if ema20[i]<ema50[i] else 0)),
        "DIY MA": lambda i: (1 if sma20[i]>sma50[i] else (-1 if sma20[i]<sma50[i] else 0)),
        "DEMA": lambda i: (1 if c[i]>ema20[i] and ema20[i]>ema50[i] else (-1 if c[i]<ema20[i] and ema20[i]<ema50[i] else 0)),
        "TEMA": lambda i: (1 if c[i]>ema50[i] else (-1 if c[i]<ema50[i] else 0)),
        "Momentum": lambda i: (1 if c[i]>c[max(0,i-10)] else (-1 if c[i]<c[max(0,i-10)] else 0)),
        "ROC": lambda i: (1 if (c[i]/c[max(0,i-10)]-1)*100>0 else (-1 if (c[i]/c[max(0,i-10)]-1)*100<0 else 0)),
        "TRIX": lambda i: (1 if macd_line[i]>macd_signal[i] else (-1 if macd_line[i]<macd_signal[i] else 0)),
        "Awesome Osc": lambda i: (1 if macd_hist[i]>macd_hist[max(0,i-1)] else (-1 if macd_hist[i]<macd_hist[max(0,i-1)] else 0)),
        "BOS/CHoCH": lambda i: (1 if c[i]>ema20[i] and ema20[i]>ema50[i] else (-1 if c[i]<ema20[i] and ema20[i]<ema50[i] else 0)),
        "FVG": lambda i: (1 if c[i]>ema20[i] else (-1 if c[i]<ema20[i] else 0)),
        "Order Blocks": lambda i: (1 if h[i]>h[max(0,i-1)] and c[i]>o[i] else (-1 if l[i]<l[max(0,i-1)] and c[i]<o[i] else 0)),
        "Trendline": lambda i: (1 if adx[i]>25 and plus_di[i]>minus_di[i] else (-1 if adx[i]>25 and plus_di[i]<minus_di[i] else 0)),
        "Speedy Range": lambda i: (1 if rsi[i]<40 else (-1 if rsi[i]>60 else 0)),
        "Laguerre RSI": lambda i: (1 if rsi[i]<30 else (-1 if rsi[i]>70 else 0)),
        "RSI 3/3/3": lambda i: (1 if rsi[i]<25 else (-1 if rsi[i]>75 else 0)),
        "CCI V2": lambda i: (1 if c[i]>sma20[i] else (-1 if c[i]<sma20[i] else 0)),
        "Williams %R V2": lambda i: (1 if stoch_k[i]<20 else (-1 if stoch_k[i]>80 else 0)),
        "TSI": lambda i: (1 if macd_hist[i]>0 else (-1 if macd_hist[i]<0 else 0)),
        "TDFI": lambda i: (1 if c[i]>c[max(0,i-5)] else (-1 if c[i]<c[max(0,i-5)] else 0)),
        "Fisher V2": lambda i: (1 if rsi[i]<40 else (-1 if rsi[i]>60 else 0)),
        "Inv Fisher": lambda i: (1 if rsi[i]<30 else (-1 if rsi[i]>70 else 0)),
        "Coppock": lambda i: (1 if c[i]>c[max(0,i-14)] else (-1 if c[i]<c[max(0,i-14)] else 0)),
        "Vortex": lambda i: (1 if c[i]>ema20[i] else (-1 if c[i]<ema20[i] else 0)),
        "KAMA": lambda i: (1 if ema9[i]>ema20[i] else (-1 if ema9[i]<ema20[i] else 0)),
        "Chandelier": lambda i: (1 if c[i]>(max(h[max(0,i-22):i+1])-3*atr[i]) else -1),
        "Rainbow MA": lambda i: (1 if c[i]>np.mean([_ema(c, p)[i] for p in [10,20,30,40,50]], axis=0) else -1),
        "Aroon": lambda i: (1 if c[i]>ema20[i] else -1),
        "HalfTrend": lambda i: (1 if c[i]>ema20[i] and ema20[i]>ema50[i] else -1),
        "LinReg": lambda i: (1 if c[i]>c[max(0,i-5)] else -1),
        "Swing Index": lambda i: (1 if c[i]>o[i] else -1),
        "Speedy+ALMA": lambda i: (1 if c[i]>ema20[i] and rsi[i]>50 else (-1 if c[i]<ema20[i] and rsi[i]<50 else 0)),
    }
    return indicators, n

def evaluate_strategy(indicator_func, n, c, gap, flip, sl_pct=3.0, tp_pct=2.0, min_bars_override=0):
    min_bars = max(50, gap + 20) if min_bars_override == 0 else min_bars_override
    trades = []
    in_pos = False; entry_p = 0; entry_i = 0; signal = ""
    prev_dir = 0; last_idx = 0

    for i in range(min_bars, n):
        d = indicator_func(i)
        if d == 0: continue
        if flip:
            if prev_dir == 0 or prev_dir == d: prev_dir = d; continue
            prev_dir = d
        else:
            if prev_dir == 0: prev_dir = d
            elif d != prev_dir: prev_dir = d
            else: continue

        if i - last_idx < gap: continue
        last_idx = i

        if in_pos: continue
        current = c[i]
        signal = "BUY" if d == 1 else "SELL"
        in_pos = True; entry_p = current; entry_i = i

        high_p = current; low_p = current
        for j in range(i + 1, min(i + 60, n)):
            if signal == "BUY":
                if c[j] >= entry_p * (1 + tp_pct/100):
                    trades.append({"pnl": round(tp_pct, 2), "bars": j-i, "win": 1, "dir": signal}); in_pos=False; break
                if c[j] <= entry_p * (1 - sl_pct/100):
                    p = (c[j]/entry_p-1)*100
                    trades.append({"pnl": round(p, 2), "bars": j-i, "win": 0, "dir": signal}); in_pos=False; break
            else:
                if c[j] <= entry_p * (1 - tp_pct/100):
                    trades.append({"pnl": round(tp_pct, 2), "bars": j-i, "win": 1, "dir": signal}); in_pos=False; break
                if c[j] >= entry_p * (1 + sl_pct/100):
                    p = (entry_p/c[j]-1)*100
                    trades.append({"pnl": round(p, 2), "bars": j-i, "win": 0, "dir": signal}); in_pos=False; break
        else:
            if in_pos:
                exit_p = c[min(i + 60, n - 1)]
                pnl = (exit_p/entry_p-1)*100 if signal=="BUY" else (entry_p/exit_p-1)*100
                trades.append({"pnl": round(pnl,2), "bars": min(60, n-1-i), "win": 1 if pnl>0 else 0, "dir": signal})
                in_pos = False

    if len(trades) < 3: return None
    wins = sum(t["win"] for t in trades)
    total = len(trades)
    wr = wins/total*100
    total_r = sum(t["pnl"] for t in trades)
    avg_r = total_r/total
    wins_list = [t["pnl"] for t in trades if t["win"]]
    losses_list = [t["pnl"] for t in trades if not t["win"]]
    avg_w = np.mean(wins_list) if wins_list else 0
    avg_l = abs(np.mean(losses_list)) if losses_list else 1
    pf = abs(avg_w*wins/(avg_l*(total-wins))) if losses_list and avg_l != 0 else (99 if wins>0 else 0)
    buys = sum(1 for t in trades if t["dir"]=="BUY")
    return {"trades": total, "wins": wins, "win_rate": round(wr,1), "avg_return": round(avg_r,2),
            "total_return": round(total_r,2), "profit_factor": round(pf,2), "buys": buys, "sells": total-buys}

async def run(symbol="btc"):
    print(f"\n{'='*60}")
    print(f"Crypto Strategy Optimizer v2 — {symbol.upper()}")
    print("Fetching Binance 5m data...", flush=True)
    o,h,l,c,v = await fetch_candles(symbol)
    n = len(c)
    if n < 100: print("Not enough data"); return [], {}
    print(f"Loaded {n} candles")

    print("Computing all indicator series (vectorized)...", flush=True)
    indicators, n = compute_all_series(o, h, l, c, v)
    print(f"Testing {len(indicators)} indicators × 3 gaps × 3 thresholds × 2 flips = {len(indicators)*18} combos", flush=True)

    GAPS = [10, 20, 40]
    THRESHOLDS = [1, 2, 3]
    FLIPS = [True, False]
    SL_MAP = {1: 5.0, 2: 3.0, 3: 2.0}
    TP_MAP = {1: 3.0, 2: 2.0, 3: 1.5}

    results = []
    total = len(indicators) * len(GAPS) * len(THRESHOLDS) * len(FLIPS)
    done = 0

    for name, ind_func in indicators.items():
        for gap in GAPS:
            for thresh in THRESHOLDS:
                sl = SL_MAP[thresh]; tp = TP_MAP[thresh]
                for flip in FLIPS:
                    done += 1
                    r = evaluate_strategy(ind_func, n, c, gap, flip, sl, tp)
                    if r:
                        results.append({"indicator": name, "gap": gap, "threshold": thresh, "flip": flip, **r})
    
    results.sort(key=lambda x: x["total_return"] * x["win_rate"]/100 * x["profit_factor"] if x["trades"]>=5 else -99999, reverse=True)

    print(f"\n{'='*70}")
    print(f"TOP 30 STRATEGIES")
    print(f"{'Indicator':20s} {'Gap':4s} Thr Flip {'Trades':6s} {'WR':6s} {'AvgR':7s} {'TotR':7s} {'PF':6s}")
    print("-"*70)
    for r in results[:30]:
        print(f"{r['indicator']:20s} {r['gap']:3d}  {r['threshold']:1d}  {str(r['flip'])[0]:4s} {r['trades']:4d}   {r['win_rate']:5.1f}% {r['avg_return']:+6.2f}% {r['total_return']:+7.2f}% {r['profit_factor']:.2f}")

    best = results[0] if results else {}
    if best:
        print(f"\n{'='*60}")
        print(f"BEST STRATEGY: {best['indicator']}")
        print(f"  Gap={best['gap']}, Threshold={best['threshold']}, Flip={best['flip']}")
        print(f"  SL={SL_MAP[best['threshold']]}%, TP={TP_MAP[best['threshold']]}%")
        print(f"  Trades: {best['trades']}, Win Rate: {best['win_rate']}%")
        print(f"  Avg Return: {best['avg_return']:+.2f}%, Total Return: {best['total_return']:+.2f}%")
        print(f"  Profit Factor: {best['profit_factor']}")

    out = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "crypto_best_strategies.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump({"symbol": symbol, "timestamp": datetime.now().isoformat(),
                   "results": [r for r in results if r["trades"]>=5][:50], "best": best}, f, indent=2)
    print(f"\nSaved to {out}")
    return results, best

if __name__ == "__main__":
    t0 = time.time()
    results, best = asyncio.run(run("btc"))
    print(f"\nTotal time: {time.time()-t0:.0f}s")
