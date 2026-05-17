import asyncio
from datetime import datetime
from typing import List, Dict
from app.config import settings
from app.services.market_data_service import market_data_service, TradingSignals
from app.services.telegram_notifier import telegram_notifier
from app.services.accuracy_tracker import record_signal, resolve_signals


signal_log: List[Dict] = []
_last_sent: Dict[str, str] = {}

# Stocks monitored for trading signals (most liquid Nifty 100)
_INDIAN_STOCKS = [
    "reliance", "tcs", "hdfcbank", "infy", "icicibank",
    "tatamotors", "sbin", "lt", "wipro", "itc",
    "bhartiartl", "maruti", "nestleind", "hindunilvr", "asianpaint",
    "sunpharma", "titan", "bajajfinance", "hcltech", "kotakbank",
    "axisbank", "ntpc", "tatasteel", "cipla", "ultratech",
]

_MONITORED_SYMBOLS = ['btc', 'eth', 'gold', 'silver'] + _INDIAN_STOCKS


async def check_and_notify():
    for symbol in _MONITORED_SYMBOLS:
        try:
            price_data = await market_data_service.get_price_data(symbol)
            if not price_data:
                continue

            prices_5m = await market_data_service.get_5min_prices(symbol, 100)
            if not prices_5m or len(prices_5m) < 20:
                continue  # Not enough data for a signal

            signal_data = TradingSignals.generate_signal(prices_5m, price_data['price'])

            sig = signal_data['signal']
            conf = signal_data['confidence']
            price = price_data['price']

            await record_signal(symbol, sig, conf, price, signal_data.get('reasons', []))

            entry = {
                'symbol': symbol.upper(),
                'signal': sig,
                'confidence': conf,
                'price': price,
                'reasons': signal_data.get('reasons', []),
                'timestamp': datetime.now().isoformat(),
                'notified': False,
            }

            if sig in ('BUY', 'SELL') and conf >= settings.signal_confidence_threshold:
                last = _last_sent.get(symbol)
                if last != sig:
                    ok = await telegram_notifier.send_signal_alert(
                        symbol, sig, conf, price, signal_data.get('reasons', [])
                    )
                    entry['notified'] = ok
                    if ok:
                        _last_sent[symbol] = sig

            signal_log.insert(0, entry)
            if len(signal_log) > 100:
                signal_log.pop()

        except Exception as e:
            print(f"Signal monitor error for {symbol}: {e}")


async def signal_monitor_loop():
    resolve_counter = 0
    while True:
        await check_and_notify()
        resolve_counter += 1
        if resolve_counter >= 30:
            resolve_counter = 0
            await resolve_signals()
        await asyncio.sleep(settings.signal_check_interval_seconds)


def get_signal_log(limit: int = 50) -> List[Dict]:
    return signal_log[:limit]
