"""CAS-month check on REAL data exported through the Dhan API (scripts/dhan_export.py).

    python scripts/cas_month_check_real.py --zip dhan_export.zip [--dates 2026-08-04,2026-08-11,...]

For every expiry day in the export it:
  1. runs the buy-score bar by bar on the real 5-min index bars (same code as the live indicator),
  2. lists GO / LEAN signals with the suggested strike,
  3. prices each signal with the REAL option bars from Dhan's expired-options data
     (entry = close of the signal bar; exit = close of the 15:10 bar = last before the CAS freeze;
      settlement = intrinsic on the actual CAS close), including the 2-OTM strike,
  4. reports the real opening straddle, the 15:10 straddle and the 15:10 nearest-OTM prices vs
     the model, and the auction lottery (nearest OTM CE/PE at 15:10 -> settlement).
Writes outputs/cas_month/real_*.csv and prints a summary.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from expiry_edge.calendar import _regime_weekday, label_expiry_days      # noqa: E402
from expiry_edge.config import CONTRACT, COST_PER_SIDE                     # noqa: E402
from expiry_edge.options import atm_strike, remaining_share, sigma_day_from_rv, straddle_price  # noqa: E402
from expiry_edge.score import BuyScore                                    # noqa: E402
from live_indicator import run_once                                       # noqa: E402

OUT = ROOT / "outputs" / "cas_month"; OUT.mkdir(parents=True, exist_ok=True)
pd.set_option("display.width", 250)
EXIT_TS = "15:10:00"          # last 5-min bar that closes before the 15:15 freeze


def load_export(path: Path) -> dict:
    files = {}
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if n.endswith(".csv"):
                    files[Path(n).name] = pd.read_csv(z.open(n))
    else:
        for p in path.glob("*.csv"):
            files[p.name] = pd.read_csv(p)
    return files


def build_daily(df: pd.DataFrame, index: str) -> pd.DataFrame:
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"])
    d = d.set_index("date").sort_index()[["open", "high", "low", "close"]].astype(float)
    d["nbars"] = 1
    d["prev_close"] = d["close"].shift(1); d["prev_high"] = d["high"].shift(1); d["prev_low"] = d["low"].shift(1)
    d["gap_pct"] = (d["open"] / d["prev_close"] - 1) * 100
    d["range_pct"] = (d["high"] - d["low"]) / d["open"] * 100
    park = np.log(d["high"] / d["low"]) ** 2 / (4 * np.log(2))
    d["rv20"] = np.sqrt(park.rolling(20).mean().shift(1)) * 100
    d["range20"] = d["range_pct"].rolling(20).mean().shift(1)
    d["rv20_rank"] = d["rv20"].rolling(250, min_periods=60).rank(pct=True)
    d["vol_regime"] = pd.cut(d["rv20_rank"], [0, 1 / 3, 2 / 3, 1.0001], labels=["low", "mid", "high"])
    d.index.name = "date"
    d = d.join(label_expiry_days(d.index, index))
    return d


def build_bars5(df: pd.DataFrame) -> pd.DataFrame:
    b = df.copy()
    b["ts"] = pd.to_datetime(b["ts"])
    t = b["ts"].dt.time
    b = b[(t >= dt.time(9, 15)) & (t <= dt.time(15, 29))].sort_values("ts")
    b["date"] = b["ts"].dt.date
    b["minute"] = ((b["ts"].dt.hour - 9) * 60 + b["ts"].dt.minute - 15).astype(int)
    b["n"] = 5
    return b[["ts", "date", "minute", "open", "high", "low", "close", "n"]].astype({"open": float, "high": float, "low": float, "close": float})


def option_series(opt: pd.DataFrame, date: dt.date, side: str, strike: float) -> pd.DataFrame:
    """All real bars of one strike on one day (stitched across the ATM±k offsets)."""
    o = opt[(opt["ts"].dt.date == date) & (opt["side"] == side) & (np.isclose(opt["strike"], strike))]
    return o.sort_values("ts").drop_duplicates("ts")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True, help="dhan_export.zip or the folder")
    ap.add_argument("--dates", default=None, help="comma-separated expiry dates to check (default: calendar)")
    a = ap.parse_args()
    files = load_export(Path(a.zip))
    model = BuyScore()
    results, calib, lottery = [], [], []
    for idx in ("NIFTY", "SENSEX", "BANKNIFTY"):
        if f"index_5m_{idx}.csv" not in files or f"index_daily_{idx}.csv" not in files:
            print(f"[{idx}] no index files in export — skipped"); continue
        bars5 = build_bars5(files[f"index_5m_{idx}.csv"])
        d = build_daily(files[f"index_daily_{idx}.csv"], idx)
        opt = files.get(f"rolling_options_{idx}.csv")
        if opt is not None and len(opt):
            opt["ts"] = pd.to_datetime(opt["ts"])
        step = CONTRACT[idx]["strike_step"]; cost = COST_PER_SIDE[idx]
        prof = np.load(ROOT / f"outputs/{'NIFTY' if idx == 'SENSEX' else idx}_profile_expiry.npy")
        cum = np.concatenate([[0.0], np.cumsum(prof)])
        if a.dates:
            dates = [pd.Timestamp(x).date() for x in a.dates.split(",")]
        else:
            dates = [x.date() for x in d[(d["is_expiry"]) & (d.index >= "2026-08-01")].index]
            # the calendar shifts an expiry to the previous trading day when the scheduled day is missing;
            # at the edge of the export the scheduled day may simply not have happened yet -> drop that label
            last = d.index.max().date(); sched_wd = _regime_weekday(idx, last)[0]
            if dates and dates[-1] == last and sched_wd is not None and last.weekday() != sched_wd:
                print(f"[{idx}] {last} is labelled expiry only because the export ends before the scheduled weekday — skipped")
                dates = dates[:-1]
        for date in dates:
            day_bars = bars5[bars5["date"] <= date]
            if not (bars5["date"] == date).any():
                print(f"[{idx} {date}] no 5-min bars — skipped"); continue
            try:
                raw = run_once(idx, day_bars, d[d.index <= pd.Timestamp(date)], model, cum, quiet=True)
            except Exception as e:                                     # noqa: BLE001
                print(f"[{idx} {date}] scoring failed: {e}"); continue
            drow = d.loc[pd.Timestamp(date)]
            close_cas = float(drow["close"])
            p1515 = float(day_bars[(day_bars["date"] == date) & (day_bars["ts"].dt.time <= dt.time(15, 10))]["close"].iloc[-1])
            print("=" * 100); print(f"{idx} {date}  expiry={bool(drow['is_expiry'])}  gap {drow['gap_pct']:+.2f}%  15:10 close {p1515:.2f} -> CAS close {close_cas:.2f} "
                                     f"({close_cas - p1515:+.2f})")
            sigs = raw[raw["verdict"].str.startswith(("GO", "LEAN"))]
            # ---- real calibration: opening straddle / 15:10 straddle vs model
            if opt is not None and len(opt):
                od = opt[opt["ts"].dt.date == date]
                if len(od):
                    first = od[od["offset"] == "ATM"].sort_values("ts")
                    if len(first):
                        open_row = first.iloc[0]; K_open = float(open_row["strike"])
                        st_open = float(od[(od["ts"] == first["ts"].min()) & (np.isclose(od["strike"], K_open))]["close"].sum())
                        last_ts = od[od["ts"].dt.time <= dt.time(15, 10)]["ts"].max()
                        atm_last = od[(od["ts"] == last_ts) & (od["offset"] == "ATM")]
                        K_last = float(atm_last["strike"].iloc[0]) if len(atm_last) else np.nan
                        st_1510 = float(od[(od["ts"] == last_ts) & (np.isclose(od["strike"], K_last))]["close"].sum()) if len(atm_last) else np.nan
                        sigma0 = sigma_day_from_rv(float(drow["rv20"]))
                        R = remaining_share(prof, cas=True)
                        spot_open = float(day_bars[day_bars["date"] == date]["open"].iloc[0])
                        m_open = float(straddle_price(spot_open, atm_strike(spot_open, idx), sigma0 ** 2 * R[0]))
                        m_1510 = float(straddle_price(p1515, atm_strike(p1515, idx), sigma0 ** 2 * R[356]))
                        calib.append({"index": idx, "date": date, "real_open_straddle": st_open, "model_open_straddle": m_open,
                                      "real_1510_straddle": st_1510, "model_1510_straddle": m_1510})
                        print(f"  straddle real vs model: open {st_open:.1f} vs {m_open:.1f} | 15:10 {st_1510:.1f} vs {m_1510:.1f}")
                    # auction lottery on real 15:10 prices
                    for side, sgn in (("CE", 1), ("PE", -1)):
                        K0 = atm_strike(p1515, idx); K = K0 + sgn * step if sgn * (K0 - p1515) <= 0 else K0
                        if sgn * (K - p1515) <= 0:
                            K += sgn * step
                        ser = option_series(od, date, side, K)
                        ser = ser[ser["ts"].dt.time <= dt.time(15, 10)]
                        if len(ser):
                            p0 = float(ser["close"].iloc[-1]); settle = max(sgn * (close_cas - K), 0.0)
                            lottery.append({"index": idx, "date": date, "side": side, "strike": K, "p1510_real": p0, "settle": settle,
                                            "net_pct": ((settle - cost) / (p0 + cost) - 1) * 100})
            # ---- signals priced with real option bars
            if len(sigs) == 0:
                print("  no GO/LEAN signal before the cutoff")
            for _, r in sigs.iterrows():
                hh, mm = 9 + (int(r["minute"]) + 15) // 60, (int(r["minute"]) + 15) % 60
                bar_ts = pd.Timestamp(f"{date} {hh:02d}:{mm - 4:02d}:00")          # bar start of the signal bar
                side = "CE" if r["dir"] == 1 else "PE"; sgn = 1 if side == "CE" else -1
                K0 = atm_strike(float(r["close"]), idx)
                rec = {"index": idx, "date": date, "time": f"{hh:02d}:{mm:02d}", "verdict": r["verdict"], "score": r["score"], "side": side,
                       "spot": r["close"], "suggested": r["strike"]}
                print(f"  {hh:02d}:{mm:02d} {r['verdict']}  score {r['score']:.2f}  spot {r['close']:.1f}  suggested {r['strike']}")
                if opt is None or not len(opt):
                    results.append(rec); continue
                for k in (0, 1, 2):
                    K = K0 + sgn * k * step
                    ser = option_series(opt, date, side, K)
                    at_entry = ser[ser["ts"] == bar_ts]
                    if not len(at_entry):
                        at_entry = ser[ser["ts"] <= bar_ts].tail(1)
                    if not len(at_entry):
                        continue
                    p0 = float(at_entry["close"].iloc[0])
                    after = ser[(ser["ts"] > bar_ts) & (ser["ts"].dt.time <= dt.time(15, 10))]
                    win60 = after[after["ts"] <= bar_ts + pd.Timedelta(minutes=60)]
                    mfe60 = float(win60["high"].max() / p0) if len(win60) else np.nan
                    p1510 = float(after["close"].iloc[-1]) if len(after) else p0
                    settle = max(sgn * (close_cas - K), 0.0)
                    rec.update({f"k{k}_strike": K, f"k{k}_entry": p0, f"k{k}_max60x": mfe60, f"k{k}_1510": p1510,
                                f"k{k}_net1510_pct": ((p1510 - cost) / (p0 + cost) - 1) * 100,
                                f"k{k}_settle": settle, f"k{k}_netsettle_pct": ((settle - cost) / (p0 + cost) - 1) * 100})
                    print(f"     k={k} {K:.0f}{side}: entry {p0:.2f}  max within 60m {mfe60:.2f}x  15:10 {p1510:.2f} ({rec[f'k{k}_net1510_pct']:+.0f}%)  "
                          f"settle {settle:.2f} ({rec[f'k{k}_netsettle_pct']:+.0f}%)")
                results.append(rec)
    pd.DataFrame(results).to_csv(OUT / "real_signals.csv", index=False)
    pd.DataFrame(calib).to_csv(OUT / "real_calibration.csv", index=False)
    pd.DataFrame(lottery).to_csv(OUT / "real_auction_lottery.csv", index=False)
    if lottery:
        L = pd.DataFrame(lottery)
        print("\nauction lottery on REAL 15:10 prices (net % per ticket):")
        print(L.pivot(index=["index", "date"], columns="side", values="net_pct").round(0).to_string())
    print("\nwrote", OUT / "real_signals.csv", OUT / "real_calibration.csv", OUT / "real_auction_lottery.csv")


if __name__ == "__main__":
    main()
