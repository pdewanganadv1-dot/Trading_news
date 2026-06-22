import os
import time
import requests
import pandas as pd
from typing import Dict, List, Optional, Tuple
from functools import lru_cache

from dhanhq import DhanContext, dhanhq
from app.config import settings

SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
SECURITY_MAP_TTL = 86400

FNO_INDICES = {
    "NIFTY": {"security_id": 13, "segment": "IDX_I"},
    "BANKNIFTY": {"security_id": 25, "segment": "IDX_I"},
    "FINNIFTY": {"security_id": 27, "segment": "IDX_I"},
    "MIDCPNIFTY": {"security_id": 442, "segment": "IDX_I"},
}

# Common F&O stocks for option buying
FNO_STOCKS = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN",
    "BAJFINANCE", "BHARTIARTL", "KOTAKBANK", "LT", "WIPRO", "HCLTECH",
    "AXISBANK", "MARUTI", "SUNPHARMA", "TITAN", "ASIANPAINT", "NTPC",
    "ONGC", "POWERGRID", "M&M", "TATAMOTORS", "TATASTEEL", "JSWSTEEL",
    "ADANIENT", "ADANIPORTS", "HINDUNILVR", "ITC", "ULTRACEMCO", "BAJAJFINSV",
    "HDFCLIFE", "SBILIFE", "HEROMOTOCO", "EICHERMOT", "COALINDIA",
    "BRITANNIA", "DIVISLAB", "DRREDDY", "CIPLA", "APOLLOHOSP",
    "NESTLEIND", "GRASIM", "HINDALCO", "INDUSINDBK", "TECHM",
    "BPCL", "IOC", "HAL", "VEDL", "BEL",
]


class DhanSource:
    def __init__(self):
        self._dhan: Optional[dhanhq] = None
        self._security_map: Dict[str, Tuple[int, str]] = {}
        self._security_map_ts: float = 0

    def _get_dhan(self) -> dhanhq:
        if self._dhan is None:
            self._init_client()
        return self._dhan

    def _init_client(self):
        cid = settings.dhan_client_id or os.environ.get("DHAN_CLIENT_ID", "")
        tok = settings.dhan_access_token or os.environ.get("DHAN_ACCESS_TOKEN", "")
        ctx = DhanContext(cid, tok)
        self._dhan = dhanhq(ctx)

    def renew_token(self):
        cid = settings.dhan_client_id or os.environ.get("DHAN_CLIENT_ID", "")
        tok = settings.dhan_access_token or os.environ.get("DHAN_ACCESS_TOKEN", "")
        if not cid or not tok:
            return {"success": False, "error": "Missing credentials"}
        try:
            resp = requests.post("https://api.dhan.co/v2/RenewToken", headers={
                "access-token": tok, "dhanClientId": cid,
            }, timeout=10)
            if resp.ok:
                data = resp.json()
                new_token = data.get("accessToken")
                if new_token:
                    os.environ["DHAN_ACCESS_TOKEN"] = new_token
                    settings.dhan_access_token = new_token
                    self._dhan = None
                    return {"success": True, "access_token": new_token}
            return {"success": False, "status": resp.status_code}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _ensure_security_map(self):
        now = time.time()
        if self._security_map and now - self._security_map_ts < SECURITY_MAP_TTL:
            return
        try:
            df = pd.read_csv(SCRIP_MASTER_URL)
            eq = df[df["SEM_SEGMENT"] == "E"]
            for _, row in eq.iterrows():
                sym = str(row.get("SEM_TRADING_SYMBOL", "")).strip().upper()
                sid = row.get("SEM_SMST_SECURITY_ID")
                if sym and sid:
                    self._security_map[sym] = (int(sid), "NSE_EQ")
            idx = df[df["SEM_SEGMENT"] == "I"]
            for _, row in idx.iterrows():
                sym = str(row.get("SEM_TRADING_SYMBOL", "")).strip().upper()
                sid = row.get("SEM_SMST_SECURITY_ID")
                if sym and sid:
                    self._security_map[sym] = (int(sid), "IDX_I")
            self._security_map_ts = now
        except Exception:
            pass

    def resolve_symbol(self, symbol: str) -> Tuple[int, str]:
        symbol = symbol.upper().strip()
        if symbol in FNO_INDICES:
            info = FNO_INDICES[symbol]
            return info["security_id"], info["segment"]
        self._ensure_security_map()
        if symbol in self._security_map:
            sid, seg = self._security_map[symbol]
            return sid, seg
        raise ValueError(f"Unknown symbol: {symbol}")

    def resolve_fno_symbol(self, symbol: str) -> Tuple[int, str]:
        symbol = symbol.upper().strip()
        if symbol in FNO_INDICES:
            info = FNO_INDICES[symbol]
            return info["security_id"], info["segment"]
        self._ensure_security_map()
        if symbol in self._security_map:
            sid, _ = self._security_map[symbol]
            return sid, "NSE_FNO"
        raise ValueError(f"Unknown symbol: {symbol}")

    def get_option_chain(self, symbol: str, expiry: str) -> Optional[dict]:
        sid, seg = self.resolve_fno_symbol(symbol)
        dhan = self._get_dhan()
        result = dhan.option_chain(sid, seg, expiry)
        if result.get("status") != "success":
            return None
        inner = result.get("data", {})
        if isinstance(inner, dict):
            data_key = "data"
            if data_key in inner:
                inner = inner[data_key]
        return inner

    def get_expiry_list(self, symbol: str) -> List[str]:
        sid, seg = self.resolve_fno_symbol(symbol)
        dhan = self._get_dhan()
        result = dhan.expiry_list(sid, seg)
        if result.get("status") != "success":
            return []
        inner = result.get("data", {})
        if isinstance(inner, dict):
            return inner.get("data", [])
        if isinstance(inner, list):
            return inner
        return []

    def get_ltp(self, symbol: str) -> Optional[float]:
        sid, seg = self.resolve_symbol(symbol)
        dhan = self._get_dhan()
        result = dhan.ticker_data({seg: [sid]})
        if result.get("status") == "success":
            data = result.get("data", {}).get("data", {})
            seg_data = data.get(seg, {})
            return seg_data.get(str(sid), {}).get("last_price")
        return None

    def search_symbols(self, query: str) -> List[dict]:
        self._ensure_security_map()
        query = query.upper().strip()
        results = []
        for sym, (sid, seg) in self._security_map.items():
            if query in sym:
                results.append({"symbol": sym, "security_id": sid, "segment": seg})
        results.sort(key=lambda x: x["symbol"])
        return results[:20]

    def get_ltp_batch(self, symbols: Dict[str, str]) -> dict:
        seg_map: Dict[str, List[int]] = {}
        sym_map: Dict[str, Tuple[int, str]] = {}
        for sym in symbols:
            try:
                sid, seg = self.resolve_symbol(sym)
                seg_map.setdefault(seg, []).append(sid)
                sym_map[sym] = (sid, seg)
            except ValueError:
                continue
        if not seg_map:
            return {}
        dhan = self._get_dhan()
        batch = {seg: ids for seg, ids in seg_map.items()}
        result = dhan.ticker_data(batch)
        if result.get("status") != "success":
            return {}
        data = result.get("data", {}).get("data", {})
        out = {}
        for sym, (sid, seg) in sym_map.items():
            seg_data = data.get(seg, {})
            out[sym] = seg_data.get(str(sid), {}).get("last_price")
        return out


dhan_source = DhanSource()
