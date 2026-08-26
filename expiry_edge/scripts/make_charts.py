"""Charts for the report (PNG + SVG) from outputs/tables.  Palette: dataviz reference
palette (blue/orange/aqua categorical, blue sequential); thin marks, recessive grid."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TAB, CH = ROOT / "outputs" / "tables", ROOT / "outputs" / "charts"
CH.mkdir(parents=True, exist_ok=True)

BLUE, ORANGE, AQUA, YELLOW, MAGENTA, VIOLET, RED = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#4a3aa7", "#e34948"
INK, MUTED, GRID = "#52514e", "#898781", "#d8d7d0"
plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
    "xtick.color": INK, "ytick.color": INK, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
    "figure.facecolor": "none", "axes.facecolor": "none", "savefig.facecolor": "none",
    "legend.frameon": False, "axes.titlecolor": INK, "axes.titleweight": "bold", "axes.titlesize": 11,
})


def save(fig, name):
    fig.tight_layout()
    fig.savefig(CH / f"{name}.png", dpi=160, transparent=True)
    fig.savefig(CH / f"{name}.svg", transparent=True)
    plt.close(fig)


def chart_variance_profiles():
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.6), sharey=False)
    for ax, idx in zip(axes, ["NIFTY", "BANKNIFTY", "SENSEX"]):
        t = pd.read_csv(TAB / f"{idx}_variance_buckets.csv", index_col=0)
        x = np.arange(len(t))
        ax.bar(x - 0.2, t["expiry_var_per_min_bp"], 0.38, color=BLUE, label="expiry day")
        ax.bar(x + 0.2, t["nonexpiry_var_per_min_bp"], 0.38, color=ORANGE, label="other days")
        ax.set_xticks(x)
        ax.set_xticklabels([w.replace("-", "\n") for w in t["window"]], fontsize=7)
        ax.set_title(idx)
        ax.set_ylabel("share of day's variance per minute (bp)" if idx == "NIFTY" else "")
        ax.yaxis.grid(True); ax.xaxis.grid(False)
    axes[0].legend(loc="upper right")
    fig.suptitle("Where in the day does the index move? (share of the day's variance per minute, bp)", color=INK, fontweight="bold", fontsize=11)
    save(fig, "variance_profiles")


def chart_decay_curves():
    t = pd.read_csv(TAB / "NIFTY_decay_curve.csv", index_col=0)
    fig, ax = plt.subplots(figsize=(7.5, 3.8))
    mins = t["minute"].values
    ax.plot(mins, t["preCAS_pct"], color=BLUE, lw=2, label="pre-CAS (index trades to 15:30)")
    ax.plot(mins, t["CAS_pct"], color=ORANGE, lw=2, label="CAS regime (auction share 25%)")
    # observed points (Zerodha weekly market metrics)
    obs = [(360, 105, "NIFTY 4 Aug 26 (15:15)", ORANGE, 8), (360, 50, "NIFTY 18 Aug 26 (15:15)", ORANGE, -16),
           (360, 52, "SENSEX 20 Aug 26 (15:15)", ORANGE, 10), (135, 63, "NIFTY 23 Jun 26 (11:30, pre-CAS)", BLUE, 8)]
    for m, y, lab, c, dy in obs:
        ax.scatter([m], [y], s=46, facecolor="white", edgecolor=c, lw=2, zorder=5)
        ax.annotate(lab, (m, y), xytext=(-8, dy), textcoords="offset points", fontsize=7.5, color=INK, ha="right")
    ax.axvline(360, color=MUTED, lw=0.8, ls="--")
    ax.text(361, 92, "15:15 cash\nfreezes (CAS)", fontsize=7.5, color=MUTED)
    ticks = [0, 60, 120, 180, 240, 300, 360]
    ax.set_xticks(ticks)
    ax.set_xticklabels([f"{9 + (m + 15) // 60:02d}:{(m + 15) % 60:02d}" for m in ticks])
    ax.set_ylabel("ATM straddle, % of 09:15 value (no index move)")
    ax.set_title("0DTE ATM straddle time-decay: model vs observed")
    ax.set_ylim(0, 115)
    ax.legend(loc="lower left")
    save(fig, "decay_curves")


def chart_signal_edge():
    order = ["LateBreak_1415", "RangeExp", "ORB30", "ORB30_tight", "MidBreak_1015_1200", "BB_squeeze_break",
             "ORB15", "SessMean_cross", "RSI_mom", "PDH_PDL", "EMA9x21_trend", "EMA9x21", "RSI_rev"]
    labels = {"LateBreak_1415": "Late-day range break (after 14:15)", "RangeExp": "Range-expansion bar (>2.5x ATR)",
              "ORB30": "Opening-range break (30m)", "ORB30_tight": "ORB30, tight range", "MidBreak_1015_1200": "Day-range break 10:15-12:00",
              "BB_squeeze_break": "Bollinger squeeze break", "ORB15": "Opening-range break (15m)",
              "SessMean_cross": "Session-mean (VWAP proxy) cross", "RSI_mom": "RSI momentum (60/40)",
              "PDH_PDL": "Prev-day high/low break", "EMA9x21_trend": "EMA 9/21 cross + trend", "EMA9x21": "EMA 9/21 cross",
              "RSI_rev": "RSI reversal (30/70)"}
    fig, ax = plt.subplots(figsize=(8.5, 4.6))
    y = np.arange(len(order))
    for off, idx, c in [(-0.2, "NIFTY", BLUE), (0.2, "BANKNIFTY", AQUA)]:
        s = pd.read_csv(TAB / f"{idx}_signals_summary.csv", index_col=[0, 1]).loc["expiry"]
        vals = [s.at[k, "ret60_net_mean"] for k in order]
        base = s.at["BASELINE_every_bar", "ret60_net_mean"]
        ax.barh(y + off, vals, 0.38, color=c, label=f"{idx} expiry days (baseline {base:+.1f}%)")
        ax.axvline(base, color=c, lw=1, ls=":")
    ax.axvline(0, color=MUTED, lw=1)
    ax.set_yticks(y)
    ax.set_yticklabels([labels[k] for k in order], fontsize=8.5)
    ax.invert_yaxis()
    ax.set_xlabel("mean 60-min return of ATM option bought at signal, net of costs (%)")
    ax.set_title("Which technical triggers pay an expiry-day option BUYER?")
    ax.legend(loc="lower right", fontsize=8)
    ax.xaxis.grid(True); ax.yaxis.grid(False)
    save(fig, "signal_edge")


def chart_baseline_by_hour():
    t = pd.read_csv(TAB / "NIFTY_baseline_by_hour.csv", index_col=[0, 1])
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6))
    for ax, col, ttl in [(axes[0], "p_mfe60_ge50", "P(premium touches +50% within 60 min)"),
                         (axes[1], "ret60_net_mean", "mean net 60-min return of a random ATM buy")]:
        for reg, c in [("expiry", BLUE), ("non_expiry", ORANGE)]:
            s = t.loc[reg][col]
            ax.plot(range(len(s)), s.values, marker="o", ms=5, lw=2, color=c, label="expiry days" if reg == "expiry" else "other days")
            ax.set_xticks(range(len(s)))
            ax.set_xticklabels(s.index, fontsize=8)
        ax.set_title(ttl)
        ax.set_ylabel("%")
        if col == "ret60_net_mean":
            ax.axhline(0, color=MUTED, lw=1)
    axes[0].legend()
    fig.suptitle("NIFTY: buying an ATM option at a random moment, by hour", color=INK, fontweight="bold", fontsize=11)
    save(fig, "baseline_by_hour")


def chart_latebreak_by_year():
    fig, ax = plt.subplots(figsize=(7.5, 3.4))
    for off, idx, c in [(-0.2, "NIFTY", BLUE), (0.2, "BANKNIFTY", AQUA)]:
        t = pd.read_csv(TAB / f"{idx}_signals_by_year_expiry.csv", index_col=[0, 1]).loc["LateBreak_1415"]
        t = t[t["n"] >= 10]
        ax.bar(t.index + off, t["ret60_net_mean"], 0.38, color=c, label=idx)
        for yr, r in t.iterrows():
            ax.text(yr + off, r["ret60_net_mean"] + (2 if r["ret60_net_mean"] >= 0 else -9), f"n={int(r['n'])}", ha="center", fontsize=6.5, color=MUTED)
    ax.axhline(0, color=MUTED, lw=1)
    ax.set_ylabel("mean net 60-min return (%)")
    ax.set_title("Late-day range breakout (after 14:15) on expiry days, by year")
    ax.legend()
    ax.xaxis.grid(False)
    save(fig, "latebreak_by_year")


def chart_short_straddle():
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6))
    ax = axes[0]
    for off, idx, c in [(-0.2, "NIFTY", BLUE), (0.2, "BANKNIFTY", AQUA)]:
        t = pd.read_csv(TAB / f"{idx}_straddle_short_SL30_by_year.csv", index_col=0)
        t = t[t.index >= 2019]
        ax.bar(t.index + off, t["mean_pct"], 0.38, color=c, label=idx)
    ax.axhline(0, color=MUTED, lw=1)
    ax.set_title("09:20 short ATM straddle, 30% SL, exit 15:15 (model)")
    ax.set_ylabel("mean P&L, % of premium")
    ax.legend(); ax.xaxis.grid(False)
    ax = axes[1]
    t = pd.read_csv(TAB / "NIFTY_decay_curve.csv", index_col=0)
    vrp = {"VRP 1.00": pd.read_csv(TAB / "NIFTY_straddle_short_SL30_vrp1.0.csv", index_col=0).at[5, "mean_pct"],
           "VRP 1.10 (default)": pd.read_csv(TAB / "NIFTY_straddle_short_SL30.csv", index_col=0).at[5, "mean_pct"],
           "VRP 1.25": pd.read_csv(TAB / "NIFTY_straddle_short_SL30_vrp1.25.csv", index_col=0).at[5, "mean_pct"],
           "CAS model (exit 15:15)": pd.read_csv(TAB / "NIFTY_straddle_short_SL30_CASmodel.csv", index_col=0).at[5, "mean_pct"]}
    ax.barh(list(vrp.keys()), list(vrp.values()), color=[BLUE, BLUE, BLUE, ORANGE], height=0.5)
    ax.axvline(0, color=MUTED, lw=1)
    ax.set_title("NIFTY 09:20 short straddle: sensitivity to pricing assumption")
    ax.set_xlabel("mean P&L, % of premium")
    ax.invert_yaxis(); ax.yaxis.grid(False)
    save(fig, "short_straddle")


def chart_last_hour_hist():
    ds = pd.read_csv(TAB / "NIFTY_day_stats.csv", index_col=0)
    fig, ax = plt.subplots(figsize=(7.5, 3.4))
    bins = np.arange(-1.2, 1.25, 0.1)
    for flag, c, lab in [(True, BLUE, "expiry days"), (False, ORANGE, "other days")]:
        x = ds[ds.is_expiry == flag]["move_1430_1525"].clip(-1.2, 1.2)
        ax.hist(x, bins=bins, density=True, histtype="step", lw=2, color=c, label=f"{lab} (n={len(x)})")
    ax.set_xlabel("NIFTY move 14:30 -> 15:25 (%)")
    ax.set_ylabel("density")
    ax.set_title("Last-hour move distribution: expiry vs other days (2019-2024)")
    ax.legend()
    save(fig, "last_hour_hist")


def chart_cas_observed():
    rows = [("NIFTY 4 Aug", 0.62), ("SENSEX 6 Aug", 0.21), ("NIFTY 11 Aug", 0.09), ("SENSEX 13 Aug*", 0.28),
            ("NIFTY 18 Aug", -0.05), ("SENSEX 20 Aug", 0.07), ("NIFTY 25 Aug (monthly)", 0.31)]
    fig, ax = plt.subplots(figsize=(7, 3.5))
    names = [r[0] for r in rows]; vals = [r[1] for r in rows]
    ax.barh(names, vals, color=[BLUE if "NIFTY" in n else ORANGE for n in names], height=0.55)
    ax.axvline(0, color=MUTED, lw=1)
    ax.set_xlabel("index move from 15:15 print to CAS close (%)")
    ax.set_title("Expiry-day auction moves since CAS went live (Aug 2026)")
    ax.invert_yaxis(); ax.yaxis.grid(False)
    ax.text(0.30, 3.35, "*SEBI interim order: manipulated (+0.32% vs reference)", fontsize=7.5, color=INK)
    save(fig, "cas_observed")


if __name__ == "__main__":
    chart_variance_profiles(); chart_decay_curves(); chart_signal_edge(); chart_baseline_by_hour()
    chart_latebreak_by_year(); chart_short_straddle(); chart_last_hour_hist(); chart_cas_observed()
    print("charts written to", CH)


# ============================================================================ model charts
MOD = ROOT / "outputs" / "model"


def chart_calibration():
    t = pd.read_csv(MOD / "calibration_logit_expiry_test.csv", index_col=0)
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8))
    ax = axes[0]
    ax.plot([0, 0.6], [0, 0.6], color=MUTED, lw=1, ls="--")
    ax.plot(t["p_mean"], t["y_rate"], marker="o", ms=6, lw=2, color=BLUE)
    ax.text(0.02, 0.56, "10 deciles, ~1,900 bars each", fontsize=7.5, color=MUTED)
    ax.set_xlabel("predicted P(straddle +30% within 60 min)")
    ax.set_ylabel("observed rate")
    ax.set_title("Calibration on 2022-24 expiry-day bars (out of sample)")
    ax.set_xlim(0, 0.6); ax.set_ylim(0, 0.6)
    ax = axes[1]
    ev = pd.read_csv(MOD / "trade_eval_test.csv")
    for idx, c in [("NIFTY", BLUE), ("BANKNIFTY", AQUA)]:
        s = ev[(ev["index"] == idx) & (ev["model"] == "logit") & (ev["tau"] > 0)]
        ax.plot(s["tau"], s["net_mean"], marker="o", ms=5, lw=2, color=c, label=f"{idx}: net 60-min return")
        for _, r in s.iterrows():
            ax.annotate(f"{int(r['trades'])}", (r["tau"], r["net_mean"]), xytext=(0, 7), textcoords="offset points", fontsize=7, color=MUTED, ha="center")
    ax.axhline(0, color=MUTED, lw=1)
    ax.set_xlabel("score threshold (buy the breakout leg when score >= threshold)")
    ax.set_ylabel("mean net return, % (labels = trades)")
    ax.set_title("Threshold sweep, test expiry days")
    ax.legend(fontsize=8)
    save(fig, "model_calibration")


def chart_walkforward():
    t = pd.read_csv(MOD / "walkforward_logit.csv")
    fig, ax = plt.subplots(figsize=(7.5, 3.4))
    for off, idx, c in [(-0.2, "NIFTY", BLUE), (0.2, "BANKNIFTY", AQUA)]:
        s = t[t[f"{idx}_trades"] >= 10]
        ax.bar(s["year"] + off, s[f"{idx}_net_mean"], 0.38, color=c, label=idx)
        for _, r in s.iterrows():
            ax.text(r["year"] + off, r[f"{idx}_net_mean"] + (2 if r[f"{idx}_net_mean"] >= 0 else -9), f"n={int(r[f'{idx}_trades'])}", ha="center", fontsize=6.5, color=MUTED)
    ax.axhline(0, color=MUTED, lw=1)
    ax.set_ylabel("mean net 60-min return (%)")
    ax.set_title("Walk-forward: model trained on all prior years, score >= 0.35 + breakout")
    ax.legend(); ax.xaxis.grid(False)
    save(fig, "model_walkforward")


def chart_score_heat():
    import json
    bars = pd.concat([pd.read_parquet(MOD / f"{i}_bars.parquet") for i in ["NIFTY", "BANKNIFTY"]])
    bars = bars[(bars["is_expiry"] == 1) & (pd.to_datetime(bars["date"]) > "2021-12-31")]
    bars["hour"] = pd.cut(bars["minute"], [-1, 104, 164, 224, 284, 344, 375], labels=["09:15-11", "11-12", "12-13", "13-14", "14-15", "15-15:30"])
    bars["spent"] = pd.qcut(bars["var_spent_ratio"], 4, labels=["coldest 25%", "cool", "warm", "hottest 25%"])
    pv = bars.pivot_table(index="spent", columns="hour", values="y_straddle30", aggfunc="mean", observed=True) * 100
    fig, ax = plt.subplots(figsize=(8, 3.4))
    im = ax.imshow(pv.values, cmap=matplotlib.colors.LinearSegmentedColormap.from_list("b", ["#cde2fb", "#0d366b"]), aspect="auto", vmin=5, vmax=60)
    ax.set_xticks(range(pv.shape[1])); ax.set_xticklabels(pv.columns, fontsize=8)
    ax.set_yticks(range(pv.shape[0])); ax.set_yticklabels(pv.index, fontsize=8)
    for i in range(pv.shape[0]):
        for j in range(pv.shape[1]):
            v = pv.values[i, j]
            ax.text(j, i, f"{v:.0f}%", ha="center", va="center", fontsize=8.5, color="white" if v > 32 else "#0b0b0b")
    ax.set_xlabel("time of day"); ax.set_ylabel("variance spent vs budget")
    ax.set_title("Observed P(ATM straddle +30% within 60 min), expiry days 2022-24")
    ax.grid(False)
    save(fig, "model_heat")


if __name__ == "__main__":
    chart_calibration(); chart_walkforward(); chart_score_heat()
    print("model charts written")


# ============================================================================ OTM blast charts
OTM = ROOT / "outputs" / "otm"


def chart_otm():
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 3.8))
    ax = axes[0]
    t = pd.read_csv(OTM / "NIFTY_blast_by_hour_k.csv", index_col=[0, 1])
    hours = [h for h in t.index.get_level_values(0).unique()]
    for k, c, lab in [(1, BLUE, "1 strike OTM"), (2, AQUA, "2 strikes OTM"), (3, ORANGE, "3 strikes OTM")]:
        s = t.xs(k, level=1)
        ax.plot(range(len(s)), s["p_5x"], marker="o", ms=5, lw=2, color=c, label=lab)
    ax.set_xticks(range(len(hours))); ax.set_xticklabels(hours, fontsize=8)
    ax.set_ylabel("P(premium reaches 5x within 60 min), %")
    ax.set_title("NIFTY: 5x odds of a random OTM buy, by hour")
    ax.legend(fontsize=8)
    ax = axes[1]
    labels = {0: "auto (nearest OTM ≤ cap)", 1: "1 OTM", 2: "2 OTM", 3: "3 OTM"}
    x = np.arange(4)
    for off, idx, tag, c, lab in [(-0.3, "NIFTY", "", BLUE, "NIFTY pre-CAS"), (-0.1, "NIFTY", "_CAS", "#86b6ef", "NIFTY CAS pricing"),
                                  (0.1, "BANKNIFTY", "", AQUA, "BANKNIFTY pre-CAS"), (0.3, "BANKNIFTY", "_CAS", "#8fd9bd", "BANKNIFTY CAS pricing")]:
        r = pd.read_csv(OTM / f"{idx}_blast_rule{tag}.csv")
        r = r[r["tau"] == 0.4].set_index("k")
        ax.bar(x + off, [r.at[k, "net_mean"] for k in range(4)], 0.19, color=c, label=lab)
    ax.axhline(0, color=MUTED, lw=1)
    ax.set_xticks(x); ax.set_xticklabels([labels[k] for k in range(4)], fontsize=8)
    ax.set_ylabel("mean net 60-min return, %")
    ax.set_title("GO signals: which strike to buy")
    ax.legend(fontsize=7.5)
    ax.xaxis.grid(False)
    save(fig, "otm_blast")


if __name__ == "__main__":
    chart_otm()
    print("otm chart written")
