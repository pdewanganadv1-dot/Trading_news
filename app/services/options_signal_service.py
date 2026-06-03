import asyncio
import math
import statistics
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
import pytz

from app.services.options_backtest.strategies import (
    STRATEGIES, Signal, _find_price, _get_atm_strike, _fii_bias,
)
from app.services.options_backtest.data_loader import options_data_loader
from app.services.options_trading.service import options_trading_service
from app.services.options_trading.dhan_source import FNO_INDICES
from .position_tracker import position_tracker

IST = pytz.timezone("Asia/Kolkata")


def _ohlc_to_snap(row: pd.Series, atm_row: Optional[dict] = None) -> dict:
    return {
        "date": str(row.get("TIMESTAMP", row.get("date", ""))),
        "underlying": row.get("UNDERLYING_VALUE", 0),
    }


def _dhan_to_snapshot(symbol: str, dhan_data: dict, expiry: str) -> Optional[dict]:
    if "error" in dhan_data:
        return None
    chain_raw = dhan_data.get("chain", [])
    underlying = dhan_data.get("underlying", 0)
    date_str = datetime.now(IST).strftime("%Y-%m-%d")

    chain = []
    for r in chain_raw:
        chain.append({
            "strike": r["strike"],
            "ce_close": r.get("ce_ltp", r.get("ce_close", 0)),
            "ce_oi": r.get("ce_oi", 0),
            "ce_chng_oi": r.get("ce_chng_oi", 0),
            "ce_volume": r.get("ce_volume", 0),
            "pe_close": r.get("pe_ltp", r.get("pe_close", 0)),
            "pe_oi": r.get("pe_oi", 0),
            "pe_chng_oi": r.get("pe_chng_oi", 0),
            "pe_volume": r.get("pe_volume", 0),
        })

    total_ce_oi = sum(r["ce_oi"] for r in chain)
    total_pe_oi = sum(r["pe_oi"] for r in chain)
    total_ce_vol = sum(r["ce_volume"] for r in chain)
    total_pe_vol = sum(r["pe_volume"] for r in chain)

    atm = _get_atm_strike(underlying)
    atm_row = next((r for r in chain if r["strike"] == atm), None)

    return {
        "date": date_str,
        "underlying": underlying,
        "chain": chain,
        "pcr_oi": round(total_pe_oi / total_ce_oi, 2) if total_ce_oi else 0,
        "pcr_vol": round(total_pe_vol / total_ce_vol, 2) if total_ce_vol else 0,
        "atm_ce_price": atm_row["ce_close"] if atm_row else 0,
        "atm_pe_price": atm_row["pe_close"] if atm_row else 0,
        "atm_ce_volume": atm_row["ce_volume"] if atm_row else 0,
        "atm_pe_volume": atm_row["pe_volume"] if atm_row else 0,
    }


class OptionsSignalService:
    def __init__(self):
        self._df: Optional[pd.DataFrame] = None
        self._snapshots: List[dict] = []
        self._fiidii_cache: Dict[str, dict] = {}
        self._last_refresh: Optional[datetime] = None

    async def refresh(self, symbol: str = "NIFTY", lookback_days: int = 60):
        today = datetime.now(IST).strftime("%Y-%m-%d")
        from_date = (datetime.now(IST).replace(tzinfo=None) - pd.Timedelta(days=lookback_days)).strftime("%d-%m-%Y")
        to_date = datetime.now(IST).strftime("%d-%m-%Y")

        try:
            df = await asyncio.to_thread(
                options_data_loader.fetch_option_data,
                symbol, "OPTIDX",
                from_date=from_date, to_date=to_date,
            )
        except Exception:
            df = pd.DataFrame()

        if df.empty:
            self._snapshots = []
            self._df = None
            self._last_refresh = None
            return

        self._df = df
        dates = sorted(df["TIMESTAMP"].unique())
        self._snapshots = []
        self._fiidii_cache = {}

        for d in dates:
            date_str = d.strftime("%Y-%m-%d")
            snap = options_data_loader.build_daily_snapshot(df, date_str)
            if snap:
                self._snapshots.append(snap)
            try:
                poi = options_data_loader.fetch_participant_oi(d.strftime("%d-%m-%Y"))
                if poi:
                    self._fiidii_cache[date_str] = poi
            except Exception:
                pass

        self._last_refresh = datetime.now(IST)

    async def get_live_signals(self, symbol: str = "NIFTY",
                               strategy_name: str = "mega",
                               trailing_stop_pct: float = 20,
                               fixed_target_pct: float = 40,
                               max_hold_days: int = 5,
                               max_positions_per_day: int = 2,
                               lot_size: int = 50) -> dict:
        if not self._snapshots or self._last_refresh is None:
            await self.refresh(symbol)

        dhan_data = await options_trading_service.get_option_chain(symbol)
        expiries = dhan_data.get("expiry_dates", []) if "error" not in dhan_data else []

        today = datetime.now(IST).strftime("%Y-%m-%d")

        if "error" in dhan_data or not dhan_data.get("chain"):
            live_snap = None
        else:
            nearest_expiry = dhan_data.get("expiry", "")
            live_snap = _dhan_to_snapshot(symbol, dhan_data, nearest_expiry)

        history = list(self._snapshots)

        if live_snap:
            history.append(live_snap)

        fiidii = self._fiidii_cache.get(today)

        signal_fn = STRATEGIES.get(strategy_name, STRATEGIES["fii_filtered"])
        signals: List[Signal] = signal_fn(today, live_snap or (history[-1] if history else {}),
                                          history[:-1] if history else [], fiidii)

        selected = signals[:max_positions_per_day] if max_positions_per_day else signals

        result_signals = []
        open_positions = position_tracker.get_open()

        existing_keys = {(p["strike"], p["option_type"]) for p in open_positions}

        for sig in selected:
            if (sig.strike, "CE" if "CE" in sig.action else "PE") in existing_keys:
                continue

            price = sig.price or _find_price(live_snap or history[-1], sig.strike, sig.action) if (live_snap or history) else 0
            if price is None or price <= 0:
                continue

            expiry = self._find_nearest_expiry(expiries)
            if not expiry:
                continue

            pos = position_tracker.open(
                symbol=symbol,
                action=sig.action,
                strike=sig.strike,
                option_type="CE" if "CE" in sig.action else "PE",
                expiry=expiry,
                entry_price=price,
                qty=lot_size,
                entry_reason=sig.reason,
                underlying_entry=live_snap["underlying"] if live_snap else 0,
                trailing_stop_pct=trailing_stop_pct / 100,
                fixed_target_pct=fixed_target_pct / 100,
                max_hold_days=max_hold_days,
            )
            result_signals.append(pos)

        return {
            "date": today,
            "strategy": strategy_name,
            "symbol": symbol,
            "data_source": "dhan" if live_snap else "nselib",
            "history_days": len(self._snapshots),
            "fiidii_available": fiidii is not None,
            "new_signals": result_signals,
            "open_positions": position_tracker.get_open(),
            "closed_trades": position_tracker.get_closed(limit=20),
            "running_pnl": position_tracker.running_pnl(),
        }

    async def update_positions(self, symbol: str = "NIFTY") -> dict:
        open_positions = position_tracker.get_open()
        today = datetime.now(IST).strftime("%Y-%m-%d")
        updated = []
        closed = []

        dhan_data = await options_trading_service.get_option_chain(symbol)
        if "error" in dhan_data or not dhan_data.get("chain"):
            return {"updated": 0, "closed": 0, "error": "Live data unavailable"}

        chain = dhan_data.get("chain", [])
        underlying = dhan_data.get("underlying", 0)

        for pos in open_positions:
            strike = pos["strike"]
            optype = pos["option_type"]
            price = _find_price({"chain": chain}, strike, optype)
            if price is None or price <= 0:
                continue
            result = position_tracker.update(pos["id"], price, today, underlying)
            if result:
                closed.append(result)
            else:
                updated.append(pos["id"])

        return {
            "date": today,
            "open_count": len(position_tracker.get_open()),
            "updated_count": len(updated),
            "closed_count": len(closed),
            "closed": closed,
        }

    def _find_nearest_expiry(self, expiries: List[str]) -> Optional[str]:
        today = datetime.now(IST).strftime("%Y-%m-%d")
        future = [e for e in expiries if e > today]
        if future:
            best = min(future)
            days_to = (datetime.strptime(best, "%Y-%m-%d") - datetime.strptime(today, "%Y-%m-%d")).days
            if days_to <= 45:
                return best
        return None


options_signal_service = OptionsSignalService()
