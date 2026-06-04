import asyncio
import hashlib
from datetime import datetime, time as dtime

import pytz

from app.services.telegram_notifier import telegram_notifier
from app.services.options_signal_service import options_signal_service
from app.services.options_trading.dhan_source import FNO_INDICES, FNO_STOCKS

IST = pytz.timezone("Asia/Kolkata")
SCAN_INTERVAL = 300

ALL_SYMBOLS = list(FNO_INDICES.keys()) + FNO_STOCKS

_sent_hashes: set = set()


def _signal_hash(sig: dict) -> str:
    raw = f"{sig['symbol']}_{sig['action']}_{sig['strike']}_{sig['option_type']}_{sig['expiry']}_{sig['entry_price']:.1f}"
    return hashlib.md5(raw.encode()).hexdigest()


def _market_open() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    return dtime(9, 15) <= now.time() <= dtime(15, 30)


async def options_signal_scanner_loop():
    await asyncio.sleep(60)
    while True:
        try:
            if not _market_open():
                await asyncio.sleep(60)
                continue

            all_fresh = []
            for symbol in ALL_SYMBOLS:
                try:
                    result = await options_signal_service.get_live_signals(
                        symbol=symbol, strategy_name="mega",
                    )
                    if not result.get("dhan_available"):
                        continue
                    for sig in result.get("new_signals", []):
                        h = _signal_hash(sig)
                        if h not in _sent_hashes:
                            _sent_hashes.add(h)
                            sig["_underlying"] = result.get("underlying")
                            all_fresh.append(sig)
                except Exception as e:
                    print(f"Scanner error for {symbol}: {e}")
                    continue

            if all_fresh:
                lines = [f"🚀 {len(all_fresh)} New Option Signal{'s' if len(all_fresh) > 1 else ''}\n"]
                for sig in all_fresh:
                    underlying = sig.get("_underlying", 0)
                    lines.append(
                        f"📌 *{sig['symbol']}*  ₹{underlying:.1f}\n"
                        f"   {'🟢 BUY' if 'BUY' in sig['action'] else '🔴 SELL'} *{sig['option_type']}* @ `{sig['strike']}`\n"
                        f"   💰 ₹{sig['entry_price']:.1f} × {sig['qty']}\n"
                        f"   📝 {sig.get('entry_reason', '')[:80]}\n"
                    )
                lines.append("🔗 [Dashboard](http://localhost:8000)")
                await telegram_notifier.send_message("\n".join(lines))

        except Exception as e:
            print(f"Signal scanner error: {e}")

        await asyncio.sleep(SCAN_INTERVAL)
