"""CAS-period OTM timetable — model-free, from REAL option bars (Dhan export), expiries since 3 Aug 2026 only.

    python scripts/cas_timetable.py --zip dhan_export.zip [--since 2026-08-03]

No option-pricing model is used anywhere here: every premium is a traded 5-minute bar of the expiring
weekly contract (Dhan charts/rollingoption).  For every expiry day since CAS went live, every 5-minute
entry bar up to the 14:55 cutoff, both sides, and strikes 1/2/3 OTM from the spot at that bar
(plus the indicator's 'auto' strike = nearest OTM priced <= cap, never the 3rd), it records:
    entry   = close of the option bar at the entry time
    mult60  = highest print within the next 60 minutes (and before 15:10) / entry
    net60   = net return at +60 min or the 15:10 bar, whichever comes first (the rule's exit)
    net1510 = net return if held to the 15:10 bar (last before the freeze)
    netset  = net return if held through the auction to settlement (intrinsic on the CAS close)
Entry bars are tagged with the buy-score verdict (GO / LEAN / other) computed from the real index bars,
so the grid can be read for GO bars, LEAN bars, and any bar (the lottery).  A fourth set, BOTH, buys the
k-OTM call and the k-OTM put together on the same bar (a strangle) — no direction needed — and the auction
ticket is also reported for CE + PE together.

Writes outputs/cas_month/cas_timetable.csv (one row per entry x side x strike) and
outputs/cas_month/cas_timetable.json (grid cells by window x strike, per-day rows, auction tickets)
in the format report/build_timetable.py renders.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from expiry_edge.config import CAS_START_DATE, CONTRACT, COST_PER_SIDE            # noqa: E402
from expiry_edge.options import atm_strike                                        # noqa: E402
from expiry_edge.score import BuyScore                                            # noqa: E402
from cas_month_check_real import build_bars5, build_daily, load_export, option_series   # noqa: E402
from live_indicator import run_once                                               # noqa: E402
from otm_timetable import CAP, LAST_ENTRY_MINUTE, WINDOWS, win_label              # noqa: E402

OUT = ROOT / "outputs" / "cas_month"; OUT.mkdir(parents=True, exist_ok=True)
EXIT_T = dt.time(15, 10)
MIN_PRINT = 0.05


def pick(ser: pd.DataFrame, ts: pd.Timestamp):
    """The option bar starting at ts (or the last one before it)."""
    at = ser[ser["ts"] == ts]
    if not len(at):
        at = ser[ser["ts"] <= ts].tail(1)
    return at.iloc[0] if len(at) else None


def outcomes(ser: pd.DataFrame, bar_ts: pd.Timestamp, K: float, sgn: int, close_cas: float, cost: float) -> dict | None:
    row = pick(ser, bar_ts)
    if row is None or not np.isfinite(row["close"]) or row["close"] <= 0:
        return None
    p0 = float(row["close"])
    after = ser[(ser["ts"] > bar_ts) & (ser["ts"].dt.time <= EXIT_T)]
    win = after[after["ts"] <= bar_ts + pd.Timedelta(minutes=60)]
    if not len(win):
        return None
    mult60 = float(win["high"].max() / p0)
    p60 = float(win["close"].iloc[-1])
    p1510 = float(after["close"].iloc[-1])
    settle = max(sgn * (close_cas - K), 0.0)
    net = lambda p: (p - cost) / (p0 + cost) - 1                                   # noqa: E731
    return {"entry": p0, "mult60": mult60, "net60": net(p60), "net1510": net(p1510), "netset": net(settle),
            "settle": settle, "bars60": int(len(win))}


def pair_outcomes(ser_ce: pd.DataFrame, ser_pe: pd.DataFrame, bar_ts: pd.Timestamp, K_ce: float, K_pe: float, close_cas: float, cost: float) -> dict | None:
    """CE + PE bought together at the same bar (a strangle; a straddle when K_ce == K_pe).  Combined premium along the
    day = sum of the two legs' closes (bar-aligned), so mult60 here is on closes, not intrabar highs.  Costs on both legs."""
    a, b = pick(ser_ce, bar_ts), pick(ser_pe, bar_ts)
    if a is None or b is None or not (np.isfinite(a["close"]) and np.isfinite(b["close"])) or a["close"] <= 0 or b["close"] <= 0:
        return None
    p0 = float(a["close"] + b["close"])
    after = ser_ce[(ser_ce["ts"] > bar_ts) & (ser_ce["ts"].dt.time <= EXIT_T)][["ts", "close"]].merge(
        ser_pe[(ser_pe["ts"] > bar_ts) & (ser_pe["ts"].dt.time <= EXIT_T)][["ts", "close"]], on="ts", suffixes=("_ce", "_pe"))
    if not len(after):
        return None
    after["sum"] = after["close_ce"] + after["close_pe"]
    win = after[after["ts"] <= bar_ts + pd.Timedelta(minutes=60)]
    if not len(win):
        return None
    settle = max(close_cas - K_ce, 0.0) + max(K_pe - close_cas, 0.0)
    c2 = 2 * cost
    net = lambda p: (p - c2) / (p0 + c2) - 1                                     # noqa: E731
    return {"entry": p0, "mult60": float(win["sum"].max() / p0), "net60": net(float(win["sum"].iloc[-1])), "net1510": net(float(after["sum"].iloc[-1])),
            "netset": net(settle), "settle": settle, "bars60": int(len(win))}


def stats(g: pd.DataFrame) -> dict:
    n = len(g)
    if n == 0:
        return {"n": 0}
    return {"n": int(n), "days": int(g["date"].nunique()),
            "p0_med": float(g["entry"].median()), "otm_pts_med": float(g["otm_pts"].median()),
            "p_2x": float((g["mult60"] >= 2).mean() * 100), "p_3x": float((g["mult60"] >= 3).mean() * 100),
            "p_5x": float((g["mult60"] >= 5).mean() * 100), "p_10x": float((g["mult60"] >= 10).mean() * 100),
            "net_mean": float(g["net60"].mean() * 100), "net_median": float(g["net60"].median() * 100),
            "p_net_pos": float((g["net60"] > 0).mean() * 100), "p_lose_half": float((g["net60"] <= -0.5).mean() * 100),
            "net1510_mean": float(g["net1510"].mean() * 100), "netset_mean": float(g["netset"].mean() * 100),
            "p_set_pos": float((g["netset"] > 0).mean() * 100),
            "k_share": (g["k_chosen"].value_counts(normalize=True).round(2).to_dict() if "k_chosen" in g and g["k_chosen"].notna().any() else None)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True)
    ap.add_argument("--since", default=CAS_START_DATE.isoformat())
    a = ap.parse_args()
    since = pd.Timestamp(a.since).date()
    files = load_export(Path(a.zip))
    model = BuyScore()
    rows, days_out, tickets = [], [], []
    for idx in ("NIFTY", "SENSEX", "BANKNIFTY"):
        need = [f"index_5m_{idx}.csv", f"index_daily_{idx}.csv", f"rolling_options_{idx}.csv"]
        if any(n not in files for n in need):
            print(f"[{idx}] missing {[n for n in need if n not in files]} — skipped"); continue
        bars5 = build_bars5(files[f"index_5m_{idx}.csv"])
        d = build_daily(files[f"index_daily_{idx}.csv"], idx)
        opt = files[f"rolling_options_{idx}.csv"].copy(); opt["ts"] = pd.to_datetime(opt["ts"])
        step = CONTRACT[idx]["strike_step"]; cost = COST_PER_SIDE[idx]; cap = CAP[idx]
        prof = np.load(ROOT / f"outputs/{'NIFTY' if idx == 'SENSEX' else idx}_profile_expiry.npy")
        cum = np.concatenate([[0.0], np.cumsum(prof)])
        opt_days = sorted(set(opt["ts"].dt.date) & set(bars5["date"]))
        dates = [x for x in opt_days if x >= since and x in d.index.date and bool(d.loc[pd.Timestamp(x), "is_expiry"])]
        for date in dates:
            day_bars = bars5[bars5["date"] <= date]
            try:
                raw = run_once(idx, day_bars, d[d.index <= pd.Timestamp(date)], model, cum, quiet=True)
            except Exception as e:                                               # noqa: BLE001
                print(f"[{idx} {date}] scoring failed: {e}"); continue
            drow = d.loc[pd.Timestamp(date)]; close_cas = float(drow["close"])
            od = opt[opt["ts"].dt.date == date]
            today = day_bars[day_bars["date"] == date].sort_values("minute")
            p1510 = float(today[today["ts"].dt.time <= EXIT_T]["close"].iloc[-1])
            verdict_by_min = {int(r["minute"]) + 4: (r["verdict"], int(r["dir"]), float(r["score"])) for _, r in raw.iterrows()}
            sig_times = [f"{9 + (m + 15) // 60:02d}:{(m + 15) % 60:02d} {'CE' if dr == 1 else 'PE'} {v.split(' ')[0]}"
                         for m, (v, dr, s) in sorted(verdict_by_min.items()) if v.startswith(("GO", "LEAN"))]
            n_rows_day = 0
            for _, b in today.iterrows():
                m_close = int(b["minute"]) + 4
                if m_close > LAST_ENTRY_MINUTE:
                    continue
                verdict, direction, score = verdict_by_min.get(m_close, ("n/a", 0, np.nan))
                cond = "GO" if verdict.startswith("GO") else ("LEAN" if verdict.startswith("LEAN") else "OTHER")
                spot = float(b["close"]); K0 = atm_strike(spot, idx); bar_ts = pd.Timestamp(b["ts"])
                legs = {}
                for side, sgn in (("CE", 1), ("PE", -1)):
                    per_k = {}
                    for k in (1, 2, 3):
                        K = K0 + sgn * k * step
                        if sgn * (K - spot) <= 0:                                  # ATM rounding put it in the money: step out once more
                            K += sgn * step
                        ser = option_series(od, date, side, K)
                        legs[(side, k)] = (ser, K)
                        o = outcomes(ser, bar_ts, K, sgn, close_cas, cost)
                        if o is None:
                            continue
                        per_k[k] = dict(o, K=K)
                        rows.append({"index": idx, "date": str(date), "time": bar_ts.strftime("%H:%M"), "minute": m_close, "side": side, "k": k,
                                     "strike": K, "spot": spot, "otm_pts": sgn * (K - spot), "cond": cond, "signal_side": direction == sgn,
                                     "score": score, **o})
                        n_rows_day += 1
                    # the indicator's strike: nearest OTM whose REAL premium is <= cap (k <= 2), else the 1-OTM
                    cheap = [k for k in (1, 2) if k in per_k and per_k[k]["entry"] <= cap]
                    kc = cheap[0] if cheap else (1 if 1 in per_k else None)
                    if kc is not None:
                        o = per_k[kc]
                        rows.append({"index": idx, "date": str(date), "time": bar_ts.strftime("%H:%M"), "minute": m_close, "side": side, "k": 0,
                                     "strike": o["K"], "spot": spot, "otm_pts": sgn * (o["K"] - spot), "cond": cond, "signal_side": direction == sgn,
                                     "score": score, "k_chosen": kc, **{kk: vv for kk, vv in o.items() if kk != "K"}})
                # both sides at once: k-OTM call + k-OTM put bought on the same bar (no direction needed)
                both = {}
                for k in (1, 2, 3):
                    (sc, Kc), (sp, Kp) = legs[("CE", k)], legs[("PE", k)]
                    o = pair_outcomes(sc, sp, bar_ts, Kc, Kp, close_cas, cost)
                    if o is None:
                        continue
                    both[k] = dict(o, K=Kc, K_pe=Kp)
                    rows.append({"index": idx, "date": str(date), "time": bar_ts.strftime("%H:%M"), "minute": m_close, "side": "BOTH", "k": k,
                                 "strike": Kc, "strike_pe": Kp, "spot": spot, "otm_pts": ((Kc - spot) + (spot - Kp)) / 2, "cond": cond, "signal_side": False,
                                 "score": score, **{kk: vv for kk, vv in o.items()}})
                    n_rows_day += 1
                cheap = [k for k in (1, 2) if k in both and both[k]["entry"] <= 2 * cap]
                kc = cheap[0] if cheap else (1 if 1 in both else None)
                if kc is not None:
                    o = both[kc]
                    rows.append({"index": idx, "date": str(date), "time": bar_ts.strftime("%H:%M"), "minute": m_close, "side": "BOTH", "k": 0,
                                 "strike": o["K"], "strike_pe": o["K_pe"], "spot": spot, "otm_pts": ((o["K"] - spot) + (spot - o["K_pe"])) / 2, "cond": cond,
                                 "signal_side": False, "score": score, "k_chosen": kc, **{kk: vv for kk, vv in o.items() if kk not in ("K", "K_pe")}})
            # auction ticket: nearest OTM at the 15:10 bar, held to settlement, real 15:10 price (each side, and both together)
            tk = {}
            for side, sgn in (("CE", 1), ("PE", -1)):
                K0 = atm_strike(p1510, idx); K = K0 if sgn * (K0 - p1510) > 0 else K0 + sgn * step
                ser = option_series(od, date, side, K); ser = ser[ser["ts"].dt.time <= EXIT_T]
                if len(ser):
                    p0 = float(ser["close"].iloc[-1]); settle = max(sgn * (close_cas - K), 0.0)
                    tk[side] = (p0, settle, K)
                    tickets.append({"index": idx, "date": str(date), "side": side, "strike": K, "otm_pts": sgn * (K - p1510), "p1510": p0,
                                    "settle": settle, "net_pct": ((settle - cost) / (p0 + cost) - 1) * 100})
            if "CE" in tk and "PE" in tk:
                p0 = tk["CE"][0] + tk["PE"][0]; settle = tk["CE"][1] + tk["PE"][1]
                tickets.append({"index": idx, "date": str(date), "side": "BOTH", "strike": f'{tk["CE"][2]:.0f}/{tk["PE"][2]:.0f}', "otm_pts": None, "p1510": p0,
                                "settle": settle, "net_pct": ((settle - 2 * cost) / (p0 + 2 * cost) - 1) * 100})
            days_out.append({"index": idx, "date": str(date), "open": float(today["open"].iloc[0]), "p1510": p1510, "cas_close": close_cas,
                             "auction_move": close_cas - p1510, "day_range": float(today["high"].max() - today["low"].min()),
                             "signals": sig_times, "rows": n_rows_day})
            print(f"[{idx} {date}] {n_rows_day} option rows; signals: {sig_times or 'none'}; 15:10 {p1510:.2f} -> CAS close {close_cas:.2f}")
    if not rows:
        print("no CAS-period rows — nothing written"); return
    R = pd.DataFrame(rows)
    R.to_csv(OUT / "cas_timetable.csv", index=False)
    # ---- grid cells in the same format as outputs/otm/timetable.json
    cards = {}
    for idx, g in R.groupby("index"):
        card = {"index": idx, "days": int(g["date"].nunique()), "cap": CAP[idx], "windows": [win_label(a_, b_) for a_, b_ in WINDOWS], "cells": {},
                "day_rows": [x for x in days_out if x["index"] == idx], "tickets": [x for x in tickets if x["index"] == idx], "real": True}
        single = g[g["side"] != "BOTH"]
        sets = {"GO": single[(single["cond"] == "GO") & single["signal_side"]], "LEAN": single[(single["cond"] == "LEAN") & single["signal_side"]],
                "ANY": single, "BOTH": g[g["side"] == "BOTH"]}
        for cond, src in sets.items():
            for k in (0, 1, 2, 3):
                s_k = src[src["k"] == k]
                for a_, b_ in WINDOWS:
                    s = stats(s_k[(s_k["minute"] >= a_) & (s_k["minute"] < b_)])
                    s["signals_per_day"] = (s["n"] / card["days"]) if cond in ("GO", "LEAN") else None
                    card["cells"][f"{cond}|{k}|{win_label(a_, b_)}"] = s
                s = stats(s_k); s["signals_per_day"] = (s["n"] / card["days"]) if cond in ("GO", "LEAN") else None
                card["cells"][f"{cond}|{k}|all"] = s
        cards[idx] = card
    (OUT / "cas_timetable.json").write_text(json.dumps(cards, indent=0, default=float))
    print("\nwrote", OUT / "cas_timetable.csv", OUT / "cas_timetable.json")
    pd.set_option("display.width", 250)
    for idx, card in cards.items():
        print(f"\n[{idx}] any-bar (lottery) auto strike by window, real premiums, {card['days']} CAS expiries:")
        t = pd.DataFrame([{**{"window": w}, **{k: v for k, v in card["cells"][f"ANY|0|{w}"].items() if k in ("n", "p0_med", "p_2x", "p_5x", "net_mean", "p_net_pos", "netset_mean", "p_set_pos")}}
                          for w in card["windows"] if card["cells"][f"ANY|0|{w}"]["n"]])
        print(t.round(1).to_string(index=False))


if __name__ == "__main__":
    main()
