"""Build the bar-level model dataset for each index -> outputs/model/<IDX>_bars.parquet"""
import sys, time
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from expiry_edge.model import build_dataset
from expiry_edge.config import LIQUID_WEEKLY_FROM, LIQUID_WEEKLY_TO
OUT = ROOT / "outputs" / "model"; OUT.mkdir(parents=True, exist_ok=True)
for idx in sys.argv[1:] or ["NIFTY", "SENSEX", "BANKNIFTY"]:
    t = time.time()
    m = pd.read_parquet(ROOT / f"data/{idx}_1m.parquet"); b5 = pd.read_parquet(ROOT / f"data/{idx}_5m.parquet")
    d = pd.read_parquet(ROOT / f"data/{idx}_daily.parquet")
    prof = np.load(ROOT / f"outputs/{idx}_profile_expiry.npy")
    lo, hi = LIQUID_WEEKLY_FROM[idx], LIQUID_WEEKLY_TO[idx]
    mask = (d.index.date >= lo) & ((d.index.date < hi) if hi else True)
    ds = build_dataset(m, b5, d, idx, prof, dates=d[mask].index)
    ds["index"] = idx
    ds.to_parquet(OUT / f"{idx}_bars.parquet")
    print(f"{idx}: {len(ds)} bars, {ds.date.nunique()} days, expiry bars {int(ds.is_expiry.sum())}, "
          f"P(straddle+30%)={ds.y_straddle30.mean():.3f} (expiry {ds[ds.is_expiry==1].y_straddle30.mean():.3f}), {time.time()-t:.0f}s")
