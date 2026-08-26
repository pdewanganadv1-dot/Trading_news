"""Expiry-day OTM timetable: WHICH strike and WHEN, from the OTM blast outcomes.

    python scripts/otm_timetable.py [NIFTY BANKNIFTY SENSEX]

For every 30-minute entry window of an expiry day and every strike choice (1/2/3 strikes OTM and the
indicator's 'auto' strike = nearest OTM priced <= cap, never the 3rd), it tabulates what happened to
an option bought in the breakout direction when the buy-score said GO (>= 0.40), LEAN (0.30-0.40),
and — for contrast — at any random bar (the lottery), under CAS pricing (exit <= 60 min and by 15:15).

Writes outputs/otm/<IDX>_timetable.csv (long format) and outputs/otm/timetable.json (for the card).
Test expiry days only (2022+ for NIFTY/BANKNIFTY; SENSEX has a small Friday-regime sample).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from expiry_edge.otm import PREMIUM_FLOOR                                  # noqa: E402

OUT = ROOT / "outputs" / "otm"
CAP = {"NIFTY": 10.0, "BANKNIFTY": 25.0, "SENSEX": 30.0}
LAST_ENTRY_MINUTE = 340                                    # bar closing 14:55 is the last entry under CAS
WINDOWS = [(0, 30), (30, 60), (60, 90), (90, 120), (120, 150), (150, 180), (180, 210), (210, 240), (240, 270), (270, 300), (300, 341)]


def win_label(a: int, b: int) -> str:
    def t(m):
        m = min(m, LAST_ENTRY_MINUTE)                         # the last window ends at the 14:55 entry cutoff
        return f"{9 + (m + 15) // 60:02d}:{(m + 15) % 60:02d}"
    return f"{t(a)}–{t(b)}"


def auto_rows(o: pd.DataFrame, index: str) -> pd.DataFrame:
    """One row per (date, minute, side): the indicator's strike choice (nearest OTM <= cap, else 1-OTM; never k=3)."""
    o = o.sort_values(["date", "minute", "side", "k"])
    cheap = o[(o["p0"] <= CAP[index]) & (o["k"] <= 2)]
    first_cheap = cheap.groupby(["date", "minute", "side"], sort=False).head(1)
    k1 = o[o["k"] == 1]
    key = ["date", "minute", "side"]
    missing = k1.merge(first_cheap[key], on=key, how="left", indicator=True)
    missing = missing[missing["_merge"] == "left_only"].drop(columns="_merge")
    auto = pd.concat([first_cheap, missing], ignore_index=True)
    auto["k_chosen"] = auto["k"]; auto["k"] = 0
    return auto


def stats(g: pd.DataFrame) -> dict:
    n = len(g)
    if n == 0:
        return {"n": 0}
    return {"n": int(n),
            "p0_med": float(g["p0"].median()), "otm_pts_med": float(g["otm_pts"].median()),
            "p_2x": float((g["mult60"] >= 2).mean() * 100), "p_3x": float((g["mult60"] >= 3).mean() * 100),
            "p_5x": float((g["mult60"] >= 5).mean() * 100), "p_10x": float((g["mult60"] >= 10).mean() * 100),
            "net_mean": float(g["net60"].mean() * 100), "net_median": float(g["net60"].median() * 100),
            "p_net_pos": float((g["net60"] > 0).mean() * 100), "p_lose_half": float((g["net60"] <= -0.5).mean() * 100),
            "take5_mean": float(g["net_take5"].mean() * 100), "take3_mean": float(g["net_take3"].mean() * 100),
            "k_share": (g["k_chosen"].value_counts(normalize=True).round(2).to_dict() if "k_chosen" in g else None)}


def build(index: str) -> tuple[pd.DataFrame, dict]:
    o = pd.read_parquet(OUT / f"{index}_otm_outcomes_CAS.parquet")
    o = o[o["minute"] <= LAST_ENTRY_MINUTE].copy()
    o["date"] = pd.to_datetime(o["date"])
    ndays = o["date"].nunique()
    side_ok = ((o["new_high"] == 1) & (o["side"] == "CE")) | ((o["new_low"] == 1) & (o["side"] == "PE"))
    auto = auto_rows(o, index)
    auto_ok = ((auto["new_high"] == 1) & (auto["side"] == "CE")) | ((auto["new_low"] == 1) & (auto["side"] == "PE"))
    sets = {"GO": (o[side_ok & (o["score"] >= 0.40)], auto[auto_ok & (auto["score"] >= 0.40)]),
            "LEAN": (o[side_ok & (o["score"] >= 0.30) & (o["score"] < 0.40)], auto[auto_ok & (auto["score"] >= 0.30) & (auto["score"] < 0.40)]),
            "ANY": (o, auto)}
    rows, card = [], {"index": index, "days": int(ndays), "cap": CAP[index], "floor": PREMIUM_FLOOR[index], "windows": [], "cells": {}}
    for a, b in WINDOWS:
        card["windows"].append(win_label(a, b))
    for cond, (base, au) in sets.items():
        for k in (0, 1, 2, 3):
            src = au if k == 0 else base[base["k"] == k]
            for a, b in WINDOWS:
                g = src[(src["minute"] >= a) & (src["minute"] < b)]
                s = stats(g)
                s["signals_per_day"] = float(len(g) / ndays) if cond != "ANY" else None
                rows.append({"index": index, "cond": cond, "k": k, "window": win_label(a, b), **{kk: vv for kk, vv in s.items() if kk != "k_share"},
                             "k_share": json.dumps(s.get("k_share")) if s.get("k_share") else ""})
                card["cells"][f"{cond}|{k}|{win_label(a, b)}"] = s
            # whole-day summary
            s = stats(src); s["signals_per_day"] = float(len(src) / ndays) if cond != "ANY" else None
            rows.append({"index": index, "cond": cond, "k": k, "window": "all", **{kk: vv for kk, vv in s.items() if kk != "k_share"},
                         "k_share": json.dumps(s.get("k_share")) if s.get("k_share") else ""})
            card["cells"][f"{cond}|{k}|all"] = s
    return pd.DataFrame(rows), card


def main():
    cards = {}
    for idx in sys.argv[1:] or ["NIFTY", "BANKNIFTY", "SENSEX"]:
        if not (OUT / f"{idx}_otm_outcomes_CAS.parquet").exists():
            print(f"[{idx}] no outcomes — run scripts/run_otm.py {idx} first"); continue
        t, card = build(idx)
        t.to_csv(OUT / f"{idx}_timetable.csv", index=False)
        cards[idx] = card
        print(f"\n[{idx}] {card['days']} test expiry days — GO signals, auto strike, by entry window (CAS pricing, exit <= 60 min / 15:15)")
        v = t[(t["cond"] == "GO") & (t["k"] == 0)][["window", "n", "signals_per_day", "p0_med", "p_2x", "p_5x", "p_10x", "net_mean", "p_net_pos", "p_lose_half"]]
        print(v.round(2).to_string(index=False))
    (OUT / "timetable.json").write_text(json.dumps(cards, indent=0))
    print("\nwrote", OUT / "timetable.json")


if __name__ == "__main__":
    main()
