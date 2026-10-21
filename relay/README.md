# stock_burner Delta relay — Super Box alerts → real stop/target orders on Delta Exchange

```
TradingView alert (Super Box Indicator v1, "Relay JSON")
        │  POST https://<your-relay>/tv/<RELAY_TOKEN>
        ▼
   relay.py  ──signed REST──►  Delta Exchange India
                               market entry + stop-loss (mark price, reduce-only) + target limits (reduce-only)
```

Delta's own TradingView bot only places market orders, so the stop had to live in the script. With the relay the stop
and the targets sit **on the exchange**; a 3 am move is handled even if TradingView never delivers the alert.

## What the relay does per message

| event (from the script) | relay action on Delta |
|---|---|
| `entry` | cancel every open order on the symbol; if an opposite position is still open (lost flip) close it; **market** entry `qty`; **stop-loss** market order, reduce-only, size = whole position, trigger = mark price; targets: single → 1 reduce-only limit at TP; 2 lots → `qty/2` at 2R + `qty/2` at 5R; pyramid → `qty` at 3R |
| `tp1` | the exchange already filled the first half; the relay cancels the stop and places a new one **at entry** for what is left |
| `be` | stop moved to entry (single / pyramid styles, +2R) |
| `add` | pyramid lot 2: market `qty`, stop re-placed for the whole (doubled) position, limit for lot 2 at 3R |
| `exit` / `flip` | cancel all orders on the symbol, close whatever is still open at market (reduce-only). Normally the exchange stop / target already did it and this is a no-op |

Guards: secret token in the URL (or in the `strategy_id` field), duplicate alerts ignored (same symbol/event/candle),
`MAX_QTY` per symbol, `ALLOWED_SYMBOLS`, one symbol handled at a time (lock), every message and every order
written to `relay.log` and `relay_events.jsonl`. `DRY_RUN=1` logs the orders without sending them.

## 1. Delta: create an API key

Delta Exchange India → Account → **API Keys** → create: **Trading** permission only (no withdrawal), and set the
**IP whitelist** to the server's IP once you know it (Railway/Render show the outbound IP in the service settings;
for the Mac tunnel use your home IP). Copy key + secret into `.env` — the relay reads them from the environment,
nothing else. Delta rejects a signed request older than 5 s, so the server clock must be right (it is on the hosts
below).

For a first live test use the **testnet** (demo account, fake money): `DELTA_BASE_URL=https://cdn-ind.testnet.deltaex.org`
with a testnet API key.

## 2. Run it

**Option A — Railway / Render (recommended, always on, free tier is enough)**
1. Put this folder in a private GitHub repo (do not commit `.env`).
2. New service from the repo. Railway detects the `Dockerfile`; Render: Web Service, Docker.
3. Environment variables = the lines of `.env.example` with your values. Keep `DRY_RUN=1` for the first day.
4. Open `https://<service-url>/health` → `{"ok": true, "dry_run": ...}`.

**Option B — your Mac** (only while the Mac is awake; TradingView needs a public https URL)
```
brew install cloudflared
cp .env.example .env   # fill in
./run_mac.sh           # starts the relay and prints a https://....trycloudflare.com URL
```

## 3. TradingView alert

1. Chart: BTCUSD.P 5m, **stock_burner Super Box Indicator v1** (version 9.0 or later).
2. Indicator settings → *Delta webhook*: **Alert message = Relay JSON**, Order quantity = 12 (even number for the
   2-lot style), *strategy_id / relay token* = your `RELAY_TOKEN` (optional second check), Exits → Exit style as
   you want to trade it (2 lots recommended).
3. Create alert → Condition: *stock_burner Super Box Indicator v1* → **"Any alert() function call"**, Options:
   *Once per bar close* (default for alert() calls), Expiration: open-ended, Notifications: **Webhook URL** =
   `https://<your-relay>/tv/<RELAY_TOKEN>`. Message box: leave as is (the script writes the message).
4. Same for ETHUSD.P (qty 40) and XAUTUSD.P (qty 200) if you want them.

What the message looks like (one per event):
```json
{"event":"entry","symbol":"BTCUSD.P","side":"buy","qty":12,"price":"85000.5","sl":"84500","tp":"86000","tp2":"87500",
 "style":"lots2","kind":"BLACK","why":"BLACK","tf":"5","bar_time":"2026-10-21T12:35:00Z","strategy_id":"<token>"}
```

## 4. First week checklist

- `DRY_RUN=1` on Railway for a day: watch `relay_events.jsonl` (Railway → Logs) — every entry should show the market
  order, the stop and the target(s) that *would* be sent.
- Then testnet key, `DRY_RUN=0`, quantity 1 for a few trades. Check in the Delta testnet UI: after an entry you must see
  the position, one stop order (reduce-only, trigger mark) and the limit(s).
- Then the real key, quantity 1 → 12.

## 5. Things to know

- The stop triggers on **mark price** (`STOP_TRIGGER`); the fill is a market order, so a fast move can fill worse than
  the stop level. At 100x the liquidation price is ~0.9 % from entry; a stop wider than that (some RED stops) is
  liquidation before stop.
- The script still evaluates its stop/target at candle close and sends `exit`; if the exchange order already
  filled, the relay only cancels leftovers. If the exchange stop did **not** trigger (mark price never crossed) but
  the candle closed through the level, the relay closes at market — same as the backtest.
- The relay keeps no position memory on purpose: it asks Delta for the position before every stop change and before
  every close, so a restart loses nothing.
- If the relay is down, no new entries happen (no message reaches it) — open positions keep their exchange-side stop
  and targets.
- Test suite: `python3 tests/test_relay.py` runs the relay against a fake Delta API (signature check, 2 lots, tp1,
  time stop, stop hit on the exchange, flip, lost flip, pyramid add/be, guards) — 27 checks.
