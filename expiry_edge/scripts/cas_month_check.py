"""CAS-month check (Aug 2026) from PUBLIC data only.

The sandbox cannot reach NSE/BSE/Yahoo, so this reconstructs each expiry day from
published anchors (NSE daily index files, Business Standard live-blog timestamps,
Zerodha Aftermarket / Weekly Market Metrics, Reuters, SEBI's 19-Aug order) and asks:
  1. did the day produce a qualifying breakout before the 15:00 cutoff?
  2. what would the buy-score have read there (range: unknown micro-features at the
     25th/50th/75th percentile of real breakout bars)?
  3. what would the ATM / 1-OTM / 2-OTM option have done — at 15:15 (model, CAS pricing,
     the mandatory exit) and at settlement (the actual CAS close) — and how does that
     compare with the option prints that were published?
Every anchor carries its source in ANCHORS below.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from expiry_edge.config import CONTRACT, COST_PER_SIDE                                   # noqa: E402
from expiry_edge.options import bs_price, remaining_share, sigma_day_from_rv             # noqa: E402
from expiry_edge.otm import PREMIUM_FLOOR                                                 # noqa: E402
from expiry_edge.score import BuyScore                                                    # noqa: E402

OUT = ROOT / "outputs" / "cas_month"; OUT.mkdir(parents=True, exist_ok=True)
pd.set_option("display.width", 250)


def m_of(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return (h - 9) * 60 + m - 15


# ----------------------------------------------------------------------------------------
# Anchors. Levels are index points; times IST. "sig" = the candidate signal bar (first close
# beyond the day's range after 13:00 and before the 15:00 cutoff), None if there was none.
# ----------------------------------------------------------------------------------------
ANCHORS = [
    dict(index="NIFTY", date="2026-08-04", label="Tue 4 Aug — first CAS weekly expiry",
         prev_close=24774.30, open=24703.90, high=24703.90, low_pre_auction=24427.95, p1515=24463.45, close=24614.90,
         path=[("09:15", 24703.9), ("10:25", 24600), ("12:00", 24510), ("14:00", 24430), ("15:15", 24463.45)],
         range20=0.78, rv20=0.52, or_width_pts=90,
         sig=dict(time="13:40", side="PE", spot=24445, day_high=24703.9, day_low=24445, note="trend day: new lows through the afternoon; low 24,428 ~14:00"),
         published=["ATM straddle ~95 at open, ~100 at 15:15 (Zerodha wk32)",
                    "pre-auction 24600 PE ₹55-60, 24600 CE ₹10-15; settled CE 14.90 / PE 0.05 (Share.Market)",
                    "24400 CE ₹97 → ₹217 through the auction (multibagg)", "auction +151 pts (Reuters)"],
         src=["NSE ind_close_all_04082026.csv", "Zerodha Aftermarket 4 Aug", "BS live blog", "Share.Market '3:15 PM Puzzle'"]),
    dict(index="NIFTY", date="2026-08-11", label="Tue 11 Aug — second weekly expiry",
         prev_close=24583.80, open=24575.10, high=24576.85, low_pre_auction=24429.25, p1515=24450.25, close=24471.70,
         path=[("09:18", 24514.6), ("10:15", 24475), ("12:00", 24430), ("14:30", 24440), ("15:06", 24464.95), ("15:15", 24450.25)],
         range20=0.67, rv20=0.43, or_width_pts=100,
         sig=None, sig_note="low printed ~12:00 (24,429); the 14:30 dip to ~24,440 did not make a new low → no afternoon breakout",
         published=["auction +21 pts (BS)", "no straddle series published (Zerodha skipped week 33)"],
         src=["NSE ind_close_all_11082026.csv", "Zerodha Aftermarket 11 Aug", "BS live blog"]),
    dict(index="NIFTY", date="2026-08-18", label="Tue 18 Aug — third weekly expiry",
         prev_close=24287.65, open=24223.85, high=24269.65, low_pre_auction=24166.35, p1515=24166.35, close=24154.90,
         path=[("09:15", 24224), ("10:30", 24195), ("11:30", 24175), ("12:00", 24220), ("14:00", 24200), ("15:00", 24195.2), ("15:15", 24166.35)],
         range20=0.64, rv20=0.40, or_width_pts=70,
         sig=None, sig_note="range-bound (105 pts); the only new low came in the 15:00-15:15 sell-off — AFTER the cutoff",
         late_trap=dict(time="15:10", side="PE", spot=24168),
         published=["ATM straddle 88 at open, 44 at 15:15 (Zerodha wk34)", "auction −11 pts; CAS close = day low"],
         src=["NSE ind_close_all_18082026.csv", "Zerodha Aftermarket 18 Aug", "BS live blog"]),
    dict(index="NIFTY", date="2026-08-25", label="Tue 25 Aug — first monthly expiry under CAS",
         prev_close=24219.05, open=24175.75, high=24260.05, low_pre_auction=24115.45, p1515=24260.05, close=24334.55,
         path=[("09:15", 24175.75), ("11:00", 24130), ("12:25", 24173.95), ("14:30", 24172.25), ("15:00", 24246.2), ("15:15", 24260.05)],
         range20=0.58, rv20=0.37, or_width_pts=80,
         sig=dict(time="14:45", side="CE", spot=24215, day_high=24205, day_low=24115.45, note="rally from 24,172 (14:30) to 24,246 (15:00) crossed the morning high (~24,200) around 14:45"),
         published=["auction +74.5 pts; CAS close = day high (BS/Reuters)", "no straddle series yet (Zerodha wk35 not out)"],
         src=["NSE ind_close_all_25082026.csv", "BS 12:25 / 14:30 / live blog", "CNBC-TV18"]),
    dict(index="SENSEX", date="2026-08-06", label="Thu 6 Aug — first SENSEX weekly expiry under CAS",
         prev_close=78581.00, open=78730.38, high=78900, low_pre_auction=78620, p1515=78785.62, close=78954.76,
         path=[("09:17", 78730.38), ("11:30", 78849.34), ("15:00", 78816.58), ("15:10", 78785.62)],
         range20=0.75, rv20=0.50, or_width_pts=150,
         sig=None, sig_note="range < 300 pts all session; no afternoon breakout reported",
         published=["ATM straddle ~500 open, ~700 peak, ~500 at 15:15 (Zerodha wk32)", "indicative SENSEX swung 78,600-79,450 in the auction; close +169",
                    "79,000 CE reportedly ₹100 → ₹330 → 0 inside the auction (multibagg, uncorroborated)"],
         src=["BS live blog 6 Aug", "Zerodha wk32", "Kotak Neo"]),
    dict(index="SENSEX", date="2026-08-13", label="Thu 13 Aug — the manipulated expiry (SEBI order 19 Aug)",
         prev_close=77966.35, open=78111.91, high=78119.39, low_pre_auction=77665.89, p1515=77861.48, close=78079.96,
         path=[("09:15", 78111.91), ("12:30", 77984.46), ("13:25", 77853.26), ("14:15", 77700), ("15:15", 77861.48)],
         range20=0.75, rv20=0.48, or_width_pts=150,
         sig=dict(time="14:05", side="PE", spot=77730, day_high=78119.39, day_low=77730, note="afternoon slide to the day low 77,666 (time not published; between 13:25 and 15:00)"),
         published=["reference 77,829.60 at 15:15; close 78,079.96 (+250) after three engineered spikes (SEBI)",
                    "78000 CE settled ₹80 (from ~₹2 per multibagg); 77800/77900/78000 PE traded ₹16 / ₹42 / ₹108 in the auction and expired at 0 (SEBI)"],
         src=["SEBI ex-parte order", "BS live blog", "Zerodha Aftermarket 13 Aug"]),
    dict(index="SENSEX", date="2026-08-20", label="Thu 20 Aug — first expiry after the SEBI order",
         prev_close=76909.68, open=77468.45, high=77611.11, low_pre_auction=77380, p1515=77485.29, close=77537.72,
         path=[("09:15", 77468.45), ("09:30", 77409.37), ("11:30", 77395.23), ("13:00", 77611.11), ("15:00", 77542.35), ("15:15", 77485.29)],
         range20=0.75, rv20=0.47, or_width_pts=140,
         sig=dict(time="12:40", side="CE", spot=77560, day_high=77530, day_low=77380, note="gap-up day; ORB/day-high break around 12:30-13:00 (high 77,611 ~13:00), then range-bound"),
         published=["ATM straddle 266 open, ~138 at 15:15 (Zerodha wk34)", "auction +52 pts"],
         src=["BS live blog 20 Aug", "Zerodha wk34", "HDFC Sky", "bbntimes"]),
]

QUANT = pd.read_csv(ROOT / "outputs" / "model" / "breakout_bar_feature_quantiles.csv", index_col=0)
RV_RANK = {"NIFTY": 0.10, "SENSEX": 0.15}       # Aug-2026 trailing vol sits near the bottom of its 1-year range (VIX ~11-12)


def score_range(model: BuyScore, a: dict, sig: dict) -> tuple[float, float, float]:
    """Score at the signal bar with the unobservable micro-features at the 25/50/75th pct of real breakout bars."""
    m0 = m_of(sig["time"]) + 4
    rng = sig["day_high"] - sig["day_low"]
    park = np.log(sig["day_high"] / sig["day_low"]) ** 2 / (4 * np.log(2))          # realised variance so far (Parkinson)
    prof = np.load(ROOT / "outputs" / "NIFTY_profile_expiry.npy")
    cum = np.cumsum(prof)
    sigma0 = sigma_day_from_rv(a["rv20"])
    var_spent = park / (sigma0 ** 2 * cum[min(m0, 374)])
    out = []
    for qcol in ["0.25", "0.5", "0.75"]:
        q = QUANT[qcol]
        row = {"tod": m0 / 375, "tod2": (m0 / 375) ** 2, "is_expiry": 1, "tod_x_expiry": m0 / 375,
               "gap_abs": min(abs(a["open"] / a["prev_close"] - 1) * 100, 3), "rv20_rank": RV_RANK[a["index"]],
               "or30_rel": min(a["or_width_pts"] / a["open"] * 100 / a["range20"], 3),
               "range_sofar_rel": min(rng / a["open"] * 100 / a["range20"], 4),
               "pos_edge": 1.0, "dist_edge_atr": 0.0,
               "abs_ret15": q["abs_ret15"], "abs_ret30": q["abs_ret30"], "bar_range_atr": q["bar_range_atr"],
               "bb_bw_pct": q["bb_bw_pct"], "abs_ema_spread_atr": q["abs_ema_spread_atr"],
               "log_var_spent": np.log(min(max(var_spent, 0.05), 20)), "breakout": 1, "or_break": 0}
        out.append(float(model.score(pd.DataFrame([row]))[0]))
    return tuple(out)


def option_paths(a: dict, sig: dict):
    """Model premiums (CAS pricing) at the signal and at 15:15; settlement from the actual CAS close."""
    idx = a["index"]; step = CONTRACT[idx]["strike_step"]; floor = PREMIUM_FLOOR[idx]; cost = COST_PER_SIDE[idx]
    prof = np.load(ROOT / "outputs" / "NIFTY_profile_expiry.npy")
    R = remaining_share(prof, cas=True); cum = np.cumsum(prof)
    sigma0 = sigma_day_from_rv(a["rv20"])
    m0 = m_of(sig["time"]) + 4
    park = np.log(sig["day_high"] / sig["day_low"]) ** 2 / (4 * np.log(2))
    sig_hat2 = park / cum[min(m0, 374)]
    sig_now = float(np.clip(np.sqrt(0.5 * sigma0 ** 2 + 0.5 * sig_hat2), 0.5 * sigma0, 3 * sigma0))
    S0 = sig["spot"]; sgn = 1 if sig["side"] == "CE" else -1
    K0 = float(np.round(S0 / step) * step)
    rows = []
    for k in (0, 1, 2):
        K = K0 + sgn * k * step
        p_entry = max(float(bs_price(S0, K, sig_now ** 2 * R[m0 + 1], sig["side"])), floor)
        p_1515 = max(float(bs_price(a["p1515"], K, sig_now ** 2 * R[361], sig["side"])), floor)    # auction reserve still in
        settle = max(sgn * (a["close"] - K), 0.0)
        rows.append({"k": k, "strike": K, "entry_est": p_entry, "at_1515_est": p_1515, "settle_actual": settle,
                     "net_1515_pct": ((p_1515 - cost) / (p_entry + cost) - 1) * 100,
                     "net_settle_pct": ((settle - cost) / (p_entry + cost) - 1) * 100,
                     "mult_settle": settle / p_entry})
    return pd.DataFrame(rows), sig_now


def auction_lottery(a: dict):
    """The CAS-specific bet: at 15:15 buy the nearest OTM CE and PE; settle on the CAS close."""
    idx = a["index"]; step = CONTRACT[idx]["strike_step"]; floor = PREMIUM_FLOOR[idx]; cost = COST_PER_SIDE[idx]
    prof = np.load(ROOT / "outputs" / "NIFTY_profile_expiry.npy"); R = remaining_share(prof, cas=True)
    sigma0 = sigma_day_from_rv(a["rv20"])
    S = a["p1515"]; K0 = float(np.round(S / step) * step)
    out = {}
    for side, sgn in (("CE", 1), ("PE", -1)):
        K = K0 + sgn * step if sgn * (K0 - S) <= 0 else K0          # nearest strictly-OTM strike
        if sgn * (K - S) <= 0:
            K += sgn * step
        p = max(float(bs_price(S, K, sigma0 ** 2 * R[361], side)), floor)
        settle = max(sgn * (a["close"] - K), 0.0)
        out[side] = {"strike": K, "otm_pts": sgn * (K - S), "p1515_est": p, "settle": settle, "net_pct": ((settle - cost) / (p + cost) - 1) * 100}
    return out


def main():
    model = BuyScore()
    summary = []
    lottery = []
    for a in ANCHORS:
        auc = a["close"] - a["p1515"]
        line = {"index": a["index"], "date": a["date"], "gap_pct": (a["open"] / a["prev_close"] - 1) * 100,
                "pre_auction_range_pct": (a["high"] - a["low_pre_auction"]) / a["open"] * 100, "range20_pct": a["range20"],
                "p1515": a["p1515"], "cas_close": a["close"], "auction_move": auc, "auction_move_pct": auc / a["p1515"] * 100}
        print("=" * 110); print(f"{a['label']}  [{a['index']} {a['date']}]")
        print(f"  gap {line['gap_pct']:+.2f}%  pre-auction range {line['pre_auction_range_pct']:.2f}% (typical day {a['range20']:.2f}%)  "
              f"15:15 {a['p1515']:.2f} → CAS close {a['close']:.2f}  auction {auc:+.2f} pts ({line['auction_move_pct']:+.2f}%)")
        if a.get("sig"):
            s = a["sig"]; lo, mid, hi = score_range(model, a, s)
            verdict = "GO" if mid >= 0.40 else ("LEAN" if mid >= 0.30 else "NO")
            line.update({"signal_time": s["time"], "side": s["side"], "score_lo": lo, "score_mid": mid, "score_hi": hi, "verdict": verdict})
            print(f"  candidate breakout {s['time']} {s['side']} at {s['spot']}: {s['note']}")
            print(f"  buy-score ≈ {mid:.2f} (range {lo:.2f}–{hi:.2f}) → {verdict}")
            tbl, sig_now = option_paths(a, s)
            print(f"  model day-vol now {sig_now*100:.2f}% (prior {sigma_day_from_rv(a['rv20'])*100:.2f}%). Option paths (CAS pricing):")
            print(tbl.round(2).to_string(index=False))
            line.update({f"k{int(r.k)}_entry": r.entry_est for r in tbl.itertuples()})
            line.update({f"k{int(r.k)}_net1515": r.net_1515_pct for r in tbl.itertuples()})
            line.update({f"k{int(r.k)}_netsettle": r.net_settle_pct for r in tbl.itertuples()})
        else:
            line.update({"signal_time": None, "verdict": "no signal"})
            print(f"  no qualifying breakout before the 15:00 cutoff — {a['sig_note']}")
            if a.get("late_trap"):
                t = a["late_trap"]; step = CONTRACT[a["index"]]["strike_step"]
                K0 = float(np.round(t["spot"] / step) * step); K = K0 - step
                print(f"  (after-cutoff trap: {t['time']} new low at {t['spot']} → {K:.0f} PE would have settled at "
                      f"{max(K - a['close'], 0):.2f} on the {a['close']:.2f} close)")
        lot = auction_lottery(a)
        for side in ("CE", "PE"):
            lottery.append({"index": a["index"], "date": a["date"], "side": side, **lot[side]})
        print(f"  15:15 auction lottery (nearest OTM, model 15:15 price → actual settlement): "
              f"CE {lot['CE']['strike']:.0f} ~{lot['CE']['p1515_est']:.1f} → {lot['CE']['settle']:.2f} ({lot['CE']['net_pct']:+.0f}%) | "
              f"PE {lot['PE']['strike']:.0f} ~{lot['PE']['p1515_est']:.1f} → {lot['PE']['settle']:.2f} ({lot['PE']['net_pct']:+.0f}%)")
        print("  published: " + " | ".join(a["published"]))
        summary.append(line)
    S = pd.DataFrame(summary); L = pd.DataFrame(lottery)
    S.to_csv(OUT / "cas_month_expiries.csv", index=False); L.to_csv(OUT / "cas_month_auction_lottery.csv", index=False)
    print("\n" + "=" * 110)
    print("MONTH SUMMARY")
    print(S[["index", "date", "gap_pct", "pre_auction_range_pct", "auction_move", "auction_move_pct", "signal_time", "side", "score_mid", "verdict"]].round(2).to_string(index=False))
    print("\nauction moves (15:15 → close):", [f"{r.index} {r.date[5:]}: {r.auction_move:+.0f}" for r in S.itertuples()])
    print("mean |auction move| %:", round(S.auction_move_pct.abs().mean(), 2), "| positive:", int((S.auction_move > 0).sum()), "of", len(S))
    print("\n15:15 lottery, nearest OTM CE vs PE (net % per ticket, model 15:15 price, actual settlement):")
    print(L.pivot(index=["index", "date"], columns="side", values="net_pct").round(0).to_string())
    print("mean net: CE", round(L[L.side == "CE"].net_pct.mean(), 0), "% | PE", round(L[L.side == "PE"].net_pct.mean(), 0), "%")


if __name__ == "__main__":
    main()
