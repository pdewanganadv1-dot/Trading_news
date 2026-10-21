"""A tiny fake of the Delta Exchange v2 REST API for testing the relay offline.
Checks that requests are signed (headers present, signature valid for the test secret), keeps a position and
open orders per product, and offers /mock/fill/{client_order_id} to simulate an exchange fill.
Run: TEST_SECRET=x uvicorn mock_delta:app --port 9001
"""
import hashlib, hmac, json, os, time
from fastapi import FastAPI, Request, HTTPException

SECRET = os.environ.get("TEST_SECRET", "testsecret")
PRODUCTS = {"BTCUSD": {"id": 27, "symbol": "BTCUSD", "tick_size": "0.5", "contract_value": "0.001"},
            "ETHUSD": {"id": 3136, "symbol": "ETHUSD", "tick_size": "0.05", "contract_value": "0.01"},
            "XAUTUSD": {"id": 5000, "symbol": "XAUTUSD", "tick_size": "0.01", "contract_value": "0.001"}}
app = FastAPI()
positions: dict[int, dict] = {}
orders: list[dict] = []
calls: list[dict] = []
_id = [5000]


def ok(result):
    return {"success": True, "result": result}


async def check_sig(request: Request, body: str):
    h = request.headers
    for k in ("api-key", "timestamp", "signature"):
        if k not in h:
            raise HTTPException(401, {"success": False, "error": {"code": "missing_" + k}})
    qs = ("?" + str(request.url.query)) if request.url.query else ""
    prehash = request.method + h["timestamp"] + request.url.path + qs + body
    exp = hmac.new(SECRET.encode(), prehash.encode(), hashlib.sha256).hexdigest()
    if exp != h["signature"]:
        raise HTTPException(401, {"success": False, "error": {"code": "Signature Mismatch", "prehash": prehash}})
    if abs(time.time() - int(h["timestamp"])) > 5:
        raise HTTPException(401, {"success": False, "error": {"code": "SignatureExpired"}})


@app.get("/v2/products/{symbol}")
def product(symbol: str):
    if symbol not in PRODUCTS:
        raise HTTPException(404, {"success": False, "error": {"code": "not_found"}})
    return ok(PRODUCTS[symbol])


@app.get("/v2/positions")
async def position(request: Request, product_id: int):
    await check_sig(request, "")
    p = positions.get(product_id, {"size": 0, "entry_price": None})
    return ok({"product_id": product_id, "size": p["size"], "entry_price": p["entry_price"]})


@app.get("/v2/orders")
async def open_orders(request: Request, product_ids: str = "", states: str = ""):
    await check_sig(request, "")
    ids = {int(x) for x in product_ids.split(",") if x}
    return ok([o for o in orders if (not ids or o["product_id"] in ids) and o["state"] in ("open", "pending")])


def apply_fill(o: dict, price: float | None = None):
    pos = positions.setdefault(o["product_id"], {"size": 0, "entry_price": None})
    d = 1 if o["side"] == "buy" else -1
    size = o["size"]
    if o.get("reduce_only"):
        size = min(size, abs(pos["size"])) if (pos["size"] * d < 0) else 0
        if size == 0:
            o["state"] = "cancelled"; o["note"] = "reduce_only with nothing to reduce"; return
    new = pos["size"] + d * size
    if pos["size"] == 0 or (pos["size"] * d > 0):
        px = price or float(o.get("limit_price") or o.get("stop_price") or 100000)
        pos["entry_price"] = px if pos["size"] == 0 else (pos["entry_price"] * abs(pos["size"]) + px * size) / (abs(pos["size"]) + size)
    pos["size"] = new
    if new == 0:
        pos["entry_price"] = None
    o["state"] = "closed"; o["unfilled_size"] = 0


@app.post("/v2/orders")
async def place(request: Request):
    body = (await request.body()).decode()
    await check_sig(request, body)
    o = json.loads(body)
    calls.append({"t": time.time(), "order": o})
    for k in ("product_id", "size", "side", "order_type"):
        if k not in o:
            raise HTTPException(400, {"success": False, "error": {"code": "bad_request", "context": k}})
    if o["size"] <= 0:
        raise HTTPException(400, {"success": False, "error": {"code": "invalid_order_size"}})
    _id[0] += 1
    o["id"] = _id[0]
    if o["order_type"] == "market_order" and "stop_order_type" not in o:
        apply_fill(o, price=None)
    elif "stop_order_type" in o:
        o["state"] = "pending"
    else:
        o["state"] = "open"
    orders.append(o)
    return ok(o)


@app.delete("/v2/orders/all")
async def cancel_all(request: Request):
    body = (await request.body()).decode()
    await check_sig(request, body)
    f = json.loads(body) if body else {}
    calls.append({"t": time.time(), "cancel_all": f})
    n = 0
    for o in orders:
        if o["state"] not in ("open", "pending"):
            continue
        if f.get("product_id") and o["product_id"] != f["product_id"]:
            continue
        is_stop = "stop_order_type" in o
        if is_stop and not f.get("cancel_stop_orders", True):
            continue
        if not is_stop and not f.get("cancel_limit_orders", True):
            continue
        o["state"] = "cancelled"; n += 1
    return {"success": True, "result": {"cancelled": n}}


# ---- test helpers
@app.post("/mock/fill/{client_order_id}")
def fill(client_order_id: str):
    for o in orders:
        if o.get("client_order_id") == client_order_id and o["state"] in ("open", "pending"):
            apply_fill(o)
            return ok(o)
    raise HTTPException(404, "no such open order")


@app.get("/mock/state")
def mstate():
    return {"positions": positions, "orders": [o for o in orders if o["state"] in ("open", "pending")],
            "all_orders": orders, "calls": calls}


@app.post("/mock/reset")
def reset():
    positions.clear(); orders.clear(); calls.clear()
    return {"ok": True}
