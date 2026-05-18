import asyncio
from datetime import datetime, timedelta
import pytz
from app.services.telegram_notifier import telegram_notifier
from app.services.telegram_bot import _fetch_dashboard_data, _build_summary
from app.services.accuracy_tracker import get_accuracy_stats
from app.services.market_edge_service import get_fii_dii_summary, get_market_breadth, scan_all_stocks

IST = pytz.timezone('Asia/Kolkata')


async def _fetch_us_markets():
    try:
        import yfinance as yf
        sp500 = yf.Ticker("^GSPC")
        nasdaq = yf.Ticker("^IXIC")
        dow = yf.Ticker("^DJI")
        lines = []
        for name, ticker in [("S&P 500", sp500), ("NASDAQ", nasdaq), ("Dow Jones", dow)]:
            try:
                data = ticker.history(period="5d")
                if not data.empty:
                    close = data["Close"].iloc[-1]
                    prev = data["Close"].iloc[-2]
                    chg = ((close - prev) / prev) * 100
                    emoji = "🟢" if chg > 0 else "🔴"
                    lines.append(f"{emoji} *{name}:* `{close:,.0f}` ({chg:+.2f}%)")
            except Exception:
                pass
        return "\n".join(lines) if lines else None
    except Exception as e:
        print(f"US markets fetch error: {e}")
        return None


async def _fetch_asian_markets():
    try:
        import yfinance as yf
        tickers = {"^N225": "Nikkei 225", "^HSI": "Hang Seng", "000300.SS": "Shanghai Comp"}
        lines = []
        for ticker, name in tickers.items():
            try:
                data = yf.Ticker(ticker).history(period="5d")
                if not data.empty:
                    close = data["Close"].iloc[-1]
                    prev = data["Close"].iloc[-2]
                    chg = ((close - prev) / prev) * 100
                    emoji = "🟢" if chg > 0 else "🔴"
                    lines.append(f"{emoji} *{name}:* `{close:,.0f}` ({chg:+.2f}%)")
            except Exception:
                pass
        return "\n".join(lines) if lines else None
    except Exception as e:
        print(f"Asian markets fetch error: {e}")
        return None


async def _fetch_top_gainers_losers(top_n: int = 5):
    try:
        results = await scan_all_stocks()
        if not results:
            return None, None
        sorted_by_change = sorted(results, key=lambda x: x.get("change_pct", 0), reverse=True)
        gainers = sorted_by_change[:top_n]
        losers = sorted_by_change[-top_n:]
        losers.reverse()
        return gainers, losers
    except Exception as e:
        print(f"Top gainers/losers error: {e}")
        return None, None


async def _signal_summary_section():
    lines = ["📈 *Signal Summary*", ""]
    try:
        from app.services.signal_monitor import signal_log
        recent = list(signal_log)
        buy_count = sum(1 for v in recent if v.get("signal") == "BUY")
        sell_count = sum(1 for v in recent if v.get("signal") == "SELL")
        hold_count = sum(1 for v in recent if v.get("signal") == "HOLD")
        total = len(recent)
        if total:
            lines.append(f"🟢 BUY: `{buy_count}`  🔴 SELL: `{sell_count}`  ⚪ HOLD: `{hold_count}`")
            lines.append(f"Recent signals: `{total}`")
        else:
            lines.append("Signal data unavailable")
    except Exception:
        lines.append("Signal data unavailable")
    return "\n".join(lines)


async def send_premarket_brief():
    lines = ["🌅 *Pre-Market Brief*", f"`{datetime.now(IST).strftime('%d %b %Y, %I:%M %p IST')}`", ""]
    lines.append("📌 *Today's Outlook*")
    try:
        us = await _fetch_us_markets()
        if us:
            lines.append("")
            lines.append(us)
    except Exception:
        pass
    try:
        asian = await _fetch_asian_markets()
        if asian:
            lines.append("")
            lines.append(asian)
    except Exception:
        pass
    try:
        d = await get_fii_dii_summary()
        fii_net = d.get("fii_net")
        if fii_net is not None:
            emoji = "🟢" if fii_net > 0 else "🔴"
            lines.append("")
            lines.append(f"🏦 {emoji} FII Net: `{fii_net:+,.2f}` Cr")
    except Exception:
        pass
    lines.append("")
    acc = get_accuracy_stats()
    lines.append(f"📊 Overall Accuracy: *{acc['win_rate']}%* (`{acc['wins']}W/{acc['losses']}L`)")
    lines.append("")
    lines.append("💡 *Key levels:* Nifty support 22500 | resistance 23000")
    lines.append("⚡ *Trading starts at 9:15am IST*")
    await telegram_notifier.send_message("\n".join(lines))


async def send_postmarket_brief():
    lines = ["🌇 *Post-Market Wrap*", f"`{datetime.now(IST).strftime('%d %b %Y, %I:%M %p IST')}`", ""]
    try:
        gainers, losers = await _fetch_top_gainers_losers(5)
        if gainers:
            lines.append("📈 *Top Gainers*")
            for g in gainers:
                lines.append(f"  🟢 `{g['symbol']:<12}` {g.get('change_pct', 0):+.2f}% | Score: {g['score']}/10")
            lines.append("")
        if losers:
            lines.append("📉 *Top Losers*")
            for l in losers:
                lines.append(f"  🔴 `{l['symbol']:<12}` {l.get('change_pct', 0):+.2f}% | Score: {l['score']}/10")
            lines.append("")
    except Exception:
        pass
    try:
        d = await get_fii_dii_summary()
        fii_net = d.get("fii_net")
        dii_net = d.get("dii_net")
        if fii_net is not None:
            lines.append("🏦 *FII/DII Flow Today*")
            emoji = "🟢" if fii_net > 0 else "🔴"
            lines.append(f"{emoji} FII Net: `{fii_net:+,.2f}` Cr")
            if dii_net is not None:
                emoji2 = "🟢" if dii_net > 0 else "🔴"
                lines.append(f"{emoji2} DII Net: `{dii_net:+,.2f}` Cr")
            lines.append("")
    except Exception:
        pass
    sig_summary = await _signal_summary_section()
    lines.append(sig_summary)
    lines.append("")
    acc = get_accuracy_stats()
    lines.append(f"📊 *Signal Accuracy*")
    lines.append(f"  Win Rate: *{acc['win_rate']}%* (`{acc['wins']}W/{acc['losses']}L`)")
    lines.append(f"  Total: `{acc['total_signals']}` | Avg PnL: `{acc['avg_pnl']}%`")
    lines.append("")
    lines.append("💡 *Type /stocks for live signals or /help for all commands*")
    await telegram_notifier.send_message("\n".join(lines))


async def send_daily_report():
    prices, signals, news_count, sentiment, social_verdict = await _fetch_dashboard_data()
    summary = _build_summary(prices, signals, news_count, sentiment)
    accuracy = get_accuracy_stats()
    acc_line = (
        f"\n📈 *Signal Accuracy:* {accuracy['win_rate']}% win rate "
        f"({accuracy['wins']}W / {accuracy['losses']}L)"
    )
    msg = f"🌅 *Good Morning! — Daily Report*\n{summary}{acc_line}"
    await telegram_notifier.send_message(msg)


async def _scheduled_loop(schedule_hour: int, schedule_min: int, callback, label: str):
    while True:
        now = datetime.now(IST)
        target = now.replace(hour=schedule_hour, minute=schedule_min, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        try:
            await callback()
            print(f"[{label}] Sent at {datetime.now(IST).strftime('%H:%M:%S')} IST")
        except Exception as e:
            print(f"[{label}] Error: {e}")


async def daily_report_loop():
    await asyncio.gather(
        _scheduled_loop(8, 0, send_daily_report, "Daily Report (8am)"),
        _scheduled_loop(9, 0, send_premarket_brief, "Pre-Market Brief (9am)"),
        _scheduled_loop(15, 45, send_postmarket_brief, "Post-Market Wrap (3:45pm)"),
    )