"""Build/refresh the parquet caches (1-min, 5-min, daily+calendar) for each index."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from expiry_edge.calendar import label_expiry_days      # noqa: E402
from expiry_edge.data import daily_table, load_minute, resample  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"

for idx in sys.argv[1:] or ["NIFTY", "SENSEX", "BANKNIFTY"]:
    m = load_minute(idx)
    d = daily_table(m).join(label_expiry_days(daily_table(m).index, idx))
    b5 = resample(m, 5)
    m.to_parquet(DATA / f"{idx}_1m.parquet")
    b5.to_parquet(DATA / f"{idx}_5m.parquet")
    d.to_parquet(DATA / f"{idx}_daily.parquet")
    e = d[d.is_expiry]
    print(f"{idx}: {len(d)} sessions {d.index.min().date()}..{d.index.max().date()} | "
          f"{len(e)} expiry days (weekday counts {e.index.dayofweek.value_counts().sort_index().to_dict()})")
