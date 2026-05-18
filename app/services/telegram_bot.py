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
from app.services.market_edge_service import scan_all_stocks, scan_stock, get_market_breadth, get_fii_dii_summary, set_fii_dii, get_fii_dii_history
from app.services.signal_monitor import _MONITORED_SYMBOLS
import docker

_price_alerts: list = []
_alert_id_counter = 0
_MAX_ALERTS = 50
_agent_instructions: list = []  # Pending instructions from Telegram for AI agent
_agent_handled_ids: set = set()
BOT_TOKEN = settings.telegram_bot_token
CHAT_ID = settings.telegram_chat_id

_SYMBOLS = _MONITORED_SYMBOLS
_CHART_SYMBOLS = '|'.join(_SYMBOLS)
_SOCIAL_SYMBOLS = '|'.join(_SYMBOLS)

_TOP_INDIAN = ['reliance', 'tcs', 'infy', 'hdfcbank', 'icicibank']
_ALL_INDIAN = {s for s in _SYMBOLS if s not in ('btc', 'eth', 'gold', 'silver')}


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
        "🤖 *Trading Bot — Full Command List*\n\n"
        "📊 *Market Data*\n"
        "• `btc` / `eth` / `gold` / `silver` — Price, signal & chart\n"
        "• `reliance` / `tcs` / `hdfcbank` / `infy` / `icicibank` — Indian stock price + signal + chart\n"
        "• `summary` — Dashboard overview for all tracked assets\n"
        "• `stocks` — List all monitored assets by category\n\n"
        "🔥 *Edge Scanner*\n"
        "• `edges` — Top 10 stocks ranked by edge score (0-10)\n"
        "• `edge <symbol>` — Edge scan for any stock (e.g. `edge reliance`)\n"
        "• `breadth` — Market breadth: % of Nifty 100 above 20-day SMA\n"
        "• `fiidii` — FII/DII institutional flow + 5-day trend\n"
        "• `setfiidii <FII_buy> <FII_sell> <DII_buy> <DII_sell>` — Update FII/DII data\n\n"
        "📈 *Charts & Signals*\n"
        "• `/chart btc` — Candlestick chart with EMAs (btc/eth/gold/silver + Indian stocks)\n"
        "• `alert BTC above 85000` — Set a price alert\n"
        "• `alert BTC below 75000` — Set a price alert\n"
        "• `alerts` — List all active price alerts\n"
        "• `remove alert 1` — Remove an alert by ID\n"
        "• `/accuracy` — Signal win/loss statistics\n\n"
        "🌐 *Social Sentiment*\n"
        "• `social btc` — StockTwits + Reddit sentiment for any symbol\n\n"
        "📰 *News Sentiment Pipeline*\n"
        "• `sentiment` — Overall Nifty market sentiment overview\n"
        "• `sentiment tcs` — Cached news sentiment for any Nifty stock\n\n"
        "🌅 *Market Briefs*\n"
        "• `premarket` — Pre-market brief: global cues, FII/DII, outlook\n"
        "• `postmarket` — Post-market wrap: gainers, losers, FII/DII\n\n"
        "🛠 *System*\n"
        "• `docker` — Container status\n"
        "• `/help` — This message\n\n"
        "💡 *Tip:* Commands are case-insensitive. Alerts trigger automatically when price hits your target."
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

    m = re.match(r'^social\s+(\w+)$', text)
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

    if text in ('stocks', '/stocks', 'list', '/list'):
        from app.services.signal_monitor import _MONITORED_SYMBOLS
        crypto = [s.upper() for s in _MONITORED_SYMBOLS if s in ('btc', 'eth')]
        metals = [s.upper() for s in _MONITORED_SYMBOLS if s in ('gold', 'silver')]
        indian = [s.upper() for s in _MONITORED_SYMBOLS if s not in ('btc', 'eth', 'gold', 'silver')]
        lines = ["📋 *Tracked Assets*\n"]
        lines.append(f"*Crypto ({len(crypto)})*")
        lines.append("`" + ", ".join(crypto) + "`\n")
        lines.append(f"*Metals ({len(metals)})*")
        lines.append("`" + ", ".join(metals) + "`\n")
        lines.append(f"*Nifty 100 Stocks ({len(indian)})*")
        lines.append("`" + ", ".join(indian) + "`\n")
        lines.append("💡 Type any symbol to get price + signal")
        return await telegram_notifier.send_message("\n".join(lines))

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

    if text == 'edges':
        results = await scan_all_stocks()
        top = results[:10]
        lines = ["🔥 *Market Edge: Top 10*", ""]
        for r in top:
            signals = " | ".join(r.get("signals", [])[:2])
            lines.append(
                f"`{r['symbol']:<12}` Score: `{r['score']}/10` "
                f"Vol: `{r.get('vol_ratio', '-')}x` "
                f"{signals}"
            )
        return await telegram_notifier.send_message("\n".join(lines))

    if text == 'breadth':
        b = await get_market_breadth()
        bar_len = 20
        filled = int(b['pct_above'] / 100 * bar_len)
        bar = "🟩" * filled + "⬜" * (bar_len - filled)
        msg = (
            f"📊 *Market Breadth*\n\n"
            f"Stocks above 20d SMA: `{b['above_sma20']}/{b['total']}`\n"
            f"`{bar}`\n"
            f"*{b['pct_above']}%* of Nifty 100 above SMA20\n\n"
            f"📌 >70% = Overbought | <30% = Oversold"
        )
        return await telegram_notifier.send_message(msg)

    if text in ('fiidii', '/fiidii'):
        d = await get_fii_dii_summary()
        history = get_fii_dii_history(5)
        lines = ["🏦 *FII / DII Flow*", ""]
        lines.append(f"📅 Date: `{d.get('date', 'N/A')}`")
        lines.append(f"🏷 Source: `{d.get('source', '?')}`")
        lines.append("")
        lines.append(f"*FII*")
        lines.append(f"  Buy:  `{d.get('fii_buy', '?')}`")
        lines.append(f"  Sell: `{d.get('fii_sell', '?')}`")
        fii_net = d.get('fii_net')
        if fii_net is not None:
            emoji = "🟢" if fii_net > 0 else "🔴" if fii_net < 0 else "⚪"
            lines.append(f"  Net:  {emoji} `{fii_net:+,.2f}` Cr")
        lines.append("")
        lines.append(f"*DII*")
        lines.append(f"  Buy:  `{d.get('dii_buy', '?')}`")
        lines.append(f"  Sell: `{d.get('dii_sell', '?')}`")
        dii_net = d.get('dii_net')
        if dii_net is not None:
            emoji = "🟢" if dii_net > 0 else "🔴" if dii_net < 0 else "⚪"
            lines.append(f"  Net:  {emoji} `{dii_net:+,.2f}` Cr")
        lines.append("")
        if len(history) >= 2:
            lines.append(f"*📊 FII Net Trend (Last {len(history)} days)*")
            bar_chars = []
            for h in reversed(history):
                net = h.get('fii_net')
                if net is not None:
                    bar_chars.append("🟢" if net > 0 else "🔴" if net < 0 else "⚪")
            if bar_chars:
                lines.append("".join(bar_chars))
            lines.append("")
        lines.append("💡 Use `setfiidii buy sell buy sell` to update")
        return await telegram_notifier.send_message("\n".join(lines))

    m = re.match(r'^setfiidii\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)$', text)
    if m:
        fii_b, fii_s, dii_b, dii_s = float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))
        d = set_fii_dii(fii_b, fii_s, dii_b, dii_s)
        net_fii = d['fii_net']
        emoji = "🟢" if net_fii > 0 else "🔴"
        return await telegram_notifier.send_message(
            f"✅ *FII/DII Updated*\n\n"
            f"{emoji} FII Net: `{net_fii:+,.2f}` Cr\n"
            f"{'🟢' if d['dii_net'] > 0 else '🔴'} DII Net: `{d['dii_net']:+,.2f}` Cr"
        )

    m = re.match(r'^edge\s+(\w+)$', text)
    if m:
        sym = m.group(1).lower()
        r = scan_stock(sym)
        if r.get("error"):
            return await telegram_notifier.send_message(f"No edge data for {sym.upper()}")
        signals = "\n".join(f"  • {s}" for s in r.get("signals", []))
        msg = (
            f"🔍 *Edge Scan — {r['symbol']}*\n\n"
            f"💰 Price: `{r.get('price', '?')}` ({r.get('change_pct', 0):+.2f}%)\n"
            f"📊 Score: `{r['score']}/10`\n"
            f"📈 Vol Ratio: `{r.get('vol_ratio', '?')}x` | RSI: `{r.get('rsi', '?')}`\n"
            f"📉 Streak: `{r.get('streak_days', 0)}d {r.get('streak', 'flat')}`\n"
            f"{signals}"
        )
        return await telegram_notifier.send_message(msg)

    m = re.match(r'^/chart\s+(\w+)$', text)
    if m:
        sym = m.group(1)
        path = await generate_signal_chart(sym)
        if path:
            return await _send_photo(f"📈 *{sym.upper()} — 5m Chart*", path)
        return await telegram_notifier.send_message(f"Could not generate chart for {sym.upper()}")

    m = re.match(r'^alert\s+(\w+)\s+(above|below)\s+([\d.]+)$', text)
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

    m = re.match(r'^(\w+)-news$', text)
    if m:
        sym = m.group(1).lower()
        try:
            from app.routes.news import _fetch_news_for_symbol, filter_news_by_symbol
            from app.services.real_news import real_news_service
            all_news = await _fetch_news_for_symbol(sym)
            articles = filter_news_by_symbol(all_news, sym)
            if not articles:
                return await telegram_notifier.send_message(f"📰 No news found for *{sym.upper()}*")
            sentiment = real_news_service.get_market_sentiment(articles)
            lines = [f"📰 *News — {sym.upper()}*", ""]
            s = sentiment.get("sentiment", "neutral")
            emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}
            lines.append(f"📊 Sentiment: {emoji.get(s, '⚪')} {s.upper()} ({sentiment.get('score', 0):+.2f})")
            lines.append("")
            for article in articles[:6]:
                title = article.get('title', '')
                source = article.get('source', '')
                lines.append(f"• {title}")
                if source:
                    lines.append(f"  └ _{source}_")
                lines.append("")
            return await telegram_notifier.send_message("\n".join(lines))
        except Exception as e:
            return await telegram_notifier.send_message(f"Error fetching news: {e}")

    if text == 'premarket':
        try:
            from app.services.daily_report import send_premarket_brief
            await send_premarket_brief()
            return
        except Exception as e:
            return await telegram_notifier.send_message(f"Error: {e}")

    if text == 'postmarket':
        try:
            from app.services.daily_report import send_postmarket_brief
            await send_postmarket_brief()
            return
        except Exception as e:
            return await telegram_notifier.send_message(f"Error: {e}")

    m = re.match(r'^sentiment\s+(\w+)$', text)
    if m:
        sym = m.group(1).lower()
        try:
            from app.services.news_sentiment_pipeline import get_cached_sentiment
            data = get_cached_sentiment(sym)
            emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪", "unknown": "❓"}
            sent = data.get("sentiment", "unknown")
            msg = (
                f"{emoji.get(sent, '❓')} *News Sentiment — {data.get('symbol', sym.upper())}*\n"
                f"Sentiment: `{sent.upper()}`\n"
                f"Score: `{data.get('score', 0)}`\n"
                f"Articles: `{data.get('article_count', 0)}`\n"
                f"🟢 Bullish: `{data.get('bullish_articles', 0)}` | "
                f"🔴 Bearish: `{data.get('bearish_articles', 0)}`"
            )
            headlines = data.get("top_headlines", [])
            if headlines:
                msg += "\n\n📰 *Top Headlines:*\n" + "\n".join(f"• {h[:80]}" for h in headlines[:2])
            return await telegram_notifier.send_message(msg)
        except Exception as e:
            return await telegram_notifier.send_message(f"Error: {e}")

    if text == 'sentiment':
        try:
            from app.services.news_sentiment_pipeline import get_market_sentiment_overview
            overview = get_market_sentiment_overview()
            if overview.get("status") == "empty":
                return await telegram_notifier.send_message("📊 Sentiment data not yet available. Try again in a few minutes.")
            msg = (
                f"📊 *Market Sentiment Overview*\n\n"
                f"🟢 Bullish: `{overview['bullish']}/{overview['total_symbols']} ({overview['bullish_pct']}%)`\n"
                f"🔴 Bearish: `{overview['bearish']}/{overview['total_symbols']} ({overview['bearish_pct']}%)`\n"
                f"⚪ Neutral: `{overview['neutral']}`\n"
                f"📈 Avg Score: `{overview['avg_score']}`\n\n"
                f"💡 Use `sentiment <symbol>` for individual stock"
            )
            return await telegram_notifier.send_message(msg)
        except Exception as e:
            return await telegram_notifier.send_message(f"Error: {e}")

    if text in _SYMBOLS:
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

    # Forward unrecognized commands to AI agent queue
    if text and chat_id:
        _agent_instructions.append({
            "text": text,
            "chat_id": chat_id,
            "timestamp": datetime.now().isoformat(),
        })
        if len(_agent_instructions) > 100:
            _agent_instructions.pop(0)
        return await telegram_notifier.send_message(
            f"🤖 Forwarded to AI agent. I'll handle this shortly."
        )


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
