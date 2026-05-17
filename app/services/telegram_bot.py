import asyncio
import httpx
from datetime import datetime
from app.config import settings
from app.services.market_data_service import market_data_service, TradingSignals
from app.services.telegram_notifier import telegram_notifier
from app.services.real_news import real_news_service
from app.services.sentiment import sentiment_monitor

_last_update_id = 0


def _build_summary(prices: dict, signals: dict, news_count: int, sentiment: str) -> str:
    lines = ["📊 *Trading Dashboard Summary*", ""]
    for sym in ['btc', 'eth', 'gold', 'silver']:
        p = prices.get(sym)
        s = signals.get(sym, {})
        if p:
            emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}
            sig = s.get('signal', 'HOLD')
            conf = s.get('confidence', 0)
            lines.append(
                f"{emoji.get(sig, '⚪')} *{sym.upper()}:* `${p['price']:,.2f}` "
                f"({p.get('change', 0):+.2f}%) — {sig} ({conf*100:.0f}%)"
            )
    lines.append("")
    lines.append(f"📰 News analyzed: `{news_count}` articles")
    lines.append(f"📊 Sentiment: `{sentiment.upper()}`")
    lines.append(f"⏱ Updated: `{datetime.now().strftime('%H:%M:%S')}`")
    lines.append("")
    lines.append("💡 Type /help for commands")
    return "\n".join(lines)


def _build_help() -> str:
    return (
        "🤖 *Available Commands*\n\n"
        "• `summary` or `/summary` — Full dashboard summary\n"
        "• `btc`, `eth`, `gold`, `silver` — Price + signal for one asset\n"
        "• `/help` — This message"
    )


async def _fetch_dashboard_data():
    symbols = ['btc', 'eth', 'gold', 'silver']
    prices = {}
    signals = {}

    for sym in symbols:
        try:
            price_data = await market_data_service.get_price_data(sym)
            if price_data:
                prices[sym] = price_data
                prices_5m = await market_data_service.get_5min_prices(sym, 100)
                signal_data = TradingSignals.generate_signal(prices_5m, price_data['price'])
                signals[sym] = signal_data
        except Exception:
            pass

    try:
        news = await real_news_service.get_all_news()
        sentiment_data = real_news_service.get_market_sentiment(news)
        news_count = len(news)
        sentiment = sentiment_data.get('sentiment', 'neutral')
    except Exception:
        news_count = 0
        sentiment = 'neutral'

    return prices, signals, news_count, sentiment


async def _handle_message(text: str, chat_id: int):
    text = text.strip().lower()

    if text in ('/start', '/help'):
        return await telegram_notifier.send_message(_build_help())

    if text in ('summary', '/summary'):
        prices, signals, news_count, sentiment = await _fetch_dashboard_data()
        msg = _build_summary(prices, signals, news_count, sentiment)
        return await telegram_notifier.send_message(msg)

    if text in ('btc', 'eth', 'gold', 'silver'):
        try:
            price_data = await market_data_service.get_price_data(text)
            if not price_data:
                return await telegram_notifier.send_message(f"Could not fetch data for {text.upper()}")
            prices_5m = await market_data_service.get_5min_prices(text, 100)
            signal_data = TradingSignals.generate_signal(prices_5m, price_data['price'])
            emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}
            sig = signal_data['signal']
            msg = (
                f"{emoji.get(sig, '⚪')} *{text.upper()}*\n"
                f"💰 Price: `${price_data['price']:,.2f}`\n"
                f"📊 Change: `{price_data.get('change', 0):+.2f}%`\n"
                f"📈 Signal: *{sig}* ({signal_data['confidence']*100:.0f}%)\n"
                f"📝 {', '.join(signal_data.get('reasons', [])[:2])}"
            )
            return await telegram_notifier.send_message(msg)
        except Exception as e:
            return await telegram_notifier.send_message(f"Error: {str(e)}")

    return False


async def telegram_poll_loop():
    global _last_update_id
    client = httpx.AsyncClient(timeout=10)
    offset = 0

    while True:
        try:
            resp = await client.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/getUpdates",
                json={"offset": offset, "timeout": 10},
            )
            data = resp.json()
            if data.get("ok"):
                for update in data.get("result", []):
                    update_id = update["update_id"]
                    if update_id >= offset:
                        offset = update_id + 1
                    msg = update.get("message", {})
                    text = msg.get("text", "")
                    chat_id = msg.get("chat", {}).get("id", "")
                    if text and str(chat_id) == settings.telegram_chat_id:
                        await _handle_message(text, chat_id)
        except Exception as e:
            print(f"Telegram poll error: {e}")

        await asyncio.sleep(3)


telegram_bot_poller = telegram_poll_loop
