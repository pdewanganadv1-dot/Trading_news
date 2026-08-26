"""Triage F&O STOCKS for the same CAS closing-auction blast, and surface the crazy option returns.

Individual stocks show the auction blast MORE than the index, for a structural reason the brokers flagged at
the first monthly expiry (25 Aug 2026): a single stock's cash book at the close is far thinner than a whole
index's, so the same settlement/hedging imbalance moves it further — and a sharp auction move flips an option
that was about to expire worthless straight into the money.  Stock F&O is MONTHLY only (no weekly stock
options), so this is a once-a-month event, on the stock's monthly expiry.

This screen reads a Dhan stock-options export (per-symbol rolling-option bars, same format as the index one,
carrying a 'spot' column) and, for each stock:
  - auction_move_pct      (CAS close vs the 15:10 spot)         — the blast, measured
  - the OI imbalance features (max-pain distance, ATM-OI share, concentration, PCR)  — the setup, at 15:10
  - blast_score           a susceptibility rank from the setup (concentration x ATM-OI x |max-pain pull|)
  - the biggest single-option return: the nearest-OTM strike bought at 15:10 and settled — the "crazy return"
It writes a ranked triage (most susceptible / biggest movers first) and the option-return leaderboard.

    python scripts/cas_stock_screen.py --dir dhan_export            # folder or zip of rolling_options_<SYM>.csv
    python scripts/cas_stock_screen.py --dir dhan_export --close stock_close.csv   # symbol,date,cas_close

Without a close file it uses the last spot print after the freeze as an approximate settlement (flagged in the
output).  Nothing here is a recommendation: single-stock expiry options are the thinnest, most manipulation-prone
corner of the market (SEBI's 13 Aug case was single stocks), and the 'crazy returns' are survivorship — the same
strikes expire worthless far more often than they blast.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from expiry_edge.oi_features import oi_features                                   # noqa: E402

OUT = ROOT / "outputs" / "stocks"; OUT.mkdir(parents=True, exist_ok=True)
EXIT_T = dt.time(15, 10)
INDEX_NAMES = {"NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY", "BANKEX"}


def load_stock_options(path: Path) -> dict[str, pd.DataFrame]:
    """All rolling_options_<SYM>.csv in a folder/zip whose SYM is not an index -> {sym: frame}."""
    out = {}
    def add(name, df):
        if name.startswith("rolling_options_") and name.endswith(".csv"):
            sym = name[len("rolling_options_"):-len(".csv")].split("_history")[0]
            if sym.upper() not in INDEX_NAMES:
                df["ts"] = pd.to_datetime(df["ts"]); out[sym] = df
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                add(Path(n).name, pd.read_csv(z.open(n)))
    else:
        for p in path.glob("rolling_options_*.csv"):
            add(p.name, pd.read_csv(p))
    return out


def guess_step(strikes: np.ndarray) -> float:
    d = np.diff(np.unique(strikes))
    return float(np.median(d)) if len(d) else 1.0


def screen_stock(sym: str, opt: pd.DataFrame, close_map: dict) -> list[dict]:
    rows = []
    for date, od in opt.groupby(opt["ts"].dt.date):
        pre = od[od["ts"].dt.time <= EXIT_T]
        if not len(pre) or "spot" not in pre.columns:
            continue
        p1510 = float(pd.to_numeric(pre["spot"], errors="coerce").dropna().iloc[-1]) if pre["spot"].notna().any() else np.nan
        if not np.isfinite(p1510) or p1510 <= 0:
            continue
        cc = close_map.get((sym, str(date)))
        approx = cc is None
        if cc is None:
            post = od[od["ts"].dt.time > EXIT_T]
            cc = float(pd.to_numeric(post["spot"], errors="coerce").dropna().iloc[-1]) if len(post) and post["spot"].notna().any() else p1510
        step = guess_step(od["strike"].dropna().values)
        snap = od[od["ts"].dt.time <= EXIT_T].sort_values("ts").groupby(["side", "strike"]).tail(1)[["side", "strike", "oi"]]
        f = oi_features(snap, p1510, step)
        move_pct = (cc / p1510 - 1) * 100
        blast_score = f["oi_hhi"] * (f["atm_oi_share"] + 0.1) * (1 + abs(f["mp_pull"]))     # setup-only susceptibility
        # biggest single-option return: nearest OTM each side at 15:10 -> settlement
        best = None
        for side, sgn in (("CE", 1), ("PE", -1)):
            atm = round(p1510 / step) * step
            K = atm + sgn * step if sgn * (atm - p1510) <= 0 else atm
            ser = od[(od["side"] == side) & (np.isclose(od["strike"], K)) & (od["ts"].dt.time <= EXIT_T)].sort_values("ts")
            if not len(ser):
                continue
            p0 = float(pd.to_numeric(ser["close"], errors="coerce").dropna().iloc[-1]) if ser["close"].notna().any() else np.nan
            if not np.isfinite(p0) or p0 <= 0:
                continue
            settle = max(sgn * (cc - K), 0.0)
            ret = (settle / p0 - 1) * 100
            if best is None or ret > best["ret_pct"]:
                best = {"side": side, "strike": float(K), "entry": round(p0, 2), "settle": round(settle, 2), "ret_pct": round(ret, 0)}
        rows.append({"symbol": sym, "date": str(date), "p1510": round(p1510, 2), "cas_close": round(cc, 2),
                     "approx_close": approx, "auction_move_pct": round(move_pct, 3), "abs_move_pct": round(abs(move_pct), 3),
                     "max_pain": f["max_pain"], "mp_dist_pct": round(f["mp_dist_pct"], 3), "atm_oi_share": round(f["atm_oi_share"] * 100, 1),
                     "oi_hhi": round(f["oi_hhi"], 3), "pcr_oi": round(f["pcr_oi"], 2), "blast_score": round(blast_score, 4),
                     "best_option": best})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="folder or zip of rolling_options_<SYM>.csv for stocks")
    ap.add_argument("--close", default=None, help="CSV symbol,date,cas_close (the real settlement close per stock)")
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args()
    close_map = {}
    if a.close and Path(a.close).exists():
        cdf = pd.read_csv(a.close)
        close_map = {(str(r["symbol"]), str(r["date"])): float(r["cas_close"]) for _, r in cdf.iterrows()}
    stocks = load_stock_options(Path(a.dir))
    if not stocks:
        print("no stock rolling_options_*.csv found — pull F&O stocks in dhan_export.py (STOCKS list / DHAN_STOCKS)"); return
    rows = []
    for sym, opt in stocks.items():
        rows += screen_stock(sym, opt, close_map)
    if not rows:
        print("no per-stock expiry rows built"); return
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "cas_stock_screen.csv", index=False)
    approx = df["approx_close"].any()
    # leaderboards
    movers = df.sort_values("abs_move_pct", ascending=False).head(a.top)
    suscept = df.sort_values("blast_score", ascending=False).head(a.top)
    crazy = df[df["best_option"].notna()].copy()
    crazy["ret"] = crazy["best_option"].apply(lambda b: b["ret_pct"] if b else np.nan)
    crazy = crazy.sort_values("ret", ascending=False).head(a.top)
    card = {"n_stock_expiries": len(df), "n_symbols": df["symbol"].nunique(), "approx_close_used": bool(approx),
            "note": "Stock F&O is monthly; the first monthly expiry under CAS was 25 Aug 2026. Single-stock auction books are "
                    "thin, so blasts are larger AND more manipulation-prone than the index. 'crazy returns' are survivorship — "
                    "the same OTM strikes expire worthless far more often than they hit.",
            "biggest_auction_movers": movers[["symbol", "date", "auction_move_pct", "atm_oi_share", "oi_hhi"]].to_dict("records"),
            "most_susceptible_by_setup": suscept[["symbol", "date", "blast_score", "oi_hhi", "atm_oi_share", "mp_dist_pct"]].to_dict("records"),
            "crazy_option_returns": [{"symbol": r["symbol"], "date": r["date"], **r["best_option"]} for _, r in crazy.iterrows() if r["best_option"]]}
    (OUT / "cas_stock_screen.json").write_text(json.dumps(card, indent=2, default=float))
    pd.set_option("display.width", 220)
    print(f"{card['n_symbols']} stocks, {card['n_stock_expiries']} expiry-days" + ("  (settlement close APPROXIMATED from post-freeze spot)" if approx else ""))
    print("\nBiggest auction movers:"); print(movers[["symbol", "date", "auction_move_pct", "atm_oi_share", "oi_hhi", "blast_score"]].to_string(index=False))
    print("\nCraziest nearest-OTM option returns (15:10 -> settlement):")
    for _, r in crazy.iterrows():
        b = r["best_option"]
        print(f"  {r['symbol']:12s} {r['date']}  {b['side']} {b['strike']:.0f}  entry {b['entry']}  settle {b['settle']}  {b['ret_pct']:+.0f}%")
    print("\nwrote", OUT / "cas_stock_screen.csv", OUT / "cas_stock_screen.json")


if __name__ == "__main__":
    main()
