"""Crypto signal tracker — monitors active signals, trails SL, records wins/losses."""
import asyncio, time, json, os, hashlib
from datetime import datetime
from typing import Dict, List, Optional
from app.services.crypto_strategy_service import crypto_strategy_service

HISTORY_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "crypto_signal_history.json")
MAX_HOLD_MINUTES = 60  # max 1 hour per signal
MONITOR_INTERVAL = 60  # check every 60s


def _load_history() -> List[Dict]:
    try:
        with open(HISTORY_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_history(history: List[Dict]):
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2, default=str)


def _signal_id(sig: Dict) -> str:
    raw = f"{sig['symbol']}_{sig['signal']}_{sig.get('entry_price', sig.get('price', 0))}_{sig.get('timestamp', '')}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


class CryptoSignalTracker:
    def __init__(self):
        self._last_check: Dict[str, float] = {}

    def record_signal(self, sig: Dict):
        """Save a new signal to history for tracking."""
        history = _load_history()
        sid = _signal_id(sig)
        for h in history:
            if h.get("id") == sid:
                return  # already recorded
        record = {
            "id": sid,
            "symbol": sig["symbol"],
            "direction": sig.get("signal", "?"),
            "entry_price": sig.get("entry_price"),
            "sl_price": sig.get("sl_price"),
            "tp_price": sig.get("tp_price"),
            "trailing_sl_pct": sig.get("trailing_sl_pct", 2.0),
            "confidence": sig.get("confidence", 0),
            "indicators": sig.get("indicators", ""),
            "entry_time": sig.get("timestamp", datetime.now().isoformat()),
            "best_price": sig.get("entry_price"),
            "status": "active",
            "outcome": None,  # "win" | "loss" | "expired"
            "exit_price": None,
            "exit_time": None,
            "pnl_pct": None,
            "reasons": sig.get("reasons", []),
        }
        if record["entry_price"]:
            history.append(record)
            _save_history(history)

    async def check_open_signals(self):
        """Check all active signals against current price, update trailing SL."""
        history = _load_history()
        active = [h for h in history if h.get("status") == "active"]
        if not active:
            return

        now = time.time()
        for record in active:
            symbol = record["symbol"].lower()
            direction = record["direction"]
            entry = record.get("entry_price")
            sl = record.get("sl_price")
            tp = record.get("tp_price")
            trail = record.get("trailing_sl_pct", 2.0)
            best = record.get("best_price", entry)
            entry_time_str = record.get("entry_time", "")
            try:
                entry_ts = datetime.fromisoformat(entry_time_str).timestamp()
            except Exception:
                entry_ts = now

            # Check expiry
            elapsed_min = (now - entry_ts) / 60
            if elapsed_min > MAX_HOLD_MINUTES:
                record["status"] = "expired"
                record["outcome"] = "expired"
                record["exit_time"] = datetime.now().isoformat()
                record["exit_price"] = record.get("exit_price") or record.get("entry_price")
                record["pnl_pct"] = 0
                continue

            # Avoid fetching too often per symbol
            last = self._last_check.get(symbol, 0)
            if now - last < 20:
                continue
            self._last_check[symbol] = now

            # Fetch current price
            data = await crypto_strategy_service._fetch(symbol)
            if not data:
                continue
            current = float(data["close"][-1])

            if direction == "BUY":
                if current > best:
                    best = current
                    record["best_price"] = best
                    # trail SL up
                    new_sl = round(best * (1 - trail / 100), 2)
                    if new_sl > sl:
                        sl = new_sl
                        record["sl_price"] = sl
                if sl and current <= sl:
                    record["status"] = "closed"
                    record["outcome"] = "loss"
                    record["exit_price"] = round(current, 2)
                    record["exit_time"] = datetime.now().isoformat()
                    record["pnl_pct"] = round((current / entry - 1) * 100, 2)
                elif tp and current >= tp:
                    record["status"] = "closed"
                    record["outcome"] = "win"
                    record["exit_price"] = round(current, 2)
                    record["exit_time"] = datetime.now().isoformat()
                    record["pnl_pct"] = round((current / entry - 1) * 100, 2)
            else:  # SELL
                if current < best:
                    best = current
                    record["best_price"] = best
                    # trail SL down
                    new_sl = round(best * (1 + trail / 100), 2)
                    if new_sl < sl or sl is None:
                        sl = new_sl
                        record["sl_price"] = sl
                if sl and current >= sl:
                    record["status"] = "closed"
                    record["outcome"] = "loss"
                    record["exit_price"] = round(current, 2)
                    record["exit_time"] = datetime.now().isoformat()
                    record["pnl_pct"] = round((entry / current - 1) * 100, 2)
                elif tp and current <= tp:
                    record["status"] = "closed"
                    record["outcome"] = "win"
                    record["exit_price"] = round(current, 2)
                    record["exit_time"] = datetime.now().isoformat()
                    record["pnl_pct"] = round((entry / current - 1) * 100, 2)

        _save_history(history)

    def get_history(self, limit: int = 50) -> List[Dict]:
        history = _load_history()
        history.reverse()
        return history[:limit]

    def get_stats(self) -> Dict:
        history = _load_history()
        closed = [h for h in history if h.get("outcome")]
        wins = sum(1 for h in closed if h["outcome"] == "win")
        losses = sum(1 for h in closed if h["outcome"] == "loss")
        total = len(closed)
        return {
            "total_signals": len(history),
            "active": sum(1 for h in history if h.get("status") == "active"),
            "closed": total,
            "wins": wins,
            "losses": losses,
            "expired": sum(1 for h in closed if h["outcome"] == "expired"),
            "win_rate": round(wins / total * 100, 1) if total else 0,
            "avg_win": round(sum(h.get("pnl_pct", 0) for h in closed if h["outcome"] == "win") / wins, 2) if wins else 0,
            "avg_loss": round(sum(abs(h.get("pnl_pct", 0)) for h in closed if h["outcome"] == "loss") / losses, 2) if losses else 0,
        }


crypto_signal_tracker = CryptoSignalTracker()


async def crypto_tracker_loop():
    """Background loop: record new signals from scanner + monitor open ones."""
    await asyncio.sleep(35)
    _last_sent_hashes = set()
    while True:
        try:
            # Pick up any new high-confidence signals and record them
            all_signals = await crypto_strategy_service.get_all_signals()
            for sig in all_signals:
                if sig.get("signal") == "HOLD":
                    continue
                if sig.get("confidence", 0) < 80:
                    continue
                sid = _signal_id(sig)
                if sid not in _last_sent_hashes:
                    _last_sent_hashes.add(sid)
                    crypto_signal_tracker.record_signal(sig)

            # Check existing open signals
            await crypto_signal_tracker.check_open_signals()

        except Exception as e:
            print(f"Crypto tracker error: {e}")

        await asyncio.sleep(MONITOR_INTERVAL)
