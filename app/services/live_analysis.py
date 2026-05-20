import asyncio
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from app.services.market_feed import get_all_live_prices, get_live_price
from app.services.telegram_notifier import telegram_notifier

# --- Volume spike tracking ---
_prev_volumes: Dict[str, int] = {}
_volume_alerts_bucket: Dict[str, float] = {}  # symbol -> last alert time


def get_gainers_losers(n: int = 10) -> Tuple[List[Dict], List[Dict]]:
    prices = get_all_live_prices()
    with_pct = []
    for sym, d in prices.items():
        op = d.get("day_open", 0)
        ltp = d.get("ltp", 0)
        if op and ltp:
            pct = (ltp - op) / op * 100
            with_pct.append({"symbol": sym, "ltp": ltp, "change_pct": round(pct, 2), "change": round(ltp - op, 2), "volume": d.get("volume", 0)})
    sorted_by_pct = sorted(with_pct, key=lambda x: x["change_pct"], reverse=True)
    return sorted_by_pct[:n], list(reversed(sorted_by_pct[-n:]))


async def volume_spike_loop():
    """Background loop: detect volume spikes and send alerts every 2 min."""
    while True:
        try:
            await asyncio.sleep(120)
            prices = get_all_live_prices()
            spikes = []
            for sym, d in prices.items():
                vol = d.get("volume", 0)
                prev = _prev_volumes.get(sym, 0)
                if prev > 10000 and vol > prev * 2.5:
                    ltp = d.get("ltp", 0)
                    chg_pct = ((ltp - d.get("day_open", 0)) / d.get("day_open", 1)) * 100
                    last_alert = _volume_alerts_bucket.get(sym, 0)
                    if time.time() - last_alert > 600:  # max 1 alert per 10 min per symbol
                        spikes.append({"symbol": sym, "volume": vol, "prev_volume": prev, "ltp": ltp, "change_pct": round(chg_pct, 2)})
                        _volume_alerts_bucket[sym] = time.time()
                _prev_volumes[sym] = vol
            if spikes:
                msg = "🚨 *Volume Spikes Detected*\n\n"
                for s in spikes[:10]:
                    direction = "🟢" if s["change_pct"] >= 0 else "🔴"
                    msg += f"{direction} `{s['symbol']}` Vol:{s['volume']:,} ({s['volume']/max(s['prev_volume'],1):.1f}x) LTP:₹{s['ltp']:,.2f} ({s['change_pct']:+.2f}%)\n"
                await telegram_notifier.send_message(msg)
        except Exception as e:
            print(f"Volume spike error: {e}")


def get_live_breadth() -> Dict:
    """Real-time breadth: stocks above/below day open."""
    prices = get_all_live_prices()
    above, below, flat = 0, 0, 0
    stocks_above, stocks_below = [], []
    for sym, d in prices.items():
        ltp = d.get("ltp", 0)
        op = d.get("day_open", 0)
        if op == 0:
            continue
        pct = (ltp - op) / op * 100
        if pct > 0:
            above += 1
            stocks_above.append((sym, round(pct, 2)))
        elif pct < 0:
            below += 1
            stocks_below.append((sym, round(pct, 2)))
        else:
            flat += 1
    return {
        "above": above,
        "below": below,
        "flat": flat,
        "total": above + below + flat,
        "stocks_above": sorted(stocks_above, key=lambda x: -x[1])[:10],
        "stocks_below": sorted(stocks_below, key=lambda x: x[1])[:10],
    }


def get_live_edge(symbol: str) -> Optional[Dict]:
    """Compute a simple edge score from live data."""
    d = get_live_price(symbol)
    if not d:
        return None
    ltp = d.get("ltp", 0)
    op = d.get("day_open", 0)
    hi = d.get("day_high", 0)
    lo = d.get("day_low", 0)
    vol = d.get("volume", 0)
    buy_qty = d.get("total_buy_qty", 0)
    sell_qty = d.get("total_sell_qty", 0)

    if not all([ltp, op, hi, lo]):
        return None

    range_val = hi - lo or 1
    pos_in_range = (ltp - lo) / range_val  # 0=low, 1=high
    day_pct = (ltp - op) / op * 100

    # Edge score: weighted combination
    buy_pressure = (buy_qty - sell_qty) / max(buy_qty + sell_qty, 1) if (buy_qty + sell_qty) > 0 else 0
    edge = round(pos_in_range * 40 + (1 if day_pct > 0 else -1) * 20 + buy_pressure * 40, 1)

    return {
        "symbol": symbol,
        "ltp": ltp,
        "day_pct": round(day_pct, 2),
        "range_position": round(pos_in_range * 100, 1),
        "buy_pressure": round(buy_pressure * 100, 1),
        "edge": edge,
    }


def get_live_edges() -> List[Dict]:
    """Edge scores for all live symbols sorted by score."""
    results = []
    for sym in get_all_live_prices():
        e = get_live_edge(sym)
        if e:
            results.append(e)
    return sorted(results, key=lambda x: -x["edge"])
