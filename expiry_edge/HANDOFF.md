# HANDOFF — finish the CAS-month check on REAL Dhan data

This bundle is the complete `expiry_edge` toolkit minus the raw minute-data caches (the 225 MB of
parquet files are not needed for this job).  It was built in a Cowork cloud session whose sandbox
could not reach `api.dhan.co` (egress allowlist), so the August-2026 CAS check in the report is a
**public-anchor reconstruction** (`scripts/cas_month_check.py`).  The job now is to redo it with
**real** 5-minute index bars and **real** expired-option bars from Piyush's Dhan account, which
needs a session whose environment allows Dhan.

Report artifact to update in place (owner: Piyush):
`https://claude.ai/code/artifact/51c5c664-09dd-4a3d-bf3d-b96bdcdbc8da`
Section to replace: `report/template.html` → `<h2>The CAS month, checked` (line ≈412) and the
one-line mention in "The short version".

## 0. What the environment must provide

* Network: `api.dhan.co` and `images.dhan.co` reachable (Custom allowlist or Full access).
  `*.frame.claudeusercontent.com` is needed only if this bundle is being fetched out of the handoff artifact.
* `DHAN_ACCESS_TOKEN` set as an environment variable (preferred over pasting it into chat).
  Optional: `DHAN_CLIENT_ID`.
* Python 3.10+, `pip install -r requirements.txt` (pyarrow/matplotlib are only needed to rebuild
  charts; the real check itself needs pandas, numpy, scipy, requests).

Check reachability first — the failure mode in the old sandbox was a proxy refusal, not an auth error:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://api.dhan.co/v2/charts/intraday \
  -H "access-token: $DHAN_ACCESS_TOKEN" -H "Content-Type: application/json" -d '{}'
# 000 = still blocked at the proxy (CONNECT 403). Any HTTP status (400/401/…) = reachable; go on.
```

## 1. Smoke test (no network)

```bash
python scripts/make_synthetic_export.py --out /tmp/synth.zip
python scripts/cas_month_check_real.py --zip /tmp/synth.zip        # must run end to end, prints per-expiry blocks
```

## 2. Pull the real data (≈2–5 min)

```bash
cd scripts && python dhan_export.py && cd ..          # writes scripts/dhan_export/ + scripts/dhan_export.zip
cat scripts/dhan_export/log.txt                      # look for "HTTP 4xx" and "no data" lines
```

What it pulls: 5-min index bars from 2026-06-01 (NIFTY/BANKNIFTY/SENSEX, `charts/intraday`, IDX_I ids
13/25/51), 1-min bars for the August expiry days, 2 years of daily bars (`charts/historical`), and the
expired weekly-option minute bars at strikes ATM-3..ATM+3 relative to spot for 2026-08-01..08-28
(`charts/rollingoption`, NSE_FNO for NIFTY/BANKNIFTY, BSE_FNO for SENSEX).  The rolling-option call is
tried with (OPTIDX,0), (OPTIDX,1), (INDEX,0), (INDEX,1) until one returns data; response shape is
`{"data": {"ce": {...}, "pe": {...}}}` with epoch timestamps (converted to IST).

Known hiccups:
* `HTTP 401/403` with a JSON body → token invalid/expired, or the account's **Data API** is not enabled
  (Dhan's historical/chart endpoints are a separate switch from the trading API).  Ask Piyush.
* `HTTP 429` → the script backs off and retries; slow down with a larger `time.sleep` if it persists.
* SENSEX rolling options via `BSE_FNO` were never verified — if every SENSEX strike logs "no data",
  keep NIFTY/BANKNIFTY and say so in the report.
* `toDate` is exclusive; `CAS_TO` = 2026-08-28 covers the 27-Aug SENSEX expiry only if run on/after 28 Aug.
* Set `FULL_HISTORY = True` for Sep-2025..Jul-2026 rolling options (30-day chunks, several minutes) —
  optional, for a real-premium validation of the model.

## 3. Run the real check

```bash
python scripts/cas_month_check_real.py --zip scripts/dhan_export.zip
# optional: --dates 2026-08-04,2026-08-11,2026-08-18,2026-08-25 (applies to every index)
```

Outputs `outputs/cas_month/real_signals.csv` (every GO/LEAN bar: suggested strike, real entry, max within
60 min, 15:10 exit, settlement — for ATM, 1-OTM, 2-OTM), `real_calibration.csv` (real vs model straddle
at open and 15:10), `real_auction_lottery.csv` (nearest-OTM CE/PE at 15:10 → settlement).

Compare with the reconstruction in `outputs/cas_month/cas_month_expiries.csv` /
`cas_month_auction_lottery.csv`.  Things to state explicitly in the report: which signals fired at the
same bars as the reconstruction, real vs modelled premiums (the model's 25 % auction-variance reserve
was calibrated to a Zerodha observation, so the 15:10 straddle comparison is the key number), and
the auction-lottery result on real 15:10 prices.

## 4. Update the report and the artifact

1. Edit `report/template.html` section "The CAS month, checked": replace the reconstructed table
   with the real numbers; keep the reconstruction as a footnote ("public anchors gave …").
2. `python report/build_report.py` → `report/expiry_edge.html` (inlines the SVG charts).
3. Publish with the Artifact tool using `url` = the artifact above (same URL, favicon unchanged).
4. Re-zip the toolkit (exclude `data/` and `*.parquet` if size matters) and send it.
5. Remind Piyush to regenerate/revoke the Dhan access token he exposed for this.

## 5. The CAS-only OTM timetable (required — Piyush's rule: no pre-CAS option history)

```bash
python scripts/cas_timetable.py --zip scripts/dhan_export.zip     # model-free: real option prints, expiries since 3 Aug 2026
python report/build_timetable.py                                  # picks up outputs/cas_month/cas_timetable.json automatically
```

Then publish `report/otm_timetable.html` with the Artifact tool using
`url` = `https://claude.ai/code/artifact/fabc125c-a24c-43be-a012-16ca642ee774` (favicon ⏱️, keep it).

Also build the auction-anatomy features (the imbalance/OI drivers behind the "blast" — see the
"Why the Auction Blasts" write-up, artifact `https://claude.ai/code/artifact/02fff61f-0ec8-4a1e-9c1c-dcbbdb75b822`):

```bash
python scripts/cas_auction_anatomy.py --zip scripts/dhan_export.zip   # -> outputs/cas_month/cas_auction_anatomy.json
python scripts/cas_regime_decay.py --zip scripts/dhan_export.zip   # -> outputs/cas_month/cas_regime_decay.json (is the blast fading?)
python report/build_auction_why.py                                    # rebuilds report/auction_why.html (also renders the decay monitor + participation proxy)
```

`cas_auction_anatomy.py` computes, per CAS expiry, the max-pain distance, ATM-OI share, OI concentration,
monthly flag and the realized auction move — model-free, from the real 15:10 option OI. With only a handful of
expiries do NOT fit a model or claim a driver; report the per-expiry table and, if `build_auction_why.py` grows
a data-driven section, keep every caveat (n small, 13 Aug manipulated, book still thin). Republish
`report/auction_why.html` to artifact `02fff61f-0ec8-4a1e-9c1c-dcbbdb75b822` (favicon 💥) only if you changed it.

## 6b. Triage F&O STOCKS for the same blast (they blast harder — thinner books)

Stock F&O is monthly (first monthly expiry under CAS was 25 Aug 2026). To pull and screen stocks, set a
symbol list before the export and run the screener:

```bash
DHAN_STOCKS="IDEA,YESBANK,TATASTEEL,SBIN,PNB,IDFCFIRSTB,TATAMOTORS,BANDHANBNK,RBLBANK,ZOMATO" \
  python scripts/dhan_export.py                 # adds rolling_options_<SYM>.csv for each stock (OPTSTK, NSE_FNO, MONTH)
python scripts/cas_stock_screen.py --dir scripts/dhan_export   # ranks movers + susceptibility + crazy option returns -> outputs/stocks/
```

Pick the symbol list toward THIN, high-option-OI names (mid-caps, PSU banks, high-retail F&O stocks) — those are
the susceptible ones; large caps (RELIANCE, HDFCBANK) barely move in the auction. The screen needs the real
settlement close per stock for exact returns; pass --close symbol,date,cas_close if you pull it, else it
approximates from the post-freeze spot (flagged). Report the leaderboard with the survivorship caveat intact:
the same OTM strikes expire worthless far more often than they blast, and single stocks are the most
manipulation-prone corner (SEBI's 13 Aug case was single stocks). Do NOT present the crazy-return list as a
strategy — it is a triage of where the mechanism is strongest, to feed the imbalance model per stock.

## 7. The imbalance model + the honest accuracy read (Piyush wants "future accurate predictions")

Set `DHAN_FULL_HISTORY=1` before the export (step 2) so `dhan_export.py` also pulls Sept-2025..Jul-2026
rolling options — ~45 weekly expiries with real OI. Then:

```bash
DHAN_FULL_HISTORY=1 python scripts/dhan_export.py            # (re-run of step 2 with history on)
python scripts/auction_model.py --zip scripts/dhan_export.zip
```

`auction_model.py` fits the imbalance trigger — P(|auction move| big) from the 15:10 OI structure — and
validates it by STRICT walk-forward plus a 200-run permutation null. Read `outputs/model/auction_walkforward.json`:
- `permutation_p_value < 0.05` ⇒ a real, out-of-sample edge; `outputs/model/auction_model.json` is written with
  `trustworthy: true` and `expiry_edge/cas_signal.py` will let the auction trigger fire.
- otherwise ⇒ NO usable edge yet; report the number honestly and leave the trigger OFF. Do NOT retune to force
  `trustworthy`. The point of the permutation control is that a lucky fit cannot be sold as a signal.

Note the target split the JSON reports: pre-CAS days measure the OI→close-drift proxy (same hedging mechanism,
no auction), CAS days measure the real auction move (~1 new one per week). A CAS-specific edge needs the CAS days
to accumulate — say so; the full-history read is the leading indicator, not the final word.

The chart buy-score itself gains the OI features automatically on any retrain that carries real OI
(`score.OI_FEATURES`; `train_model.py` only fits the features that vary, so it never breaks without them).
`expiry_edge/cas_signal.py` fuses the two: intraday chart blast + settlement imbalance blast → STRONG when they
agree. Update the "How accurate can this get" section of `report/auction_why.html` with the REAL walk-forward
AUC and p-value once you have them, keeping every caveat, and republish artifact `02fff61f`.
The page shows, per half-hour entry window and strike (auto / 1 / 2 / 3 OTM), the real outcome of buying on every
CAS expiry day: best print within the hour, rule exit (60 min / 15:10), and held through the auction to settlement,
for GO bars, LEAN bars, any bar, and the call + put bought together (the BOTH set — Piyush asked for the
both-sides variant explicitly; the 15:10 auction ticket is also reported for CE + PE together). `build_timetable.py` also writes the "what the CAS expiries say" notes from the
JSON; read them once and rewrite anything that reads oddly. The pre-CAS study stays folded at the bottom — do not
promote it. If `cas_timetable.py` finds no option rows for an index (SENSEX via BSE_FNO is the likely gap), say so on
the page rather than filling it from the model.

## Where things are

```
expiry_edge/         library (config, data, calendar, features, signals, options, evaluate, strategies,
                     anatomy, model, score, otm)
scripts/             dhan_export.py, cas_month_check_real.py, make_synthetic_export.py, live_indicator.py,
                     cas_month_check.py (public reconstruction), the rest of the research pipeline
outputs/model/       buy_score_logit.json (coefficients used by both Python and Pine), calibration tables
outputs/*_profile_expiry.npy   intraday variance profiles (needed by the pricer)
outputs/cas_month/   public reconstruction results (+ real_* once step 3 has run)
outputs/tables, outputs/otm, outputs/charts   research tables and SVG/PNG charts used by the report
report/              template.html + build_report.py → expiry_edge.html
pine/                ExpiryEdge_BuyScore.pine (TradingView v6 indicator)
data/cas/            NSE daily NIFTY 50 / India VIX CSVs (29 Jun–25 Aug 2026) used by the reconstruction
```
