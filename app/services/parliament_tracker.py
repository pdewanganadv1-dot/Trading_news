import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from app.services.parliament_news_service import parliament_news_service
from app.services.telegram_notifier import telegram_notifier
from app.services.dhanhq_service import place_order, get_fund_limit, dhan_enabled
from app.services.market_data_service import market_data_service

_POLL_INTERVAL = 900
_STRONG_SURGE_PCT = 3.0
_MODERATE_SURGE_PCT = 1.5
_MIN_VOLUME_MULTIPLIER = 1.5
_MIN_PRESIGNAL_SCORE = 4
_already_alerted: set = set()
_pending_buy_approval: Dict[str, Dict] = {}
_cached_price: Dict[str, Dict] = {}
_cached_history: Dict[str, List[float]] = {}
_last_scan_time: float = 0


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


async def _get_history(symbol: str, days: int = 10) -> List[float]:
    try:
        prices = await market_data_service.get_historical_prices(symbol, days=days)
        if prices and len(prices) >= 3:
            _cached_history[symbol] = prices
            return prices
    except Exception:
        pass
    return _cached_history.get(symbol, [])


def _calc_surge_score(
    price_data: Optional[Dict],
    history: List[float],
    article_count: int,
) -> Tuple[Optional[str], int, str]:
    if not price_data:
        return None, 0, "no price data"

    change_pct = price_data.get("change", 0)
    volume = price_data.get("volume", 0)
    price = price_data.get("price", 0)

    avg_volume = _get_avg_volume(history, price)
    volume_spike = (volume / max(avg_volume, 1)) if avg_volume > 0 else 1.0

    multi_day_momentum = _calc_momentum(history, price)

    score = 0
    reasons = []

    if change_pct >= _STRONG_SURGE_PCT:
        score += 4
        reasons.append(f"surge +{change_pct:.1f}%")
    elif change_pct >= _MODERATE_SURGE_PCT:
        score += 2
        reasons.append(f"up +{change_pct:.1f}%")
    elif change_pct <= -_STRONG_SURGE_PCT:
        score += 3
        reasons.append(f"dump {change_pct:.1f}%")
    elif change_pct <= -_MODERATE_SURGE_PCT:
        score += 1
        reasons.append(f"down {change_pct:.1f}%")
    else:
        reasons.append(f"flat {change_pct:+.1f}%")

    if volume_spike >= 2.0:
        score += 2
        reasons.append(f"volume {volume_spike:.1f}x avg")
    elif volume_spike >= _MIN_VOLUME_MULTIPLIER:
        score += 1
        reasons.append(f"volume {volume_spike:.1f}x avg")

    if multi_day_momentum > 5:
        score += 2
        reasons.append("multi-day uptrend")
    elif multi_day_momentum < -5:
        score += 1
        reasons.append("multi-day downtrend")

    if article_count >= 5:
        score += 2
        reasons.append(f"{article_count} articles")
    elif article_count >= 2:
        score += 1
        reasons.append(f"{article_count} articles")

    direction = None
    if change_pct >= _MODERATE_SURGE_PCT and score >= _MIN_PRESIGNAL_SCORE:
        direction = "BUY"
    elif change_pct <= -_MODERATE_SURGE_PCT and score >= _MIN_PRESIGNAL_SCORE:
        direction = "SELL"
    elif score >= _MIN_PRESIGNAL_SCORE and multi_day_momentum > 3:
        direction = "BUY"

    detail = f"score {score}/10 | {' | '.join(reasons)}"
    return direction, score, detail


def _get_avg_volume(history: List[float], current_price: float) -> float:
    if not history:
        return 0
    return sum(history) / len(history) * 1000 if current_price else 0


def _calc_momentum(history: List[float], current_price: float) -> float:
    if len(history) < 3:
        return 0
    p1 = history[-1] if history[-1] != 0 else current_price
    p5 = history[-min(5, len(history))]
    p10 = history[0]
    return ((current_price - p5) / max(p5, 1)) * 100


def _format_presignal_msg(
    symbol: str,
    direction: str,
    score: int,
    detail: str,
    price_data: Optional[Dict],
    articles: List[Dict],
) -> str:
    emoji = "🟢" if direction == "BUY" else "🔴" if direction == "SELL" else "⚪"
    lines = [
        f"{emoji} *PRESIGNAL: {direction} {symbol.upper()}*",
        f"━━━━━━━━━━━━━━━━━━━",
    ]
    if price_data:
        p = price_data.get("price", 0)
        chg = price_data.get("change", 0)
        vol = price_data.get("volume", 0)
        lines.append(f"💰 LTP: `₹{p:,.2f}` ({chg:+.2f}%)")
        lines.append(f"📊 Volume: `{vol:,.0f}`")
    lines.append(f"🎯 Strength: `{score}/10`")
    lines.append(f"📋 {detail}")
    if articles:
        art = articles[0]
        lines.append(f"━━━━━━━━━━━━━━━━━━━")
        lines.append(f"🏛 {art.get('title', '')[:120]}")
        if art.get("url"):
            lines.append(f"🔗 {art['url']}")
        if len(articles) > 1:
            lines.append(f"📰 +{len(articles)-1} more articles")
    if direction == "BUY" and _is_market_hours() and dhan_enabled:
        lines.append(f"━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💡 `/parliament_buy {symbol.upper()} <qty>` to place BUY")
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
    now_ts = datetime.now().timestamp()

    await parliament_news_service.get_parliament_news(force=True)
    unusual = await parliament_news_service.get_unusual_mentions()

    all_signals = []
    for item in unusual[:15]:
        sym = item["symbol"].lower()
        price_data = await _get_price(sym)
        history = await _get_history(sym, days=10)
        direction, score, detail = _calc_surge_score(
            price_data, history, item["article_count"]
        )
        all_signals.append({
            "symbol": sym.upper(),
            "direction": direction,
            "score": score,
            "detail": detail,
            "price_data": price_data,
            "articles": item["articles"],
            "article_count": item["article_count"],
            "is_new": item["is_new"],
        })

    strong_signals = [s for s in all_signals if s["direction"] and s["score"] >= _MIN_PRESIGNAL_SCORE]

    if strong_signals:
        strong_signals.sort(key=lambda x: x["score"], reverse=True)
        for sig in strong_signals[:5]:
            alert_key = f"{sig['symbol']}_{sig['direction']}"
            if alert_key not in _already_alerted:
                msg = _format_presignal_msg(
                    sig["symbol"], sig["direction"], sig["score"],
                    sig["detail"], sig["price_data"], sig["articles"],
                )
                await telegram_notifier.send_message(msg)
                _already_alerted.add(alert_key)
                parliament_news_service.mark_alerted(sig["symbol"].lower())
                await asyncio.sleep(1)

        summary = (
            f"🏛 *Parliament Presignals — {len(strong_signals)} actionable*\n"
            + "\n".join(
                f"{'🟢' if s['direction']=='BUY' else '🔴'} `{s['symbol']}` "
                f"score {s['score']}/10 | {s['detail'][:60]}"
                for s in strong_signals[:10]
            )
        )
        await telegram_notifier.send_message(summary)

    _last_scan_time = now_ts


async def get_parliament_snapshot() -> Dict:
    unusual = await parliament_news_service.get_unusual_mentions()
    all_mentions = await parliament_news_service.get_stock_mentions()
    all_signals = []
    for item in unusual[:15]:
        sym = item["symbol"].lower()
        price_data = _cached_price.get(sym) or await _get_price(sym)
        history = _cached_history.get(sym, [])
        direction, score, detail = _calc_surge_score(
            price_data, history, item["article_count"]
        )
        all_signals.append({
            "symbol": sym.upper(),
            "direction": direction,
            "score": score,
            "detail": detail,
            "article_count": item["article_count"],
            "is_new": item["is_new"],
        })
    return {
        "total_mentions": len(all_mentions),
        "active_presignals": len([s for s in all_signals if s["direction"]]),
        "signals": sorted(all_signals, key=lambda x: x["score"], reverse=True)[:20],
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
