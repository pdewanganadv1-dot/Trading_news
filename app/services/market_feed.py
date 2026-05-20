import asyncio
import json
import struct
import time
from datetime import datetime
from typing import Dict, Optional
import websockets

from app.config import settings
from app.services.dhanhq_service import (
    _headers, _client, ensure_security_map, get_security_id,
)

WS_URL = "wss://api-feed.dhan.co"

# Shared live price cache: symbol -> dict with ltp, volume, ohlc, timestamp
_live_prices: Dict[str, Dict] = {}
_live_prices_lock = asyncio.Lock()
_ws_connected = False

# Reverse lookup: security_id -> symbol (built once)
_security_id_to_symbol: Dict[str, str] = {}

# Symbols we track — loaded dynamically from Dhan security map on connect.
# Falls back to INDIAN_STOCKS (119) if map hasn't loaded yet.
from app.data.stocks import INDIAN_STOCKS
TRACKED_SYMBOLS: list = []
_FALLBACK_SYMBOLS = [s.upper() for s in INDIAN_STOCKS]


def get_live_price(symbol: str) -> Optional[Dict]:
    return _live_prices.get(symbol.upper())


def get_all_live_prices() -> Dict[str, Dict]:
    return dict(_live_prices)


async def _parse_quote_packet(data: bytes, sec_id: int) -> Optional[Dict]:
    """Parse Quote packet (response code 4) from binary data.
    Structure: header(8B) + LTP(4B f32) + LTQ(2B i16) + LTT(4B i32)
               + ATP(4B f32) + Volume(4B i32) + SellQty(4B i32)
               + BuyQty(4B i32) + Open(4B f32) + Close(4B f32)
               + High(4B f32) + Low(4B f32)
    """
    try:
        if len(data) < 50:
            return None
        ltp = struct.unpack_from("<f", data, 8)[0]
        last_qty = struct.unpack_from("<h", data, 12)[0]
        last_time = struct.unpack_from("<i", data, 14)[0]
        atp = struct.unpack_from("<f", data, 18)[0]
        volume = struct.unpack_from("<i", data, 22)[0]
        sell_qty = struct.unpack_from("<i", data, 26)[0]
        buy_qty = struct.unpack_from("<i", data, 30)[0]
        day_open = struct.unpack_from("<f", data, 34)[0]
        day_close = struct.unpack_from("<f", data, 38)[0]
        day_high = struct.unpack_from("<f", data, 42)[0]
        day_low = struct.unpack_from("<f", data, 46)[0]
        return {
            "ltp": ltp,
            "last_quantity": last_qty,
            "last_trade_time": last_time,
            "atp": atp,
            "volume": volume,
            "total_sell_qty": sell_qty,
            "total_buy_qty": buy_qty,
            "day_open": day_open,
            "day_close": day_close,
            "day_high": day_high,
            "day_low": day_low,
            "timestamp": time.time(),
        }
    except Exception:
        return None


async def _parse_ticker_packet(data: bytes, sec_id: int) -> Optional[Dict]:
    """Parse Ticker packet (response code 2): header(8B) + LTP(4B f32) + LTT(4B i32)"""
    try:
        if len(data) < 16:
            return None
        ltp = struct.unpack_from("<f", data, 8)[0]
        last_time = struct.unpack_from("<i", data, 12)[0]
        return {
            "ltp": ltp,
            "timestamp": time.time(),
        }
    except Exception:
        return None


def _read_header(data: bytes) -> Optional[tuple]:
    """Read 8-byte header: response_code(1B) + length(2B i16) + exchange(1B) + securityId(4B i32)"""
    try:
        resp_code = data[0]
        msg_len = struct.unpack_from("<h", data, 1)[0]
        exchange = data[3]
        sec_id = struct.unpack_from("<i", data, 4)[0]
        return resp_code, msg_len, exchange, sec_id
    except Exception:
        return None


async def _subscribe_all(ws, symbols: list):
    """Subscribe to Quote (RequestCode 17) for all symbols in batches of 100."""
    batch = []
    for sym in symbols:
        sid = get_security_id(sym)
        if sid:
            batch.append({"ExchangeSegment": "NSE_EQ", "SecurityId": sid})
            if len(batch) >= 100:
                msg = {
                    "RequestCode": 17,
                    "InstrumentCount": len(batch),
                    "InstrumentList": batch,
                }
                await ws.send(json.dumps(msg))
                batch = []
    if batch:
        msg = {
            "RequestCode": 17,
            "InstrumentCount": len(batch),
            "InstrumentList": batch,
        }
        await ws.send(json.dumps(msg))


async def _parse_packet(data: bytes):
    """Parse incoming binary packet and update live prices."""
    header = _read_header(data)
    if not header:
        return
    resp_code, msg_len, exchange, sec_id = header

    # Fast reverse lookup
    symbol = _security_id_to_symbol.get(str(sec_id))
    if not symbol:
        return

    parsed = None
    if resp_code == 2:
        parsed = await _parse_ticker_packet(data, sec_id)
    elif resp_code == 4:
        parsed = await _parse_quote_packet(data, sec_id)
    elif resp_code == 5:
        return  # OI packet, skip
    elif resp_code == 6:
        return  # Prev close packet, skip
    elif resp_code == 8:
        parsed = await _parse_quote_packet(data, sec_id)
    elif resp_code == 50:
        print(f"Market feed disconnect code: {sec_id}")
        return

    if parsed:
        parsed["symbol"] = symbol
        async with _live_prices_lock:
            _live_prices[symbol] = parsed


async def feed_loop():
    """Background task: maintain WebSocket connection and stream live prices."""
    global _ws_connected, TRACKED_SYMBOLS

    while True:
        try:
            await ensure_security_map()
            token = _headers()["access-token"]
            cid = _client()
            if not token or not cid:
                await asyncio.sleep(5)
                continue

            # Build tracked symbols from security map (all NSE EQ symbols)
            from app.services.dhanhq_service import _security_map
            if _security_map:
                TRACKED_SYMBOLS = list(_security_map.keys())
                # Build reverse lookup cache
                global _security_id_to_symbol
                _security_id_to_symbol = {sid: sym for sym, sid in _security_map.items()}
            else:
                TRACKED_SYMBOLS = _FALLBACK_SYMBOLS
            print(f"Market feed tracking {len(TRACKED_SYMBOLS)} symbols")

            url = f"{WS_URL}?version=2&token={token}&clientId={cid}&authType=2"
            async with websockets.connect(url, ping_interval=10, ping_timeout=5) as ws:
                _ws_connected = True
                print(f"Market feed connected at {datetime.now().isoformat()}")

                await _subscribe_all(ws, TRACKED_SYMBOLS)

                async for message in ws:
                    if isinstance(message, bytes):
                        # Binary message — parse market data
                        await _parse_packet(message)
                    elif isinstance(message, str):
                        # JSON message — could be acknowledgment or error
                        try:
                            data = json.loads(message)
                            # ACK for subscribe request
                        except json.JSONDecodeError:
                            pass

        except websockets.ConnectionClosed:
            print(f"Market feed disconnected at {datetime.now().isoformat()}")
        except Exception as e:
            print(f"Market feed error: {e}")
        finally:
            _ws_connected = False

        await asyncio.sleep(3)  # Reconnect delay
