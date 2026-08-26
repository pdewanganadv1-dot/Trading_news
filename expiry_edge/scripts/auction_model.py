"""The imbalance trigger: predict the closing-auction move on an expiry day from the 15:10 OI structure.

Two heads, both model-free in their inputs (only OI + strikes + the calendar):
  MAG   P(|auction move| is 'big')  — big = top-third of |move%| in the training window (or --big-thresh)
  DIR   P(auction move is UP)       — only trained/evaluated on days the MAG head flags, where direction pays
Tiny, strongly-regularised logistic regressions (few expiries — must not overfit), validated by STRICT
walk-forward: sorted by date, expiry k is predicted using only expiries < k (expanding window, refit each step).
That is the number that matters — in-sample fit on 40-odd expiries is meaningless.

    # build the per-expiry feature table from a Dhan export, then walk-forward:
    python scripts/auction_model.py --zip dhan_export.zip
    # or validate the harness on a prepared table (columns = OI_FEATURES + move_pct + date + index):
    python scripts/auction_model.py --table features.csv

Writes outputs/model/auction_features.csv (the table) and outputs/model/auction_walkforward.json
(the out-of-sample scores + per-expiry predictions).  Prints an honest scorecard, including a
scrambled-target control so the AUC can be trusted (a real edge beats the control; noise ties it).
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
from expiry_edge.config import CAS_START_DATE, CONTRACT                             # noqa: E402
from expiry_edge.oi_features import FEATURES as OI_ALL, oi_features, snapshot_from_rolling   # noqa: E402

OUT = ROOT / "outputs" / "model"; OUT.mkdir(parents=True, exist_ok=True)
EXIT_T = dt.time(15, 10)
FEATS = ["mp_pull", "atm_oi_share", "oi_hhi", "pcr_oi", "net_atm_skew", "pre_auction_range_pct", "monthly", "is_cas"]


def _standardise(tr, te):
    mu, sd = tr.mean(0), tr.std(0); sd[sd < 1e-9] = 1.0
    return (tr - mu) / sd, (te - mu) / sd


def _fit_logit(X, y, C=0.3, iters=500, lr=0.1):
    """Plain L2-regularised logistic regression by gradient descent (no sklearn dependency at runtime)."""
    n, d = X.shape; w = np.zeros(d); b = 0.0
    if len(np.unique(y)) < 2:
        return w, (np.log((y.mean() + 1e-6) / (1 - y.mean() + 1e-6)) if 0 < y.mean() < 1 else 0.0)
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ w + b)))
        g = p - y
        w -= lr * (X.T @ g / n + w / (C * n))
        b -= lr * g.mean()
    return w, b


def _auc(y, p):
    y = np.asarray(y); p = np.asarray(p)
    pos, neg = p[y == 1], p[y == 0]
    if not len(pos) or not len(neg):
        return np.nan
    # Mann-Whitney U
    order = np.argsort(np.concatenate([pos, neg]))
    ranks = np.empty_like(order, dtype=float); ranks[order] = np.arange(1, len(order) + 1)
    r_pos = ranks[:len(pos)].sum()
    return (r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def walk_forward(df: pd.DataFrame, feats: list[str], target: str, min_train: int = 12, C: float = 0.3, seed_scramble: int = 0):
    """Expanding-window walk-forward.  Returns per-row out-of-sample prob (NaN before min_train) and the AUC."""
    d = df.sort_values("date").reset_index(drop=True)
    y = d[target].values.astype(float)
    if seed_scramble:                                                    # control: break the feature->target link
        rng = np.random.default_rng(seed_scramble); y = rng.permutation(y)
    X = d[feats].values.astype(float)
    oos = np.full(len(d), np.nan)
    for k in range(min_train, len(d)):
        Xtr, Xte = _standardise(X[:k], X[k:k + 1])
        w, b = _fit_logit(Xtr, y[:k], C=C)
        oos[k] = float(1 / (1 + np.exp(-(Xte[0] @ w + b))))
    mask = ~np.isnan(oos)
    return oos, _auc(y[mask], oos[mask]), int(mask.sum())


def build_table_from_export(zip_path: Path, since: str | None) -> pd.DataFrame:
    from cas_month_check_real import build_bars5, build_daily, load_export        # noqa: E402
    files = load_export(zip_path); rows = []
    for idx in ("NIFTY", "SENSEX", "BANKNIFTY"):
        if any(f"{p}_{idx}.csv" not in files for p in ("index_5m", "index_daily", "rolling_options")):
            continue
        bars5 = build_bars5(files[f"index_5m_{idx}.csv"]); d = build_daily(files[f"index_daily_{idx}.csv"], idx)
        opt = files[f"rolling_options_{idx}.csv"].copy(); opt["ts"] = pd.to_datetime(opt["ts"])
        step = CONTRACT[idx]["strike_step"]
        exp = [x for x in sorted(set(opt["ts"].dt.date)) if x in d.index.date and bool(d.loc[pd.Timestamp(x), "is_expiry"])]
        if since:
            exp = [x for x in exp if x >= pd.Timestamp(since).date()]
        for date in exp:
            today = bars5[bars5["date"] == date].sort_values("minute")
            pre = today[today["ts"].dt.time <= EXIT_T]
            if not len(pre):
                continue
            p1510 = float(pre["close"].iloc[-1]); close_cas = float(d.loc[pd.Timestamp(date), "close"])
            snap = snapshot_from_rolling(opt, date, EXIT_T)
            f = oi_features(snap, p1510, step)
            rows.append({"index": idx, "date": pd.Timestamp(date), **{k: f[k] for k in OI_ALL},
                         "pre_auction_range_pct": float((pre["high"].max() - pre["low"].min()) / pre["open"].iloc[0] * 100),
                         "monthly": int(bool(d.loc[pd.Timestamp(date), "is_monthly"])),
                         "is_cas": int(pd.Timestamp(date).date() >= CAS_START_DATE),
                         "move_pct": (close_cas / p1510 - 1) * 100})     # 15:10 -> official close; an AUCTION move on CAS days, a close-drift proxy before
    return pd.DataFrame(rows)


def scorecard(df: pd.DataFrame, big_thresh: float | None):
    df = df.dropna(subset=["move_pct"]).sort_values("date").reset_index(drop=True)
    n = len(df)
    thr = big_thresh if big_thresh is not None else float(np.quantile(df["move_pct"].abs(), 2 / 3))
    df["big"] = (df["move_pct"].abs() >= thr).astype(int)
    df["up"] = (df["move_pct"] > 0).astype(int)
    feats = [f for f in FEATS if f in df.columns and df[f].std() > 1e-9]
    n_cas = int(df["is_cas"].sum()) if "is_cas" in df.columns else 0
    out = {"n_expiries": n, "n_cas_expiries": n_cas, "n_precas_expiries": n - n_cas,
           "target_note": ("15:10->close: an AUCTION move on the %d CAS days, a close-drift proxy on the %d pre-CAS days "
                           "(same dealer-hedging mechanism, no auction amplification). Pooled here for sample size; "
                           "the CAS-specific edge needs the CAS days to accumulate." % (n_cas, n - n_cas)),
           "big_threshold_pct": round(thr, 3), "base_rate_big": round(df["big"].mean(), 3),
           "base_rate_up": round(df["up"].mean(), 3), "features": feats, "min_train": min(12, max(6, n // 3))}
    mt = out["min_train"]
    if n < mt + 3:
        out["status"] = f"only {n} expiries — need ~{mt + 3}+ for a walk-forward read; collect more (FULL_HISTORY pull)."
        return out, df
    mag, auc_mag, k = walk_forward(df, feats, "big", min_train=mt)
    df["p_big"] = mag
    # Honest control: a permutation NULL, not one scramble.  Refit the whole walk-forward on many
    # target permutations; the real AUC only counts as an edge if it beats the null distribution
    # (p = fraction of scrambles >= real).  On pure noise the real AUC lands inside the null -> p high.
    n_perm = 200
    null = np.array([walk_forward(df, feats, "big", min_train=mt, seed_scramble=1000 + i)[1] for i in range(n_perm)])
    null = null[~np.isnan(null)]
    p_val = float((null >= auc_mag).mean()) if len(null) and not np.isnan(auc_mag) else np.nan
    dmask = df["big"] == 1
    if dmask.sum() >= mt + 3:
        dirdf = df[dmask].reset_index(drop=True)
        _, auc_dir, _ = walk_forward(dirdf, feats, "up", min_train=min(8, max(5, dmask.sum() // 3)))
        out["dir_auc_on_big_days"] = None if np.isnan(auc_dir) else round(auc_dir, 3)
    else:
        out["dir_auc_on_big_days"] = None
    out.update({"oos_days": k, "mag_auc": None if np.isnan(auc_mag) else round(auc_mag, 3),
                "null_auc_mean": round(float(np.nanmean(null)), 3), "null_auc_p95": round(float(np.nanpercentile(null, 95)), 3),
                "permutation_p_value": None if np.isnan(p_val) else round(p_val, 3), "n_permutations": int(len(null))})
    out["reads"] = ("real imbalance edge — MAG AUC is above the permutation null (p < 0.05)" if (p_val is not None and p_val < 0.05)
                    else f"NO usable edge yet — MAG AUC sits inside the noise band (p = {p_val:.2f}); more expiries needed")
    return out, df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", default=None); ap.add_argument("--table", default=None)
    ap.add_argument("--since", default=None, help="only expiries on/after this date")
    ap.add_argument("--big-thresh", type=float, default=None, help="|move %%| that counts as 'big' (default: top third)")
    a = ap.parse_args()
    if a.table:
        df = pd.read_csv(a.table); df["date"] = pd.to_datetime(df["date"])
    elif a.zip:
        df = build_table_from_export(Path(a.zip), a.since)
        df.to_csv(OUT / "auction_features.csv", index=False); print("wrote", OUT / "auction_features.csv", f"({len(df)} expiries)")
    else:
        raise SystemExit("give --zip dhan_export.zip or --table features.csv")
    card, scored = scorecard(df, a.big_thresh)
    (OUT / "auction_walkforward.json").write_text(json.dumps(card, indent=2))
    print(json.dumps(card, indent=2))
    if "p_big" in scored.columns:
        scored[["index", "date", "move_pct", "big", "up", "p_big"] + card["features"]].to_csv(OUT / "auction_predictions.csv", index=False)
        # deployable coefficients: fit the MAG head on ALL expiries (the walk-forward AUC above says whether to trust it).
        feats = card["features"]; X = scored[feats].values.astype(float)
        mu, sd = X.mean(0), X.std(0); sd[sd < 1e-9] = 1.0
        w, b = _fit_logit((X - mu) / sd, scored["big"].values.astype(float))
        deploy = {"features": feats, "mean": dict(zip(feats, mu.round(6))), "std": dict(zip(feats, sd.round(6))),
                  "coef": dict(zip(feats, w.round(6))), "intercept": float(b), "big_threshold_pct": card["big_threshold_pct"],
                  "walk_forward_auc": card.get("mag_auc"), "permutation_p_value": card.get("permutation_p_value"),
                  "trustworthy": bool(card.get("permutation_p_value") is not None and card["permutation_p_value"] < 0.05),
                  "note": "P(|auction move| >= big_threshold) from the 15:10 OI structure. Deploy ONLY if trustworthy=true; "
                          "otherwise it is not better than the base rate at this sample size."}
        (OUT / "auction_model.json").write_text(json.dumps(deploy, indent=2, default=float))
        print("\nwrote", OUT / "auction_model.json", f"(trustworthy={deploy['trustworthy']})")


if __name__ == "__main__":
    main()
