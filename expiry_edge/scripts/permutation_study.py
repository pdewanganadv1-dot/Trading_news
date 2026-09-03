"""Grid search over the strangle/straddle rulebook on all 12 Aug CAS index expiries + 25 Aug stock tickets.
Dimensions: entry time, structure (OTM1 strangle / ATM straddle / v1 pin-pull single leg), premium cap,
geometry cap, per-leg delta floor. Scores: trades, win rate, total P&L (Rs 20k per trade), worst, P&L ex-BANKEX.
n is tiny (12 expiries) -> treat as hypothesis ranking, not proof."""
import datetime as dt, itertools, math, glob
import numpy as np, pandas as pd
from scipy.stats import norm
import importlib.util
spec = importlib.util.spec_from_file_location("st", "scripts/strangle_1505.py")
st = importlib.util.module_from_spec(spec); spec.loader.exec_module(st)

def delta(S, K, iv, minutes, side):
    if not iv or iv <= 0 or np.isnan(iv): return np.nan
    T = max(minutes, 1) / (365 * 24 * 60); s = iv / 100
    d1 = (math.log(S / K) + 0.5 * s * s * T) / (s * math.sqrt(T))
    return abs(norm.cdf(d1) if side == "CE" else norm.cdf(d1) - 1)

DAYS = {}
for idx, iso in st.EXPIRIES:
    d = st.load(idx); d["ts"] = pd.to_datetime(d.ts, errors="coerce"); d = d.dropna(subset=["ts"])
    if "iv" not in d.columns: d["iv"] = np.nan
    day = d[d.ts.dt.date == dt.date.fromisoformat(iso)].assign(time=lambda x: x.ts.dt.time)
    DAYS[(idx, iso)] = day

def leg(day, t_entry, side, k, settle, minutes_left):
    pre = day[day.time <= t_entry]
    o = pre[(pre.side == side) & (pre.strike == k)].sort_values("ts")
    if o.empty: return None
    prem = float(o.close.iloc[-1]); iv = float(o.iv.iloc[-1]) if "iv" in o.columns else np.nan
    spot = float(pre.sort_values("ts").spot.iloc[-1])
    return {"prem": prem, "intr": max(0.0, (settle - k) if side == "CE" else (k - settle)),
            "delta": delta(spot, k, iv, minutes_left, side), "k": k}

def run(entry, structure, prem_cap, need_cap, delta_floor, budget=20000):
    t_entry = dt.time(*entry); minutes_left = (15 * 60 + 30) - (entry[0] * 60 + entry[1])
    res = []
    for (idx, iso), day in DAYS.items():
        pre = day[day.time <= t_entry]
        if pre.empty: continue
        spot = float(pre.sort_values("ts").spot.iloc[-1]); settle = float(day.sort_values("ts").spot.iloc[-1])
        ks = sorted(day.strike.unique())
        ce_k = min((k for k in ks if k > spot), default=None); pe_k = max((k for k in ks if k < spot), default=None)
        atm = min(ks, key=lambda k: abs(k - spot))
        if ce_k is None or pe_k is None: continue
        if structure == "OTM1":
            legs = [leg(day, t_entry, "CE", ce_k, settle, minutes_left), leg(day, t_entry, "PE", pe_k, settle, minutes_left)]
        elif structure == "ATM":
            legs = [leg(day, t_entry, "CE", atm, settle, minutes_left), leg(day, t_entry, "PE", atm, settle, minutes_left)]
        else:  # v1 pin-pull single leg (needs OI-based max pain over visible strikes at entry)
            snap = pre.sort_values("ts").groupby(["side", "strike"]).oi.last().reset_index()
            best, bp = None, None
            for s in ks:
                pain = sum(r.oi * (s - r.strike) for r in snap.itertuples() if r.side == "CE" and s > r.strike) + \
                       sum(r.oi * (r.strike - s) for r in snap.itertuples() if r.side == "PE" and s < r.strike)
                if bp is None or pain < bp: best, bp = s, pain
            if best is None or abs(best - spot) < 0.0004 * spot: continue
            legs = [leg(day, t_entry, "CE", ce_k, settle, minutes_left)] if best > spot else [leg(day, t_entry, "PE", pe_k, settle, minutes_left)]
        if any(l is None for l in legs): continue
        comb = sum(l["prem"] for l in legs)
        if comb <= 0: continue
        prem_pct = comb / spot * 100
        need = min((ce_k - spot) + comb, (spot - pe_k) + comb) / spot * 100 if structure != "v1" else (abs(legs[0]["k"] - spot) + comb) / spot * 100
        if prem_cap and prem_pct > prem_cap: continue
        if need_cap and need > need_cap: continue
        if delta_floor and any((not np.isnan(l["delta"])) and l["delta"] < delta_floor for l in legs): continue
        lot = st.LOTS[idx]; lots = int(budget // (comb * lot))
        if lots == 0: continue
        pnl = round(lots * (sum(l["intr"] for l in legs) - comb) * lot)
        res.append({"idx": idx, "date": iso, "pnl": pnl})
    if not res: return None
    r = pd.DataFrame(res)
    return {"entry": f"{entry[0]:02d}:{entry[1]:02d}", "structure": structure, "prem_cap": prem_cap, "need_cap": need_cap,
            "delta_floor": delta_floor, "trades": len(r), "wins": int((r.pnl > 0).sum()),
            "win_rate": round((r.pnl > 0).mean() * 100), "pnl": int(r.pnl.sum()),
            "pnl_exBANKEX": int(r[r.idx != "BANKEX"].pnl.sum()), "worst": int(r.pnl.min())}

grid = list(itertools.product([(14, 50), (15, 0), (15, 5), (15, 10)], ["OTM1", "ATM", "v1"],
                              [None, 0.20, 0.25, 0.30], [None, 0.30, 0.35, 0.40], [None, 0.10, 0.20, 0.30]))
out = [x for x in (run(*g) for g in grid) if x]
t = pd.DataFrame(out)
t.to_csv("outputs/model/permutation_study_index.csv", index=False)
pd.set_option("display.width", 220)
base = t[(t.entry == "15:05") & (t.structure == "OTM1") & t.prem_cap.eq(0.25) & t.need_cap.eq(0.35) & t.delta_floor.isna()]
print("CURRENT RULE:"); print(base.to_string(index=False))
print("\nTOP 12 by WIN RATE (min 6 trades):")
print(t[t.trades >= 6].sort_values(["win_rate", "pnl"], ascending=False).head(12).to_string(index=False))
print("\nTOP 12 by P&L ex-BANKEX (min 6 trades):")
print(t[t.trades >= 6].sort_values("pnl_exBANKEX", ascending=False).head(12).to_string(index=False))
print("\nTOP 8 by total P&L (min 6 trades):")
print(t[t.trades >= 6].sort_values("pnl", ascending=False).head(8).to_string(index=False))
