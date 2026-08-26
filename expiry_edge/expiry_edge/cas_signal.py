"""The fused CAS decision: the chart buy-score for the intraday blast, the OI auction model for the settlement blast.

Under CAS an OTM option can pay two different ways, and they want two different triggers:

  INTRADAY blast   a directional move during the session fattens the premium — the chart buy-score
                   (score.py) already predicts this; exit <= 60 min or by 15:15.
  SETTLEMENT blast the closing auction jumps the index and the option settles deep ITM — this is an
                   order-imbalance event, predicted from the 15:10 OI structure by scripts/auction_model.py.

This module loads the deployable auction model (outputs/model/auction_model.json, written by auction_model.py
once enough CAS expiries exist) and turns an OI snapshot into an auction verdict, then fuses the two.  If the
auction model is absent or its walk-forward was not trustworthy, the auction trigger stays OFF and only the
chart score fires — the system never invents an imbalance edge it has not measured.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .config import CONTRACT
from .oi_features import oi_features

AUCTION_MODEL_PATH = Path(__file__).resolve().parent.parent / "outputs" / "model" / "auction_model.json"


class AuctionModel:
    """P(|auction move| >= threshold) from the 15:10 OI structure; OFF unless a trustworthy model exists."""

    def __init__(self, path: Path = AUCTION_MODEL_PATH):
        self.ok = False
        if Path(path).exists():
            s = json.load(open(path))
            self.spec = s
            self.ok = bool(s.get("trustworthy"))
            self.features = s["features"]
            self.mu = np.array([s["mean"][f] for f in self.features])
            self.sd = np.array([s["std"][f] for f in self.features])
            self.coef = np.array([s["coef"][f] for f in self.features])
            self.b0 = s["intercept"]

    def p_big(self, feat: dict) -> float:
        if not self.ok:
            return float("nan")
        z = (np.array([feat.get(f, 0.0) for f in self.features]) - self.mu) / self.sd
        return float(1 / (1 + np.exp(-(z @ self.coef + self.b0))))


def auction_verdict(snapshot, spot: float, index: str, pre_auction_range_pct: float, monthly: bool,
                    model: "AuctionModel", go: float = 0.60):
    """Verdict for the settlement-blast trade at the cutoff.  Returns (verdict, side, p_big, reason).
    side comes from the max-pain pull (where the OI wants the close): toward the pin from 15:10 spot."""
    f = oi_features(snapshot, spot, CONTRACT[index]["strike_step"])
    if not model.ok:
        return "OFF (no trustworthy auction model yet)", None, float("nan"), \
               "collect more CAS expiries and retrain (scripts/auction_model.py)"
    feat = {**f, "pre_auction_range_pct": pre_auction_range_pct, "monthly": int(bool(monthly)), "is_cas": 1}
    p = model.p_big(feat)
    # direction: the pin pull.  mp_pull > 0 means max-pain is above spot -> a CE settlement blast; < 0 -> PE.
    side = "CE" if f["mp_pull"] > 0 else ("PE" if f["mp_pull"] < 0 else None)
    verdict = "AUCTION GO" if p >= go else ("AUCTION LEAN" if p >= go - 0.15 else "no auction signal")
    return verdict, side, p, f"max-pain {f['max_pain']:.0f} ({f['mp_dist_pct']:+.2f}% from spot), ATM-OI {f['atm_oi_share']*100:.0f}%, PCR {f['pcr_oi']:.2f}"


def fuse(chart_verdict: str, chart_side: int, auction_verdict_str: str, auction_side: str | None):
    """Combine the two triggers into the deployed call.  They are independent reasons to buy an OTM option;
    either can fire.  When both fire on the same side, that is the strongest setup."""
    chart_go = chart_verdict.startswith(("GO", "LEAN"))
    auc_go = auction_verdict_str.startswith("AUCTION")
    cs = "CE" if chart_side == 1 else ("PE" if chart_side == -1 else None)
    if chart_go and auc_go and cs == auction_side:
        return f"STRONG {cs} — chart blast and auction imbalance agree"
    if chart_go and auc_go:
        return f"MIXED — chart says {cs} (intraday), auction says {auction_side} (settlement); size down or pick one"
    if chart_go:
        return f"{chart_verdict} {cs} — intraday blast only"
    if auc_go:
        return f"{auction_verdict_str} {auction_side} — settlement blast only (hold through the auction)"
    return "no signal"
