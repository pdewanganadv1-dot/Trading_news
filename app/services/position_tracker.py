import json
import os
import uuid
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict


POSITIONS_FILE = os.path.join(os.path.dirname(__file__), "../../data/positions.json")


@dataclass
class Position:
    id: str
    symbol: str
    action: str
    strike: int
    option_type: str
    expiry: str
    entry_date: str
    entry_price: float
    qty: int
    entry_reason: str
    underlying_entry: float
    trailing_stop_pct: float
    fixed_target_pct: float
    stop_loss_pct: float
    max_hold_days: int
    is_short: bool
    highest_price: float
    lowest_price: float
    status: str

    def to_dict(self) -> dict:
        return asdict(self)


class PositionTracker:
    def __init__(self, path: str = POSITIONS_FILE):
        self._path = path
        self._positions: List[dict] = []
        self._load()

    def _load(self):
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        if os.path.exists(self._path):
            try:
                with open(self._path) as f:
                    self._positions = json.load(f)
            except (json.JSONDecodeError, Exception):
                self._positions = []

    def _save(self):
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self._positions, f, indent=2)

    def open(self, symbol: str, action: str, strike: int, option_type: str,
             expiry: str, entry_price: float, qty: int, entry_reason: str,
             underlying_entry: float, trailing_stop_pct: float = 0,
             fixed_target_pct: float = 0, stop_loss_pct: float = 0,
             max_hold_days: int = 5) -> dict:
        entry_date = datetime.now().strftime("%Y-%m-%d")
        is_short = "SELL" in action
        pos = {
            "id": str(uuid.uuid4())[:8],
            "symbol": symbol,
            "action": action,
            "strike": strike,
            "option_type": option_type,
            "expiry": expiry,
            "entry_date": entry_date,
            "entry_price": entry_price,
            "qty": qty,
            "entry_reason": entry_reason,
            "underlying_entry": underlying_entry,
            "trailing_stop_pct": trailing_stop_pct,
            "fixed_target_pct": fixed_target_pct,
            "stop_loss_pct": stop_loss_pct,
            "max_hold_days": max_hold_days,
            "is_short": is_short,
            "highest_price": entry_price,
            "lowest_price": entry_price,
            "status": "open",
        }
        self._positions.append(pos)
        self._save()
        return pos

    def update(self, pos_id: str, current_price: float, current_date: str,
               underlying_current: float) -> Optional[dict]:
        pos = self._find(pos_id)
        if not pos or pos["status"] != "open":
            return None

        pos["highest_price"] = max(pos["highest_price"], current_price)
        pos["lowest_price"] = min(pos["lowest_price"], current_price)

        entry = pos["entry_price"]
        days_held = (datetime.strptime(current_date, "%Y-%m-%d") -
                     datetime.strptime(pos["entry_date"], "%Y-%m-%d")).days

        should_exit = False
        exit_reason = ""

        if pos["is_short"]:
            if pos["trailing_stop_pct"] > 0 and pos["lowest_price"] < entry:
                stop = pos["lowest_price"] * (1 + pos["trailing_stop_pct"] / 100)
                if current_price >= stop:
                    should_exit = True
                    exit_reason = f"Trailing stop ({pos['trailing_stop_pct']}% from trough)"
            if pos["stop_loss_pct"] > 0 and not should_exit:
                sl = entry * (1 + pos["stop_loss_pct"] / 100)
                if current_price >= sl:
                    should_exit = True
                    exit_reason = f"Stop loss {pos['stop_loss_pct']}%"
            if pos["fixed_target_pct"] > 0 and not should_exit:
                tgt = entry * (1 - pos["fixed_target_pct"] / 100)
                if current_price <= tgt:
                    should_exit = True
                    exit_reason = f"Target {pos['fixed_target_pct']}%"
        else:
            if pos["trailing_stop_pct"] > 0 and pos["highest_price"] > entry:
                stop = pos["highest_price"] * (1 - pos["trailing_stop_pct"] / 100)
                if current_price <= stop:
                    should_exit = True
                    exit_reason = f"Trailing stop ({pos['trailing_stop_pct']}% from peak)"
            if pos["stop_loss_pct"] > 0 and not should_exit:
                sl = entry * (1 - pos["stop_loss_pct"] / 100)
                if current_price <= sl:
                    should_exit = True
                    exit_reason = f"Stop loss {pos['stop_loss_pct']}%"
            if pos["fixed_target_pct"] > 0 and not should_exit:
                tgt = entry * (1 + pos["fixed_target_pct"] / 100)
                if current_price >= tgt:
                    should_exit = True
                    exit_reason = f"Target {pos['fixed_target_pct']}%"

        if pos["max_hold_days"] > 0 and days_held >= pos["max_hold_days"]:
            should_exit = True
            exit_reason = f"Max hold {pos['max_hold_days']}d"

        if current_date >= pos["expiry"]:
            should_exit = True
            exit_reason = "Expiry"

        if should_exit:
            pos["exit_date"] = current_date
            pos["exit_price"] = current_price
            pos["exit_reason"] = exit_reason
            pos["underlying_exit"] = underlying_current
            pos["days_held"] = days_held
            pos["pnl"] = (entry - current_price) * pos["qty"] if pos["is_short"] else (current_price - entry) * pos["qty"]
            pos["pnl_pct"] = ((entry - current_price) / entry) * 100 if pos["is_short"] else ((current_price - entry) / entry) * 100
            pos["status"] = "closed"
            self._save()
            return pos

        self._save()
        return None

    def close(self, pos_id: str, current_price: float,
              underlying_current: float, reason: str = "Manual") -> Optional[dict]:
        pos = self._find(pos_id)
        if not pos or pos["status"] != "open":
            return None
        exit_date = datetime.now().strftime("%Y-%m-%d")
        entry = pos["entry_price"]
        days_held = (datetime.strptime(exit_date, "%Y-%m-%d") -
                     datetime.strptime(pos["entry_date"], "%Y-%m-%d")).days
        pos["exit_date"] = exit_date
        pos["exit_price"] = current_price
        pos["exit_reason"] = reason
        pos["underlying_exit"] = underlying_current
        pos["days_held"] = days_held
        pos["pnl"] = (entry - current_price) * pos["qty"] if pos["is_short"] else (current_price - entry) * pos["qty"]
        pos["pnl_pct"] = ((entry - current_price) / entry) * 100 if pos["is_short"] else ((current_price - entry) / entry) * 100
        pos["status"] = "closed"
        self._save()
        return pos

    def get_open(self) -> List[dict]:
        return [p for p in self._positions if p["status"] == "open"]

    def get_closed(self, limit: int = 50) -> List[dict]:
        closed = [p for p in self._positions if p["status"] == "closed"]
        return sorted(closed, key=lambda x: x.get("exit_date", ""), reverse=True)[:limit]

    def all(self) -> List[dict]:
        return list(self._positions)

    def running_pnl(self) -> float:
        return sum(p.get("pnl", 0) for p in self._positions if p["status"] == "closed")

    def _find(self, pos_id: str) -> Optional[dict]:
        for p in self._positions:
            if p["id"] == pos_id:
                return p
        return None


position_tracker = PositionTracker()
