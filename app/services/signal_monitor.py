import asyncio
from datetime import datetime
from typing import List, Dict
from app.config import settings
from app.services.market_data_service import market_data_service, TradingSignals
from app.services.telegram_notifier import telegram_notifier
from app.data.stocks import INDIAN_STOCKS, MONITORED_SYMBOLS
from app.services.accuracy_tracker import (
    record_signal, resolve_signals,
    save_signal_cache, save_realtime_cache, save_sent_signal,
    load_signal_cache, load_realtime_cache, load_sent_signals,
)
from app.services.signal_confirmer import confirm_signal
from app.services.signal_explainer import signal_explainer


_INDIAN_STOCKS = INDIAN_STOCKS
_MONITORED_SYMBOLS = MONITORED_SYMBOLS

signal_log: List[Dict] = []
_signal_cache: Dict[str, Dict] = load_signal_cache()
_realtime_cache: Dict[str, Dict] = load_realtime_cache()
_last_sent: Dict[str, str] = {}
_CONFIRMED_SENT: Dict[str, str] = load_sent_signals()
def _ist_now_str() -> str:
    """Current time as ISO string in IST."""
    from datetime import timezone, timedelta
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).isoformat()

_cache_start: str = _ist_now_str()
_initial_scan_done: bool = False

async def _process_symbol(symbol: str) -> None:
    """Process a single symbol: fetch data, generate signal, explain, alert."""
    try:
        price_data = await market_data_service.get_price_data(symbol)
        if not price_data:
            # Cache as unavailable so it's not permanently PENDING
            _cache_fallback(symbol, None)
            return

        price = price_data['price']

        prices_5m = await market_data_service.get_5min_prices(symbol, 100)
        if not prices_5m or len(prices_5m) < 20:
            # Cache price only (no signal data available — e.g. market closed)
            _cache_realtime_only(symbol, price_data)
            return

        signal_data = TradingSignals.generate_signal(prices_5m, price_data['price'])

        sig = signal_data['signal']
        conf = signal_data['confidence']

        await record_signal(symbol, sig, conf, price, signal_data.get('reasons', []))

        uses_llm = sig in ('BUY', 'SELL') and conf >= 0.5
        if uses_llm:
            explanation = signal_explainer.explain(
                symbol.upper(), sig, conf, signal_data.get('reasons', []),
                signal_data.get('indicators', {}),
                price=price,
            )
        else:
            explanation = signal_explainer._template_explain(
                symbol.upper(), sig, conf, signal_data.get('reasons', []),
                signal_data.get('indicators', {}),
                price=price,
            )

        entry = {
            'symbol': symbol.upper(),
            'signal': sig,
            'confidence': conf,
            'price': price,
            'reasons': signal_data.get('reasons', []),
            'explanation': explanation,
            'timestamp': datetime.now().isoformat(),
            'notified': False,
        }

        should_alert = sig in ('BUY', 'SELL') and conf >= settings.signal_confidence_threshold
        if should_alert:
            confirmed = await confirm_signal(symbol, sig, conf, signal_data.get('reasons', []), price, prices_5m=prices_5m)
            comp_sig = confirmed["signal"]
            comp_conf = confirmed["confidence"]
            reasons = confirmed["reasons"]

            entry["composite_signal"] = comp_sig
            entry["composite_confidence"] = comp_conf
            entry["confirmations"] = confirmed.get("confirmations", [])
            entry["warnings"] = confirmed.get("warnings", [])

            if comp_sig == sig and comp_conf >= settings.signal_confidence_threshold * 0.9:
                last = _CONFIRMED_SENT.get(symbol)
                if last != f"{sig}_{comp_conf}":
                    display_conf = max(conf, comp_conf)
                    ok = await telegram_notifier.send_signal_alert(
                        symbol, sig, display_conf, price, reasons[:3],
                        explanation=explanation,
                    )
                    entry['notified'] = ok
                    if ok:
                        _CONFIRMED_SENT[symbol] = f"{sig}_{comp_conf}"
                        save_sent_signal(symbol, f"{sig}_{comp_conf}")

        cache_entry = {
            "symbol": entry["symbol"],
            "signal": entry["signal"],
            "confidence": entry["confidence"],
            "price": entry["price"],
            "reasons": entry["reasons"],
            "explanation": entry["explanation"],
            "timestamp": entry["timestamp"],
        }
        _signal_cache[symbol] = cache_entry
        save_signal_cache(symbol, cache_entry)

        _cache_realtime_only(symbol, price_data)

        signal_log.insert(0, entry)
        if len(signal_log) > 100:
            signal_log.pop()

    except Exception as e:
        print(f"Signal monitor error for {symbol}: {e}")


def _cache_fallback(symbol: str, price_data):
    fallback_entry = {
        "symbol": symbol.upper(),
        "signal": "NODATA",
        "confidence": 0,
        "price": None,
        "reasons": ["Price data unavailable"],
        "timestamp": datetime.now().isoformat(),
    }
    _signal_cache[symbol] = fallback_entry


def _cache_realtime_only(symbol: str, price_data):
    rt_entry = {
        "symbol": symbol.upper(),
        "price": price_data,
        "timestamp": datetime.now().isoformat(),
    }
    _realtime_cache[symbol] = rt_entry
    save_realtime_cache(symbol, rt_entry)


async def check_and_notify():
    """Process all monitored symbols. Uses Dhan bulk OHLC for Indian stocks (1 API call vs 119 yfinance calls)."""
    # Pre-warm Dhan price cache with a single bulk call
    try:
        from app.services.dhanhq_service import get_market_ohlc
        if _INDIAN_STOCKS:
            await get_market_ohlc(_INDIAN_STOCKS)
    except Exception:
        pass

    for symbol in ('btc', 'eth', 'gold', 'silver'):
        await _process_symbol(symbol)

    tasks = []
    for symbol in _INDIAN_STOCKS:
        tasks.append(_process_symbol(symbol))
        if len(tasks) >= 5:
            await asyncio.gather(*tasks)
            tasks = []
    if tasks:
        await asyncio.gather(*tasks)


_last_edge_alert: Dict[str, float] = {}


async def _edge_scan():
    """Periodic edge scan — alerts on high-score stocks."""
    try:
        from app.services.market_edge_service import scan_all_stocks
        results = await scan_all_stocks()
        for r in results[:5]:  # Top 5
            score = r.get("score", 0)
            sym = r.get("symbol", "").lower()
            if score >= 8:
                last = _last_edge_alert.get(sym, 0)
                if datetime.now().timestamp() - last > 3600 * 4:  # Max once per 4h
                    signals = "; ".join(r.get("signals", []))
                    msg = (
                        f"🔥 *EDGE ALERT: {sym.upper()}* (Score: {score}/10)\n"
                        f"💰 Price: `{r.get('price', '?')}`\n"
                        f"📊 Vol: `{r.get('vol_ratio', '?')}x avg` | RSI: `{r.get('rsi', '?')}`\n"
                        f"🎯 {signals}"
                    )
                    await telegram_notifier.send_message(msg)
                    _last_edge_alert[sym] = datetime.now().timestamp()
    except Exception as e:
        print(f"Edge scan error: {e}")


def _is_market_hours() -> bool:
    """Check if Indian market is open (weekday 9:15am-3:30pm IST = UTC 3:45-10:00)."""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    utc_hour = now.hour + now.minute / 60.0
    ist_hour = utc_hour + 5.5
    return 9.25 <= ist_hour < 15.5


async def signal_monitor_loop():
    global _initial_scan_done
    resolve_counter = 0
    edge_counter = 0
    while True:
        if _is_market_hours() or not _initial_scan_done:
            start = datetime.now()
            _initial_scan_done = True
            await check_and_notify()
            elapsed = (datetime.now() - start).total_seconds()
            resolve_counter += 1
            edge_counter += 1
            if resolve_counter >= 30:
                resolve_counter = 0
                await resolve_signals()
            if edge_counter >= 15:  # Edge scan every ~150 min during market hours
                edge_counter = 0
                asyncio.ensure_future(_edge_scan())
            await asyncio.sleep(max(60, settings.signal_check_interval_seconds - elapsed))
        else:
            await asyncio.sleep(300)  # Check every 5min outside market hours


def get_signal_log(limit: int = 50) -> List[Dict]:
    return signal_log[:limit]


def get_cached_signals() -> Dict[str, Dict]:
    return dict(_signal_cache)


def get_cached_realtime() -> Dict[str, Dict]:
    return dict(_realtime_cache)


def get_cache_stats() -> Dict:
    now = datetime.now()
    total = len(_MONITORED_SYMBOLS)
    cached = len(_signal_cache)
    ages = []
    for v in _signal_cache.values():
        try:
            ages.append((now - datetime.fromisoformat(v["timestamp"])).total_seconds())
        except Exception:
            pass
    max_age = round(max(ages), 1) if ages else None
    min_age = round(min(ages), 1) if ages else None
    return {
        "total_symbols": total,
        "cached_signals": cached,
        "cached_realtime": len(_realtime_cache),
        "max_age_seconds": max_age,
        "min_age_seconds": min_age,
        "started_at": _cache_start,
        "pct_complete": round(cached / total * 100, 1) if total else 0,
    }
