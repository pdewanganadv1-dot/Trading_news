import asyncio
import hashlib

from app.services.telegram_notifier import telegram_notifier
from app.services.crypto_strategy_service import crypto_strategy_service, ALL_SYMBOLS
from app.services.crypto_signal_tracker import crypto_signal_tracker

CRYPTO_SYMBOLS = ALL_SYMBOLS
SCAN_INTERVAL = 300
_sent_hashes: set = set()


def _signal_hash(sig: dict) -> str:
    raw = f"{sig['symbol']}_{sig['signal']}_{sig.get('entry_price', sig['price']):.1f}_{sig.get('_indicator','')}"
    return hashlib.md5(raw.encode()).hexdigest()


MIN_CONFIDENCE = 80

def _fmt_price(p, is_metal=False):
    if p is None: return "—"
    return f"${p:,.2f}" if not is_metal else f"${p:.2f}"

async def crypto_signal_scanner_loop():
    await asyncio.sleep(30)
    while True:
        try:
            all_signals = await crypto_strategy_service.get_all_signals()
            fresh = []
            for sig in all_signals:
                if sig.get("error"):
                    continue
                signal = sig.get("signal", "HOLD")
                if signal == "HOLD":
                    continue
                conf = sig.get("confidence", 0)
                if conf < MIN_CONFIDENCE:
                    continue
                sig["_indicator"] = sig.get("indicators", sig.get("indicator", "?"))
                h = _signal_hash(sig)
                if h not in _sent_hashes:
                    _sent_hashes.add(h)
                    fresh.append(sig)

            if fresh:
                lines = [f"🚀 *Signal{'s' if len(fresh) > 1 else ''}*\n"]
                for sig in fresh:
                    emoji = "🟢" if sig["signal"] == "BUY" else "🔴"
                    sym = sig["symbol"]
                    is_metal = sym in ("GOLD", "SILVER")
                    sym_label = sym if not is_metal else f"{sym} (COMEX)"
                    ep = sig.get("entry_price")
                    sl = sig.get("sl_price")
                    tp = sig.get("tp_price")
                    lines.append(
                        f"{emoji} *{sig['signal']}* *{sym_label}*\n"
                        f"💰 Entry: `{_fmt_price(ep, is_metal)}`\n"
                        f"🛑 SL: `{_fmt_price(sl, is_metal)}`  🎯 TP: `{_fmt_price(tp, is_metal)}`\n"
                        f"📊 Confidence: `{sig['confidence']}%`\n"
                        f"📈 {sig['indicator']} — {', '.join(sig.get('reasons', []))}\n"
                    )
                    crypto_signal_tracker.record_signal(sig)
                lines.append("💡 Trade at your own risk ⚡")
                await telegram_notifier.send_message("\n".join(lines))

        except Exception as e:
            print(f"Crypto scanner error: {e}")

        await asyncio.sleep(SCAN_INTERVAL)
