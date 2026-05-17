import asyncio
from datetime import datetime, timedelta
import pytz
from app.services.telegram_notifier import telegram_notifier
from app.services.telegram_bot import _fetch_dashboard_data, _build_summary
from app.services.accuracy_tracker import get_accuracy_stats


IST = pytz.timezone('Asia/Kolkata')


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


async def daily_report_loop():
    while True:
        now = datetime.now(IST)
        target = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        await send_daily_report()
