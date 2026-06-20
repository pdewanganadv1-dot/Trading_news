"""
Liquidity Sweep Strategy on Crypto & Gold (1-minute)
Tests BTC, ETH, Gold, Silver using same logic as liquidity_sweep_backtest.py
"""
import sys, os, pickle, time, json
sys.path.insert(0, os.path.dirname(__file__))
os.environ["PERSISTENT_DIR"] = os.path.join(os.path.dirname(__file__), "data")

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import yfinance as yf
import pandas as pd

CACHE = os.path.join(os.path.dirname(__file__), "data", "ohlc_1m_cache")
RPT_DIR = os.path.join(os.path.dirname(__file__), "data", "backtest_reports")
os.makedirs(CACHE, exist_ok=True)
os.makedirs(RPT_DIR, exist_ok=True)

SYMBOLS = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "GOLD": "GC=F",
    "SILVER": "SI=F",
}

MIN_BARS = 60
MAX_STOP_PCT = 2.0
MIN_RR = 3.0


def swing_highs(h, left=5, right=5):
    n = len(h); r = [0.0]*n
    for i in range(left, n-right):
        if all(h[j]<h[i] for j in range(i-left,i) if j>=0) and all(h[j]<h[i] for j in range(i+1,i+right+1) if j<n): r[i]=1.0
    return r

def swing_lows(l, left=5, right=5):
    n = len(l); r = [0.0]*n
    for i in range(left, n-right):
        if all(l[j]>l[i] for j in range(i-left,i) if j>=0) and all(l[j]>l[i] for j in range(i+1,i+right+1) if j<n): r[i]=1.0
    return r

def prev_swing(i, arr):
    for j in range(i-1,-1,-1):
        if j<len(arr) and arr[j]==1.0: return j
    return None

def sim(entry_idx, direction, entry, stop, target, closes, highs, lows):
    be=False; cs=stop
    for j in range(entry_idx+1, len(closes)):
        ch,cl=highs[j],lows[j]
        if not be:
            if direction=="BUY" and ch>=entry+(entry-stop): be=True; cs=entry
            elif direction=="SELL" and cl<=entry-(stop-entry): be=True; cs=entry
        if direction=="BUY" and cl<=cs: return {"exit_idx":j,"exit_price":round(cs,2),"pnl_pct":round((cs-entry)/entry*100,2),"exit_reason":"stop","be_triggered":be,"bars_held":j-entry_idx}
        if direction=="SELL" and ch>=cs: return {"exit_idx":j,"exit_price":round(cs,2),"pnl_pct":round((entry-cs)/entry*100,2),"exit_reason":"stop","be_triggered":be,"bars_held":j-entry_idx}
        if direction=="BUY" and ch>=target: return {"exit_idx":j,"exit_price":round(target,2),"pnl_pct":round((target-entry)/entry*100,2),"exit_reason":"target","be_triggered":be,"bars_held":j-entry_idx}
        if direction=="SELL" and cl<=target: return {"exit_idx":j,"exit_price":round(target,2),"pnl_pct":round((entry-target)/entry*100,2),"exit_reason":"target","be_triggered":be,"bars_held":j-entry_idx}
    f=closes[-1]; pnl=((f-entry)/entry*100) if direction=="BUY" else ((entry-f)/entry*100)
    return {"exit_idx":len(closes)-1,"exit_price":round(f,2),"pnl_pct":round(pnl,2),"exit_reason":"expired","be_triggered":be,"bars_held":len(closes)-1-entry_idx}

def make_trade(i, direction, entry, stop, target, closes, highs, lows, dates, extra):
    r=sim(i,direction,entry,stop,target,closes,highs,lows)
    if r is None: return None
    rr=(target-entry)/(entry-stop) if direction=="BUY" and entry!=stop else ((entry-target)/(stop-entry) if direction=="SELL" and stop!=entry else 0)
    sp=abs(entry-stop)/entry*100
    return dict(extra,**{"direction":direction,"entry_time":dates[i].strftime("%Y-%m-%d %H:%M") if hasattr(dates[i],"strftime") else str(dates[i])[:16],"exit_time":dates[r["exit_idx"]].strftime("%Y-%m-%d %H:%M") if hasattr(dates[r["exit_idx"]],"strftime") else str(dates[r["exit_idx"]])[:16],"entry_price":round(entry,2),"stop_loss":round(stop,2),"target":round(target,2),"rr_setup":round(rr,1),"stop_pct":round(sp,2),**r})

def backtest(name, yf_sym):
    try:
        path = os.path.join(CACHE, f"{name}.pkl")
        if not os.path.exists(path):
            df = yf.download(yf_sym, period="7d", interval="1m", progress=False, auto_adjust=True)
            if df.empty or len(df) < MIN_BARS: return None
            if isinstance(df.columns, pd.MultiIndex): df.columns = [c[0] for c in df.columns]
            df = df.dropna(subset=["Open","High","Low","Close"])
            if len(df) >= MIN_BARS:
                with open(path, "wb") as f: pickle.dump(df, f)
        else:
            with open(path, "rb") as f: df = pickle.load(f)

        if isinstance(df.columns, pd.MultiIndex): df.columns = [c[0] for c in df.columns]
        df = df.dropna(subset=["Open","High","Low","Close"])
        if len(df) < MIN_BARS: return None

        opens=[float(x) for x in df["Open"]]; highs=[float(x) for x in df["High"]]
        lows=[float(x) for x in df["Low"]]; closes=[float(x) for x in df["Close"]]
        dates=list(df.index)

        sh=swing_highs(highs,5,5); sl=swing_lows(lows,5,5)
        trades=[]; entries=set()

        # Session levels
        sess_h={}; sess_l={}
        for idx,dt in enumerate(dates):
            dk=dt.strftime("%Y-%m-%d") if hasattr(dt,"strftime") else str(dt)[:10]
            if dk not in sess_h: sess_h[dk]=highs[idx]; sess_l[dk]=lows[idx]
            else: sess_h[dk]=max(sess_h[dk],highs[idx]); sess_l[dk]=min(sess_l[dk],lows[idx])

        for i in range(MIN_BARS, len(closes)):
            if i in entries: continue
            dt=dates[i]; dk=dt.strftime("%Y-%m-%d") if hasattr(dt,"strftime") else str(dt)[:10]
            sh_idx=prev_swing(i,sh); sl_idx=prev_swing(i,sl)
            ci=closes[i]; hi=highs[i]; li=lows[i]

            # Session sweeps
            yesterday=(dt-timedelta(days=1)).strftime("%Y-%m-%d") if hasattr(dt,"strftime") else None
            if yesterday and yesterday in sess_h:
                ph=sess_h[yesterday]; pl=sess_l[yesterday]
                if li<pl and ci>pl:
                    e=ci; s=min(li,pl-(hi-li)*0.05)
                    if abs(e-s)/e*100<=MAX_STOP_PCT:
                        tgt=ph if ph>e else e+(e-s)*10; rr=(tgt-e)/(e-s) if e!=s else 0
                        if rr>=MIN_RR:
                            t=make_trade(i,"BUY",e,s,tgt,closes,highs,lows,dates,{"symbol":name,"type":"session_sellside"})
                            if t: trades.append(t); entries.add(i)
                if i not in entries and hi>ph and ci<ph:
                    e=ci; s=max(hi,ph+(hi-li)*0.05)
                    if abs(e-s)/e*100<=MAX_STOP_PCT:
                        tgt=pl if pl<e else e-(s-e)*10; rr=(e-tgt)/(s-e) if s!=e else 0
                        if rr>=MIN_RR:
                            t=make_trade(i,"SELL",e,s,tgt,closes,highs,lows,dates,{"symbol":name,"type":"session_buyside"})
                            if t: trades.append(t); entries.add(i)

            # Swing sweeps
            if sl_idx is not None and i not in entries and i-sl_idx>=20:
                sw=lows[sl_idx]
                if li<sw and ci>sw:
                    e=ci; s=min(li,sw-(hi-li)*0.05)
                    if abs(e-s)/e*100<=MAX_STOP_PCT:
                        tgt=None
                        for j in range(i,max(i-80,-1),-1):
                            if j<len(sh) and sh[j]==1.0 and highs[j]>e: tgt=highs[j]; break
                        if tgt is None: tgt=e+(e-s)*10
                        rr=(tgt-e)/(e-s) if e!=s else 0
                        if rr>=MIN_RR:
                            t=make_trade(i,"BUY",e,s,tgt,closes,highs,lows,dates,{"symbol":name,"type":"swing_sellside","candles_since":i-sl_idx,"sweep_level":round(sw,2)})
                            if t: trades.append(t); entries.add(i)

            if sh_idx is not None and i not in entries and i-sh_idx>=20:
                sw=highs[sh_idx]
                if hi>sw and ci<sw:
                    e=ci; s=max(hi,sw+(hi-li)*0.05)
                    if abs(e-s)/e*100<=MAX_STOP_PCT:
                        tgt=None
                        for j in range(i,max(i-80,-1),-1):
                            if j<len(sl) and sl[j]==1.0 and lows[j]<e: tgt=lows[j]; break
                        if tgt is None: tgt=e-(s-e)*10
                        rr=(e-tgt)/(s-e) if s!=e else 0
                        if rr>=MIN_RR:
                            t=make_trade(i,"SELL",e,s,tgt,closes,highs,lows,dates,{"symbol":name,"type":"swing_buyside","candles_since":i-sh_idx,"sweep_level":round(sw,2)})
                            if t: trades.append(t); entries.add(i)

        return trades if trades else None
    except Exception as e:
        return None

def run():
    t0=time.time()
    print("="*60)
    print("LIQUIDITY SWEEP — Crypto & Gold 1-Minute Backtest")
    print("="*60)
    print(f"Symbols: {', '.join(SYMBOLS.keys())}")
    print(f"Max stop: {MAX_STOP_PCT}% | Min RR: {MIN_RR}:1")
    print()

    all_trades=[]
    for name, yf_sym in SYMBOLS.items():
        print(f"Testing {name} ({yf_sym})...")
        trades=backtest(name, yf_sym)
        if trades:
            all_trades.extend(trades)
            print(f"  → {len(trades)} trades")
        else:
            print(f"  → No trades or insufficient data")
        print()

    print(f"\nDone in {time.time()-t0:.0f}s\n")
    report(all_trades, time.time()-t0)

def report(all_trades, elapsed):
    if not all_trades:
        print("No trades!")
        return

    total=len(all_trades)
    types={}
    for t in all_trades: types.setdefault(t["type"],[]).append(t)

    buys=[t for t in all_trades if t["direction"]=="BUY"]
    sells=[t for t in all_trades if t["direction"]=="SELL"]
    winners=[t for t in all_trades if t["pnl_pct"]>0]
    losers=[t for t in all_trades if t["pnl_pct"]<=0]
    targets=[t for t in all_trades if t["exit_reason"]=="target"]
    stops=[t for t in all_trades if t["exit_reason"]=="stop"]
    be=[t for t in all_trades if t["be_triggered"]]

    wr=len(winners)/total*100
    avg_pnl=sum(t["pnl_pct"] for t in all_trades)/total
    avg_win=sum(t["pnl_pct"] for t in winners)/len(winners) if winners else 0
    avg_loss=sum(abs(t["pnl_pct"]) for t in losers)/len(losers) if losers else 0
    rr_r=avg_win/avg_loss if avg_loss>0 else 0
    tot_r=sum(t["pnl_pct"] for t in all_trades)
    gw=sum(t["pnl_pct"] for t in winners)
    gl=abs(sum(t["pnl_pct"] for t in losers))
    pf=gw/gl if gl>0 else 999

    print("="*60)
    print("RESULTS")
    print("="*60)
    print(f"Total trades:   {total}")
    print(f"BUY: {len(buys)}  SELL: {len(sells)}")
    print(f"Types: {', '.join(f'{k}={len(v)}' for k,v in sorted(types.items()))}")
    print(f"Winners: {len(winners)}  Losers: {len(losers)}")
    print(f"Targets: {len(targets)}  Stops: {len(stops)}  BE: {len(be)}/{total} ({len(be)/total*100:.1f}%)")
    print()
    print(f"Win Rate:        {wr:.1f}%")
    print(f"Avg PnL:         {avg_pnl:+.4f}%")
    print(f"Avg Win:         {avg_win:+.4f}%")
    print(f"Avg Loss:        {avg_loss:.4f}%")
    print(f"Realized RR:     {rr_r:.2f}:1")
    print(f"Profit Factor:   {pf:.2f}")
    print(f"Total Return:    {tot_r:+.2f}%")
    print()

    per_sym={}
    for t in all_trades: per_sym.setdefault(t["symbol"],[]).append(t)
    for sym in sorted(per_sym):
        s=per_sym[sym]; w=[t for t in s if t["pnl_pct"]>0]
        print(f"  {sym:8s}: {len(s):4d} trades, WR {len(w)/len(s)*100:.1f}%, Avg {sum(t['pnl_pct'] for t in s)/len(s):+.4f}%")

    for tname, tlist in sorted(types.items()):
        tw=[t for t in tlist if t["pnl_pct"]>0]
        print(f"  {tname:25s}: {len(tlist):4d} trades, WR {len(tw)/len(tlist)*100:.1f}%, Avg {sum(t['pnl_pct'] for t in tlist)/len(tlist):+.4f}%")

    # Print best 5 and worst 5 trades
    st=sorted(all_trades, key=lambda x: x["pnl_pct"], reverse=True)
    print(f"\n  Best trades:")
    for t in st[:5]:
        print(f"    {t['symbol']} {t['type']} {t['direction']} @{t['entry_price']} → {t['exit_price']} ({t['pnl_pct']:+.2f}%) RR={t['rr_setup']}:1")
    print(f"\n  Worst trades:")
    for t in st[-5:]:
        print(f"    {t['symbol']} {t['type']} {t['direction']} @{t['entry_price']} → {t['exit_price']} ({t['pnl_pct']:+.2f}%) RR={t['rr_setup']}:1")

    ts=datetime.now().strftime("%Y%m%d_%H%M%S")
    rpt=os.path.join(RPT_DIR, f"liq_sweep_crypto_{ts}.md")
    with open(rpt,"w") as f:
        f.write(f"# Liquidity Sweep — Crypto & Gold 1m\n\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"| Metric | Value |\n|---|---|\n| Total | {total} |\n| WR | {wr:.1f}% |\n| Avg PnL | {avg_pnl:+.4f}% |\n| Total Return | {tot_r:+.2f}% |\n| RR | {rr_r:.2f}:1 |\n| PF | {pf:.2f} |\n")
    print(f"\nReport: {rpt}")

if __name__=="__main__":
    run()
