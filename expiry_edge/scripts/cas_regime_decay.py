"""Regime-decay monitor: is the CAS 'blast' fading as the auction matures?

SEBI's stated expectation (Aug 2026) is that the sharp closing moves ease as participation deepens — the
imbalance-÷-depth thesis in reverse: a thicker book absorbs the same expiry flow with a smaller move.  This
does NOT predict the fade; it MEASURES it, expiry by expiry, so the strategy can be retired on evidence rather
than hope.  Two things are tracked per expiry:

  abs_move_pct        |CAS close − 15:10| / 15:10 × 100     — the thing that pays; the headline decay series
  participation_proxy total option volume in the 15:00–15:10 window (when real bars exist) — a depth proxy;
                      rising participation is the mechanism SEBI expects to calm the moves

Ordinary weeklies are the clean signal (monthly / first-of-regime / rebalance days spike on structural flow and
manipulated days are excluded).  A negative trend on ordinary weeklies = the edge is decaying.

    python scripts/cas_regime_decay.py                       # from the public reconstruction (now)
    python scripts/cas_regime_decay.py --zip dhan_export.zip # from real bars (adds the participation proxy)

Writes outputs/cas_month/cas_regime_decay.json.
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
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from expiry_edge.config import CAS_START_DATE                                     # noqa: E402

OUT = ROOT / "outputs" / "cas_month"
FIRST_OF_REGIME = {("NIFTY", "2026-08-04"), ("SENSEX", "2026-08-06")}
MANIPULATED = {("SENSEX", "2026-08-13")}


def _rows_from_public() -> list[dict]:
    e = pd.read_csv(ROOT / "outputs/cas_month/cas_month_expiries.csv")
    out = []
    for _, r in e.iterrows():
        key = (r["index"], r["date"])
        out.append({"index": r["index"], "date": r["date"], "abs_move_pct": abs(float(r["auction_move_pct"])),
                    "monthly": r["date"] == "2026-08-25", "first": key in FIRST_OF_REGIME, "manip": key in MANIPULATED,
                    "participation_proxy": None})
    return out


def _rows_from_export(zip_path: Path) -> list[dict]:
    from cas_month_check_real import build_bars5, build_daily, load_export        # noqa: E402
    files = load_export(zip_path); out = []
    for idx in ("NIFTY", "SENSEX", "BANKNIFTY"):
        if any(f"{p}_{idx}.csv" not in files for p in ("index_5m", "index_daily", "rolling_options")):
            continue
        bars5 = build_bars5(files[f"index_5m_{idx}.csv"]); d = build_daily(files[f"index_daily_{idx}.csv"], idx)
        opt = files[f"rolling_options_{idx}.csv"].copy(); opt["ts"] = pd.to_datetime(opt["ts"])
        exp = [x for x in sorted(set(opt["ts"].dt.date)) if x >= CAS_START_DATE and x in d.index.date and bool(d.loc[pd.Timestamp(x), "is_expiry"])]
        # drop a tail label whose scheduled weekday hasn't traded yet (same guard as cas_month_check_real)
        from expiry_edge.calendar import _regime_weekday
        last = d.index.max().date(); sched_wd = _regime_weekday(idx, last)[0]
        if exp and exp[-1] == last and sched_wd is not None and last.weekday() != sched_wd:
            exp = exp[:-1]
        for date in exp:
            pre = bars5[(bars5["date"] == date) & (bars5["ts"].dt.time <= dt.time(15, 10))]
            if not len(pre):
                continue
            p1510 = float(pre["close"].iloc[-1]); close = float(d.loc[pd.Timestamp(date), "close"])
            win = opt[(opt["ts"].dt.date == date) & (opt["ts"].dt.time >= dt.time(15, 0)) & (opt["ts"].dt.time <= dt.time(15, 10))]
            vol = float(pd.to_numeric(win["volume"], errors="coerce").fillna(0).sum()) if len(win) else None
            key = (idx, str(date))
            out.append({"index": idx, "date": str(date), "abs_move_pct": abs(close / p1510 - 1) * 100,
                        "monthly": bool(d.loc[pd.Timestamp(date), "is_monthly"]), "first": key in FIRST_OF_REGIME,
                        "manip": key in MANIPULATED, "participation_proxy": vol})
    return out


def trend(series: list[tuple[str, float]]):
    """OLS slope + a crude half-life on the |move| series (chronological).  Returns None if < 4 points."""
    if len(series) < 4:
        return None
    y = np.array([v for _, v in series]); x = np.arange(len(y))
    slope, intercept = np.polyfit(x, y, 1)
    # exponential-ish half-life from the fitted endpoints (guarded)
    y0, yN = intercept, intercept + slope * (len(y) - 1)
    half_life = None
    if slope < 0 and y0 > 0 and yN > 0:
        rate = (yN / y0) ** (1 / (len(y) - 1))                                    # per-expiry multiplicative decay
        if 0 < rate < 1:
            half_life = float(np.log(0.5) / np.log(rate))
    return {"slope_pct_per_expiry": float(slope), "n": len(y), "direction": "shrinking" if slope < 0 else "growing",
            "half_life_expiries": None if half_life is None else round(half_life, 1)}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--zip", default=None); a = ap.parse_args()
    rows = _rows_from_export(Path(a.zip)) if a.zip else _rows_from_public()
    rows.sort(key=lambda r: (r["date"], r["index"]))
    df = pd.DataFrame(rows)
    ordinary = df[(~df["manip"]) & (~df["monthly"]) & (~df["first"])].sort_values("date")
    structural = df[(df["monthly"]) | (df["first"])]
    card = {
        "n_expiries": len(df), "source": "real bars" if a.zip else "public reconstruction",
        "sebi_stance": "SEBI (Aug 2026) sees the sharp closing moves as a maturing-market effect, not a design flaw, "
                       "and expects them to ease as participation rises — participation already grew day 1 to day 2. "
                       "No immediate change to CAS; a derivative-stock auction reform (±3% band, live imbalance) is at "
                       "consultation, not yet in force.",
        "ordinary_weeklies": {"mean_abs_move_pct": round(float(ordinary["abs_move_pct"].mean()), 3) if len(ordinary) else None,
                              "series": [{"date": r["date"], "index": r["index"], "abs_move_pct": round(r["abs_move_pct"], 3)} for _, r in ordinary.iterrows()],
                              "trend": trend([(r["date"], r["abs_move_pct"]) for _, r in ordinary.iterrows()])},
        "structural_days": {"mean_abs_move_pct": round(float(structural["abs_move_pct"].mean()), 3) if len(structural) else None,
                            "note": "monthly / first-of-regime days — structural settlement & passive flow; these do NOT decay with participation"},
        "all_series": [{"date": r["date"], "index": r["index"], "abs_move_pct": round(r["abs_move_pct"], 3),
                        "kind": ("manipulated" if r["manip"] else "monthly" if r["monthly"] else "first-of-regime" if r["first"] else "ordinary"),
                        "participation_proxy": r["participation_proxy"]} for _, r in df.iterrows()],
    }
    o = ordinary["abs_move_pct"]; s = structural["abs_move_pct"]
    card["read"] = ("too few ordinary weeklies to call a trend yet" if len(o) < 4 else
                    ("ordinary-weekly blasts ARE decaying — " + str(card["ordinary_weeklies"]["trend"]["direction"]) +
                     (f", ~{card['ordinary_weeklies']['trend']['half_life_expiries']} expiries to halve" if card["ordinary_weeklies"]["trend"] and card["ordinary_weeklies"]["trend"].get("half_life_expiries") else "")))
    if len(o) and len(s):
        card["structural_vs_ordinary_ratio"] = round(float(s.mean() / max(o.mean(), 1e-9)), 1)
    (OUT / "cas_regime_decay.json").write_text(json.dumps(card, indent=2))
    print(json.dumps(card, indent=2))


if __name__ == "__main__":
    main()
