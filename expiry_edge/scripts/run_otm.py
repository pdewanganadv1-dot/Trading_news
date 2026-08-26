"""Cheap-OTM blast study on expiry days (buyer only).

Reads the bar datasets, scores every bar with the buy-score, prices 1/2/3-strike OTM
options along the real path (pre-CAS and CAS variants) and writes tables:
  outputs/otm/<IDX>_blast_by_hour_k.csv            random bar, by hour x strikes-OTM
  outputs/otm/<IDX>_blast_breakout_score.csv       breakout-direction buys, by score bucket x k
  outputs/otm/<IDX>_blast_rule.csv                 the GO/LEAN rule with k = 0..3 (0 = ATM from the model dataset)
  outputs/otm/<IDX>_blast_*_CAS.csv                same under CAS pricing / 15:15 exit
  outputs/otm/<IDX>_otm_outcomes.parquet           raw rows
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from expiry_edge.config import COST_PER_SIDE, LIQUID_WEEKLY_FROM, LIQUID_WEEKLY_TO   # noqa: E402
from expiry_edge.model import build_dataset                                          # noqa: E402
from expiry_edge.otm import blast_table, otm_outcomes                                # noqa: E402
from expiry_edge.score import BuyScore, engineer                                     # noqa: E402

OUT = ROOT / "outputs" / "otm"; OUT.mkdir(parents=True, exist_ok=True)
pd.set_option("display.width", 250)
TEST_FROM = pd.Timestamp("2022-01-01")


def rule_rows(o: pd.DataFrame, index: str) -> pd.DataFrame:
    """Buy the k-OTM option in the breakout direction when score >= tau (test expiry days)."""
    side_ok = ((o["new_high"] == 1) & (o["side"] == "CE")) | ((o["new_low"] == 1) & (o["side"] == "PE"))
    d = o[side_ok].copy()
    # 'auto' strike: the nearest OTM strike whose premium is <= cap (else the 1-OTM), one row per bar
    cap = {"NIFTY": 10.0, "BANKNIFTY": 25.0, "SENSEX": 30.0}[index]
    picks = []
    for (dt_, mn, sd_), g in d.groupby(["date", "minute", "side"], sort=False):
        g = g.sort_values("k")
        cheap = g[(g["p0"] <= cap) & (g["k"] <= 2)]
        picks.append((cheap.iloc[0] if len(cheap) else g.iloc[0]).to_dict())
    auto = pd.DataFrame(picks); auto["k"] = 0            # k = 0 denotes the auto rule
    d = pd.concat([d, auto], ignore_index=True)
    rows = []
    for tau in [0.0, 0.30, 0.40, 0.50]:
        s = d[d["score"] >= tau]
        t = blast_table(s, by=("k",))
        t.insert(0, "tau", tau)
        tc = blast_table(s, by=("k",), tag="close")[["p_3x", "p_5x", "p_10x", "net_mean", "p_net_pos"]]
        tc.columns = [c + "_toclose" for c in tc.columns]
        rows.append(t.join(tc))
    return pd.concat(rows).reset_index()


def main():
    model = BuyScore()
    for idx in sys.argv[1:] or ["NIFTY", "BANKNIFTY"]:
        m = pd.read_parquet(ROOT / f"data/{idx}_1m.parquet"); b5 = pd.read_parquet(ROOT / f"data/{idx}_5m.parquet")
        d = pd.read_parquet(ROOT / f"data/{idx}_daily.parquet")
        prof = np.load(ROOT / f"outputs/{idx}_profile_expiry.npy")
        bars = pd.read_parquet(ROOT / f"outputs/model/{idx}_bars.parquet")
        bars = bars[bars["is_expiry"] == 1].copy()
        bars["date"] = pd.to_datetime(bars["date"])
        bars = bars[bars["date"] >= TEST_FROM]                     # out-of-sample years only
        eng = engineer(bars)
        bars["score"] = model.score(eng.fillna(eng.median(numeric_only=True)))
        print(f"[{idx}] test expiry days {bars.date.nunique()}, bars {len(bars)}")
        for cas in (False, True):
            tag = "_CAS" if cas else ""
            if cas:
                # CAS pricing needs CAS labels for the ATM reference too; bars features are unchanged
                o = otm_outcomes(m, bars, d, idx, prof, cas=True)
            else:
                o = otm_outcomes(m, bars, d, idx, prof, cas=False)
            o.to_parquet(OUT / f"{idx}_otm_outcomes{tag}.parquet")
            t_hour = blast_table(o, by=("hour", "k"))
            t_hour.to_csv(OUT / f"{idx}_blast_by_hour_k{tag}.csv")
            o["score_bucket"] = pd.cut(o["score"], [0, 0.2, 0.3, 0.4, 0.5, 1.0], labels=["<0.2", "0.2-0.3", "0.3-0.4", "0.4-0.5", ">=0.5"])
            side_ok = ((o["new_high"] == 1) & (o["side"] == "CE")) | ((o["new_low"] == 1) & (o["side"] == "PE"))
            t_sc = blast_table(o[side_ok], by=("score_bucket", "k"))
            t_sc.to_csv(OUT / f"{idx}_blast_breakout_score{tag}.csv")
            t_rule = rule_rows(o, idx)
            t_rule.to_csv(OUT / f"{idx}_blast_rule{tag}.csv", index=False)
            # lottery reference: random bar, either side, by k (no breakout, no score)
            t_lot = blast_table(o, by=("k",))
            t_lot.to_csv(OUT / f"{idx}_blast_lottery{tag}.csv")
            print(f"\n[{idx}{tag}] random-bar lottery by strikes OTM:"); print(t_lot.to_string())
            print(f"\n[{idx}{tag}] breakout-direction buy, by score bucket x k (60-min):")
            print(t_sc[["n", "p0_med", "p_3x", "p_5x", "p_10x", "net_mean", "p_net_pos", "p_lose_half", "take3_mean", "take5_mean"]].to_string())
            print(f"\n[{idx}{tag}] rule table:"); print(t_rule.to_string(index=False))


if __name__ == "__main__":
    main()
