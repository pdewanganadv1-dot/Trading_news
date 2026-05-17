import asyncio
from datetime import datetime
from typing import List, Dict
from app.config import settings
from app.services.market_data_service import market_data_service, TradingSignals
from app.services.telegram_notifier import telegram_notifier


signal_log: List[Dict] = []
_last_sent: Dict[str, str] = {}


async def check_and_notify():
    symbols = ['btc', 'eth', 'gold', 'silver']

    for symbol in symbols:
        try:
            price_data = await market_data_service.get_price_data(symbol)
            if not price_data:
                continue

            prices_5m = await market_data_service.get_5min_prices(symbol, 100)
            signal_data = TradingSignals.generate_signal(prices_5m, price_data['price'])

            sig = signal_data['signal']
            conf = signal_data['confidence']
            price = price_data['price']

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
    while True:
        await check_and_notify()
        await asyncio.sleep(settings.signal_check_interval_seconds)


def get_signal_log(limit: int = 50) -> List[Dict]:
    return signal_log[:limit]
