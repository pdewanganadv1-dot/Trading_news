import asyncio
from datetime import datetime
from typing import Dict, List, Optional
from app.services.parliament_news_service import parliament_news_service
from app.services.telegram_notifier import telegram_notifier
from app.services.dhanhq_service import place_order, get_fund_limit, dhan_enabled
from app.services.market_data_service import market_data_service

_POLL_INTERVAL = 900
_PARLIAMENT_START = 11.0
_PARLIAMENT_END = 18.0
_already_alerted: set = set()
_pending_buy_approval: Dict[str, Dict] = {}
_cached_price: Dict[str, Dict] = {}
_last_scan_time: float = 0


def _is_parliament_hours() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    utc_h = now.hour + now.minute / 60.0
    ist_h = utc_h + 5.5
    return _PARLIAMENT_START <= ist_h < _PARLIAMENT_END


def _is_market_hours() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    utc_h = now.hour + now.minute / 60.0
    ist_h = utc_h + 5.5
    return 9.25 <= ist_h < 15.5


async def _get_price(symbol: str) -> Optional[Dict]:
    try:
        data = await market_data_service.get_price_data(symbol)
        if data:
            _cached_price[symbol] = data
            return data
    except Exception:
        pass
    return _cached_price.get(symbol)


def _format_signal_msg(symbol: str, articles: List[Dict], price_data: Optional[Dict]) -> str:
    lines = [f"📜 *{symbol.upper()} — Parliament Stock Alert*"]
    if price_data:
        p = price_data.get("price", 0)
        chg = price_data.get("change_pct", 0)
        lines.append(f"💰 LTP: `₹{p:,.2f}` ({chg:+.2f}%)")
    art = articles[0] if articles else {}
    lines.append(f"📰 {art.get('title', '')[:120]}")
    if art.get("url"):
        lines.append(f"🔗 {art['url']}")
    lines.append(f"🏛 {len(articles)} articles in parliament news")
    buy_hint = ""
    if _is_market_hours() and dhan_enabled:
        buy_hint = "\n💡 Type `/parliament_buy {symbol.upper()} <qty>` to place BUY"
    lines.append(buy_hint)
    return "\n".join(lines)


async def parliament_scanner_loop():
    await asyncio.sleep(60)
    while True:
        try:
            await _scan_cycle()
        except Exception as e:
            print(f"[ParliamentTracker] Scan error: {e}")
        await asyncio.sleep(_POLL_INTERVAL)


async def _scan_cycle():
    global _last_scan_time
    now = datetime.now().timestamp()
    await parliament_news_service.get_parliament_news(force=True)
    unusual = await parliament_news_service.get_unusual_mentions()
    new_alerts = [u for u in unusual if u["is_new"] and u["symbol"] not in _already_alerted]
    if not new_alerts:
        _last_scan_time = now
        return

    for item in new_alerts[:5]:
        sym = item["symbol"].lower()
        price_data = await _get_price(sym)
        msg = _format_signal_msg(sym, item["articles"][:3], price_data)
        await telegram_notifier.send_message(msg)
        _already_alerted.add(sym)
        parliament_news_service.mark_alerted(sym)
        await asyncio.sleep(2)

    summary_msg = (
        f"🏛 *Parliament Scanner — {len(new_alerts)} new stock mentions*\n"
        + "\n".join(
            f"• `{u['symbol']}` — {u['article_count']} articles"
            for u in new_alerts[:10]
        )
    )
    await telegram_notifier.send_message(summary_msg)
    _last_scan_time = now


async def get_parliament_snapshot() -> Dict:
    unusual = await parliament_news_service.get_unusual_mentions()
    all_mentions = await parliament_news_service.get_stock_mentions()
    total_symbols = len(all_mentions)
    active_alerts = [u for u in unusual if u["is_new"] or u["cooldown_remaining_sec"] > 0]
    return {
        "total_mentions": total_symbols,
        "active_unusual": len(active_alerts),
        "top_mentions": unusual[:15],
        "last_scan": datetime.fromtimestamp(_last_scan_time).isoformat()
        if _last_scan_time else None,
        "pending_approvals": len(_pending_buy_approval),
    }


async def buy_from_parliament(symbol: str, qty: int) -> Dict:
    if not dhan_enabled:
        return {"error": "DhanHQ is disabled. Use /dhanon to enable."}
    funds = await get_fund_limit()
    available = 0
    if funds and isinstance(funds, dict) and "error" not in funds:
        available = float(funds.get("availabelBalance", 0))
    price_data = await _get_price(symbol)
    est_cost = 0
    if price_data:
        est_cost = price_data.get("price", 0) * qty
    if est_cost > available and available > 0:
        return {"error": f"Insufficient funds. Need ₹{est_cost:,.2f}, have ₹{available:,.2f}"}
    result = await place_order(symbol, qty, "BUY")
    if isinstance(result, dict) and "error" not in result:
        oid = result.get("orderId", "--")
        return {
            "success": True,
            "order_id": oid,
            "symbol": symbol.upper(),
            "qty": qty,
            "message": f"✅ BUY {symbol.upper()} x{qty} placed. ID: {oid}",
        }
    err = result.get("error", "Unknown") if result else "No response"
    return {"error": f"Order failed: {err}"}
