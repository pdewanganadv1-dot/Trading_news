"""Minimal signed REST client for Delta Exchange India (v2 API).

Signature (from docs.delta.exchange): hex HMAC-SHA256 of
    method + timestamp + request_path + query_string + body
with the API secret, sent in headers api-key / timestamp / signature.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

import httpx

log = logging.getLogger("delta")


class DeltaError(RuntimeError):
    pass


class DeltaClient:
    def __init__(self, api_key: str, api_secret: str, base_url: str = "https://api.india.delta.exchange",
                 dry_run: bool = False, timeout: float = 10.0):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.dry_run = dry_run
        self.http = httpx.Client(timeout=timeout)
        self._products: dict[str, dict] = {}
        self._fake_id = 1000

    # ------------------------------------------------------------------ core
    def _request(self, method: str, path: str, query: Optional[dict] = None, body: Optional[dict] = None,
                 signed: bool = True) -> Any:
        qs = ""
        if query:
            qs = "?" + "&".join(f"{k}={v}" for k, v in query.items())
        payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = {"Content-Type": "application/json", "User-Agent": "stock_burner-relay/1.0", "Accept": "application/json"}
        if signed:
            ts = str(int(time.time()))
            prehash = method + ts + path + qs + payload
            sig = hmac.new(self.api_secret.encode(), prehash.encode(), hashlib.sha256).hexdigest()
            headers.update({"api-key": self.api_key, "timestamp": ts, "signature": sig})
        url = self.base_url + path + qs
        r = self.http.request(method, url, headers=headers, content=payload if body is not None else None)
        try:
            data = r.json()
        except Exception:
            raise DeltaError(f"{method} {path}: HTTP {r.status_code} non-JSON response: {r.text[:200]}")
        if r.status_code >= 400 or (isinstance(data, dict) and data.get("success") is False):
            raise DeltaError(f"{method} {path}: HTTP {r.status_code} {json.dumps(data)[:400]}")
        return data.get("result", data) if isinstance(data, dict) else data

    # -------------------------------------------------------------- products
    def product(self, symbol: str) -> dict:
        """symbol like BTCUSD (Delta spelling). Cached."""
        if symbol not in self._products:
            p = self._request("GET", f"/v2/products/{symbol}", signed=False)
            self._products[symbol] = {"id": int(p["id"]), "symbol": p["symbol"], "tick_size": Decimal(str(p["tick_size"])),
                                      "contract_value": Decimal(str(p.get("contract_value", "0")))}
        return self._products[symbol]

    def round_price(self, symbol: str, price: float | str | Decimal) -> str:
        tick = self.product(symbol)["tick_size"]
        q = (Decimal(str(price)) / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick
        return format(q.normalize(), "f")

    # -------------------------------------------------------------- account
    def position(self, symbol: str) -> dict:
        """Returns {'size': int (negative = short), 'entry_price': float|None}."""
        pid = self.product(symbol)["id"]
        if self.dry_run:
            return self._dry_pos.get(symbol, {"size": 0, "entry_price": None})
        res = self._request("GET", "/v2/positions", query={"product_id": pid})
        if isinstance(res, list):
            res = next((x for x in res if int(x.get("product_id", -1)) == pid), None) or {}
        size = int(float(res.get("size", 0) or 0))
        ep = res.get("entry_price")
        return {"size": size, "entry_price": float(ep) if ep not in (None, "") else None}

    def open_orders(self, symbol: str) -> list[dict]:
        pid = self.product(symbol)["id"]
        if self.dry_run:
            return []
        res = self._request("GET", "/v2/orders", query={"product_ids": pid, "states": "open,pending"})
        return res if isinstance(res, list) else []

    # --------------------------------------------------------------- orders
    _dry_pos: dict[str, dict] = {}

    def _send_order(self, body: dict) -> dict:
        if self.dry_run:
            self._fake_id += 1
            log.info("DRY-RUN order %s", json.dumps(body))
            # keep a rough position for dry runs
            sym = body.get("product_symbol")
            if body.get("order_type") == "market_order" and "stop_order_type" not in body and sym:
                pos = self._dry_pos.setdefault(sym, {"size": 0, "entry_price": None})
                d = 1 if body["side"] == "buy" else -1
                pos["size"] += d * int(body["size"])
                if pos["size"] == 0:
                    pos["entry_price"] = None
            return {"id": self._fake_id, "state": "closed" if body.get("order_type") == "market_order" else "open", **body}
        return self._request("POST", "/v2/orders", body=body)

    def market(self, symbol: str, side: str, size: int, reduce_only: bool = False, client_order_id: str | None = None) -> dict:
        p = self.product(symbol)
        body = {"product_id": p["id"], "product_symbol": symbol, "size": int(size), "side": side,
                "order_type": "market_order", "reduce_only": bool(reduce_only)}
        if client_order_id:
            body["client_order_id"] = client_order_id[:32]
        return self._send_order(body)

    def limit_reduce(self, symbol: str, side: str, size: int, price, client_order_id: str | None = None) -> dict:
        p = self.product(symbol)
        body = {"product_id": p["id"], "product_symbol": symbol, "size": int(size), "side": side,
                "order_type": "limit_order", "limit_price": self.round_price(symbol, price),
                "reduce_only": True, "time_in_force": "gtc"}
        if client_order_id:
            body["client_order_id"] = client_order_id[:32]
        return self._send_order(body)

    def stop_market_reduce(self, symbol: str, side: str, size: int, stop_price, trigger: str = "mark_price",
                           client_order_id: str | None = None) -> dict:
        p = self.product(symbol)
        body = {"product_id": p["id"], "product_symbol": symbol, "size": int(size), "side": side,
                "order_type": "market_order", "stop_order_type": "stop_loss_order",
                "stop_price": self.round_price(symbol, stop_price), "stop_trigger_method": trigger,
                "reduce_only": True}
        if client_order_id:
            body["client_order_id"] = client_order_id[:32]
        return self._send_order(body)

    def cancel_all(self, symbol: str, limit: bool = True, stop: bool = True, reduce_only: bool = True) -> None:
        p = self.product(symbol)
        body = {"product_id": p["id"], "cancel_limit_orders": bool(limit), "cancel_stop_orders": bool(stop),
                "cancel_reduce_only_orders": bool(reduce_only)}
        if self.dry_run:
            log.info("DRY-RUN cancel_all %s", json.dumps(body))
            return
        self._request("DELETE", "/v2/orders/all", body=body)
