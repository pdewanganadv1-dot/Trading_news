"""Buy-score: P(ATM straddle bought at this 5-min bar close touches +30% within 60 min).

Standardised logistic regression.  The coefficient file outputs/model/buy_score_logit.json is
produced by scripts/train_model.py; this module turns raw bar features into the model's
engineered inputs and evaluates the score — in batch (backtest) or bar-by-bar (live).
The Pine Script indicator implements exactly the same arithmetic.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .config import SESSION_MINUTES, VRP_MULTIPLIER

MODEL_PATH = Path(__file__).resolve().parent.parent / "outputs" / "model" / "buy_score_logit.json"

CHART_FEATURES = ["tod", "tod2", "is_expiry", "tod_x_expiry", "gap_abs", "rv20_rank", "or30_rel", "range_sofar_rel",
                  "pos_edge", "dist_edge_atr", "abs_ret15", "abs_ret30", "bar_range_atr", "bb_bw_pct", "abs_ema_spread_atr",
                  "log_var_spent", "breakout", "or_break"]
# OI / max-pain imbalance features (from expiry_edge.oi_features) — the imbalance trigger.  train_model.py adds
# them to the fit only when the dataset actually carries real OI (a Dhan pull), so the score fires on imbalance
# once retrained; engineer() fills neutral zeros when a chain snapshot is absent, and a model json trained
# without them (the current one) simply never references them via BuyScore.features.
OI_FEATURES = ["mp_pull", "atm_oi_share", "oi_hhi", "pcr_oi", "net_atm_skew"]
LOGIT_FEATURES = CHART_FEATURES + OI_FEATURES


def engineer(df: pd.DataFrame) -> pd.DataFrame:
    """Raw per-bar features (model.build_dataset / live_features) -> model inputs."""
    d = df.copy()
    for f in OI_FEATURES:                                              # neutral when no chain snapshot fed the row
        d[f] = pd.to_numeric(d[f], errors="coerce").fillna(1.0 if f == "pcr_oi" else 0.0) if f in d.columns else (1.0 if f == "pcr_oi" else 0.0)
    d["tod2"] = d["tod"] ** 2
    d["tod_x_expiry"] = d["tod"] * d["is_expiry"]
    d["pos_edge"] = (d["pos_in_range"] - 0.5).abs() * 2                 # 0 = mid-range, 1 = at an extreme
    d["dist_edge_atr"] = np.minimum(d["dist_high_atr"], d["dist_low_atr"]).clip(0, 5)
    d["abs_ret15"] = d["ret15"].abs().clip(0, 2)
    d["abs_ret30"] = d["ret30"].abs().clip(0, 3)
    d["abs_ema_spread_atr"] = d["ema_spread_atr"].abs().clip(0, 5)
    d["log_var_spent"] = np.log(d["var_spent_ratio"].clip(0.05, 20))
    d["breakout"] = ((d["new_high"] + d["new_low"]) > 0).astype(int)
    d["or_break"] = ((d["or_break_up"] + d["or_break_dn"]) > 0).astype(int)
    d["bar_range_atr"] = d["bar_range_atr"].clip(0, 6)
    d["range_sofar_rel"] = d["range_sofar_rel"].clip(0, 4)
    d["or30_rel"] = d["or30_rel"].fillna(d["range_sofar_rel"]).clip(0, 3)
    d["gap_abs"] = d["gap_abs"].clip(0, 3)
    if "date" in d.columns:
        d["date"] = pd.to_datetime(d["date"])
    return d


class BuyScore:
    def __init__(self, path: Path = MODEL_PATH):
        self.spec = json.load(open(path))
        self.features = self.spec["features"]
        self.mu = np.array([self.spec["mean"][f] for f in self.features])
        self.sd = np.array([self.spec["std"][f] for f in self.features])
        self.coef = np.array([self.spec["coef"][f] for f in self.features])
        self.b0 = self.spec["intercept"]
        self.lean = self.spec["score_thresholds"]["lean"]
        self.go = self.spec["score_thresholds"]["go"]

    def score(self, engineered: pd.DataFrame) -> np.ndarray:
        Z = (engineered[self.features].values - self.mu) / self.sd
        return 1.0 / (1.0 + np.exp(-(Z @ self.coef + self.b0)))

    def score_raw(self, raw: pd.DataFrame) -> np.ndarray:
        return self.score(engineer(raw))

    def verdict(self, p: float, breakout_dir: int) -> str:
        if breakout_dir == 0:
            return "WAIT (no range breakout on this bar)"
        side = "CE" if breakout_dir > 0 else "PE"
        if p >= self.go:
            return f"GO — buy ATM {side}"
        if p >= self.lean:
            return f"LEAN — ATM {side}, half size"
        return "NO"


# ----------------------------------------------------------------------------
# Live feature construction from today's 5-min bars + daily context
# ----------------------------------------------------------------------------
def live_features(b5_today: pd.DataFrame, f_today: pd.DataFrame, daily_row: pd.Series, sigma0: float,
                  cum_profile: np.ndarray, is_expiry: int, is_monthly: int, weekday: int) -> pd.DataFrame:
    """Per-bar raw features for one session (no labels), same definitions as model.build_dataset.
    b5_today: today's 5-min bars [minute, open, high, low, close]; f_today: the same bars with
    features from features.add_features (atr14, rsi14, bb_bw_pct, ema9, ema21, or30_*, day_open,
    gap_pct, rv20, range20); daily_row: today's row of the daily table (rv20_rank).
    cum_profile: cumulative variance share by minute (len 376)."""
    fd = f_today.sort_values("minute").reset_index(drop=True)
    closes, highs, lows = fd["close"].values, fd["high"].values, fd["low"].values
    mins = fd["minute"].values.astype(int)
    day_hi, day_lo = np.maximum.accumulate(highs), np.minimum.accumulate(lows)
    prev_hi = np.concatenate([[np.nan], day_hi[:-1]]); prev_lo = np.concatenate([[np.nan], day_lo[:-1]])
    r5 = np.log(closes / np.concatenate([[fd["open"].values[0]], closes[:-1]]))
    r2cum5 = np.cumsum(r5 ** 2)
    rows = []
    for i in range(len(fd)):
        m0 = mins[i] + 4
        r = fd.iloc[i]
        atr = r["atr14"] if np.isfinite(r["atr14"]) and r["atr14"] > 0 else np.nan
        rng = day_hi[i] - day_lo[i]
        exp_var = sigma0 ** 2 * max(cum_profile[min(m0 + 1, len(cum_profile) - 1)], 1e-6)
        rows.append({
            "minute": m0, "tod": m0 / SESSION_MINUTES, "is_expiry": is_expiry, "is_monthly": is_monthly,
            "gap_abs": abs(r["gap_pct"]), "gap_signed": r["gap_pct"], "rv20_rank": daily_row.get("rv20_rank", np.nan),
            "rv20": r["rv20"], "or30_rel": r["or30_rel"] if i >= 5 else np.nan,
            "range_sofar_rel": rng / r["day_open"] * 100 / r["range20"] if r["range20"] > 0 else np.nan,
            "pos_in_range": (closes[i] - day_lo[i]) / rng if rng > 0 else 0.5,
            "dist_high_atr": (day_hi[i] - closes[i]) / atr if atr else np.nan,
            "dist_low_atr": (closes[i] - day_lo[i]) / atr if atr else np.nan,
            "ret15": (closes[i] / closes[i - 3] - 1) * 100 if i >= 3 else 0.0,
            "ret30": (closes[i] / closes[i - 6] - 1) * 100 if i >= 6 else 0.0,
            "ret60": (closes[i] / closes[i - 12] - 1) * 100 if i >= 12 else 0.0,
            "bar_range_atr": (highs[i] - lows[i]) / atr if atr else np.nan,
            "rsi14": r["rsi14"], "bb_bw_pct": r["bb_bw_pct"],
            "ema_spread_atr": (r["ema9"] - r["ema21"]) / atr if atr else np.nan,
            "var_spent_ratio": r2cum5[i] / exp_var,
            "new_high": int(i > 0 and closes[i] > prev_hi[i]), "new_low": int(i > 0 and closes[i] < prev_lo[i]),
            "or_break_up": int(i >= 6 and closes[i] > r["or30_high"] and (i == 6 or closes[i - 1] <= r["or30_high"])),
            "or_break_dn": int(i >= 6 and closes[i] < r["or30_low"] and (i == 6 or closes[i - 1] >= r["or30_low"])),
            "weekday": weekday, "close": closes[i], "day_high": day_hi[i], "day_low": day_lo[i],
        })
    return pd.DataFrame(rows)
