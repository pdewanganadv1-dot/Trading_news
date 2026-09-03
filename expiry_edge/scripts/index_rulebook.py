"""Index-only rulebook study on 13 CAS expiries (12 Aug + 1 Sep NIFTY live), nearest-OTM strangle at 15:05.
Fills missing IV by solving Black-Scholes from the premium so the delta filter is evaluated on EVERY leg.
Scores each candidate rule: trades, win rate with Wilson 95% CI, P&L at Rs 20k/trade, ex-BANKEX, worst,
and leave-one-out stability (min win rate when any single expiry is dropped)."""
import datetime as dt, math, json
import numpy as np, pandas as pd
from scipy.stats import norm
from scipy.optimize import brentq
import importlib.util
spec = importlib.util.spec_from_file_location("st", "scripts/strangle_1505.py")
st = importlib.util.module_from_spec(spec); spec.loader.exec_module(st)

MONTHLY = {("BANKNIFTY","2026-08-25"),("FINNIFTY","2026-08-25"),("MIDCPNIFTY","2026-08-25"),("NIFTY","2026-08-25"),
           ("BANKEX","2026-08-27"),("SENSEX","2026-08-27")}
FIRST_CAS = {("NIFTY","2026-08-04"),("SENSEX","2026-08-06")}
MIN = 25 / (365 * 24 * 60)

def bs(S, K, s, side):
    d1 = (math.log(S / K) + 0.5 * s * s * MIN) / (s * math.sqrt(MIN)); d2 = d1 - s * math.sqrt(MIN)
    return (S * norm.cdf(d1) - K * norm.cdf(d2)) if side == "CE" else (K * norm.cdf(-d2) - S * norm.cdf(-d1))
def solve_delta(S, K, prem, side):
    intr = max(0.0, (S - K) if side == "CE" else (K - S))
    if prem <= intr + 1e-6: return 1.0
    try: s = brentq(lambda v: bs(S, K, v, side) - prem, 0.01, 10.0)
    except ValueError: return np.nan
    d1 = (math.log(S / K) + 0.5 * s * s * MIN) / (s * math.sqrt(MIN))
    return abs(norm.cdf(d1) if side == "CE" else norm.cdf(d1) - 1)

rows = []
for idx, iso in st.EXPIRIES:
    d = st.load(idx); d["ts"] = pd.to_datetime(d.ts, errors="coerce"); d = d.dropna(subset=["ts"])
    day = d[d.ts.dt.date == dt.date.fromisoformat(iso)].assign(time=lambda x: x.ts.dt.time)
    r = st.evaluate(day, 20000, st.LOTS[idx])
    if not r: continue
    dce = solve_delta(r["spot"], r["ce_k"], r["ce_p"], "CE"); dpe = solve_delta(r["spot"], r["pe_k"], r["pe_p"], "PE")
    rows.append({"index": idx, "date": iso, "monthly": (idx, iso) in MONTHLY, "first_cas": (idx, iso) in FIRST_CAS,
                 "prem_pct": r["prem_pct"], "need_pct": r["need_pct"], "d_ce": dce, "d_pe": dpe,
                 "auction_pts": r["settle"] - r["spot"], "pnl": r["net"]})
# 1 Sep NIFTY live (15:01 snapshot legs, settlement 24055.80)
S, ce_k, pe_k, ce_p, pe_p, settle = 23975.8, 24000, 23950, 26.10, 17.35, 24055.80
comb = ce_p + pe_p; lots = int(20000 // (comb * 65)); pnl = round(lots * (max(0, settle - ce_k) + max(0, pe_k - settle) - comb) * 65)
rows.append({"index": "NIFTY", "date": "2026-09-01", "monthly": False, "first_cas": False, "prem_pct": comb / S * 100,
             "need_pct": min((ce_k - S) + comb, (S - pe_k) + comb) / S * 100, "d_ce": solve_delta(S, ce_k, ce_p, "CE"),
             "d_pe": solve_delta(S, pe_k, pe_p, "PE"), "auction_pts": settle - S, "pnl": pnl})
t = pd.DataFrame(rows); t["win"] = t.pnl > 0
t.to_csv("outputs/model/index_rulebook_table.csv", index=False)
pd.set_option("display.width", 220); print(t.round(2).to_string(index=False))

def wilson(w, n, z=1.96):
    if n == 0: return (0, 0)
    p = w / n; den = 1 + z*z/n; c = (p + z*z/(2*n)) / den; h = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / den
    return (max(0, c - h) * 100, min(1, c + h) * 100)

RULES = {
 "A. take every expiry": lambda x: pd.Series(True, index=x.index),
 "B. current gates (prem<=.25, need<=.35)": lambda x: (x.prem_pct <= .25) & (x.need_pct <= .35),
 "C. B + both deltas >= .30": lambda x: (x.prem_pct <= .25) & (x.need_pct <= .35) & (x.d_ce >= .30) & (x.d_pe >= .30),
 "D. both deltas >= .30 only": lambda x: (x.d_ce >= .30) & (x.d_pe >= .30),
 "E. monthly-expiry days only": lambda x: x.monthly,
 "F. monthly OR first-CAS day": lambda x: x.monthly | x.first_cas,
 "G. E + prem<=.25": lambda x: x.monthly & (x.prem_pct <= .25),
 "H. (monthly) OR (weekly with both deltas>=.30 and prem<=.25)": lambda x: x.monthly | ((x.d_ce >= .30) & (x.d_pe >= .30) & (x.prem_pct <= .25)),
 "I. H + need<=.35": lambda x: (x.monthly | ((x.d_ce >= .30) & (x.d_pe >= .30) & (x.prem_pct <= .25))) & (x.need_pct <= .35),
}
out = []
for name, f in RULES.items():
    m = t[f(t)]
    if m.empty: continue
    w, n = int(m.win.sum()), len(m); lo, hi = wilson(w, n)
    loo = min(((m.drop(i).win.mean()) for i in m.index), default=np.nan) * 100 if n > 1 else np.nan
    out.append({"rule": name, "trades": n, "wins": w, "win%": round(w / n * 100), "CI95": f"{lo:.0f}-{hi:.0f}%",
                "LOO_min_win%": round(loo), "pnl": int(m.pnl.sum()), "ex_BANKEX": int(m[m["index"] != "BANKEX"].pnl.sum()),
                "worst": int(m.pnl.min()), "skipped_wins": int(t[~f(t)].win.sum())})
res = pd.DataFrame(out); print("\n", res.to_string(index=False))
res.to_csv("outputs/model/index_rulebook_results.csv", index=False)
