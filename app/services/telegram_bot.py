import asyncio
import httpx
import re
import time
from datetime import datetime
from app.config import settings
from app.services.market_data_service import market_data_service, TradingSignals
from app.services.telegram_notifier import telegram_notifier
from app.services.real_news import real_news_service
from app.services.chart_generator import generate_signal_chart
from app.services.accuracy_tracker import get_accuracy_stats
from app.services.social_sentiment import fetch_stocktwits, fetch_reddit
from app.services.market_edge_service import scan_all_stocks, scan_stock, get_market_breadth, get_fii_dii_summary, set_fii_dii, get_fii_dii_history
from app.services.signal_monitor import _MONITORED_SYMBOLS, get_cached_realtime, get_cached_signals
from app.services.options_chain_service import options_chain_service
from app.services.insider_service import insider_trading_service
from app.services.sector_service import sector_rotation_service
from app.services.politician_service import politician_trades_service
from app.services.ai_agent_service import ai_agent_service
from app.services.strategy_marketplace import strategy_marketplace_service
from app.services.ema_bounce_scanner import get_recent_bounces, run_backtest
import app.services.ema_bounce_scanner as _ebs
from app.services.dhanhq_service import (
    get_dashboard, get_fund_limit, get_market_ltp, place_order,
    get_order_book, get_positions, dhan_enabled,
)
import app.services.dhanhq_service as _dhan

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
        "• `/scalp` — SCALP signals: EMA 200 bounce on 1min chart across all 119 stocks\n"
        "• `/scalpbt` — Backtest EMA 200 scalp strategy on 6mo daily data\n"
        "• `/scalpon` — Enable SCALP signals & auto-scan\n"
        "• `/scalpoff` — Disable SCALP signals & auto-scan\n"
        "• `/dhan` — DhanHQ dashboard (funds, account, data plan)\n"
        "• `/dhanon` — Enable DhanHQ auto-trading\n"
        "• `/dhanoff` — Disable DhanHQ auto-trading\n"
        "• `/buy <sym> <qty>` — Place BUY order via Dhan\n"
        "• `/sell <sym> <qty>` — Place SELL order via Dhan\n"
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
        "🤖 *AI Agent*\n"
        "• `/agent <symbol>` — Multi-modal AI analysis (news + tech + FII/DII + social)\n"
        "• `social <symbol>` — StockTwits + Reddit sentiment\n\n"
        "📊 *Options & Derivatives*\n"
        "• `/options <symbol>` — Option chain, PCR, max pain, key levels\n"
        "• `edges` — Top 10 stocks by edge score\n"
        "• `fiidii` — FII/DII institutional flow\n\n"
        "📋 *Strategy Marketplace*\n"
        "• `/strategies` — Browse top trading strategies\n"
        "• `/backtest <id>` — Run backtest on NIFTY\n\n"
        "🏛 *Institutional & Political Flows*\n"
        "• `/insider` — Bulk & block deals summary\n"
        "• `/politicians` — Group/entity trade flows\n\n"
        "🔄 *Sector Analysis*\n"
        "• `/sectors` — Sector rotation performance\n\n"
        "🌅 *Market Briefs*\n"
        "• `premarket` — Pre-market brief: global cues, FII/DII, outlook\n"
        "• `postmarket` — Post-market wrap: gainers, losers, FII/DII\n\n"
        "🛠 *System*\n"
        "• `docker` — Container status\n"
        "• `/markets` — All feature overview\n"
        "• `/live <sym>` — Live market price via WebSocket\n"
        "• `/gainers` — Top gainers (live)\n"
        "• `/losers` — Top losers (live)\n"
        "• `/breadth` — Live breadth: stocks above/below day open\n"
        "• `/edges` — Live edge scores\n"
        "• `/help` — This message\n\n"
        "💡 *Tip:* Commands are case-insensitive. Alerts trigger automatically when price hits your target."
    )


async def _fetch_dashboard_data():
    symbols = ['btc', 'eth', 'gold', 'silver'] + _TOP_INDIAN
    realtime = get_cached_realtime()
    cached_sigs = get_cached_signals()
    prices = {}
    signals = {}
    for sym in symbols:
        rt = realtime.get(sym.lower())
        cs = cached_sigs.get(sym.lower())
        if rt and cs and rt.get("timestamp") and cs.get("timestamp"):
            # Use cache if less than 5min old
            try:
                age = (datetime.now() - datetime.fromisoformat(rt["timestamp"])).total_seconds()
                if age < 300:
                    prices[sym] = rt.get("price", {})
                    sig = cs.get("signal", "HOLD")
                    conf = cs.get("confidence", 0)
                    reasons = cs.get("reasons", [])
                    signals[sym] = {"signal": sig, "confidence": conf, "reasons": reasons}
                    continue
            except Exception:
                pass
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
    # Strip bot username from commands (e.g. /dhanon@bot -> /dhanon)
    if '@' in text:
        text = text.split('@')[0].strip()

    if text in ('/start', '/help'):
        return await telegram_notifier.send_message(_build_help())

    if text in ('docker', '/docker'):
        try:
            import docker
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

    if text in ('list', '/list'):
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

    if text in ('edges', '/edges'):
        from app.services.live_analysis import get_live_edges
        edges = get_live_edges()
        if not edges:
            return await telegram_notifier.send_message("No live data yet.")
        msg = "⚡ *Live Edge Scores*\n\n"
        for e in edges[:15]:
            direction = "🟢" if e["edge"] > 0 else "🔴"
            msg += f"{direction} `{e['symbol']:<12}` Edge: `{e['edge']:+.1f}` ₹{e['ltp']:,.2f} ({e['day_pct']:+.2f}%)\n"
        return await telegram_notifier.send_message(msg)

    if text in ('/feed', 'feed'):
        from app.services.market_feed import _ws_connected, TRACKED_SYMBOLS, _live_prices
        count = len(_live_prices)
        tracked = len(TRACKED_SYMBOLS)
        status = "🟢 Connected" if _ws_connected else "🔴 Disconnected"
        if count and tracked:
            pct = f" ({count*100//tracked}% populated)"
        else:
            pct = ""
        msg = (
            f"📡 *Market Feed Status*\n\n"
            f"Status: {status}\n"
            f"Tracking: `{tracked:,}` symbols\n"
            f"Live prices: `{count:,}`{pct}\n"
        )
        if count:
            sample = sorted(_live_prices.keys())[:5]
            msg += f"\nSample: `{', '.join(sample)}`\n"
        latest_ts = max((v.get("timestamp", 0) for v in _live_prices.values()), default=0)
        from datetime import datetime
        if latest_ts:
            age = int(time.time() - latest_ts)
            msg += f"Last update: `{age}s` ago"
        return await telegram_notifier.send_message(msg)

    if text in ('/scalpbt', 'scalpbt'):
        if not _ebs.scalp_enabled:
            return await telegram_notifier.send_message("⚙️ SCALP backtest is disabled. Use `/scalpon` to enable.")
        bt = await run_backtest()
        if bt.get("status") == "empty":
            return await telegram_notifier.send_message("📊 Backtest: No trades generated.")
        msg = (
            f"📊 *SCALP Backtest Results*\n\n"
            f"Period: `6 months` (daily) | Stocks: `{bt['stocks_with_signals']}`\n\n"
            f"*Summary*\n"
            f"📈 Total Trades: `{bt['total_trades']}`\n"
            f"🎯 Win Rate: `{bt['win_rate']}%`\n"
            f"💰 Avg Return: `{bt['avg_return']:+.2f}%`\n"
            f"🟢 Avg Win: `{bt['avg_win']:+.2f}%` | 🔴 Avg Loss: `{bt['avg_loss']:+.2f}%`\n"
            f"📊 Sharpe: `{bt['sharpe']}`\n"
            f"📈 Max: `{bt['max_return']:+.2f}%` | 📉 Min: `{bt['min_return']:+.2f}%`\n"
            f"🟢 BUY trades: `{bt['buy_trades']}` avg `{bt['buy_avg']:+.2f}%`\n"
            f"🔴 SELL trades: `{bt['sell_trades']}` avg `{bt['sell_avg']:+.2f}%`\n\n"
            f"*Top 5 Trades:*\n"
        )
        for t in bt.get("best_trades", []):
            emoji = "🟢" if t['return_pct'] > 0 else "🔴"
            msg += f"{emoji} `{t['symbol']}` {t['direction']} {t['return_pct']:+.2f}% ({t['exit_reason']})\n"
        msg += f"\n*Worst 5 Trades:*\n"
        for t in bt.get("worst_trades", []):
            emoji = "🟢" if t['return_pct'] > 0 else "🔴"
            msg += f"{emoji} `{t['symbol']}` {t['direction']} {t['return_pct']:+.2f}% ({t['exit_reason']})\n"
        return await telegram_notifier.send_message(msg)

    if text in ('/scalpon', 'scalpon'):
        _ebs.scalp_enabled = True
        return await telegram_notifier.send_message("✅ SCALP signals enabled. `/scalp` and auto-scan are now active.")

    if text in ('/scalpoff', 'scalpoff'):
        _ebs.scalp_enabled = False
        return await telegram_notifier.send_message("❌ SCALP signals disabled. Use `/scalpon` to re-enable later.")

    if text in ('/scalp', 'scalp'):
        if not _ebs.scalp_enabled:
            return await telegram_notifier.send_message("⚙️ SCALP signals are disabled. Use `/scalpon` to enable.")
        status_msg = await telegram_notifier.send_message("🔍 SCALP scan on 119 stocks (1m EMA200)... ⏳")
        signals = await get_recent_bounces(min_strength=0.3)
        buys = [s for s in signals if s['direction'] == 'BUY']
        sells = [s for s in signals if s['direction'] == 'SELL']
        lines = ["⚡ *SCALP Signals*", ""]
        lines.append(f"Found *{len(signals)}* active scalp setups")
        lines.append(f"🟢 SCALP BUY: `{len(buys)}`  🔴 SCALP SELL: `{len(sells)}`")
        lines.append("")
        if buys:
            lines.append("*🟢 SCALP BUY (bounce up from EMA200):*")
            for s in buys[:8]:
                lines.append(
                    f"`{s['symbol']:<12}` ₹{s['price']:<8} "
                    f"EMA: `{s['ema200']}` "
                    f"Str: `{s['strength']}` "
                    f"{'📈' if s.get('vol_ratio', 0) > 1.5 else ''}"
                )
                if s.get('reason'):
                    lines.append(f"  └ {s['reason'][:80]}")
            lines.append("")
        if sells:
            lines.append("*🔴 SCALP SELL (break below EMA200):*")
            for s in sells[:8]:
                lines.append(
                    f"`{s['symbol']:<12}` ₹{s['price']:<8} "
                    f"EMA: `{s['ema200']}` "
                    f"Str: `{s['strength']}` "
                    f"{'📉' if s.get('vol_ratio', 0) > 1.5 else ''}"
                )
                if s.get('reason'):
                    lines.append(f"  └ {s['reason'][:80]}")
        lines.append("")
        lines.append("🎯 Target: 10% | ⏱ 1min TF | Intraday/weekend hold")
        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:3900] + "\n\n... (truncated)"
        return await telegram_notifier.send_message(msg)

    if text in ('breadth', '/breadth'):
        from app.services.live_analysis import get_live_breadth
        b = get_live_breadth()
        if b["total"] == 0:
            return await telegram_notifier.send_message("No live data yet.")
        above_pct = round(b["above"] / max(b["total"], 1) * 100, 1)
        bar_len = 20
        filled = int(above_pct / 100 * bar_len)
        bar = "🟩" * filled + "⬜" * (bar_len - filled)
        msg = (
            f"📊 *Live Market Breadth*\n\n"
            f"Above day open: 🟢 `{b['above']}/{b['total']}` ({above_pct}%)\n"
            f"Below day open: 🔴 `{b['below']}/{b['total']}`\n"
            f"`{bar}`\n\n"
        )
        if b["stocks_above"]:
            msg += "*Top Gainers:*\n"
            for sym, pct in b["stocks_above"][:5]:
                msg += f"🟢 `{sym:<12}` +{pct}%\n"
        if b["stocks_below"]:
            msg += "\n*Top Losers:*\n"
            for sym, pct in b["stocks_below"][:5]:
                msg += f"🔴 `{sym:<12}` {pct}%\n"
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
                f"*Top Bullish Stocks:*\n"
            )
            for sym, sc in overview.get("bullish_stocks", [])[:5]:
                msg += f"🟢 `{sym:<12}` Score: `{sc}`\n"
            msg += f"\n*Top Bearish Stocks:*\n"
            for sym, sc in overview.get("bearish_stocks", [])[:5]:
                msg += f"🔴 `{sym:<12}` Score: `{sc}`\n"
            msg += f"\n💡 Use `sentiment <symbol>` for individual stock"
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

    m = re.match(r'^/agent\s+(\w+)$', text)
    if m:
        sym = m.group(1).upper()
        status_msg = await telegram_notifier.send_message(f"🤖 AI Agent analyzing *{sym}*... Please wait ⏳")
        try:
            result = await ai_agent_service.analyze_stock(sym, 50)
            v = result.get("verdict", {})
            bias = v.get("bias", "NEUTRAL")
            emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪"}
            action = v.get("action", "HOLD")
            msg = (
                f"{emoji.get(bias, '⚪')} *AI Agent — {sym}*\n\n"
                f"*Bias:* {bias} | *Action:* {action}\n"
                f"*Conviction:* {v.get('conviction', 'MEDIUM')}\n\n"
                f"_{v.get('analysis', 'No analysis')[:800]}_\n\n"
                f"🤖 Source: {v.get('source', 'template')}"
            )
            return await telegram_notifier.send_message(msg)
        except Exception as e:
            return await telegram_notifier.send_message(f"❌ Agent error: {e}")

    if text == '/strategies':
        strategies = strategy_marketplace_service.get_strategies()
        lines = ["📋 *Strategy Marketplace — Top Strategies*", ""]
        for s in strategies[:6]:
            ret = s["metrics"]["total_return"]
            emoji = "🟢" if ret.startswith("+") else "🔴"
            lines.append(f"{emoji} *{s['name']}* by {s['author']}")
            lines.append(f"   ├ Return: `{ret}` | Win: `{s['metrics']['win_rate']}` | Sharpe: `{s['metrics']['sharpe']}`")
            lines.append(f"   ├ Type: `{s['type']}` | TF: `{s['timeframe']}` | Copies: `{s['copies']}`")
            lines.append(f"   └ ⭐ `{s['rating']}/5` | Tags: `{', '.join(s['tags'][:3])}`")
            lines.append("")
        lines.append("💡 Use `/backtest <id>` to run a backtest")
        return await telegram_notifier.send_message("\n".join(lines))

    m = re.match(r'^/backtest\s+(\w+)$', text)
    if m:
        sid = m.group(1)
        status_msg = await telegram_notifier.send_message(f"🔄 Running backtest for `{sid}`...")
        try:
            bt = strategy_marketplace_service.run_backtest(sid, "NIFTY", 365)
            msg = (
                f"📊 *Backtest — {bt.get('strategy_id')}*\n"
                f"📈 Period: `{bt.get('period')}` on `{bt.get('symbol')}`\n\n"
                f"*Results:*\n"
                f"🟢 Return: `{bt.get('total_return_pct', 0):+.2f}%`\n"
                f"🎯 Win Rate: `{bt.get('win_rate', 0)}%`\n"
                f"📉 Max DD: `{bt.get('max_drawdown', 0)}%`\n"
                f"📊 Profit Factor: `{bt.get('profit_factor', 'inf')}`\n"
                f"🔄 Trades: `{bt.get('total_trades', 0)}`\n"
                f"💰 Capital: `₹{bt.get('final_capital', 0):,.0f}` (from ₹{bt.get('initial_capital', 0):,.0f})"
            )
            return await telegram_notifier.send_message(msg)
        except Exception as e:
            return await telegram_notifier.send_message(f"❌ Backtest error: {e}")

    m = re.match(r'^/options\s+(\w+)$', text)
    if m:
        sym = m.group(1).upper()
        try:
            data = await options_chain_service.get_option_chain(sym)
            if "error" in data:
                return await telegram_notifier.send_message(f"❌ {data['error']}")
            chain = data.get("chain", [])
            lines = [f"📊 *Options Chain — {sym}*", ""]
            lines.append(f"📅 Expiry: `{data.get('expiry_date', 'N/A')}`")
            lines.append(f"💰 Underlying: `₹{data.get('underlying', 0):,.2f}`")
            lines.append(f"🟢 Max Pain: `₹{data.get('max_pain', 0):,}`")
            lines.append(f"📊 PCR OI: `{data.get('pcr_oi', '--')}` | PCR Vol: `{data.get('pcr_vol', '--')}`")
            lines.append("")
            k = data.get("key_levels", {})
            if k.get("max_ce_oi"):
                lines.append(f"🔴 Resistance (Max CE OI): `₹{k['max_ce_oi']['strike']:,}`")
            if k.get("max_pe_oi"):
                lines.append(f"🟢 Support (Max PE OI): `₹{k['max_pe_oi']['strike']:,}`")
            lines.append("")
            atm = None
            for r in chain:
                if data.get("underlying") and abs(r["strike"] - data["underlying"]) / data["underlying"] < 0.005:
                    atm = r
                    break
            if atm:
                pcr_strike = round(atm["pe_oi"] / max(atm["ce_oi"], 1), 2)
                lines.append(f"*ATM Strike (₹{atm['strike']:,}):*")
                lines.append(f"CE OI: `{atm['ce_oi']:,}` | PE OI: `{atm['pe_oi']:,}` | PCR: `{pcr_strike}`")
            lines.append(f"\n💡 Total OI — CE: `{data.get('total_ce_oi', 0):,}` | PE: `{data.get('total_pe_oi', 0):,}`")
            return await telegram_notifier.send_message("\n".join(lines))
        except Exception as e:
            return await telegram_notifier.send_message(f"❌ Options error: {e}")

    if text == '/insider':
        try:
            summary = await insider_trading_service.get_insider_summary("1M")
            bulk = await insider_trading_service.get_bulk_deals("1W")
            block = await insider_trading_service.get_block_deals("1W")
            lines = ["🔍 *Insider Trading — Bulk & Block Deals*", ""]
            lines.append(f"*Bulk Deals (1M)*")
            lines.append(f"Total: `{summary.get('bulk_total', 0)}` transactions")
            bn = summary.get("bulk_net", 0)
            lines.append(f"Net Value: `₹{abs(bn)/10000000:.2f}Cr` {'🟢' if bn > 0 else '🔴' if bn < 0 else '⚪'}")
            lines.append(f"Buys: `{summary.get('bulk_buy_value', 0)/10000000:.2f}Cr` | Sells: `{summary.get('bulk_sell_value', 0)/10000000:.2f}Cr`")
            lines.append("")
            lines.append(f"*Block Deals (1M)*")
            lines.append(f"Total: `{summary.get('block_total', 0)}` transactions")
            bln = summary.get("block_net", 0)
            lines.append(f"Net Value: `₹{abs(bln)/10000000:.2f}Cr` {'🟢' if bln > 0 else '🔴' if bln < 0 else '⚪'}")
            lines.append("")
            if isinstance(bulk, list) and bulk:
                lines.append(f"*Latest Bulk Deals (Top 5)*")
                for d in bulk[:5]:
                    direction = d.get("Buy/Sell", "")
                    emoji = "🟢" if direction.upper() == "BUY" else "🔴"
                    lines.append(f"{emoji} {d.get('Symbol','')} — {direction} `{d.get('QuantityTraded', 0):,.0f}` @ `₹{float(d.get('TradePrice/Wght.Avg.Price', 0)):.2f}`")
            return await telegram_notifier.send_message("\n".join(lines))
        except Exception as e:
            return await telegram_notifier.send_message(f"❌ Insider error: {e}")

    if text == '/sectors':
        try:
            view = await sector_rotation_service.get_full_rotation_view("1W")
            sectors = view.get("sectors", [])
            lines = ["🔄 *Sector Rotation — 1 Week Performance*", ""]
            for s in sectors:
                pct = s.get("change_pct", 0)
                emoji = "🟢" if pct > 0 else "🔴" if pct < 0 else "⚪"
                bar = "▰" * max(1, min(10, int(abs(pct) * 2))) + "▱" * max(0, 10 - max(1, min(10, int(abs(pct) * 2))))
                lines.append(f"{emoji} `#{s.get('rank', '-')}` *{s['sector']}*: `{pct:+.2f}%`")
                lines.append(f"   `{bar}`")
            lines.append(f"\n📊 Total: `{view.get('total_sectors', 0)}` sectors, `{view.get('total_stocks', 0)}` stocks")
            return await telegram_notifier.send_message("\n".join(lines))
        except Exception as e:
            return await telegram_notifier.send_message(f"❌ Sector error: {e}")

    if text == '/politicians':
        try:
            dash = await politician_trades_service.get_politician_dashboard("6M")
            groups = dash.get("groups", {})
            active = {k: v for k, v in groups.items() if isinstance(v, dict) and v.get("count", 0) > 0}
            lines = ["🏛 *Congressional Trading — Group Flows*", ""]
            for name, g in sorted(active.items(), key=lambda x: abs(x[1].get("net", 0)), reverse=True)[:10]:
                net = g.get("net", 0)
                emoji = "🟢" if net > 0 else "🔴" if net < 0 else "⚪"
                lines.append(f"{emoji} *{name}*: `₹{abs(net)/10000000:.2f}Cr` Net")
                lines.append(f"   ├ Buys: `{g.get('buy_count', 0)}` | Sells: `{g.get('sell_count', 0)}` | Total: `{g.get('count', 0)}`")
                lines.append(f"   └ Stocks: `{', '.join(g.get('symbols_found', [])[:5])}`")
            lines.append("")
            fii = dash.get("fiidii", {}).get("current", {})
            fn = fii.get("fii_net", 0)
            dn = fii.get("dii_net", 0)
            lines.append(f"*FII/DII Flow*")
            lines.append(f"FII: `₹{fn:+,.2f}Cr` {'🟢' if fn > 0 else '🔴'} | DII: `₹{dn:+,.2f}Cr` {'🟢' if dn > 0 else '🔴'}")
            return await telegram_notifier.send_message("\n".join(lines))
        except Exception as e:
            return await telegram_notifier.send_message(f"❌ Politician error: {e}")

    if text == 'stocks':
        from app.data.stocks import INDIAN_STOCKS
        groups = {}
        for s in sorted(INDIAN_STOCKS):
            c = s[0].upper()
            groups.setdefault(c, []).append(s.upper())
        msg = "📊 *Monitored Stocks — Nifty 100*\n\n"
        for letter in sorted(groups):
            stocks = groups[letter]
            msg += f"`{letter}:` {' '.join(stocks)}\n"
        msg += f"\n📈 *{len(INDIAN_STOCKS)}* Indian stocks tracked"
        return await telegram_notifier.send_message(msg)

    if text in ('/live', 'live') or re.match(r'^/live\s+\w+$', text):
        from app.services.market_feed import get_live_price, get_all_live_prices, _ws_connected
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            sym = parts[1].upper()
            data = get_live_price(sym)
            if not data:
                return await telegram_notifier.send_message(f"❌ No live data for `{sym}`")
            chg = data.get("ltp", 0) - data.get("day_open", 0)
            pct = (chg / data.get("day_open", 1)) * 100 if data.get("day_open", 0) else 0
            await telegram_notifier.send_message(
                f"📡 *{sym}* — Live\n"
                f"LTP: `₹{data['ltp']:,.2f}`\n"
                f"Change: `₹{chg:+,.2f}` (`{pct:+.2f}%`)\n"
                f"Day: H `{data.get('day_high', 0):,.2f}` L `{data.get('day_low', 0):,.2f}`\n"
                f"Volume: `{data.get('volume', 0):,}`"
            )
            return
        prices = get_all_live_prices()
        top = sorted(prices.items(), key=lambda x: x[1].get("volume", 0), reverse=True)[:10]
        msg = f"📡 *Live Market Feed* — {len(prices)} stocks\n\n"
        for sym, d in top:
            chg = d.get("ltp", 0) - d.get("day_open", 0)
            msg += f"`{sym}` ₹{d['ltp']:,.2f} {chg:+.2f} Vol:{d.get('volume',0):,}\n"
        msg += "\nUse `/live <SYMBOL>` for a single stock"
        return await telegram_notifier.send_message(msg)

    if text in ('/gainers', 'gainers'):
        from app.services.live_analysis import get_gainers_losers
        gainers, _ = get_gainers_losers(10)
        if not gainers:
            return await telegram_notifier.send_message("No live data yet.")
        msg = "🟢 *Top Gainers (Live)*\n\n"
        for s in gainers:
            msg += f"`{s['symbol']}` ₹{s['ltp']:,.2f} *+{s['change_pct']}%* (₹+{s['change']:,.2f}) Vol:{s['volume']:,}\n"
        return await telegram_notifier.send_message(msg)

    if text in ('/losers', 'losers'):
        from app.services.live_analysis import get_gainers_losers
        _, losers = get_gainers_losers(10)
        if not losers:
            return await telegram_notifier.send_message("No live data yet.")
        msg = "🔴 *Top Losers (Live)*\n\n"
        for s in losers:
            msg += f"`{s['symbol']}` ₹{s['ltp']:,.2f} *{s['change_pct']}%* (₹{s['change']:,.2f}) Vol:{s['volume']:,}\n"
        return await telegram_notifier.send_message(msg)

    if text in ('/breadth', 'breadth'):
        from app.services.live_analysis import get_live_breadth
        b = get_live_breadth()
        if b["total"] == 0:
            return await telegram_notifier.send_message("No live data yet.")
        msg = "📊 *Live Market Breadth*\n\n"
        msg += f"Above Day Open: 🟢 `{b['above']}`\n"
        msg += f"Below Day Open: 🔴 `{b['below']}`\n"
        msg += f"Flat: `{b['flat']}`\n"
        msg += f"Total: `{b['total']}`\n\n"
        if b["stocks_above"]:
            msg += "*Top Gainers:*\n"
            for sym, pct in b["stocks_above"][:5]:
                msg += f"🟢 `{sym}` +{pct}%\n"
        if b["stocks_below"]:
            msg += "\n*Top Losers:*\n"
            for sym, pct in b["stocks_below"][:5]:
                msg += f"🔴 `{sym}` {pct}%\n"
        return await telegram_notifier.send_message(msg)

    if text == '/markets':
        msg = "📊 *Market Overview — All Features*\n\n"
        msg += "Use these commands for detailed analysis:\n"
        msg += "• `/agent <symbol>` — AI multi-modal analysis\n"
        msg += "• `/options <symbol>` — Option chain with PCR & max pain\n"
        msg += "• `/insider` — Bulk & block deals summary\n"
        msg += "• `/sectors` — Sector rotation performance\n"
        msg += "• `/politicians` — Group political trades\n"
        msg += "• `/strategies` — Strategy marketplace\n"
        msg += "• `/backtest <id>` — Backtest a strategy\n"
        msg +=         "• `/scalp` — SCALP signals: EMA 200 bounce on 1min chart\n"
        "• `/scalpbt` — Backtest SCALP strategy on 6mo daily data\n"
        "• `/scalpon` / `/scalpoff` — Toggle SCALP signals\n"
        "• `/dhan` — DhanHQ dashboard\n"
        "• `/dhanon` / `/dhanoff` — Toggle DhanHQ auto-trading\n"
        "• `/buy <sym> <qty>` — Place BUY order\n"
        "• `/sell <sym> <qty>` — Place SELL order\n"
        "• `/live <sym>` — Live price via WebSocket feed\n\n"
        msg += "Or use any of these quick ones:\n"
        msg += "`/scalp` / `/scalpbt` / `/scalpon` / `stocks` / `fiidii` / `edges` / `breadth` / `sentiment` / `summary`"
        return await telegram_notifier.send_message(msg)

    if text in ('/dhan', 'dhan'):
        if not _dhan.dhan_enabled:
            return await telegram_notifier.send_message("⚙️ DhanHQ is disabled. Use `/dhanon` to enable.")
        dash = await get_dashboard()
        p = dash.get("profile", {}) or {}
        f = dash.get("funds", {}) or {}
        p_err = p.get("error") if isinstance(p, dict) else None
        f_err = f.get("error") if isinstance(f, dict) else None
        msg = f"🏦 *DhanHQ Dashboard*\n\n"
        msg += f"*Account:*\n"
        msg += f"Client ID: `{p.get('dhanClientId', '--')}`\n"
        msg += f"Active: `{p.get('activeSegment', '--')}`\n"
        msg += f"DDPI: `{p.get('ddpi', '--')}`\n"
        dp = p.get('dataPlan', '--')
        msg += f"Data Plan: `{dp}`\n"
        if p_err and p_err != "TOKEN_EXPIRED":
            msg += f"⚠️ Profile error: `{p_err}`\n"
        msg += f"\n*Funds:*\n"
        msg += f"Available: `₹{f.get('availabelBalance', 0):,.2f}`\n"
        msg += f"Used: `₹{f.get('utilizedAmount', 0):,.2f}`\n"
        msg += f"Withdrawable: `₹{f.get('withdrawableBalance', 0):,.2f}`\n"
        if f_err:
            msg += f"⚠️ Funds error: `{f_err}`\n"
        return await telegram_notifier.send_message(msg)

    if text in ('/dhanon', 'dhanon'):
        _dhan.dhan_enabled = True
        return await telegram_notifier.send_message("✅ DhanHQ auto-trading enabled. Signals can now place live orders.")

    if text in ('/dhanoff', 'dhanoff'):
        _dhan.dhan_enabled = False
        return await telegram_notifier.send_message("❌ DhanHQ auto-trading disabled.")

    m = re.match(r'^/(buy|sell)\s+(\w+)\s+(\d+)$', text)
    if m:
        if not _dhan.dhan_enabled:
            return await telegram_notifier.send_message("⚙️ DhanHQ is disabled. Use `/dhanon` to enable.")
        action = m.group(1).upper()
        symbol = m.group(2).upper()
        qty = int(m.group(3))
        status_msg = await telegram_notifier.send_message(f"⏳ Placing {action} order for {symbol} x{qty}...")
        ttype = "BUY" if action == "BUY" else "SELL"
        result = await place_order(symbol, qty, ttype)
        if result and "error" not in result:
            oid = result.get("orderId", "--")
            ost = result.get("orderStatus", "--")
            await telegram_notifier.send_message(
                f"✅ *Order Placed*\n{action} `{symbol}` x{qty}\nID: `{oid}`\nStatus: `{ost}`"
            )
        else:
            err = result.get("error", "Unknown") if result else "No response"
            detail = result.get("detail", "") if result else ""
            msg = f"❌ Order failed: {err}"
            if detail:
                msg += f"\n`{detail[:500]}`"
            await telegram_notifier.send_message(msg)
        return

    # Unrecognized command — silent ignore
    return


async def _auto_scalp_scan():
    """Background scanner: runs EMA200 bounce scan every 5 min during market hours."""
    while True:
        try:
            if not _ebs.scalp_enabled:
                await asyncio.sleep(300)
                continue
            now = datetime.now()
            utc_h = now.hour
            is_market_open = 3 <= utc_h <= 10
            if is_market_open:
                signals = await get_recent_bounces(min_strength=0.3)
                buys = [s for s in signals if s['direction'] == 'BUY']
                sells = [s for s in signals if s['direction'] == 'SELL']
                if buys or sells:
                    msg = f"⚡ *SCALP Alert*\n🟢 SCALP BUY: `{len(buys)}`  🔴 SCALP SELL: `{len(sells)}`\n\n"
                    for s in (buys + sells)[:5]:
                        emoji = "🟢" if s['direction'] == 'BUY' else "🔴"
                        msg += f"{emoji} `{s['symbol']}` ₹{s['price']} EMA{s['ema200']} Str{s['strength']}\n"
                    msg += "\n💡 `/scalp` for full list"
                    await telegram_notifier.send_message(msg)
                    # Auto-trade top signal if Dhan enabled
                    if _dhan.dhan_enabled and signals:
                        top = signals[0]
                        qty = 10  # default qty
                        ttype = "BUY" if top['direction'] == 'BUY' else "SELL"
                        await telegram_notifier.send_message(
                            f"🤖 Auto-trading: {ttype} {top['symbol']} x{qty} via DhanHQ..."
                        )
                        await place_order(top['symbol'], qty, ttype)
        except Exception as e:
            print(f"Auto scalp error: {e}")
        await asyncio.sleep(300)


async def telegram_poll_loop():
    offset = 0
    check_counter = 0
    asyncio.create_task(_auto_scalp_scan())
    asyncio.create_task(_dhan.auto_renew_loop())

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
