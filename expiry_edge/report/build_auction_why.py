"""Fill the 'Why the Auction Blasts' explainer with the seven CAS expiries' numbers + the regime-decay monitor."""
import contextlib
import io
import json
import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import cas_regime_decay as crd                                              # noqa: E402
_zip = sys.argv[1] if len(sys.argv) > 1 else None
_argv = sys.argv; sys.argv = ["cas_regime_decay"] + (["--zip", _zip] if _zip else [])
with contextlib.redirect_stdout(io.StringIO()):
    crd.main()
sys.argv = _argv
decay = json.loads((ROOT / "outputs/cas_month/cas_regime_decay.json").read_text())
e = pd.read_csv(ROOT / "outputs/cas_month/cas_month_expiries.csv")
lot = pd.read_csv(ROOT / "outputs/cas_month/cas_month_auction_lottery.csv")
first = {("NIFTY", "2026-08-04"), ("SENSEX", "2026-08-06")}          # first CAS weekly on each exchange
manip = {("SENSEX", "2026-08-13")}                                    # SEBI interim order 19 Aug 2026
days = []
for _, r in e.iterrows():
    ce = lot[(lot["index"] == r["index"]) & (lot["date"] == r["date"]) & (lot["side"] == "CE")]["net_pct"]
    days.append({"index": r["index"], "date": r["date"], "monthly": r["date"] == "2026-08-25", "first": (r["index"], r["date"]) in first,
                 "manip": (r["index"], r["date"]) in manip, "range": float(r["pre_auction_range_pct"]),
                 "move": float(r["auction_move"]), "move_pct": float(r["auction_move_pct"]), "abs_move": abs(float(r["auction_move_pct"])),
                 "ce_net": float(ce.iloc[0]) if len(ce) else None})
days.sort(key=lambda d: d["date"])
tpl = (ROOT / "report/auction_why_template.html").read_text(encoding="utf-8")
out = tpl.replace("{{DAYS}}", json.dumps(days, separators=(",", ":"))).replace("{{DECAY}}", json.dumps(decay, separators=(",", ":")))
assert "{{" not in out
dest = ROOT / "report/auction_why.html"; dest.write_text(out, encoding="utf-8")
print(dest, len(out) // 1024, "KB")
