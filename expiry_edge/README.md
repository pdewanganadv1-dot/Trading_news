# expiry_edge — NIFTY / SENSEX expiry-day option entry analyzer

A research toolkit that answers one question with data: **on an index-option expiry
day, when a technical trigger fires, how often does the option premium actually
expand — and by how much, net of costs?**  It also re-models the last 25 minutes of
the day for the Closing Auction Session (CAS) regime that went live on 3 Aug 2026.

```
expiry_edge/            the library
  config.py             expiry-weekday regimes, lot sizes, CAS clock, cost & model defaults
  data.py               loaders (GitHub 1-min history) + fetchers for your own machine (NSE/BSE bhavcopy, yfinance, openchart)
  calendar.py           expiry-day labelling (weekday regimes, holiday shift, monthly)
  features.py           5-min technical features (EMA/RSI/ATR/Bollinger/opening range/PDH-PDL/session mean)
  signals.py            13 triggers -> event table
  options.py            synthetic 0DTE pricer (BS kernel + empirical intraday variance profile + reactive IV + CAS auction lump)
  evaluate.py           what happened to an ATM / 1-OTM option bought at each trigger (MFE, fixed exits, bracket)
  strategies.py         short / long ATM straddles at fixed times
  anatomy.py            model-free expiry-day statistics
  model.py              bar-level dataset: 5-min features + option-model labels (straddle +30% within 60 min)
  score.py              the deployable buy-score (standardised logistic) + live feature builder
  otm.py                cheap-OTM "blast" outcomes: 1/2/3-strike OTM options priced along the path with a premium floor
  oi_features.py        open-interest / max-pain features from an option-chain snapshot (the imbalance inputs)
  cas_signal.py         fuses the chart buy-score (intraday blast) with the OI auction model (settlement blast)
pine/
  ExpiryEdge_BuyScore.pine   TradingView v6 indicator: score 0-100, GO/LEAN shading, CE/PE marks, alerts (JSON webhook payload)
scripts/
  build_cache.py        raw CSV -> parquet caches
  run_analysis.py       everything -> outputs/tables/*.csv + outputs/summary.json
  make_charts.py        PNG/SVG charts
  scorecard.py          the decision tool: GO / LEAN / NO for a trigger + context
  live_day.py           run on expiry day on your Mac: pulls today's bars, lists fired triggers, calls the scorecard
  build_dataset.py      -> outputs/model/<IDX>_bars.parquet (one row per 5-min bar)
  train_model.py        trains + validates the buy-score (time split, calibration, walk-forward), writes buy_score_logit.json
  live_indicator.py     the buy-score bar by bar + suggested strike: --replay YYYY-MM-DD (anywhere) or --source yf/openchart --watch (live)
  run_otm.py            the OTM blast study -> outputs/otm/*.csv (lottery vs GO signals, by strike, pre-CAS and CAS)
  cas_month_check.py    Aug-2026 CAS expiries reconstructed from public anchors (NSE daily files, live blogs, Zerodha, SEBI) -> outputs/cas_month/
  export_recent.py      run on your Mac: exports 60d of 5-min bars + real bhavcopy option O/H/L/C for the August expiries (turns the check exact)
  dhan_export.py        NO MAC NEEDED: paste into Google Colab with your Dhan access token -> dhan_export.zip (5-min/1-min index bars
                        + REAL expired-option minute bars ATM±3 for the August expiries via Dhan's rolling-option API)
  cas_month_check_real.py  runs the score on the real bars and prices every signal with the real option bars from dhan_export.zip
  make_synthetic_export.py SYNTHETIC dhan_export.zip in the same layout — smoke test for cas_month_check_real.py (not market data)
  otm_timetable.py      (pre-CAS reference) WHICH strike and WHEN from the model-priced 2022-24 study -> outputs/otm/timetable.json
  cas_timetable.py      THE timetable: model-free, REAL option prints from dhan_export.zip, expiries since 3 Aug 2026 only ->
                        outputs/cas_month/cas_timetable.{csv,json}; report/build_timetable.py renders it CAS-first (pre-CAS folded as reference)
  cas_auction_anatomy.py  WHY the close blasts: per-CAS-expiry order-imbalance/OI drivers (max-pain distance, ATM-OI share, OI
                        concentration, monthly flag) vs the realized auction move -> outputs/cas_month/cas_auction_anatomy.json
                        (report/build_auction_why.py -> report/auction_why.html)
  auction_model.py      THE imbalance trigger: P(|auction move| big) from the 15:10 OI structure; strict walk-forward + a
                        200-run permutation null (a lucky fit cannot pass) -> outputs/model/auction_{walkforward.json,model.json}
  cas_regime_decay.py   IS THE EDGE FADING? tracks |auction move| per expiry over time (ordinary weeklies vs structural
                        days) + a participation proxy; SEBI expects the moves to ease as CAS matures -> outputs/cas_month/cas_regime_decay.json
  cas_stock_screen.py   SAME BLAST ON STOCKS: ranks F&O stocks by auction move + OI susceptibility and lists the crazy
                        nearest-OTM option returns (worthless->ITM flips) from a Dhan stock-options pull -> outputs/stocks/
HANDOFF.md              runbook for finishing the CAS-month check on real Dhan data in a Dhan-enabled cloud session
outputs/                tables, charts, per-event parquet files
```

## Quick start

```bash
pip install -r requirements.txt
python scripts/build_cache.py          # clones the 1-min history from GitHub the first time (~150 MB)
python scripts/run_analysis.py         # ~3 min for all three indices
python scripts/make_charts.py
python scripts/scorecard.py --index NIFTY --rank --time 14:30
python scripts/scorecard.py --index NIFTY --signal ORB30 --time 10:20 --gap 0.6 --vol mid
```

On an expiry day (from a normal home/office IP; NSE/BSE/Yahoo block cloud IPs):

```bash
pip install yfinance openchart
python scripts/live_day.py --index NIFTY --source yf
```

## The buy-score (stage 2)

`P(ATM straddle bought at this 5-min bar close touches +30% within 60 min)` from 18 chart-computable
features. Trained on NIFTY+BANKNIFTY 2016-2021 (149k bars), tested 2022-24 (90k bars): expiry-day AUC 0.68,
calibrated (top decile 51% predicted / 50% observed). Direction is *not* predictable (AUC 0.51) — the side
comes from the bar's breakout (new day high -> CE, new day low -> PE). Rule: GO = score >= 0.40 on a breakout
bar, LEAN = 0.30-0.40 half size, exit <= 60 min and by 15:15 under CAS. Test expiry days: NIFTY 150 GO trades,
mean net +52%, P(+50%) 67%; BANKNIFTY 161 trades, +70%. SENSEX transfer is poor (AUC 0.56) — diary only.

```bash
python scripts/build_dataset.py            # ~1 min
python scripts/train_model.py              # ~1 min, prints all validation tables
python scripts/live_indicator.py --index NIFTY --replay 2023-11-23
python scripts/live_indicator.py --index NIFTY --source yf --watch     # live on your Mac
DHAN_ACCESS_TOKEN=... python scripts/live_indicator.py --index NIFTY --source dhan --watch   # live via Dhan (Colab/any machine)
```

### Cheap OTM blasts (stage 3) — buyer only

GO signals re-priced for 1/2/3-strike OTM options (premium floor NIFTY 1.5 / BANKNIFTY 3 / SENSEX 5 pts).
NIFTY test expiries: the nearest OTM strike priced <= 10 pts reached 5x within the hour on 21% of GO signals
(10x: 11%), mean net +83%, but positive only 15% of the time and >= half lost 79% of the time. 2 strikes OTM
+10% (5x: 6%), 3 strikes OTM -22%. Random-moment OTM buys lose at every strike (5x odds 3-4%). Under CAS
pricing (exit 15:15) the auto strike is +58% and positive 39% of the time; 2-OTM becomes viable (+40%).
BANKNIFTY: auto strike +161% (5x 27%). The indicator's "auto" strike = nearest OTM <= cap, never the 3rd strike.

```bash
python scripts/run_otm.py NIFTY BANKNIFTY SENSEX
python scripts/otm_timetable.py && python report/build_timetable.py      # the "which strike, what time" card
```

Timetable headline (CAS pricing, test expiry days 2022-24): the GO rule fires almost only after 13:45 and 76% of NIFTY GO
bars fall in 14:15-14:55, where the auto strike (1-OTM ~Rs 5-7 three times out of four, else 2-OTM) returns +83% mean net,
P(5x) 16%, profitable 41% of the time, median -30%; 13:45-14:15 is break-even (n=17); before that nothing fires and random
OTM buys lose. BANKNIFTY: the 2-OTM (~Rs 18-20) beats the 1-OTM in the same window (+143% vs +87%). Selling at 5x lowers
the mean (the 10x tail pays for the losers). The edge is lumpy across years (NIFTY 2023 flat) and volatility regimes.

TradingView: open `pine/ExpiryEdge_BuyScore.pine` in the Pine editor on a **5-minute** chart of NSE:NIFTY
(or BSE:SENSEX, NSE:BANKNIFTY), set the expiry weekday, strike step (50 NIFTY / 100 SENSEX, BANKNIFTY) and premium cap inputs, add alerts on "EE GO CE" / "EE GO PE" or use
the `alert()` JSON payload with a webhook. The Python and Pine implementations use the same coefficients
(`outputs/model/buy_score_logit.json`); after retraining, paste the new MEAN/STD/COEF arrays into the Pine file.
If the Pine compiler flags a line, `scripts/live_indicator.py --replay` is the reference behaviour.

## Data

| Source | What | Coverage | Used for |
|---|---|---|---|
| GitHub `sandeepkapri/Nifty50-Minute-Data` | NIFTY 1-min OHLC | 2015-01 .. 2024-03 | 268 weekly/monthly expiry days (Thursday regime) |
| GitHub `sandeepkapri/Sensex-Minute-Data` | SENSEX 1-min | 2018-03 .. 2024-03 | 45 weekly expiry days (Friday regime, small sample) |
| GitHub `sandeepkapri/BankNifty-Data` | BANKNIFTY 1-min | 2015-01 .. 2026-04 | 442 expiry days; also a proxy for NIFTY's Tuesday regime (Sep-2025+) |
| NSE F&O bhavcopy (UDiFF) | daily O/H/L/C, settlement, OI per contract | 2024-07+ | `data.fetch_nse_fo_bhavcopy` — real option O/H/L/C on expiry days (run locally) |
| BSE derivatives bhavcopy | same for SENSEX options | | `data.fetch_bse_fo_bhavcopy` |
| yfinance / openchart | recent intraday index bars, India VIX | last 60 days / NSE charting history | `live_day.py`, `data.append_recent_to_raw` |

Extend the history: fetch recent 1-min bars on your machine and call
`data.append_recent_to_raw("NIFTY", bars)`, then re-run `build_cache.py` and `run_analysis.py`.
The calendar already knows the Tuesday (NIFTY) / Thursday (SENSEX) regime from 1-Sep-2025
and flags CAS days from 3-Aug-2026.

## How the option pricing works (and its limits)

Free intraday **option** data does not exist, so options are priced along the **real**
index path with a Black-Scholes kernel whose remaining variance follows the empirical
minute-of-day variance profile of expiry days (estimated from the data).  Directional
and gamma P&L are real; the time-value component is modelled:

* `sigma_day = VRP x trailing-20-session Parkinson realised vol` (VRP = 1.10 default).
  Sensitivity tables at VRP 1.00 and 1.25 are produced.  Plug India VIX or an observed
  opening straddle in via `options.sigma_from_vix` / `sigma_from_straddle`.
* `reactive IV`: the day's vol re-prices toward the last 30 minutes of realised vol
  (real 0DTE premiums fatten immediately after a big bar).  Static-IV tables are also produced.
* `CAS model`: 25% of the day's variance is reserved for the auction (index frozen
  15:15-15:35, options trade to 15:40).  This reproduces Zerodha's observation that ~50% of the
  0DTE straddle is still intact at 15:15 under CAS.  Exit deadline moves to 15:15.
* Costs: 0.5 pt per side (NIFTY), 1.0 (SENSEX, BANKNIFTY), i.e. spread + brokerage/taxes.

Consequences: **buyer results after a volatility burst are optimistic in static-IV mode
and closer to reality in reactive mode; seller results scale with the VRP assumption.**
Treat the *ranking* of triggers and the *conditional* differences as the robust output,
and the absolute expectancies as model-dependent.  Validate with real bhavcopy O/H/L/C
before trading size.

## Output tables (outputs/tables)

* `<IDX>_variance_buckets.csv` — where in the day the variance is (expiry vs other days)
* `<IDX>_anatomy_*.csv` — range, last-hour move, range-vs-straddle statistics
* `<IDX>_signals_summary.csv` — every trigger x {expiry, non-expiry, static-IV, CAS-model}
* `<IDX>_signals_by_{hour,vol_regime,gap_bucket,or_bucket,year,direction}_expiry.csv`
* `<IDX>_signals_vrp_sensitivity_expiry.csv`
* `<IDX>_straddle_*.csv` — short straddles (no SL / 30% / 50% SL), long straddles, CAS variants
* `<IDX>_events_expiry.parquet` — one row per trigger event with all outcome columns (feeds the scorecard)

Column glossary: `idx_hit30` = index moved in the signal direction after 30 min (%);
`p_mfe60_ge50` = premium touched >= +50% within 60 min (%); `ret60_net_mean` = mean 60-min
return net of costs (%); `bracket_*` = SL -30% / TP +50% / 60-min time stop; `p0_med` = median
entry premium (index points).
