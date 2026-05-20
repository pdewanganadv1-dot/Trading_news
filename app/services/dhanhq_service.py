import asyncio
import csv
import io
import time
import httpx
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from app.config import settings
from app.data.stocks import INDIAN_STOCKS

DHAN_BASE = "https://api.dhan.co/v2"
DHAN_AUTH = "https://auth.dhan.co"

# Global state
dhan_enabled = False
_client_id: Optional[str] = None
_access_token: Optional[str] = None
_token_expiry: Optional[datetime] = None

# Cache for security ID mapping (symbol -> security_id)
_security_map: Dict[str, str] = {}
_security_map_ts: float = 0
_SECURITY_MAP_TTL = 86400

# Cache for market quotes
_quote_cache: Dict[str, Dict] = {}
_quote_cache_ts: float = 0
_QUOTE_CACHE_TTL = 10


def _init():
    global _client_id, _access_token
    _client_id = settings.dhan_client_id
    _access_token = settings.dhan_access_token


def _headers() -> Dict[str, str]:
    return {
        "access-token": _access_token or "",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def _load_security_map():
    global _security_map, _security_map_ts
    now = time.time()
    if _security_map and (now - _security_map_ts) < _SECURITY_MAP_TTL:
        return
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get("https://images.dhan.co/api-data/api-scrip-master.csv")
            if resp.status_code != 200:
                return
            content = resp.text
            reader = csv.DictReader(io.StringIO(content))
            for row in reader:
                sym = row.get("SYMBOL_NAME", "").strip().upper()
                sem_id = row.get("SEM_SMST_SECURITY_ID", "").strip()
                if sym and sem_id:
                    _security_map[sym] = sem_id
            _security_map_ts = time.time()
    except Exception as e:
        print(f"Dhan security map load error: {e}")


def get_security_id(symbol: str) -> Optional[str]:
    symbol = symbol.upper().strip()
    if symbol in _security_map:
        return _security_map[symbol]
    return None


async def ensure_security_map():
    if not _security_map:
        await _load_security_map()


async def _get(endpoint: str) -> Optional[Dict]:
    if not _access_token:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{DHAN_BASE}{endpoint}",
                headers={**_headers(), "client-id": _client_id or ""},
            )
            if resp.status_code == 401:
                return {"error": "TOKEN_EXPIRED"}
            if resp.status_code != 200:
                return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
            return resp.json()
    except Exception as e:
        return {"error": str(e)}


async def _post(endpoint: str, data: dict) -> Optional[Dict]:
    if not _access_token:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{DHAN_BASE}{endpoint}",
                headers={**_headers(), "client-id": _client_id or ""},
                json=data,
            )
            if resp.status_code == 401:
                return {"error": "TOKEN_EXPIRED"}
            if resp.status_code not in (200, 201, 202):
                return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
            return resp.json()
    except Exception as e:
        return {"error": str(e)}


async def renew_token() -> bool:
    global _access_token, _token_expiry
    if not _access_token or not _client_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{DHAN_BASE}/RenewToken",
                headers={
                    "access-token": _access_token,
                    "dhanClientId": _client_id,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                new_token = data.get("accessToken")
                if new_token:
                    _access_token = new_token
                    _token_expiry = datetime.now() + timedelta(hours=24)
                    return True
            return False
    except Exception:
        return False


async def get_profile() -> Optional[Dict]:
    return await _get("/profile")


async def get_fund_limit() -> Optional[Dict]:
    return await _get("/fundlimit")


async def get_positions() -> Optional[Dict]:
    return await _get("/positions")


async def get_order_book() -> Optional[Dict]:
    return await _get("/orders")


async def get_trade_book() -> Optional[Dict]:
    return await _get("/trades")


async def get_market_ltp(symbols: List[str]) -> Dict[str, float]:
    """Fetch LTP for multiple symbols using Market Quote API."""
    await ensure_security_map()
    segments: Dict[str, list] = {}
    sym_map: Dict[str, str] = {}
    for sym in symbols:
        sid = get_security_id(sym)
        if sid:
            segments.setdefault("NSE_EQ", []).append(sid)
            sym_map[sid] = sym.upper()

    if not segments:
        return {}

    result = {}
    for exchange, ids in segments.items():
        data = {exchange: ids}
        resp = await _post("/marketfeed/ltp", data)
        if resp and resp.get("status") == "success":
            feed = resp.get("data", {}).get(exchange, {})
            for sid, info in feed.items():
                sym = sym_map.get(sid, sid)
                result[sym] = info.get("last_price", 0)
    return result


async def get_market_ohlc(symbols: List[str]) -> Dict[str, Dict]:
    """Fetch OHLC for multiple symbols."""
    await ensure_security_map()
    segments: Dict[str, list] = {}
    sym_map: Dict[str, str] = {}
    for sym in symbols:
        sid = get_security_id(sym)
        if sid:
            segments.setdefault("NSE_EQ", []).append(sid)
            sym_map[sid] = sym.upper()

    if not segments:
        return {}

    result = {}
    for exchange, ids in segments.items():
        data = {exchange: ids}
        resp = await _post("/marketfeed/ohlc", data)
        if resp and resp.get("status") == "success":
            feed = resp.get("data", {}).get(exchange, {})
            for sid, info in feed.items():
                sym = sym_map.get(sid, sid)
                ohlc = info.get("ohlc", {})
                result[sym] = {
                    "ltp": info.get("last_price", 0),
                    "open": ohlc.get("open", 0),
                    "high": ohlc.get("high", 0),
                    "low": ohlc.get("low", 0),
                    "close": ohlc.get("close", 0),
                }
    return result


async def place_order(
    symbol: str,
    qty: int,
    transaction_type: str,
    product_type: str = "INTRADAY",
    order_type: str = "MARKET",
    price: float = 0,
) -> Optional[Dict]:
    """Place an order via DhanHQ."""
    await ensure_security_map()
    sid = get_security_id(symbol)
    if not sid:
        return {"error": f"Security ID not found for {symbol}"}

    payload = {
        "dhanClientId": _client_id,
        "transactionType": transaction_type.upper(),
        "exchangeSegment": "NSE_EQ",
        "productType": product_type.upper(),
        "orderType": order_type.upper(),
        "validity": "DAY",
        "securityId": sid,
        "quantity": str(qty),
    }
    if order_type.upper() == "LIMIT" and price > 0:
        payload["price"] = str(price)

    return await _post("/orders", payload)


async def cancel_order(order_id: str) -> Optional[Dict]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.delete(
                f"{DHAN_BASE}/orders/{order_id}",
                headers={**_headers(), "client-id": _client_id or ""},
            )
            if resp.status_code in (200, 202):
                return resp.json()
            return {"error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"error": str(e)}


async def get_dashboard() -> Dict:
    """Full DhanHQ dashboard: funds, positions, orders, profile."""
    profile, funds, positions, orders = await asyncio.gather(
        get_profile(), get_fund_limit(), get_positions(), get_order_book(),
        return_exceptions=True,
    )
    return {
        "profile": profile if isinstance(profile, dict) else None,
        "funds": funds if isinstance(funds, dict) else None,
        "positions": positions if isinstance(positions, dict) else None,
        "orders": orders if isinstance(orders, dict) else None,
    }


async def auto_renew_loop():
    """Background loop: renew Dhan access token every 23 hours."""
    while True:
        try:
            if _access_token and _client_id:
                success = await renew_token()
                if success:
                    print(f"Dhan token renewed at {datetime.now().isoformat()}")
        except Exception as e:
            print(f"Dhan token renew error: {e}")
        await asyncio.sleep(82800)  # 23 hours
