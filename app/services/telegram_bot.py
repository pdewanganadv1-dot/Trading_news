import asyncio
import httpx
import re
from datetime import datetime
from app.config import settings
from app.services.market_data_service import market_data_service, TradingSignals
from app.services.telegram_notifier import telegram_notifier
from app.services.real_news import real_news_service
from app.services.chart_generator import generate_signal_chart
from app.services.accuracy_tracker import get_accuracy_stats

_price_alerts: list = []
_alert_id_counter = 0
BOT_TOKEN = settings.telegram_bot_token
CHAT_ID = settings.telegram_chat_id


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
        "• `summary` — Full dashboard summary\n"
        "• `btc` / `eth` / `gold` / `silver` — Price + signal\n"
        "• `/chart btc` — Candlestick chart with EMAs\n"
        "• `alert BTC above 85000` — Set price alert\n"
        "• `alerts` — List active alerts\n"
        "• `remove alert 1` — Remove alert by ID\n"
        "• `/accuracy` — Signal win/loss stats\n"
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


async def _send_photo(caption: str, photo_path: str):
    async with httpx.AsyncClient(timeout=20) as client:
        with open(photo_path, 'rb') as f:
            resp = await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "Markdown"},
                files={"photo": f},
            )
        return resp.status_code == 200


async def _check_price_alerts():
    symbols = set(a['symbol'] for a in _price_alerts if a['active'])
    for sym in symbols:
        try:
            price_data = await market_data_service.get_price_data(sym.lower())
            if not price_data:
                continue
            current = price_data['price']
            triggered = []
            for alert in _price_alerts:
                if not alert['active'] or alert['symbol'].lower() != sym.lower():
                    continue
                direction = alert['direction']
                target = alert['value']
                hit = (direction == 'above' and current >= target) or (direction == 'below' and current <= target)
                if hit:
                    alert['active'] = False
                    triggered.append(alert)
            for a in triggered:
                msg = (
                    f"🚨 *Price Alert Triggered!*\n"
                    f"{a['symbol']} {a['direction']} `${a['value']:,.2f}`\n"
                    f"💰 Current: `${current:,.2f}`"
                )
                await telegram_notifier.send_message(msg)
        except Exception:
            pass


async def _handle_message(text: str, chat_id: int):
    text = text.strip().lower()

    if text in ('/start', '/help'):
        return await telegram_notifier.send_message(_build_help())

    if text in ('summary', '/summary'):
        prices, signals, news_count, sentiment = await _fetch_dashboard_data()
        msg = _build_summary(prices, signals, news_count, sentiment)
        return await telegram_notifier.send_message(msg)

    if text == '/accuracy':
        stats = get_accuracy_stats()
        msg = (
            f"📈 *Signal Accuracy*\n\n"
            f"Total: `{stats['total_signals']}`\n"
            f"Resolved: `{stats['resolved']}`\n"
            f"✅ Wins: `{stats['wins']}`\n"
            f"❌ Losses: `{stats['losses']}`\n"
            f"🎯 Win Rate: *{stats['win_rate']}%*\n"
            f"💰 Avg PnL: `{stats['avg_pnl']}%`"
        )
        return await telegram_notifier.send_message(msg)

    m = re.match(r'^/chart\s+(btc|eth|gold|silver)$', text)
    if m:
        sym = m.group(1)
        path = await generate_signal_chart(sym)
        if path:
            return await _send_photo(f"📈 *{sym.upper()} — 5m Chart*", path)
        return await telegram_notifier.send_message(f"Could not generate chart for {sym.upper()}")

    m = re.match(r'^alert\s+(btc|eth|gold|silver)\s+(above|below)\s+([\d.]+)$', text)
    if m:
        global _alert_id_counter
        _alert_id_counter += 1
        alert = {
            'id': _alert_id_counter,
            'symbol': m.group(1).upper(),
            'direction': m.group(2),
            'value': float(m.group(3)),
            'active': True,
        }
        _price_alerts.append(alert)
        return await telegram_notifier.send_message(
            f"✅ *Alert #{alert['id']} Set*\n"
            f"{alert['symbol']} {alert['direction']} `${alert['value']:,.2f}`"
        )

    if text == 'alerts':
        active = [a for a in _price_alerts if a['active']]
        if not active:
            return await telegram_notifier.send_message("No active price alerts.")
        lines = ["🔔 *Active Price Alerts*", ""]
        for a in active:
            lines.append(f"`#{a['id']}` — {a['symbol']} {a['direction']} `${a['value']:,.2f}`")
        return await telegram_notifier.send_message("\n".join(lines))

    m = re.match(r'^remove\s+alert\s+(\d+)$', text)
    if m:
        alert_id = int(m.group(1))
        for a in _price_alerts:
            if a['id'] == alert_id and a['active']:
                a['active'] = False
                return await telegram_notifier.send_message(f"✅ Alert #{alert_id} removed.")
        return await telegram_notifier.send_message(f"Alert #{alert_id} not found.")

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
            chart_path = await generate_signal_chart(text)
            if chart_path:
                return await _send_photo(msg, chart_path)
            return await telegram_notifier.send_message(msg)
        except Exception as e:
            return await telegram_notifier.send_message(f"Error: {str(e)}")

    return False


async def telegram_poll_loop():
    client = httpx.AsyncClient(timeout=10)
    offset = 0
    check_counter = 0

    while True:
        try:
            resp = await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
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
                    if text and str(chat_id) == CHAT_ID:
                        await _handle_message(text, chat_id)

            check_counter += 1
            if check_counter >= 10:
                check_counter = 0
                await _check_price_alerts()

        except Exception as e:
            print(f"Telegram poll error: {e}")

        await asyncio.sleep(3)
