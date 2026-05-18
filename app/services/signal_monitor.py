import asyncio
from datetime import datetime
from typing import List, Dict
from app.config import settings
from app.services.market_data_service import market_data_service, TradingSignals
from app.services.telegram_notifier import telegram_notifier
from app.services.accuracy_tracker import record_signal, resolve_signals
from app.services.signal_confirmer import confirm_signal
from app.services.signal_explainer import signal_explainer


signal_log: List[Dict] = []
_signal_cache: Dict[str, Dict] = {}
_realtime_cache: Dict[str, Dict] = {}
_last_sent: Dict[str, str] = {}
_CONFIRMED_SENT: Dict[str, str] = {}  # Tracks composite signal sends
_cache_start: str = ""

# Nifty 100 stocks (monitored for trading signals)
_INDIAN_STOCKS = [
    # Nifty 50
    "reliance", "tcs", "hdfcbank", "infy", "icicibank",
    "sbin", "lt", "wipro", "itc", "bhartiartl",
    "maruti", "nestleind", "hindunilvr", "asianpaint", "sunpharma",
    "titan", "bajajfinsv", "hcltech", "kotakbank", "axisbank",
    "ntpc", "tatasteel", "cipla", "ultracemco", "adaniports",
    "adanient", "apollohosp", "bajajauto", "bajfinance", "bpcl",
    "britannia", "coalindia", "divislab", "drreddy", "eichermot",
    "grasim", "hdfclife", "hindalco", "indusindbk", "jswsteel",
    "m&m", "ongc", "powergrid", "sbilife", "shriramfin",
    "tataconsum", "tatamotors", "techm", "trent",
    # Nifty Next 50
    "abb", "abfrl", "abcap", "adanienergy", "adani green",
    "ambujacem", "auropharma", "bandhanbnk", "bankbaroda", "bergerpaint",
    "biocon", "bse", "canbk", "castrol", "chambalfert",
    "colgate", "concor", "coforget", "cummins", "dabur",
    "dlf", "esi", "exideind", "federalbnk", "gail",
    "godrejcp", "godrejpro", "gvk", "havells", "heromotoco",
    "hindustan", "hindzinc", "idfcfirstb", "ioc", "irctc",
    "irfc", "lic", "lutrading", "mcdowell",
    "motherson", "mphend", "muthoot", "navin", "pageind",
    "petronet", "pidilite", "pfc", "ramco", "rb",
    "recl", "relianceind", "sail", "samvardhana", "sir",
    "siemens", "srtrans", "tatachem", "tatacoffee", "tatapower",
    "thermax", "torrentpow", "torrentpharm", "tvs",
    "ujjivan", "unionbank", "varunever", "vestutech", "voltas",
    "yesbank", "zyduslife",
]

_MONITORED_SYMBOLS = ['btc', 'eth', 'gold', 'silver'] + _INDIAN_STOCKS

async def _process_symbol(symbol: str) -> None:
    """Process a single symbol: fetch data, generate signal, explain, alert."""
    try:
        price_data = await market_data_service.get_price_data(symbol)
        if not price_data:
            return

        prices_5m = await market_data_service.get_5min_prices(symbol, 100)
        if not prices_5m or len(prices_5m) < 20:
            return  # Not enough data for a signal

        signal_data = TradingSignals.generate_signal(prices_5m, price_data['price'])

        sig = signal_data['signal']
        conf = signal_data['confidence']
        price = price_data['price']

        await record_signal(symbol, sig, conf, price, signal_data.get('reasons', []))

        # Only use Groq LLM for signals that may trigger alerts (BUY/SELL ≥ 50%)
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

        # Primary gate: base signal must cross threshold
        should_alert = sig in ('BUY', 'SELL') and conf >= settings.signal_confidence_threshold
        if should_alert:
            confirmed = await confirm_signal(symbol, sig, conf, signal_data.get('reasons', []), price)
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

        _signal_cache[symbol] = {
            "symbol": entry["symbol"],
            "signal": entry["signal"],
            "confidence": entry["confidence"],
            "price": entry["price"],
            "reasons": entry["reasons"],
            "explanation": entry["explanation"],
            "timestamp": entry["timestamp"],
        }

        if not _cache_start:
            _cache_start = datetime.now().isoformat()

        _realtime_cache[symbol] = {
            "symbol": symbol.upper(),
            "price": price_data,
            "timestamp": datetime.now().isoformat(),
        }

        signal_log.insert(0, entry)
        if len(signal_log) > 100:
            signal_log.pop()

    except Exception as e:
        print(f"Signal monitor error for {symbol}: {e}")


async def check_and_notify():
    """Process all monitored symbols sequentially (Yahoo rate-limits parallel requests)."""
    for symbol in _MONITORED_SYMBOLS:
        await _process_symbol(symbol)


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


async def signal_monitor_loop():
    resolve_counter = 0
    edge_counter = 0
    while True:
        start = datetime.now()
        await check_and_notify()
        elapsed = (datetime.now() - start).total_seconds()
        resolve_counter += 1
        edge_counter += 1
        if resolve_counter >= 30:
            resolve_counter = 0
            await resolve_signals()
        if edge_counter >= 15:  # Edge scan every ~30 min
            edge_counter = 0
            _ = asyncio.create_task(_edge_scan())
        # Sleep for the remaining interval (min 60s between cycles)
        await asyncio.sleep(max(60, settings.signal_check_interval_seconds - elapsed))


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
