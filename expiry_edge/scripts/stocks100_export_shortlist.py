"""Stage-2: export expiry-week option bars ONLY for the spike-shortlisted stocks."""
import json
from pathlib import Path

from stocks100_export import OUT, export_stock

import time

shortlist = json.load(open(OUT / "spike_shortlist.json"))
ids = json.load(open(OUT / "top100.json"))
t0 = time.time()
for n, sym in enumerate(shortlist, 1):
    f = OUT / f"rolling_options_{sym}.csv"
    if f.exists() and f.stat().st_size > 100:
        print(f"[{n}/{len(shortlist)}] {sym}: exists, skip", flush=True)
        continue
    if sym not in ids:
        print(f"[{n}/{len(shortlist)}] {sym}: no id, skip", flush=True)
        continue
    nrows = export_stock(sym, ids[sym])
    print(f"[{n}/{len(shortlist)}] {sym}: {nrows} rows  ({time.time() - t0:.0f}s)", flush=True)
print(f"done in {time.time() - t0:.0f}s")
