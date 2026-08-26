"""End-to-end analysis: expiry-day anatomy, signal evaluation (buyers), straddle
strategies (sellers/buyers), CAS-regime re-run.  Writes CSV tables + JSON summary +
PNG charts to outputs/.

Usage:  python scripts/run_analysis.py [NIFTY SENSEX BANKNIFTY]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from expiry_edge.anatomy import anatomy_summary, bucket_table, day_stats          # noqa: E402
from expiry_edge.calendar import label_expiry_days                                # noqa: E402
from expiry_edge.config import LIQUID_WEEKLY_FROM, LIQUID_WEEKLY_TO                # noqa: E402
from expiry_edge.evaluate import evaluate_events, summarize                        # noqa: E402
from expiry_edge.features import add_features                                      # noqa: E402
from expiry_edge.options import remaining_share, sigma_day_from_rv, theoretical_decay_curve, variance_profile  # noqa: E402
from expiry_edge.signals import build_events                                       # noqa: E402
from expiry_edge.strategies import long_straddle_pnl, short_straddle_pnl, straddle_paths, summarize_pnl  # noqa: E402

DATA, OUT = ROOT / "data", ROOT / "outputs"
TAB = OUT / "tables"
TAB.mkdir(parents=True, exist_ok=True)
pd.set_option("display.width", 250)

TOP_SIGNALS = ["ORB30", "ORB30_tight", "LateBreak_1415", "RangeExp", "MidBreak_1015_1200", "EMA9x21_trend",
               "RSI_mom", "BB_squeeze_break", "PDH_PDL", "SessMean_cross", "BASELINE_every_bar"]


def save(df: pd.DataFrame, name: str):
    df.to_csv(TAB / f"{name}.csv")
    return df


def run_index(idx: str, summary: dict):
    t0 = time.time()
    m = pd.read_parquet(DATA / f"{idx}_1m.parquet")
    b5 = pd.read_parquet(DATA / f"{idx}_5m.parquet")
    d = pd.read_parquet(DATA / f"{idx}_daily.parquet")
    lo, hi = LIQUID_WEEKLY_FROM[idx], LIQUID_WEEKLY_TO[idx]
    mask = (d.index.date >= lo) & ((d.index.date < hi) if hi else True)
    d_liq = d[mask]
    exp_dates, non_dates = d_liq[d_liq.is_expiry].index, d_liq[~d_liq.is_expiry].index
    S = {"index": idx, "window": [str(d_liq.index.min().date()), str(d_liq.index.max().date())],
         "sessions": int(len(d_liq)), "expiry_days": int(len(exp_dates)),
         "expiry_weekdays": {int(k): int(v) for k, v in exp_dates.dayofweek.value_counts().items()}}

    # ---------------- 1. variance profiles & decay curves
    prof_e, prof_n = variance_profile(m, exp_dates), variance_profile(m, non_dates)
    np.save(OUT / f"{idx}_profile_expiry.npy", prof_e)
    bt = save(bucket_table(prof_e, prof_n), f"{idx}_variance_buckets")
    S["variance_buckets"] = bt.to_dict("records")
    spot = float(d_liq["close"].iloc[-1])
    sig_typ = sigma_day_from_rv(float(d_liq.loc[exp_dates, "rv20"].median()))
    dc_pre = theoretical_decay_curve(sig_typ, spot, idx, remaining_share(prof_e))
    dc_cas = theoretical_decay_curve(sig_typ, spot, idx, remaining_share(prof_e, cas=True))
    dc = pd.DataFrame({"minute": dc_pre["minute"], "preCAS_pct": dc_pre["pct_of_open"], "CAS_pct": dc_cas["pct_of_open"],
                       "preCAS_pts": dc_pre["straddle"], "CAS_pts": dc_cas["straddle"]})
    save(dc, f"{idx}_decay_curve")
    S["decay_curve_checkpoints"] = {f"{9 + (mm + 15) // 60:02d}:{(mm + 15) % 60:02d}":
                                    {"preCAS": round(float(dc_pre.pct_of_open[mm]), 1), "CAS": round(float(dc_cas.pct_of_open[mm]), 1)}
                                    for mm in [0, 60, 135, 225, 300, 345, 360, 370]}
    S["typical_open_straddle_pct_of_spot"] = round(float(dc_pre.straddle[0] / spot * 100), 3)

    # ---------------- 2. anatomy (model free + modelled opening straddle)
    ds = day_stats(m[m["date"].isin(set(d_liq.index.date))], d, idx, prof_e)
    ds = ds[ds.index.isin(d_liq.index)]
    save(ds, f"{idx}_day_stats")
    an = save(anatomy_summary(ds), f"{idx}_anatomy_expiry_vs_non")
    S["anatomy"] = {str(k): v for k, v in an.to_dict("index").items()}
    e = ds[ds.is_expiry]
    by_year = e.groupby(e.index.year).agg(days=("range_pct", "size"), range_pct_med=("range_pct", "median"),
                                          abs_last_hour_med=("move_1430_1525", lambda s: s.abs().median()),
                                          p_last_hour_gt_0_3=("move_1430_1525", lambda s: (s.abs() > 0.3).mean() * 100),
                                          p_range_gt_straddle=("range_gt_straddle", "mean"),
                                          p_close_outside_straddle=("close_outside_straddle", "mean"),
                                          straddle_pct_med=("straddle_open_pct", "median")).round(3)
    save(by_year, f"{idx}_anatomy_expiry_by_year")
    S["anatomy_by_year"] = {str(k): v for k, v in by_year.to_dict("index").items()}
    by_vol = e.groupby("vol_regime", observed=True).agg(days=("range_pct", "size"), range_pct_med=("range_pct", "median"),
                                                        abs_last_hour_med=("move_1430_1525", lambda s: s.abs().median()),
                                                        p_range_gt_straddle=("range_gt_straddle", "mean"),
                                                        p_close_outside_straddle=("close_outside_straddle", "mean")).round(3)
    save(by_vol, f"{idx}_anatomy_expiry_by_volregime")
    # last-hour move vs first-hour range / gap (does a quiet morning predict a loud afternoon?)
    e2 = e.dropna(subset=["fh_range_pct", "range20"]).copy()
    e2["fh_rel"] = pd.qcut(e2["fh_range_pct"] / e2["range20"], 3, labels=["tight", "mid", "wide"])
    fh = e2.groupby("fh_rel", observed=True).agg(days=("range_pct", "size"),
                                                 abs_last_hour_med=("move_1430_1525", lambda s: s.abs().median()),
                                                 p_last_hour_gt_0_3=("move_1430_1525", lambda s: (s.abs() > 0.3).mean() * 100),
                                                 range_1430_1525_med=("range_1430_1525_pct", "median")).round(3)
    save(fh, f"{idx}_lasthour_by_firsthour_width")
    S["lasthour_by_firsthour_width"] = {str(k): v for k, v in fh.to_dict("index").items()}

    # ---------------- 3. signal events & evaluation (buyers)
    f = add_features(b5, d)
    ev = build_events(f)
    ev = ev[ev["date"].isin(set(d_liq.index.date))]
    ev = ev[(ev.signal != "BASELINE_every_bar") | (ev.minute % 15 == 0)]
    res_e = evaluate_events(ev[ev.is_expiry], m, d, idx, prof_e, reactive_iv=True)
    res_n = evaluate_events(ev[~ev.is_expiry], m, d, idx, prof_n, reactive_iv=True)
    res_e_static = evaluate_events(ev[ev.is_expiry], m, d, idx, prof_e, reactive_iv=False)
    res_e_cas = evaluate_events(ev[ev.is_expiry], m, d, idx, prof_e, cas=True, reactive_iv=True)
    res_e["regime"], res_n["regime"], res_e_static["regime"], res_e_cas["regime"] = "expiry", "non_expiry", "expiry_staticIV", "expiry_CASmodel"
    res_e.to_parquet(OUT / f"{idx}_events_expiry.parquet")
    res_n.to_parquet(OUT / f"{idx}_events_nonexpiry.parquet")
    sig_e, sig_n = summarize(res_e), summarize(res_n)
    sig_es, sig_ec = summarize(res_e_static), summarize(res_e_cas)
    sig_all = pd.concat({"expiry": sig_e, "non_expiry": sig_n, "expiry_staticIV": sig_es, "expiry_CASmodel": sig_ec}, names=["regime"])
    save(sig_all, f"{idx}_signals_summary")
    S["signals"] = {reg: {s: r for s, r in tbl.to_dict("index").items()} for reg, tbl in
                    [("expiry", sig_e), ("non_expiry", sig_n), ("expiry_staticIV", sig_es), ("expiry_CASmodel", sig_ec)]}
    # OTM variant on expiry days
    save(summarize(res_e, tag="otm"), f"{idx}_signals_summary_otm_expiry")
    S["signals_otm_expiry"] = summarize(res_e, tag="otm").to_dict("index")
    # baseline by hour bucket (expiry vs non-expiry) – "when does buying work at all?"
    base = pd.concat([res_e, res_n])
    hb = summarize(base[base.signal == "BASELINE_every_bar"], by=("regime", "hour_bucket"))
    save(hb, f"{idx}_baseline_by_hour")
    S["baseline_by_hour"] = {f"{k[0]}|{k[1]}": v for k, v in hb.to_dict("index").items()}
    # top signals by hour bucket on expiry days
    tb = summarize(res_e[res_e.signal.isin(TOP_SIGNALS)], by=("signal", "hour_bucket"))
    save(tb, f"{idx}_signals_by_hour_expiry")
    # by vol regime / gap / OR width for expiry days
    r = res_e.copy()
    r["gap_bucket"] = pd.cut(r["gap_abs"], [-0.01, 0.2, 0.5, 100], labels=["gap<0.2%", "0.2-0.5%", ">0.5%"])
    r["or_bucket"] = pd.cut(r["or30_rel"], [-0.01, 0.4, 0.7, 100], labels=["OR<40%", "40-70%", ">70% of typ range"])
    for col in ["vol_regime", "gap_bucket", "or_bucket"]:
        t = summarize(r[r.signal.isin(TOP_SIGNALS)], by=("signal", col))
        save(t, f"{idx}_signals_by_{col}_expiry")
        S[f"signals_by_{col}"] = {f"{k[0]}|{k[1]}": v for k, v in t.to_dict("index").items()}
    # by year for stability
    r["year"] = pd.to_datetime(r["date"]).dt.year
    ty = summarize(r[r.signal.isin(["ORB30", "LateBreak_1415", "RangeExp", "BASELINE_every_bar"])], by=("signal", "year"))
    save(ty, f"{idx}_signals_by_year_expiry")
    S["signals_by_year"] = {f"{k[0]}|{k[1]}": v for k, v in ty.to_dict("index").items()}
    # direction split for late breakout (up vs down)
    tdir = summarize(res_e[res_e.signal.isin(["LateBreak_1415", "ORB30", "RangeExp"])], by=("signal", "direction"))
    save(tdir, f"{idx}_signals_by_direction_expiry")
    S["signals_by_direction"] = {f"{k[0]}|{k[1]}": v for k, v in tdir.to_dict("index").items()}

    # VRP sensitivity: does the buyer edge survive if options are priced 25% richer?
    sens = {}
    for vrp in (1.0, 1.25):
        rr = evaluate_events(ev[ev.is_expiry & ev.signal.isin(["ORB30", "LateBreak_1415", "RangeExp", "BASELINE_every_bar"])],
                             m, d, idx, prof_e, reactive_iv=True, vrp=vrp)
        sens[f"vrp{vrp}"] = summarize(rr)
    sens = pd.concat(sens, names=["vrp"])
    save(sens, f"{idx}_signals_vrp_sensitivity_expiry")
    S["signals_vrp_sensitivity"] = {f"{k[0]}|{k[1]}": v for k, v in sens.to_dict("index").items()}

    # ---------------- 4. straddle strategies on expiry days
    paths = straddle_paths(m, d, idx, prof_e, exp_dates)
    paths_cas = straddle_paths(m, d, idx, prof_e, exp_dates, cas=True)
    out = {}
    for lab, sl in [("noSL", None), ("SL30", 0.30), ("SL50", 0.50)]:
        p = short_straddle_pnl(paths, idx, exit_minute=360, sl_pct=sl)
        p["vol_regime"] = p["date"].map(dict(zip(d.index.date, d["vol_regime"])))
        out[f"short_{lab}"] = summarize_pnl(p)
        out[f"short_{lab}_by_vol"] = summarize_pnl(p[p.entry_minute == 5], by=("vol_regime",))
        p["year"] = pd.to_datetime(p["date"]).dt.year
        out[f"short_{lab}_by_year"] = summarize_pnl(p[p.entry_minute == 5], by=("year",))
        if lab == "SL30":
            pc = short_straddle_pnl(paths_cas, idx, exit_minute=360, sl_pct=sl)
            out["short_SL30_CASmodel"] = summarize_pnl(pc)
    for vrp in (1.0, 1.25):
        pv = straddle_paths(m, d, idx, prof_e, exp_dates, entry_minutes=(5,), vrp=vrp)
        out[f"short_SL30_vrp{vrp}"] = summarize_pnl(short_straddle_pnl(pv, idx, exit_minute=360, sl_pct=0.30))
    lp = long_straddle_pnl(paths, idx, exit_minute=370)
    lp["vol_regime"] = lp["date"].map(dict(zip(d.index.date, d["vol_regime"])))
    out["long_to_1525"] = summarize_pnl(lp)
    out["long_to_1525_by_vol"] = summarize_pnl(lp[lp.entry_minute.isin([315, 345])], by=("entry_minute", "vol_regime"))
    lp_cas = long_straddle_pnl(paths_cas, idx, exit_minute=360)
    out["long_to_1515_CASmodel"] = summarize_pnl(lp_cas)
    for k, tbl in out.items():
        save(tbl, f"{idx}_straddle_{k}")
    S["straddles"] = {k: {str(i): v for i, v in tbl.to_dict("index").items()} for k, tbl in out.items()}
    S["elapsed_s"] = round(time.time() - t0, 1)
    summary[idx] = S
    print(f"[{idx}] done in {S['elapsed_s']}s: {S['sessions']} sessions, {S['expiry_days']} expiry days")
    return S


def run_tuesday_proxy(summary: dict):
    """BANKNIFTY minute data on NIFTY's *Tuesday* expiry days (Sep-2025..Apr-2026) vs other
    days in the same window – a proxy check that the new Tuesday regime shows the same
    expiry-day anatomy (BANKNIFTY itself has no weekly expiry any more)."""
    idx = "BANKNIFTY"
    m = pd.read_parquet(DATA / f"{idx}_1m.parquet")
    d = pd.read_parquet(DATA / f"{idx}_daily.parquet")
    win = d[d.index >= "2025-09-01"].copy()
    cal_n = label_expiry_days(win.index, "NIFTY")
    win["is_expiry"] = cal_n["is_expiry"].reindex(win.index).fillna(False).astype(bool)
    win["is_monthly"] = cal_n["is_monthly"].reindex(win.index).fillna(False).astype(bool)
    exp_dates, non_dates = win[win.is_expiry].index, win[~win.is_expiry].index
    prof_e, prof_n = variance_profile(m, exp_dates), variance_profile(m, non_dates)
    bt = save(bucket_table(prof_e, prof_n), "PROXY_BANKNIFTY_on_NIFTY_Tuesday_variance_buckets")
    ds = day_stats(m[m["date"].isin(set(win.index.date))], win, idx, prof_e)
    an = save(anatomy_summary(ds), "PROXY_BANKNIFTY_on_NIFTY_Tuesday_anatomy")
    summary["PROXY_TUESDAY"] = {"note": "BANKNIFTY 1-min bars, 2025-09-01..2026-04-22; expiry flag = NIFTY Tuesday expiry",
                                "expiry_days": int(len(exp_dates)), "other_days": int(len(non_dates)),
                                "variance_buckets": bt.to_dict("records"),
                                "anatomy": {str(k): v for k, v in an.to_dict("index").items()}}
    print(f"[PROXY] Tuesday-regime proxy: {len(exp_dates)} NIFTY-expiry Tuesdays vs {len(non_dates)} other days")


if __name__ == "__main__":
    indices = sys.argv[1:] or ["NIFTY", "SENSEX", "BANKNIFTY"]
    summary = {}
    for idx in indices:
        run_index(idx, summary)
    if "BANKNIFTY" in indices:
        run_tuesday_proxy(summary)
    with open(OUT / "summary.json", "w") as fh:
        json.dump(summary, fh, indent=1, default=lambda o: float(o) if isinstance(o, (np.floating, np.integer)) else str(o))
    print("wrote", OUT / "summary.json")
