"""Build report/otm_timetable.html: CAS-period timetable first (real bars when outputs/cas_month/cas_timetable.json exists,
otherwise the public-anchor reconstruction of the CAS expiries), pre-CAS model study folded away as reference.

    python report/build_timetable.py [--no-cas]      # --no-cas ignores cas_timetable.json even if present
"""
import argparse
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser(); ap.add_argument("--no-cas", action="store_true"); a = ap.parse_args()

data = json.loads((ROOT / "outputs/otm/timetable.json").read_text())
ctx = json.loads((ROOT / "outputs/otm/timetable_context.json").read_text())
for idx in ctx:
    for key in ("year", "vol_regime"):
        for r in ctx[idx][key]:
            for k, v in list(r.items()):
                r[k] = int(float(v)) if k == "yr" else (v if k == "vol_regime" else float(v))

cas_path = ROOT / "outputs/cas_month/cas_timetable.json"
cas = json.loads(cas_path.read_text()) if (cas_path.exists() and not a.no_cas) else None

# ---- public-anchor reconstruction of the CAS expiries (always embedded: it is the fallback and the day table's source)
e = pd.read_csv(ROOT / "outputs/cas_month/cas_month_expiries.csv")
lot = pd.read_csv(ROOT / "outputs/cas_month/cas_month_auction_lottery.csv")
days = []
for _, r in e.iterrows():
    L = lot[(lot["index"] == r["index"]) & (lot["date"] == r["date"])]
    ce = L[L["side"] == "CE"]["net_pct"]; pe = L[L["side"] == "PE"]["net_pct"]
    both = None
    if len(L) == 2:
        c2 = 2 * {"NIFTY": 0.5, "SENSEX": 1.0, "BANKNIFTY": 1.0}[r["index"]]
        p0 = float(L["p1515_est"].sum()); pay = float(L["settle"].sum())
        both = ((pay - c2) / (p0 + c2) - 1) * 100
    days.append({"index": r["index"], "date": r["date"], "verdict": r["verdict"], "signal_time": None if pd.isna(r["signal_time"]) else r["signal_time"],
                 "side": None if pd.isna(r["side"]) else r["side"], "p1515": float(r["p1515"]), "cas_close": float(r["cas_close"]),
                 "auction_move": float(r["auction_move"]), "ce_net": float(ce.iloc[0]) if len(ce) else None, "pe_net": float(pe.iloc[0]) if len(pe) else None, "both_net": both,
                 "k1_entry": None if pd.isna(r["k1_entry"]) else float(r["k1_entry"]),
                 "k1_net1515": None if pd.isna(r["k1_net1515"]) else float(r["k1_net1515"]),
                 "k1_netsettle": None if pd.isna(r["k1_netsettle"]) else float(r["k1_netsettle"])})
notes_public = [
    "Seven CAS expiries so far: four NIFTY Tuesdays and three SENSEX Thursdays (BANKNIFTY's monthly on 25 Aug shares the NIFTY day). The auction moved the close away from the 15:15 level on every one of them — +151, +21, −11 and +75 points on NIFTY, +169, +218 and +52 on SENSEX — six of seven upward.",
    "The 15:15 ticket: the nearest OTM call bought at 15:15 and held through the auction settled in profit on four of the seven days (roughly +724%, +436%, +91% and +174% on the model's estimate of the 15:15 price) and expired worthless on the other three; the put side expired worthless all seven times. That is what happened, not a rule — 13 Aug is the expiry SEBI later called manipulated, and the two biggest payouts came from the first week.",
    "Before the 14:55 cutoff the buy-score fired GO twice: 4 Aug 13:40 PE (the auction reversed the whole move — about −63% by 15:15, worthless at settlement) and 25 Aug 14:45 CE (+231% by 15:15, about 14× at settlement). On 11 and 18 Aug it correctly stayed out; on 18 Aug the only new low of the day came in the 15:00–15:15 sell-off, after the cutoff. SENSEX gave one LEAN (13 Aug 14:05 PE, lost) and one NO.",
    "Both sides at once — the nearest OTM call and put bought together at 15:15 and held through the auction — would have cost about ₹21–35 on NIFTY and ₹117–130 on SENSEX and paid on four of the seven days (+216%, +53%, +16%, +47%), losing the whole premium on the other three: a mean of roughly +8% per ticket against +166% for the call alone and −102% for the put alone. The strangle never beats the better single side on average — it is the same payoff bought twice over — what it buys is protection from picking the wrong side, which in these seven auctions was never the call. Whether that upward skew is a feature of the auction or of one month is exactly what more expiries will tell.",
    "What this cannot yet say is which half-hour and which strike work on CAS days as a habit. Two signals and seven auctions are anecdotes. The traded prints from Dhan turn the same seven days into roughly a thousand entry bars per index, which is where the grid above comes from.",
]
notes_real = []
if cas:
    for idx, c in cas.items():
        moves = [d["auction_move"] for d in c["day_rows"]]
        up = sum(1 for m in moves if m > 0)
        ce = [t for t in c["tickets"] if t["side"] == "CE"]; pe = [t for t in c["tickets"] if t["side"] == "PE"]; bo = [t for t in c["tickets"] if t["side"] == "BOTH"]
        sigs = [f'{d["date"]}: {", ".join(d["signals"])}' for d in c["day_rows"] if d["signals"]]
        notes_real.append(f"{idx}: {c['days']} CAS expiry day{'s' if c['days'] != 1 else ''} of traded prints. The auction moved the close up on {up} of {len(moves)} day{'s' if len(moves) != 1 else ''} "
                          f"(moves {', '.join(f'{m:+.0f}' for m in moves)}). The 15:10 nearest-OTM ticket settled in profit on "
                          f"{sum(1 for t in ce if t['net_pct'] > 0)}/{len(ce)} days for calls and {sum(1 for t in pe if t['net_pct'] > 0)}/{len(pe)} for puts; "
                          f"mean {sum(t['net_pct'] for t in ce) / max(len(ce), 1):+.0f}% (CE) and {sum(t['net_pct'] for t in pe) / max(len(pe), 1):+.0f}% (PE) per ticket; "
                          f"call and put together: {sum(1 for t in bo if t['net_pct'] > 0)}/{len(bo)} in profit, mean {sum(t['net_pct'] for t in bo) / max(len(bo), 1):+.0f}%. "
                          f"Signals before the cutoff: {'; '.join(sigs) if sigs else 'none'}.")
    notes_real.append("Caveats that ride every number above: eight expiry days in total is a handful, not a sample; SENSEX 13 Aug is the day SEBI found manipulated, so its +225 auction move and its call payoff are engineered, not organic; and the auction book is young and thin — the first-of-regime blasts (4 Aug NIFTY, 6 Aug SENSEX) may never repeat at that size.")
    notes_real.append("Read the grid with the bar counts in view: a handful of expiry days can make any half-hour look like a rule. The lottery rows (any bar) are the model-free baseline; the GO rows are what the indicator would have had you do.")
anchors = {"days": days, "notes_public": notes_public, "notes_real": notes_real}

head = (ROOT / "report/otm_timetable_template.html").read_text(encoding="utf-8")
head = head[: head.index("</style>") + len("</style>")]
body = (ROOT / "report/_timetable_body.html").read_text(encoding="utf-8")
J = lambda o: json.dumps(o, separators=(",", ":"), default=float)                 # noqa: E731
out = (head + "\n\n" + body).replace("{{DATA}}", J(data)).replace("{{CTX}}", J(ctx)).replace("{{CAS}}", J(cas)).replace("{{ANCHORS}}", J(anchors))
assert "{{" not in out
dest = ROOT / "report/otm_timetable.html"
dest.write_text(out, encoding="utf-8")
print(dest, len(out) // 1024, "KB", "| CAS grid:", "REAL" if cas else "pending (public anchors)")
