import asyncio
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from app.config import settings

from dhanhq import DhanContext, dhanhq

DHAN_BASE = "https://api.dhan.co/v2"

# Global state
dhan_enabled = False
_client_id: Optional[str] = None
_access_token: Optional[str] = None
_token_expiry: Optional[datetime] = None

# SDK instances (lazy-init)
_dhan_context: Optional[DhanContext] = None
_dhan: Optional[dhanhq] = None
_dhan_init_lock = asyncio.Lock()

# Cache for security ID mapping (symbol -> security_id)
_security_map: Dict[str, str] = {}
_security_map_ts: float = 0
_SECURITY_MAP_TTL = 86400


def _init():
    global _client_id, _access_token
    _client_id = settings.dhan_client_id or os.environ.get("DHAN_CLIENT_ID")
    _access_token = settings.dhan_access_token or os.environ.get("DHAN_ACCESS_TOKEN")


_init()


async def _get_dhan():
    global _dhan_context, _dhan, _client_id, _access_token
    cid = _client_id or settings.dhan_client_id or os.environ.get("DHAN_CLIENT_ID", "")
    tok = _access_token or settings.dhan_access_token or os.environ.get("DHAN_ACCESS_TOKEN", "")
    async with _dhan_init_lock:
        if _dhan is None or _dhan_context is None or _dhan_context.client_id != cid:
            _dhan_context = DhanContext(cid, tok)
            _dhan = dhanhq(_dhan_context)
    return _dhan


def _headers() -> Dict[str, str]:
    token = _access_token or settings.dhan_access_token or ""
    return {
        "access-token": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _client() -> str:
    return settings.dhan_client_id or _client_id or ""


async def _load_security_map():
    global _security_map, _security_map_ts
    now = time.time()
    if _security_map and (now - _security_map_ts) < _SECURITY_MAP_TTL:
        return
    try:
        import pandas as pd
        import requests

        url = "https://images.dhan.co/api-data/api-scrip-master.csv"
        resp = requests.get(url, timeout=30)
        if resp.status_code != 200:
            return
        df = pd.read_csv(pd.io.common.StringIO(resp.text))
        for _, row in df.iterrows():
            exch = str(row.get("SEM_EXM_EXCH_ID", "")).strip().upper()
            seg = str(row.get("SEM_SEGMENT", "")).strip().upper()
            if exch != "NSE" or seg != "E":
                continue
            sym = str(row.get("SEM_TRADING_SYMBOL", "")).strip().upper()
            sem_id = str(row.get("SEM_SMST_SECURITY_ID", "")).strip()
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


_security_map_lock = asyncio.Lock()


async def ensure_security_map():
    async with _security_map_lock:
        now = time.time()
        if not _security_map or (now - _security_map_ts) > _SECURITY_MAP_TTL:
            await _load_security_map()


async def renew_token() -> bool:
    global _access_token, _token_expiry, _dhan_context, _dhan
    cid = _client()
    token = _access_token or settings.dhan_access_token or ""
    if not token or not cid:
        return False
    try:
        from dhanhq import DhanLogin
        login = DhanLogin(cid)
        result = await asyncio.to_thread(login.renew_token, token)
        new_token = result.get("accessToken")
        if new_token:
            _access_token = new_token
            _dhan_context = None
            _dhan = None
            _token_expiry = datetime.now() + timedelta(hours=24)
            return True
        return False
    except Exception:
        return False


async def get_profile() -> Optional[Dict]:
    try:
        dhan = await _get_dhan()
        result = await asyncio.to_thread(
            dhan.dhan_http.get, "/profile"
        )
        return result if isinstance(result, dict) else None
    except Exception as e:
        return {"error": str(e)}


async def get_fund_limit() -> Optional[Dict]:
    try:
        dhan = await _get_dhan()
        result = await asyncio.to_thread(dhan.get_fund_limits)
        return result if isinstance(result, dict) else None
    except Exception as e:
        return {"error": str(e)}


async def get_positions() -> Optional[Dict]:
    try:
        dhan = await _get_dhan()
        result = await asyncio.to_thread(dhan.get_positions)
        return result if isinstance(result, dict) else None
    except Exception as e:
        return {"error": str(e)}


async def get_order_book() -> Optional[Dict]:
    try:
        dhan = await _get_dhan()
        result = await asyncio.to_thread(dhan.get_order_list)
        return result if isinstance(result, dict) else None
    except Exception as e:
        return {"error": str(e)}


async def get_trade_book() -> Optional[Dict]:
    try:
        dhan = await _get_dhan()
        result = await asyncio.to_thread(dhan.get_trade_book)
        return result if isinstance(result, dict) else None
    except Exception as e:
        return {"error": str(e)}


async def get_market_ltp(symbols: List[str]) -> Dict[str, float]:
    await ensure_security_map()
    dhan = await _get_dhan()
    ids = []
    sym_map = {}
    for sym in symbols:
        sid = get_security_id(sym)
        if sid:
            ids.append(int(sid))
            sym_map[sid] = sym.upper()
    if not ids:
        return {}
    try:
        result = await asyncio.to_thread(
            dhan.ticker_data, {"NSE_EQ": ids}
        )
        out = {}
        if isinstance(result, dict) and result.get("status") == "success":
            feed = result.get("data", {}).get("NSE_EQ", {})
            for sid, info in feed.items():
                sym = sym_map.get(sid, sid)
                out[sym] = info.get("last_price", 0)
        return out
    except Exception:
        return {}


async def get_market_ohlc(symbols: List[str]) -> Dict[str, Dict]:
    await ensure_security_map()
    dhan = await _get_dhan()
    ids = []
    sym_map = {}
    for sym in symbols:
        sid = get_security_id(sym)
        if sid:
            ids.append(int(sid))
            sym_map[sid] = sym.upper()
    if not ids:
        return {}
    try:
        result = await asyncio.to_thread(
            dhan.ohlc_data, {"NSE_EQ": ids}
        )
        out = {}
        if isinstance(result, dict) and result.get("status") == "success":
            feed = result.get("data", {}).get("NSE_EQ", {})
            for sid, info in feed.items():
                sym = sym_map.get(sid, sid)
                ohlc = info.get("ohlc", {})
                out[sym] = {
                    "ltp": info.get("last_price", 0),
                    "open": ohlc.get("open", 0),
                    "high": ohlc.get("high", 0),
                    "low": ohlc.get("low", 0),
                    "close": ohlc.get("close", 0),
                }
        return out
    except Exception:
        return {}


async def place_order(
    symbol: str,
    qty: int,
    transaction_type: str,
    product_type: str = "INTRADAY",
    order_type: str = "MARKET",
    price: float = 0,
    after_market: bool = False,
    amo_time: str = "",
) -> Optional[Dict]:
    await ensure_security_map()
    sid = get_security_id(symbol)
    if not sid:
        return {"error": f"Security ID not found for {symbol}"}

    try:
        dhan = await _get_dhan()
        result = await asyncio.to_thread(
            dhan.place_order,
            security_id=sid,
            exchange_segment="NSE_EQ",
            transaction_type=transaction_type.upper(),
            quantity=qty,
            order_type=order_type.upper(),
            product_type=product_type.upper(),
            price=price if order_type.upper() == "LIMIT" and price > 0 else 0,
            after_market_order=after_market,
            amo_time=amo_time or "OPEN",
        )
        return result if isinstance(result, dict) else {"error": "unexpected_response"}
    except Exception as e:
        return {"error": str(e)}


async def cancel_order(order_id: str) -> Optional[Dict]:
    try:
        dhan = await _get_dhan()
        result = await asyncio.to_thread(dhan.cancel_order, order_id)
        return result if isinstance(result, dict) else {"error": "unexpected_response"}
    except Exception as e:
        return {"error": str(e)}


async def get_dashboard() -> Dict:
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
    while True:
        try:
            token = _headers()["access-token"]
            if token and _client():
                success = await renew_token()
                if success:
                    print(f"Dhan token renewed at {datetime.now().isoformat()}")
                else:
                    print(f"Dhan token renewal failed, retrying in 5min")
                    await asyncio.sleep(300)
                    continue
        except Exception as e:
            print(f"Dhan token renew error: {e}, retrying in 5min")
            await asyncio.sleep(300)
            continue
        await asyncio.sleep(82800)


def get_debug_status() -> Dict:
    cid = _client()
    token = _headers()["access-token"]
    return {
        "client_id": cid,
        "client_id_source": "settings" if settings.dhan_client_id else "module_cache" if _client_id else "none",
        "has_token": bool(token),
        "token_source": "settings" if settings.dhan_access_token else "module_cache" if _access_token else "none",
        "dhan_enabled": dhan_enabled,
        "has_security_map": bool(_security_map),
    }
