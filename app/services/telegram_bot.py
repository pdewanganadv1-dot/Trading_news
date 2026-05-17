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
from app.services.social_sentiment import fetch_stocktwits, fetch_reddit
import docker

_price_alerts: list = []
_alert_id_counter = 0
_MAX_ALERTS = 50
BOT_TOKEN = settings.telegram_bot_token
CHAT_ID = settings.telegram_chat_id


_TOP_INDIAN = ['reliance', 'tcs', 'infy', 'hdfcbank', 'icicibank']
_ALL_INDIAN = set(_TOP_INDIAN) | {
    'tatamotors', 'sbin', 'lt', 'wipro', 'itc', 'bhartiartl', 'maruti',
    'nestleind', 'hindunilvr', 'asianpaint', 'sunpharma', 'titan',
    'bajajfinance', 'hcltech', 'kotakbank', 'axisbank', 'ntpc', 'tatasteel',
    'cipla', 'ultratech'
}


def _price_fmt(price: float, sym: str) -> str:
    if sym in _ALL_INDIAN:
        return f'₹{price:,.2f}'
    return f'${price:,.2f}'


def _build_summary(prices: dict, signals: dict, news_count: int, sentiment: str, social_verdict: str = "⚪ N/A") -> str:
    lines = ["📊 *Trading Dashboard Summary*", ""]
    for sym in ['btc', 'eth', 'gold', 'silver'] + _TOP_INDIAN:
        p = prices.get(sym)
        s = signals.get(sym, {})
        if p:
            emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}
            sig = s.get('signal', 'HOLD')
            conf = s.get('confidence', 0)
            lines.append(
                f"{emoji.get(sig, '⚪')} *{sym.upper()}:* {_price_fmt(p['price'], sym)} "
                f"({p.get('change', 0):+.2f}%) — {sig} ({conf*100:.0f}%)"
            )
    lines.append("")
    lines.append(f"📰 News analyzed: `{news_count}` articles")
    lines.append(f"📊 Sentiment: `{sentiment.upper()}`")
    lines.append(f"🌐 Social: {social_verdict}")
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
        "• `social btc` — StockTwits + Reddit social sentiment\n"
        "• `reliance` / `tcs` / `hdfcbank` / `infy` — Indian stock price + signal\n"
        "• `docker` — Container status\n"
        "• `/help` — This message"
    )


async def _fetch_dashboard_data():
    symbols = ['btc', 'eth', 'gold', 'silver'] + _TOP_INDIAN
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
    try:
        stocktwits, reddit = await asyncio.gather(fetch_stocktwits("BTC"), fetch_reddit("BTC"))
        social_bull = stocktwits.get('bullish',0)
        social_bear = stocktwits.get('bearish',0)
        social_verdict = "🟢 Bullish" if social_bull > social_bear else "🔴 Bearish" if social_bear > social_bull else "⚪ Neutral"
    except Exception:
        social_verdict = "⚪ N/A"
    return prices, signals, news_count, sentiment, social_verdict


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
                sym = a['symbol'].lower()
                msg = (
                    f"🚨 *Price Alert Triggered!*\n"
                    f"{a['symbol']} {a['direction']} `{_price_fmt(a['value'], sym)}`\n"
                    f"💰 Current: `{_price_fmt(current, sym)}`"
                )
                await telegram_notifier.send_message(msg)
            # Trim inactive alerts
            _price_alerts[:] = [a for a in _price_alerts if a['active']]
        except Exception:
            pass


async def _handle_message(text: str, chat_id: int):
    text = text.strip().lower()

    if text in ('/start', '/help'):
        return await telegram_notifier.send_message(_build_help())

    if text in ('docker', '/docker'):
        try:
            client = docker.from_env()
            containers = client.containers.list(all=True)
            lines = ["🐳 *Docker Status*", ""]
            for c in containers:
                status = c.status
                emoji = "🟢" if status == "running" else "🔴" if status == "exited" else "⚪"
                name = c.name
                lines.append(f"{emoji} `{name}` — {status}")
            return await telegram_notifier.send_message("\n".join(lines))
        except Exception as e:
            return await telegram_notifier.send_message(f"❌ Docker error: {e}")

    m = re.match(r'^social\s+(btc|eth|gold|silver|aapl|tsla|nvda|amzn|msft|googl|reliance|tcs|hdfcbank|infy|icicibank)$', text)
    if m:
        sym = m.group(1).upper()
        stocktwits, reddit = await asyncio.gather(fetch_stocktwits(sym), fetch_reddit(sym))
        lines = [f"🌐 *Social Sentiment — {sym}*", ""]
        lines.append(f"📱 *StockTwits*")
        tw = stocktwits
        lines.append(f"  🟢 Bullish: `{tw.get('bullish',0)}` ({tw.get('bullish_pct',0)}%)")
        lines.append(f"  🔴 Bearish: `{tw.get('bearish',0)}` ({tw.get('bearish_pct',0)}%)")
        lines.append(f"  ⚪ Unlabeled: `{tw.get('unlabeled',0)}`")
        lines.append("")
        lines.append(f"💬 *Reddit*")
        rd = reddit
        lines.append(f"  Posts found: `{rd.get('post_count',0)}`")
        lines.append(f"  Total score: `{rd.get('total_score',0)}`")
        if rd.get('posts'):
            lines.append(f"  Hot: `{rd['posts'][0]['title']}`" if rd['posts'] else "")
        lines.append("")
        total_bull = tw.get('bullish',0) + max(0, rd.get('total_score',0)/10)
        total_bear = tw.get('bearish',0) + abs(min(0, rd.get('total_score',0)/10))
        if total_bull > total_bear:
            lines.append(f"📊 *Overall: BULLISH* 🟢")
        elif total_bear > total_bull:
            lines.append(f"📊 *Overall: BEARISH* 🔴")
        else:
            lines.append(f"📊 *Overall: NEUTRAL* ⚪")
        return await telegram_notifier.send_message("\n".join(lines))

    if text in ('summary', '/summary'):
        prices, signals, news_count, sentiment, social_verdict = await _fetch_dashboard_data()
        msg = _build_summary(prices, signals, news_count, sentiment, social_verdict)
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

    m = re.match(r'^/chart\s+(btc|eth|gold|silver|reliance|tcs|hdfcbank|infy|icicibank)$', text)
    if m:
        sym = m.group(1)
        path = await generate_signal_chart(sym)
        if path:
            return await _send_photo(f"📈 *{sym.upper()} — 5m Chart*", path)
        return await telegram_notifier.send_message(f"Could not generate chart for {sym.upper()}")

    m = re.match(r'^alert\s+(btc|eth|gold|silver|reliance|tcs|hdfcbank|infy|icicibank)\s+(above|below)\s+([\d.]+)$', text)
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
        if len(_price_alerts) > _MAX_ALERTS:
            _price_alerts[:] = [a for a in _price_alerts if a['active']][:_MAX_ALERTS]
        sym_lower = m.group(1).lower()
        return await telegram_notifier.send_message(
            f"✅ *Alert #{alert['id']} Set*\n"
            f"{alert['symbol']} {alert['direction']} `{_price_fmt(alert['value'], sym_lower)}`"
        )

    if text == 'alerts':
        active = [a for a in _price_alerts if a['active']]
        if not active:
            return await telegram_notifier.send_message("No active price alerts.")
        lines = ["🔔 *Active Price Alerts*", ""]
        for a in active:
            sym_lower = a['symbol'].lower()
            lines.append(f"`#{a['id']}` — {a['symbol']} {a['direction']} `{_price_fmt(a['value'], sym_lower)}`")
        return await telegram_notifier.send_message("\n".join(lines))

    m = re.match(r'^remove\s+alert\s+(\d+)$', text)
    if m:
        alert_id = int(m.group(1))
        for a in _price_alerts:
            if a['id'] == alert_id and a['active']:
                a['active'] = False
                return await telegram_notifier.send_message(f"✅ Alert #{alert_id} removed.")
        return await telegram_notifier.send_message(f"Alert #{alert_id} not found.")

    if text in ('btc', 'eth', 'gold', 'silver', 'reliance', 'tcs', 'hdfcbank', 'infy', 'icicibank'):
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
                f"💰 Price: `{_price_fmt(price_data['price'], text)}`\n"
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
    offset = 0
    check_counter = 0

    while True:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
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
            import traceback
            print(f"Telegram poll error ({type(e).__name__}): {e}")
            traceback.print_exc()

        await asyncio.sleep(3)
