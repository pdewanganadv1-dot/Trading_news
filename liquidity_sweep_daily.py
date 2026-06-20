"""
Liquidity Sweep Strategy — Daily Data Backtest
Tests on 149 Indian stocks using cached 180-day daily OHLC data.

Rules adapted from the YouTube video for daily timeframe:
- S/R levels = multi-week swing highs/lows (meaningful)
- 20-30+ candles = 20-30+ trading days between level creation and retest
- Liquidity sweep on daily: price breaches level intraday, closes back
- Entry at close of rejection candle
- SL beyond sweep extreme, TP at next major swing level
"""
import sys, os, pickle, time, json
sys.path.insert(0, os.path.dirname(__file__))
os.environ["PERSISTENT_DIR"] = os.path.join(os.path.dirname(__file__), "data")

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from app.data.stocks import INDIAN_STOCKS
import pandas as pd

CACHE_DIR = os.path.join(os.path.dirname(__file__), "data", "ohlc_180_cache")
RPT_DIR = os.path.join(os.path.dirname(__file__), "data", "backtest_reports")
os.makedirs(RPT_DIR, exist_ok=True)

MIN_BARS = 40
MAX_STOP_PCT = 8.0
MIN_RR = 2.0
MIN_CANDLES = 20  # candles between level creation and retest


def swing_highs(h, left=3, right=3):
    n = len(h); r = [0.0]*n
    for i in range(left, n-right):
        if all(h[j]<h[i] for j in range(i-left,i) if j>=0) and all(h[j]<h[i] for j in range(i+1,i+right+1) if j<n): r[i]=1.0
    return r

def swing_lows(l, left=3, right=3):
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
    return dict(extra,**{"direction":direction,"entry_time":dates[i].strftime("%Y-%m-%d") if hasattr(dates[i],"strftime") else str(dates[i])[:10],"exit_time":dates[r["exit_idx"]].strftime("%Y-%m-%d") if hasattr(dates[r["exit_idx"]],"strftime") else str(dates[r["exit_idx"]])[:10],"entry_price":round(entry,2),"stop_loss":round(stop,2),"target":round(target,2),"rr_setup":round(rr,1),"stop_pct":round(sp,2),**r})


def backtest_stock(symbol):
    try:
        path = os.path.join(CACHE_DIR, f"{symbol}.pkl")
        if not os.path.exists(path): return None
        with open(path, "rb") as f: df = pickle.load(f)
        if isinstance(df.columns, pd.MultiIndex): df.columns = [c[0] for c in df.columns]
        df = df.dropna(subset=["Open","High","Low","Close"])
        if len(df) < MIN_BARS: return None

        opens=[float(x) for x in df["Open"]]; highs=[float(x) for x in df["High"]]
        lows=[float(x) for x in df["Low"]]; closes=[float(x) for x in df["Close"]]
        dates=list(df.index)

        sh=swing_highs(highs,3,3); sl=swing_lows(lows,3,3)
        trades=[]; entries=set()

        for i in range(MIN_BARS, len(closes)):
            if i in entries: continue
            sh_idx=prev_swing(i,sh); sl_idx=prev_swing(i,sl)
            ci=closes[i]; hi=highs[i]; li=lows[i]; oi=opens[i]

            # ── 1) Swing low sweep → BUY (sellside liquidity) ──
            if sl_idx is not None and i-sl_idx >= MIN_CANDLES:
                swing_low = lows[sl_idx]
                if li < swing_low and ci > swing_low:
                    e = ci; s = min(li, swing_low - (hi-li)*0.05)
                    if abs(e-s)/e*100 <= MAX_STOP_PCT:
                        tgt = None
                        for j in range(i, max(i-120, -1), -1):
                            if j < len(sh) and sh[j]==1.0 and highs[j] > e: tgt=highs[j]; break
                        if tgt is None: tgt = e + (e-s)*10
                        rr = (tgt-e)/(e-s) if e!=s else 0
                        if rr >= MIN_RR:
                            t = make_trade(i,"BUY",e,s,tgt,closes,highs,lows,dates,{"symbol":symbol.upper(),"type":"sellside","candles_since":i-sl_idx,"sweep_level":round(swing_low,2)})
                            if t: trades.append(t); entries.add(i)

            # ── 2) Swing high sweep → SELL (buyside liquidity) ──
            if sh_idx is not None and i not in entries and i-sh_idx >= MIN_CANDLES:
                swing_high = highs[sh_idx]
                if hi > swing_high and ci < swing_high:
                    e = ci; s = max(hi, swing_high + (hi-li)*0.05)
                    if abs(e-s)/e*100 <= MAX_STOP_PCT:
                        tgt = None
                        for j in range(i, max(i-120, -1), -1):
                            if j < len(sl) and sl[j]==1.0 and lows[j] < e: tgt=lows[j]; break
                        if tgt is None: tgt = e - (s-e)*10
                        rr = (e-tgt)/(s-e) if s!=e else 0
                        if rr >= MIN_RR:
                            t = make_trade(i,"SELL",e,s,tgt,closes,highs,lows,dates,{"symbol":symbol.upper(),"type":"buyside","candles_since":i-sh_idx,"sweep_level":round(swing_high,2)})
                            if t: trades.append(t); entries.add(i)

        return trades if trades else None
    except Exception as e:
        return None


def run():
    t0=time.time()
    print("="*60)
    print("LIQUIDITY SWEEP STRATEGY — Daily Backtest")
    print("="*60)
    print(f"Stocks: {len(INDIAN_STOCKS)}")
    print(f"Min candles between level & retest: {MIN_CANDLES}")
    print(f"Max stop: {MAX_STOP_PCT}% | Min RR: {MIN_RR}:1")
    print()

    all_trades=[]; sym_with=0

    with ThreadPoolExecutor(max_workers=8) as pool:
        fut_map={pool.submit(backtest_stock, sym): sym for sym in INDIAN_STOCKS}
        for fut in as_completed(fut_map):
            try:
                trades=fut.result(timeout=60)
                if trades:
                    all_trades.extend(trades); sym_with+=1
                    print(f"  {fut_map[fut].upper():15s} → {len(trades):3d} trades")
            except Exception:
                pass

    print(f"\nDone in {time.time()-t0:.0f}s\n")
    report(all_trades, sym_with, time.time()-t0)


def report(all_trades, sym_with, elapsed):
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
    avg_bars=sum(t["bars_held"] for t in all_trades)/total
    avg_rr_s=sum(t["rr_setup"] for t in all_trades)/total

    print("="*60)
    print("RESULTS")
    print("="*60)
    print(f"Total trades:            {total}")
    print(f"Stocks with trades:      {sym_with}")
    print(f"BUY: {len(buys)}  SELL: {len(sells)}")
    print(f"Types: {', '.join(f'{k}={len(v)}' for k,v in sorted(types.items()))}")
    print(f"Winners: {len(winners)}  Losers: {len(losers)}")
    print(f"Targets: {len(targets)}  Stops: {len(stops)}")
    print(f"BE triggered: {len(be)}/{total} ({len(be)/total*100:.1f}%)")
    print()
    print(f"Win Rate:               {wr:.1f}%")
    print(f"Avg PnL:                {avg_pnl:+.4f}%")
    print(f"Avg Win:                {avg_win:+.4f}%")
    print(f"Avg Loss:               {avg_loss:.4f}%")
    print(f"Realized RR:            {rr_r:.2f}:1")
    print(f"Avg Setup RR:           {avg_rr_s:.2f}:1")
    print(f"Profit Factor:          {pf:.2f}")
    print(f"Total Return:           {tot_r:+.2f}%")
    print(f"Avg Days Held:          {avg_bars:.0f}")

    for tname, tlist in sorted(types.items()):
        tw=[t for t in tlist if t["pnl_pct"]>0]
        print(f"  {tname:20s}: {len(tlist):4d} trades, WR {len(tw)/len(tlist)*100:.1f}%, Avg {sum(t['pnl_pct'] for t in tlist)/len(tlist):+.4f}%")

    # Per stock
    ss={}
    for t in all_trades: ss.setdefault(t["symbol"],[]).append(t)
    print(f"\n  Per stock:")
    for sym in sorted(ss):
        s=ss[sym]; w=[t for t in s if t["pnl_pct"]>0]; ap=sum(t["pnl_pct"] for t in s)/len(s)
        print(f"    {sym:12s}: {len(s):3d} trades, {len(w)/len(s)*100:5.1f}% WR, {ap:+7.4f}% avg")

    # Best/worst
    st=sorted(all_trades, key=lambda x: x["pnl_pct"], reverse=True)
    print(f"\n  Best 5:")
    for t in st[:5]:
        print(f"    {t['symbol']:8s} {t['type']:10s} {t['direction']:4s} @{t['entry_price']:>8.2f} → {t['exit_price']:>7.2f} ({t['pnl_pct']:+6.2f}%) days={t['bars_held']}")
    print(f"\n  Worst 5:")
    for t in st[-5:]:
        print(f"    {t['symbol']:8s} {t['type']:10s} {t['direction']:4s} @{t['entry_price']:>8.2f} → {t['exit_price']:>7.2f} ({t['pnl_pct']:+6.2f}%) days={t['bars_held']}")

    ts=datetime.now().strftime("%Y%m%d_%H%M%S")
    rpt=os.path.join(RPT_DIR,f"liq_sweep_daily_{ts}.md")
    jf=os.path.join(RPT_DIR,f"liq_sweep_daily_{ts}.json")

    lines=[
        f"# Liquidity Sweep Strategy — Daily Backtest",
        f"",
        f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Duration**: {elapsed:.0f}s",
        f"**Stocks**: {sym_with}/{len(INDIAN_STOCKS)}",
        f"**Params**: MinCandles={MIN_CANDLES}, MaxStop={MAX_STOP_PCT}%, MinRR={MIN_RR}:1",
        f"",
        f"## Summary",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total Trades | {total} |",
        f"| BUY / SELL | {len(buys)} / {len(sells)} |",
        f"| Win Rate | {wr:.1f}% |",
        f"| Avg PnL | {avg_pnl:+.4f}% |",
        f"| Avg Win | {avg_win:+.4f}% |",
        f"| Avg Loss | {avg_loss:.4f}% |",
        f"| Realized RR | {rr_r:.2f}:1 |",
        f"| Avg Setup RR | {avg_rr_s:.2f}:1 |",
        f"| Profit Factor | {pf:.2f} |",
        f"| Total Return | {tot_r:+.2f}% |",
        f"| Avg Days Held | {avg_bars:.0f} |",
        f"| Targets Hit | {len(targets)}/{total} ({len(targets)/total*100:.1f}%) |",
        f"| Stop Losses | {len(stops)}/{total} ({len(stops)/total*100:.1f}%) |",
        f"| BE Triggered | {len(be)}/{total} ({len(be)/total*100:.1f}%) |",
        f"",
        f"## Per-Type Breakdown",
        f"",
        f"| Type | Trades | WR | Avg PnL | Avg RR |",
        f"|------|--------|----|---------|--------|",
    ]
    for tname, tlist in sorted(types.items()):
        tw=[t for t in tlist if t["pnl_pct"]>0]
        tp=sum(t["pnl_pct"] for t in tlist)/len(tlist)
        trr=sum(t["rr_setup"] for t in tlist)/len(tlist)
        lines.append(f"| {tname:20s} | {len(tlist):5d} | {len(tw)/len(tlist)*100:.1f}% | {tp:+.4f}% | {trr:.2f}:1 |")

    lines.extend(["","## Per-Stock","","| Symbol | Trades | WR | Avg PnL | Total Return |","|--------|--------|----|---------|-------------|"])
    for sym in sorted(ss):
        s=ss[sym]; w=[t for t in s if t["pnl_pct"]>0]; ap=sum(t["pnl_pct"] for t in s)/len(s)
        lines.append(f"| {sym:10s} | {len(s):4d} | {len(w)/len(s)*100:5.1f}% | {ap:+7.4f}% | {sum(t['pnl_pct'] for t in s):+9.2f}% |")

    lines.extend(["","## Best Trades","","| # | Symbol | Type | Dir | Entry | Exit | PnL% | RR | Days |","|---|--------|------|-----|-------|------|------|-----|------|"])
    for k,t in enumerate(st[:15]):
        lines.append(f"| {k+1} | {t['symbol']:10s} | {t['type'][:10]:10s} | {t['direction']:4s} | {t['entry_price']:>8.2f} | {t['exit_price']:>8.2f} | {t['pnl_pct']:+6.2f}% | {t['rr_setup']:.1f}:1 | {t['bars_held']:3d} |")
    lines.extend(["","## Worst Trades","","| # | Symbol | Type | Dir | Entry | Exit | PnL% | RR | Days |","|---|--------|------|-----|-------|------|------|-----|------|"])
    for k,t in enumerate(st[-15:]):
        lines.append(f"| {k+1} | {t['symbol']:10s} | {t['type'][:10]:10s} | {t['direction']:4s} | {t['entry_price']:>8.2f} | {t['exit_price']:>8.2f} | {t['pnl_pct']:+6.2f}% | {t['rr_setup']:.1f}:1 | {t['bars_held']:3d} |")

    with open(rpt,"w") as f: f.write("\n".join(lines))
    with open(jf,"w") as f: json.dump({"generated":datetime.now().isoformat(),"params":{"min_candles":MIN_CANDLES,"max_stop_pct":MAX_STOP_PCT,"min_rr":MIN_RR},"summary":{"total":total,"stocks":sym_with,"win_rate":round(wr,1),"avg_pnl":round(avg_pnl,4),"avg_win":round(avg_win,4),"avg_loss":round(avg_loss,4),"realized_rr":round(rr_r,2),"avg_setup_rr":round(avg_rr_s,2),"profit_factor":round(pf,2),"total_return":round(tot_r,2),"targets":len(targets),"stops":len(stops),"be":len(be)},"trades":st},f,indent=2)

    print(f"\nReport: {rpt}")

    # Telegram
    print(f"\n{'='*50}")
    print("TELEGRAM REPORT")
    print(f"{'='*50}")
    print(f"📊 *Liquidity Sweep Daily Backtest*")
    print(f"Stocks: {sym_with}/{len(INDIAN_STOCKS)} | Trades: {total}")
    print(f"WR: {wr:.1f}% | Avg: {avg_pnl:+.4f}% | Total: {tot_r:+.2f}%")
    print(f"RR: {rr_r:.2f}:1 | PF: {pf:.2f}")
    print(f"Targets: {len(targets)}/{total} | BE: {len(be)}/{total}")
    for tname, tlist in sorted(types.items()):
        tw=[t for t in tlist if t["pnl_pct"]>0]
        print(f"  {tname}: {len(tlist)} trades, {len(tw)/len(tlist)*100:.1f}% WR")


if __name__=="__main__":
    run()
