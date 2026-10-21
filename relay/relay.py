"""stock_burner Delta relay.

TradingView alert (Super Box "Relay JSON" mode)  -->  POST /tv/<RELAY_TOKEN>  -->  real orders on Delta Exchange India:
    entry : market entry + stop-loss (market, reduce-only, mark-price trigger) + target limit(s) (reduce-only)
    tp1   : first half booked by the exchange limit -> stop re-placed at the new level (entry) for what is left
    be    : stop moved to entry (single-target / pyramid styles)
    add   : pyramid lot 2 at market + stop re-sized + its target limit
    exit  : cancel everything on the symbol, close whatever is still open at market (safety net: the exchange
            stop / target normally did it already)
    flip  : same as exit; the entry for the opposite trade arrives as the next message

Every message is logged to relay.log and relay_events.jsonl. DRY_RUN=1 logs orders without sending them.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import OrderedDict
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from delta_client import DeltaClient, DeltaError

# ----------------------------------------------------------------------------- config (environment)
RELAY_TOKEN = os.environ.get("RELAY_TOKEN", "")              # secret part of the webhook URL (and/or strategy_id field)
DELTA_API_KEY = os.environ.get("DELTA_API_KEY", "")
DELTA_API_SECRET = os.environ.get("DELTA_API_SECRET", "")
DELTA_BASE_URL = os.environ.get("DELTA_BASE_URL", "https://api.india.delta.exchange")  # testnet: https://cdn-ind.testnet.deltaex.org
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
STOP_TRIGGER = os.environ.get("STOP_TRIGGER", "mark_price")   # mark_price | last_traded_price
MAX_QTY = {k.strip().upper(): int(v) for k, v in (kv.split("=") for kv in os.environ.get("MAX_QTY", "").split(",") if "=" in kv)}
ALLOWED_SYMBOLS = {s.strip().upper() for s in os.environ.get("ALLOWED_SYMBOLS", "").split(",") if s.strip()}
LOG_DIR = Path(os.environ.get("LOG_DIR", "."))

LOG_DIR.mkdir(parents=True, exist_ok=True)
handler = RotatingFileHandler(LOG_DIR / "relay.log", maxBytes=5_000_000, backupCount=3)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                    handlers=[handler, logging.StreamHandler()])
log = logging.getLogger("relay")

if not RELAY_TOKEN:
    log.warning("RELAY_TOKEN is empty - set it, otherwise anyone who finds the URL can trade")
if not DRY_RUN and (not DELTA_API_KEY or not DELTA_API_SECRET):
    log.warning("DELTA_API_KEY / DELTA_API_SECRET missing - running as if DRY_RUN=1")
    DRY_RUN = True

client = DeltaClient(DELTA_API_KEY, DELTA_API_SECRET, DELTA_BASE_URL, dry_run=DRY_RUN)
app = FastAPI(title="stock_burner Delta relay")
locks: dict[str, threading.Lock] = {}
seen: "OrderedDict[str, float]" = OrderedDict()
state: dict[str, dict] = {}          # per symbol: last event, style, qty, stop, targets (for /state)


def delta_symbol(tv_symbol: str) -> str:
    """DELTAIN:BTCUSD.P / BTCUSD.P / BTCUSD -> BTCUSD"""
    s = tv_symbol.split(":")[-1].upper()
    return s[:-2] if s.endswith(".P") else s


def events_log(rec: dict) -> None:
    with open(LOG_DIR / "relay_events.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")


def mk_tag(m: dict) -> str:
    """HHMMSS of the candle (bar_time 2026-10-21T12:35:00Z -> 123500); used in client_order_id so orders are traceable."""
    bt = str(m.get("bar_time", ""))
    return bt[11:19].replace(":", "") if len(bt) >= 19 else str(int(time.time()))[-6:]


def opp(side: str) -> str:
    return "sell" if side == "buy" else "buy"


def fnum(x) -> float | None:
    try:
        return float(x) if x not in (None, "", "na", "NaN") else None
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------- order logic
def place_stop(sym: str, side: str, size: int, sl: float, tag: str) -> dict | None:
    if size <= 0 or sl is None:
        return None
    return client.stop_market_reduce(sym, opp(side), size, sl, trigger=STOP_TRIGGER, client_order_id=f"sb_sl_{tag}")


def replace_stop(sym: str, side: str, sl: float, tag: str) -> dict:
    """Cancel the stop orders on the symbol and put a fresh one for the whole remaining position."""
    client.cancel_all(sym, limit=False, stop=True, reduce_only=True)
    pos = client.position(sym)
    remaining = abs(pos["size"])
    o = place_stop(sym, side, remaining, sl, tag)
    return {"remaining": remaining, "stop": o}


def on_entry(sym: str, m: dict) -> dict:
    side, qty, style = m["side"], int(m["qty"]), m.get("style", "single")
    sl, tp, tp2 = fnum(m.get("sl")), fnum(m.get("tp")), fnum(m.get("tp2"))
    tag = mk_tag(m)
    actions = {}
    # 1. clean slate on this symbol
    client.cancel_all(sym)
    pos = client.position(sym)
    if pos["size"] != 0 and ((pos["size"] > 0) != (side == "buy")):
        # opposite position still open (flip message lost / manual trade): close it first
        actions["closed_opposite"] = client.market(sym, side, abs(pos["size"]), reduce_only=True, client_order_id=f"sb_fx_{tag}")
    elif pos["size"] != 0:
        log.warning("%s: same-side position %s already open before entry; adding %s", sym, pos["size"], qty)
    # 2. entry
    actions["entry"] = client.market(sym, side, qty, client_order_id=f"sb_in_{tag}")
    # 3. stop for the whole position
    pos = client.position(sym)
    total = abs(pos["size"]) if pos["size"] != 0 else qty
    actions["stop"] = place_stop(sym, side, total, sl, tag)
    # 4. targets (reduce-only limits)
    if style == "lots2" and tp is not None and tp2 is not None:
        h1 = qty // 2
        h2 = qty - h1
        actions["tp1"] = client.limit_reduce(sym, opp(side), h1, tp, client_order_id=f"sb_t1_{tag}")
        actions["tp2"] = client.limit_reduce(sym, opp(side), h2, tp2, client_order_id=f"sb_t2_{tag}")
    elif tp is not None:
        actions["tp"] = client.limit_reduce(sym, opp(side), qty, tp, client_order_id=f"sb_tp_{tag}")
    state[sym] = {"event": "entry", "side": side, "qty": qty, "style": style, "sl": sl, "tp": tp, "tp2": tp2,
                  "kind": m.get("kind"), "bar_time": m.get("bar_time")}
    return actions


def on_add(sym: str, m: dict) -> dict:
    side, qty = m["side"], int(m["qty"])
    sl, tp = fnum(m.get("sl")), fnum(m.get("tp"))
    tag = mk_tag(m)
    actions = {"add": client.market(sym, side, qty, client_order_id=f"sb_ad_{tag}")}
    actions["stop"] = replace_stop(sym, side, sl, tag + "a")
    if tp is not None:
        actions["tp_lot2"] = client.limit_reduce(sym, opp(side), qty, tp, client_order_id=f"sb_t2_{tag}")
    st = state.setdefault(sym, {})
    st.update({"event": "add", "sl": sl, "qty2": qty})
    return actions


def on_move_stop(sym: str, m: dict, ev: str) -> dict:
    """tp1 (half booked, stop to entry) and be (stop to entry)."""
    sl = fnum(m.get("sl"))
    side = m["side"] if ev == "be" else opp(m["side"])      # tp1 message carries the EXIT side
    tag = mk_tag(m)
    pos = client.position(sym)
    if pos["size"] == 0:
        client.cancel_all(sym)
        state.setdefault(sym, {}).update({"event": ev + "_flat"})
        return {"note": "no position on Delta - cancelled leftovers"}
    actions = {"stop": replace_stop(sym, side, sl, tag + ev)}
    state.setdefault(sym, {}).update({"event": ev, "sl": sl})
    return actions


def on_exit(sym: str, m: dict, ev: str) -> dict:
    tag = mk_tag(m)
    client.cancel_all(sym)
    pos = client.position(sym)
    actions = {"cancelled": True, "position_before": pos["size"]}
    if pos["size"] != 0:
        side = "sell" if pos["size"] > 0 else "buy"
        actions["close"] = client.market(sym, side, abs(pos["size"]), reduce_only=True, client_order_id=f"sb_ex_{tag}")
    state.setdefault(sym, {}).update({"event": ev, "why": m.get("why")})
    return actions


HANDLERS = {"entry": on_entry, "add": on_add}


def handle(m: dict) -> dict:
    ev = str(m.get("event", "")).lower()
    sym = delta_symbol(str(m.get("symbol", "")))
    if not sym:
        raise HTTPException(400, "symbol missing")
    if ALLOWED_SYMBOLS and sym not in ALLOWED_SYMBOLS:
        raise HTTPException(400, f"symbol {sym} not in ALLOWED_SYMBOLS")
    qty = int(m.get("qty", 0) or 0)
    if ev in ("entry", "add"):
        if qty <= 0:
            raise HTTPException(400, "qty must be > 0")
        if sym in MAX_QTY and qty > MAX_QTY[sym]:
            raise HTTPException(400, f"qty {qty} above MAX_QTY for {sym} ({MAX_QTY[sym]})")
        if m.get("side") not in ("buy", "sell"):
            raise HTTPException(400, "side must be buy or sell")
    key = f"{sym}|{ev}|{m.get('bar_time')}|{m.get('side')}|{m.get('why','')}"
    now = time.time()
    for k in [k for k, t in seen.items() if now - t > 3600]:
        seen.pop(k, None)
    if key in seen:
        return {"duplicate": True, "key": key}
    seen[key] = now
    lock = locks.setdefault(sym, threading.Lock())
    with lock:
        if ev in ("entry", "add"):
            return HANDLERS[ev](sym, m)
        if ev in ("tp1", "be"):
            return on_move_stop(sym, m, ev)
        if ev in ("exit", "flip"):
            return on_exit(sym, m, ev)
    raise HTTPException(400, f"unknown event {ev}")


# ----------------------------------------------------------------------------- http
@app.get("/health")
def health():
    return {"ok": True, "dry_run": DRY_RUN, "base_url": DELTA_BASE_URL, "stop_trigger": STOP_TRIGGER}


@app.get("/state")
def get_state():
    return state


@app.post("/tv/{token}")
async def tv(token: str, request: Request):
    raw = (await request.body()).decode("utf-8", "replace")
    try:
        m = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("non-JSON alert body: %s", raw[:200])
        raise HTTPException(400, "body is not JSON")
    if not RELAY_TOKEN or (token != RELAY_TOKEN and str(m.get("strategy_id", "")) != RELAY_TOKEN):
        log.warning("bad token from %s", request.client.host if request.client else "?")
        raise HTTPException(403, "bad token")
    rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "msg": m}
    try:
        res = handle(m)
        rec["result"] = res
        log.info("%s %s %s -> %s", m.get("symbol"), m.get("event"), m.get("why", ""), json.dumps(res, default=str)[:600])
        events_log(rec)
        return JSONResponse({"ok": True, "result": res}, status_code=200)
    except DeltaError as e:
        rec["error"] = str(e)
        log.error("Delta error: %s", e)
        events_log(rec)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)
    except HTTPException as e:
        rec["error"] = e.detail
        events_log(rec)
        raise


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
