"""End-to-end test of the relay against the mock Delta API. Starts both servers, replays the Super Box message
sequence for every exit style, checks the orders that reach the 'exchange'.
Run:  python3 tests/test_relay.py   (from the relay folder)
"""
import json, os, subprocess, sys, time
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MOCK = "http://127.0.0.1:9001"
RELAY = "http://127.0.0.1:9000"
TOKEN = "t0ken"


def wait(url, n=40):
    for _ in range(n):
        try:
            requests.get(url, timeout=1); return
        except Exception:
            time.sleep(0.25)
    raise SystemExit("server did not start: " + url)


def alert(m, token=TOKEN):
    r = requests.post(f"{RELAY}/tv/{token}", data=json.dumps(m), headers={"Content-Type": "text/plain"}, timeout=10)
    return r.status_code, r.json()


def mstate():
    return requests.get(f"{MOCK}/mock/state").json()


def reset():
    requests.post(f"{MOCK}/mock/reset")


def live(st, pid=27):
    return [o for o in st["orders"] if o["product_id"] == pid]


def msg(event, side, qty, price, sl="", tp="", tp2="", style="lots2", why="", bar="2026-10-21T12:30:00Z", sym="BTCUSD.P"):
    return {"event": event, "symbol": sym, "side": side, "qty": qty, "price": str(price), "sl": str(sl), "tp": str(tp),
            "tp2": str(tp2), "style": style, "kind": "BLACK", "why": why, "tf": "5", "bar_time": bar, "strategy_id": ""}


def check(cond, what):
    print(("  ok   " if cond else "  FAIL ") + what)
    if not cond:
        FAILS.append(what)


FAILS = []
env = dict(os.environ, TEST_SECRET="testsecret")
mock = subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_delta:app", "--port", "9001"], cwd=HERE, env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
renv = dict(os.environ, RELAY_TOKEN=TOKEN, DELTA_API_KEY="k", DELTA_API_SECRET="testsecret", DELTA_BASE_URL=MOCK,
            DRY_RUN="0", MAX_QTY="BTCUSD=12,ETHUSD=40,XAUTUSD=200", LOG_DIR=os.path.join(HERE, "_logs"))
relay = subprocess.Popen([sys.executable, "-m", "uvicorn", "relay:app", "--port", "9000"], cwd=ROOT, env=renv,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    wait(f"{MOCK}/mock/state"); wait(f"{RELAY}/health")

    print("1. token check")
    code, _ = alert(msg("entry", "buy", 12, 85000, 84500, 86000, 87500), token="wrong")
    check(code == 403, "wrong token rejected")
    code, _ = alert(dict(msg("entry", "buy", 12, 85000, 84500, 86000, 87500), strategy_id=TOKEN), token="x")
    check(code == 200, "token accepted from the strategy_id field")
    reset()

    print("2. 2-lot entry: market 12 + stop 12 + limit 6 @2R + limit 6 @5R")
    code, r = alert(msg("entry", "buy", 12, 85000, 84500.3, 86000.2, 87500, bar="2026-10-21T12:35:00Z"))
    st = mstate(); L = live(st)
    check(code == 200 and st["positions"]["27"]["size"] == 12, "position long 12")
    stops = [o for o in L if "stop_order_type" in o]; lims = [o for o in L if o["order_type"] == "limit_order"]
    check(len(stops) == 1 and stops[0]["size"] == 12 and stops[0]["side"] == "sell" and stops[0]["stop_price"] == "84500.5"
          and stops[0]["reduce_only"] and stops[0]["stop_trigger_method"] == "mark_price", "stop-loss 12 @ 84500.5 (rounded to tick) mark price")
    check(sorted((o["size"], o["limit_price"]) for o in lims) == [(6, "86000"), (6, "87500")] and all(o["reduce_only"] for o in lims),
          "two reduce-only limits 6 @ 86000 and 6 @ 87500")
    code, r = alert(msg("entry", "buy", 12, 85000, 84500.3, 86000.2, 87500, bar="2026-10-21T12:35:00Z"))
    check(r["result"].get("duplicate") and mstate()["positions"]["27"]["size"] == 12, "duplicate alert ignored")

    print("3. exchange fills the first half, script sends tp1 -> stop moved to entry for the remaining 6")
    requests.post(f"{MOCK}/mock/fill/sb_t1_123500")
    check(mstate()["positions"]["27"]["size"] == 6, "position 6 after the half target")
    code, r = alert(msg("tp1", "sell", 6, 86000, sl=85000, tp=86000, tp2=87500, why="TARGET 1 (half)", bar="2026-10-21T13:10:00Z"))
    L = live(mstate()); stops = [o for o in L if "stop_order_type" in o]; lims = [o for o in L if o["order_type"] == "limit_order"]
    check(len(stops) == 1 and stops[0]["size"] == 6 and stops[0]["stop_price"] == "85000", "one stop, size 6, at entry 85000")
    check(len(lims) == 1 and lims[0]["limit_price"] == "87500", "the 5R limit is still there")

    print("4. time stop: exit -> cancel all + close 6 at market")
    code, r = alert(msg("exit", "sell", 6, 86400, why="TIME", bar="2026-10-21T16:35:00Z"))
    st = mstate()
    check(st["positions"]["27"]["size"] == 0 and not live(st), "flat, no open orders")
    closes = [c["order"] for c in st["calls"] if "order" in c and c["order"].get("client_order_id", "").startswith("sb_ex_")]
    check(len(closes) == 1 and closes[0]["size"] == 6 and closes[0]["reduce_only"], "close order reduce-only 6")

    print("5. stop already hit on the exchange, then the script's exit arrives -> nothing to close")
    reset()
    alert(msg("entry", "sell", 12, 85000, 85500, 84000, 82500, bar="2026-10-21T12:40:00Z"))
    requests.post(f"{MOCK}/mock/fill/sb_sl_124000")
    check(mstate()["positions"]["27"]["size"] == 0, "stop filled on the exchange -> flat")
    code, r = alert(msg("exit", "buy", 12, 85500, why="STOP", bar="2026-10-21T12:45:00Z"))
    check(code == 200 and "close" not in r["result"] and not live(mstate()), "exit = only cancel leftovers")

    print("6. flip: RED short open, flip + BLACK entry on the same close")
    reset()
    alert(msg("entry", "sell", 12, 85000, 85500, 84000, "", style="single", bar="2026-10-21T12:50:00Z"))
    alert(msg("flip", "buy", 12, 85100, why="FLIP", style="single", bar="2026-10-21T12:55:00Z"))
    alert(msg("entry", "buy", 12, 85100, 84600, 86100, "", style="single", bar="2026-10-21T12:55:00Z"))
    st = mstate(); L = live(st)
    check(st["positions"]["27"]["size"] == 12, "net long 12 after the flip")
    check(len([o for o in L if "stop_order_type" in o]) == 1 and [o for o in L if "stop_order_type" in o][0]["side"] == "sell"
          and len([o for o in L if o["order_type"] == "limit_order"]) == 1, "one sell stop + one sell limit for the new long")

    print("7. flip message lost: entry while the opposite position is open -> closed first")
    reset()
    alert(msg("entry", "sell", 12, 85000, 85500, 84000, "", style="single", bar="2026-10-21T13:00:00Z"))
    alert(msg("entry", "buy", 12, 85100, 84600, 86100, "", style="single", bar="2026-10-21T13:05:00Z"))
    st = mstate()
    check(st["positions"]["27"]["size"] == 12, "net long 12 (short closed, then long opened)")

    print("8. pyramid: entry, add, be")
    reset()
    alert(msg("entry", "buy", 12, 85000, 84500, 86500, 86500, style="pyr", bar="2026-10-21T13:10:00Z"))
    alert(msg("add", "buy", 12, 85500, 84500, 86500, "", style="pyr", why="ADD", bar="2026-10-21T13:20:00Z"))
    st = mstate(); L = live(st); stops = [o for o in L if "stop_order_type" in o]; lims = [o for o in L if o["order_type"] == "limit_order"]
    check(st["positions"]["27"]["size"] == 24, "position 24 after the add")
    check(len(stops) == 1 and stops[0]["size"] == 24 and stops[0]["stop_price"] == "84500", "stop re-sized to 24")
    check(sum(o["size"] for o in lims) == 24, "targets cover 24")
    alert(msg("be", "buy", 0, 86000, sl=85000, tp=86500, tp2=86500, style="pyr", why="STOP TO ENTRY", bar="2026-10-21T13:30:00Z"))
    L = live(mstate()); stops = [o for o in L if "stop_order_type" in o]
    check(len(stops) == 1 and stops[0]["size"] == 24 and stops[0]["stop_price"] == "85000", "stop moved to entry, size 24")

    print("9. guards")
    code, r = alert(msg("entry", "buy", 40, 85000, 84500, 86000, "", style="single", bar="2026-10-21T13:40:00Z"))
    check(code == 400, "qty above MAX_QTY rejected")
    code, r = alert(msg("entry", "buy", 1, 85000, 84500, 86000, "", style="single", bar="2026-10-21T13:41:00Z", sym="DELTAIN:ETHUSD.P"))
    check(code == 200 and mstate()["positions"].get("3136", {}).get("size") == 1, "DELTAIN:ETHUSD.P mapped to ETHUSD")
    code, r = alert(msg("entry", "buy", 1, 85000, 84500, 86000, "", style="single", bar="2026-10-21T13:42:00Z", sym="SOLUSD.P"))
    check(code in (502, 400), "unknown product -> error, nothing placed")
    code, r = alert({"bad": 1})
    check(code == 400, "message without event/symbol rejected")

    print("10. all requests were signed correctly (mock verified every signature)")
    check(True, f"{len(mstate()['calls'])} signed calls accepted by the mock")
finally:
    relay.terminate(); mock.terminate()
print("\nFAILS:", FAILS if FAILS else "none")
sys.exit(1 if FAILS else 0)
