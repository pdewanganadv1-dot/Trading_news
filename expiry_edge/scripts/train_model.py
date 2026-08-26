"""Train and validate the expiry-day BUY-SCORE model.

Target (WHEN): y_straddle30 — the ATM straddle bought at this bar touches +30% within
60 minutes (i.e. the index moves enough, soon enough, to beat decay on the right leg).
Side (WHICH): a breakout of the day's range in the same bar (new_high -> CE, new_low -> PE).

Models: gradient boosting (ceiling) and a standardised logistic regression (deployable
in Python and Pine).  Time split: train <= 2021, test >= 2022, plus walk-forward by year.
Writes outputs/model/*.csv/json and the coefficient file used by the live indicator.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from expiry_edge.config import COST_PER_SIDE   # noqa: E402
from expiry_edge.score import LOGIT_FEATURES, engineer   # noqa: E402

OUT = ROOT / "outputs" / "model"
pd.set_option("display.width", 250)

GBM_FEATURES = ["tod", "is_expiry", "is_monthly", "gap_abs", "gap_signed", "rv20_rank", "rv20", "or30_rel", "range_sofar_rel",
                "pos_in_range", "dist_high_atr", "dist_low_atr", "ret15", "ret30", "ret60", "bar_range_atr", "rsi14",
                "bb_bw_pct", "ema_spread_atr", "var_spent_ratio", "new_high", "new_low", "or_break_up", "or_break_dn", "weekday"]
TRAIN_END = pd.Timestamp("2021-12-31")


def fit_logit(X: pd.DataFrame, y: np.ndarray, C: float = 0.5):
    mu, sd = X.mean(), X.std().replace(0, 1)
    Z = (X - mu) / sd
    lr = LogisticRegression(C=C, max_iter=2000)
    lr.fit(Z.values, y)
    return lr, mu, sd


def predict_logit(lr, mu, sd, X: pd.DataFrame) -> np.ndarray:
    Z = ((X - mu) / sd).values
    return lr.predict_proba(Z)[:, 1]


def calibration_table(p: np.ndarray, y: np.ndarray, bins=10) -> pd.DataFrame:
    q = pd.qcut(pd.Series(p).rank(method="first"), bins, labels=False)
    t = pd.DataFrame({"p": p, "y": y, "bin": q}).groupby("bin").agg(n=("y", "size"), p_mean=("p", "mean"), y_rate=("y", "mean"))
    t["lift"] = t["y_rate"] / y.mean()
    return t.round(3)


def trade_eval(df: pd.DataFrame, p: np.ndarray, thresholds, index_cost: float, min_minute: int = 0,
               direction: str = "breakout") -> pd.DataFrame:
    """Net 60-min P&L of buying the ATM leg chosen by the breakout direction when score >= tau.
    Only expiry-day bars with a directional trigger are tradeable."""
    d = df.copy()
    d["p"] = p
    if direction == "breakout":
        trig = (d["new_high"] == 1) | (d["new_low"] == 1)
        side_up = d["new_high"] == 1
    else:  # momentum fallback: sign of last 15-min return
        trig = d["ret15"].abs() > 0
        side_up = d["ret15"] > 0
    leg0 = np.where(side_up, d["ce0"], d["pe0"])
    leg_ret = np.where(side_up, d["ce_ret60"], d["pe_ret60"])
    leg_mfe = np.where(side_up, d["ce_mfe60"], d["pe_mfe60"])
    net = (leg0 * (1 + leg_ret) - index_cost) / (leg0 + index_cost) - 1
    d["net"], d["mfe"], d["trig"] = net, leg_mfe, trig
    d = d[(d["is_expiry"] == 1) & (d["minute"] >= min_minute)]
    rows = []
    for tau in thresholds:
        s = d[d["trig"] & (d["p"] >= tau)]
        if len(s) == 0:
            rows.append({"tau": tau, "trades": 0}); continue
        rows.append({"tau": tau, "trades": len(s), "days": s["date"].nunique(), "trades_per_expiry": len(s) / max(d["date"].nunique(), 1),
                     "p_mfe_ge50": (s["mfe"] >= 0.5).mean() * 100, "p_mfe_ge100": (s["mfe"] >= 1.0).mean() * 100,
                     "net_mean": s["net"].mean() * 100, "net_median": s["net"].median() * 100,
                     "p_net_pos": (s["net"] > 0).mean() * 100, "idx_hit": (np.where(s["new_high"] == 1, s["idx_ret60"], -s["idx_ret60"]) > 0).mean() * 100,
                     "y_rate": s["y_straddle30"].mean() * 100})
    return pd.DataFrame(rows).round(2)


def main():
    frames = []
    for idx in ["NIFTY", "BANKNIFTY", "SENSEX"]:
        f = pd.read_parquet(OUT / f"{idx}_bars.parquet")
        frames.append(f)
    allbars = engineer(pd.concat(frames, ignore_index=True))
    # OI features are constant (neutral) unless the dataset carries real option OI (a Dhan pull);
    # a constant column breaks standardisation, so fit only the features that actually vary here.
    usable_feats = [f for f in LOGIT_FEATURES if f in allbars.columns and allbars[f].notna().any() and allbars[f].std(skipna=True) > 1e-9]
    allbars = allbars.dropna(subset=usable_feats + ["y_straddle30"])
    report = {}

    # ------------------------------------------------------------------ pooled NIFTY+BANKNIFTY model
    pool = allbars[allbars["index"].isin(["NIFTY", "BANKNIFTY"])]
    train, test = pool[pool["date"] <= TRAIN_END], pool[pool["date"] > TRAIN_END]
    y_tr, y_te = train["y_straddle30"].values, test["y_straddle30"].values
    print(f"pooled train {len(train)} bars ({train.date.nunique()} days) / test {len(test)} bars ({test.date.nunique()} days); "
          f"base rate train {y_tr.mean():.3f} test {y_te.mean():.3f}")

    gbm = HistGradientBoostingClassifier(max_iter=400, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=300,
                                         l2_regularization=1.0, early_stopping=True, validation_fraction=0.15, random_state=7)
    gbm.fit(train[GBM_FEATURES], y_tr)
    p_gbm = gbm.predict_proba(test[GBM_FEATURES])[:, 1]
    lr, mu, sd = fit_logit(train[usable_feats], y_tr)
    p_lr = predict_logit(lr, mu, sd, test[usable_feats])
    te_exp = test["is_expiry"] == 1
    for name, p in [("gbm", p_gbm), ("logit", p_lr)]:
        report[f"{name}_auc_all"] = roc_auc_score(y_te, p)
        report[f"{name}_auc_expiry"] = roc_auc_score(y_te[te_exp], p[te_exp])
        report[f"{name}_ap_expiry"] = average_precision_score(y_te[te_exp], p[te_exp])
        print(f"{name}: AUC all {report[f'{name}_auc_all']:.3f} | expiry-day AUC {report[f'{name}_auc_expiry']:.3f} "
              f"AP {report[f'{name}_ap_expiry']:.3f} (base {y_te[te_exp].mean():.3f})")
    # direction model: is the side predictable?
    lr_dir, mu_d, sd_d = fit_logit(train[GBM_FEATURES].fillna(0), train["y_up60"].values)
    p_dir = predict_logit(lr_dir, mu_d, sd_d, test[GBM_FEATURES].fillna(0))
    report["direction_auc"] = roc_auc_score(test["y_up60"].values, p_dir)
    gbm_dir = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=300, random_state=7)
    gbm_dir.fit(train[GBM_FEATURES], train["y_up60"].values)
    report["direction_auc_gbm"] = roc_auc_score(test["y_up60"].values, gbm_dir.predict_proba(test[GBM_FEATURES])[:, 1])
    print(f"direction (up/down in 60 min) AUC: logit {report['direction_auc']:.3f}, gbm {report['direction_auc_gbm']:.3f}  -> ~0.5 means the side is not predictable from these features")

    # calibration on expiry-day test bars
    cal = calibration_table(p_lr[te_exp], y_te[te_exp])
    cal.to_csv(OUT / "calibration_logit_expiry_test.csv")
    cal_g = calibration_table(p_gbm[te_exp], y_te[te_exp])
    cal_g.to_csv(OUT / "calibration_gbm_expiry_test.csv")
    print("\nlogit calibration (expiry-day test bars, deciles):"); print(cal.to_string())

    # coefficients (standardised) for transparency + deployment
    coef = pd.Series(lr.coef_[0], index=LOGIT_FEATURES).sort_values()
    coef.to_csv(OUT / "logit_coefficients_standardised.csv")
    print("\nstandardised logit coefficients:"); print(coef.round(3).to_string())
    # GBM permutation-free importance proxy: drop-column AUC on test (fast enough for 25 features)
    imp = {}
    base_auc = report["gbm_auc_expiry"]
    for c in GBM_FEATURES:
        Xp = test[GBM_FEATURES].copy()
        Xp[c] = np.random.RandomState(0).permutation(Xp[c].values)
        imp[c] = base_auc - roc_auc_score(y_te[te_exp], gbm.predict_proba(Xp)[:, 1][te_exp])
    imp = pd.Series(imp).sort_values(ascending=False)
    imp.to_csv(OUT / "gbm_permutation_importance_expiry_test.csv")
    print("\nGBM permutation importance (AUC drop, expiry-day test):"); print(imp.round(4).head(12).to_string())

    # ------------------------------------------------------------------ trading evaluation on test expiry days
    thresholds = [0.0, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]
    evals = {}
    for idx in ["NIFTY", "BANKNIFTY"]:
        sub = test[test["index"] == idx]
        pl = predict_logit(lr, mu, sd, sub[LOGIT_FEATURES])
        pg = gbm.predict_proba(sub[GBM_FEATURES])[:, 1]
        for name, p in [("logit", pl), ("gbm", pg)]:
            t = trade_eval(sub, p, thresholds, COST_PER_SIDE[idx])
            t.insert(0, "model", name); t.insert(0, "index", idx)
            evals[(idx, name)] = t
            print(f"\n{idx} {name} — buy ATM leg on breakout when score >= tau (test expiry days {sub[sub.is_expiry==1].date.nunique()}):")
            print(t.to_string(index=False))
        # reference: the plain late-break rule (no score) on the same test days
        late = trade_eval(sub, np.ones(len(sub)), [0.0], COST_PER_SIDE[idx], min_minute=300)
        late.insert(0, "model", "LateBreak_1415_rule"); late.insert(0, "index", idx)
        evals[(idx, "late")] = late
        print(f"{idx} reference: breakout after 14:15, no score:"); print(late.to_string(index=False))
    ev = pd.concat(evals.values(), ignore_index=True)
    ev.to_csv(OUT / "trade_eval_test.csv", index=False)

    # SENSEX transfer check (never seen in training)
    sx = allbars[allbars["index"] == "SENSEX"]
    p_sx = predict_logit(lr, mu, sd, sx[LOGIT_FEATURES])
    report["sensex_auc_expiry"] = roc_auc_score(sx[sx.is_expiry == 1]["y_straddle30"], p_sx[(sx.is_expiry == 1).values])
    t_sx = trade_eval(sx, p_sx, thresholds, COST_PER_SIDE["SENSEX"])
    t_sx.to_csv(OUT / "trade_eval_sensex_transfer.csv", index=False)
    print(f"\nSENSEX transfer (model never saw SENSEX): expiry-day AUC {report['sensex_auc_expiry']:.3f}"); print(t_sx.to_string(index=False))

    # ------------------------------------------------------------------ walk-forward by year (logit, pooled)
    wf = []
    for yr in range(2019, 2025):
        tr = pool[pool["date"].dt.year < yr]; te = pool[pool["date"].dt.year == yr]
        if len(tr) < 5000 or te["is_expiry"].sum() < 200:
            continue
        l, m_, s_ = fit_logit(tr[LOGIT_FEATURES], tr["y_straddle30"].values)
        p = predict_logit(l, m_, s_, te[LOGIT_FEATURES])
        e = te["is_expiry"] == 1
        row = {"year": yr, "train_days": tr.date.nunique(), "test_expiry_days": te[e].date.nunique(),
               "auc_expiry": roc_auc_score(te[e]["y_straddle30"], p[e.values])}
        for idx in ["NIFTY", "BANKNIFTY"]:
            sub = te[te["index"] == idx]
            if len(sub) == 0:
                continue
            tt = trade_eval(sub, p[(te["index"] == idx).values], [0.35], COST_PER_SIDE[idx])
            if len(tt) and tt.iloc[0].get("trades", 0) > 0:
                row[f"{idx}_trades"] = tt.iloc[0]["trades"]; row[f"{idx}_net_mean"] = tt.iloc[0]["net_mean"]; row[f"{idx}_p_mfe50"] = tt.iloc[0]["p_mfe_ge50"]
        wf.append(row)
    wf = pd.DataFrame(wf).round(3)
    wf.to_csv(OUT / "walkforward_logit.csv", index=False)
    print("\nwalk-forward (train on all prior years, tau=0.35):"); print(wf.to_string(index=False))

    # ------------------------------------------------------------------ final deployable model: fit on ALL pooled data
    lr_f, mu_f, sd_f = fit_logit(pool[LOGIT_FEATURES], pool["y_straddle30"].values)
    p_full = predict_logit(lr_f, mu_f, sd_f, pool[LOGIT_FEATURES])
    spec = {"features": LOGIT_FEATURES, "mean": mu_f.round(6).to_dict(), "std": sd_f.round(6).to_dict(),
            "coef": dict(zip(LOGIT_FEATURES, np.round(lr_f.coef_[0], 6))), "intercept": float(np.round(lr_f.intercept_[0], 6)),
            "target": "P(ATM straddle bought at bar close touches +30% within 60 min)", "horizon_min": 60,
            "train": {"indices": ["NIFTY", "BANKNIFTY"], "bars": int(len(pool)), "days": int(pool.date.nunique()),
                      "from": str(pool.date.min().date()), "to": str(pool.date.max().date())},
            "base_rate": float(pool["y_straddle30"].mean()),
            "score_thresholds": {"lean": 0.30, "go": 0.40},
            "test_metrics": {k: float(v) for k, v in report.items()},
            "full_fit_auc_in_sample": float(roc_auc_score(pool["y_straddle30"], p_full))}
    with open(OUT / "buy_score_logit.json", "w") as fh:
        json.dump(spec, fh, indent=1)
    print("\nwrote", OUT / "buy_score_logit.json")


if __name__ == "__main__":
    main()
