import asyncio
import time
import yfinance as yf
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from app.data.stocks import INDIAN_STOCKS

_INDIAN = INDIAN_STOCKS

_scan_cache: List[Dict] = []
_scan_cache_ts: float = 0
_SCAN_TTL = 300

_yf_last_call_ts: float = 0


def _yf_ticker(symbol: str) -> str:
    return f"{symbol.upper()}.NS"


def _calc_ema(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = (v - ema) * multiplier + ema
    return ema


def _find_support_resistance(closes: List[float], lookback: int = 50) -> Tuple[Optional[float], Optional[float]]:
    if len(closes) < lookback:
        return None, None
    recent = closes[-lookback:]
    return min(recent), max(recent)


def _detect_bounce(symbol: str) -> Optional[Dict]:
    global _yf_last_call_ts
    ticker = _yf_ticker(symbol)

    now = time.time()
    since_last = now - _yf_last_call_ts
    if since_last < 1.0:
        time.sleep(1.0 - since_last)
    _yf_last_call_ts = time.time()

    try:
        stock = yf.Ticker(ticker)
        data = stock.history(period="2d", interval="1m")
        if data.empty or len(data) < 210:
            return None
    except Exception:
        return None

    closes = data["Close"].values.tolist()
    highs = data["High"].values.tolist()
    lows = data["Low"].values.tolist()
    volumes = data["Volume"].values.tolist()

    ema200 = _calc_ema(closes, 200)
    if ema200 is None:
        return None

    last_price = closes[-1]
    prev_price = closes[-2] if len(closes) >= 2 else last_price
    change_pct = ((last_price - prev_price) / prev_price) * 100

    # Check position relative to EMA 200 for last N candles
    n_check = 6
    above_flags = []
    valid = True
    for i in range(n_check):
        idx = -(i + 1)
        if len(closes) + idx < 0:
            valid = False
            break
        sub_closes = closes[:idx] if idx != 0 else closes
        ema = _calc_ema(sub_closes, 200)
        if ema is None:
            valid = False
            break
        above_flags.append(closes[idx] > ema)

    if not valid or len(above_flags) < 3:
        return None

    last_above = above_flags[0]
    prev_above = above_flags[1]

    support, resistance = _find_support_resistance(closes)

    avg_vol = float(np.mean(volumes[-20:])) if len(volumes) >= 20 else 1.0
    last_vol = volumes[-1] if volumes else 1
    vol_ratio = last_vol / avg_vol if avg_vol > 0 else 1

    direction = None
    strength = 0.0
    reason = ""

    # BUY: was below for >=3 candles, now crosses above
    if not prev_above and last_above:
        candles_below = sum(1 for f in above_flags if not f)
        if candles_below >= 2:
            direction = "BUY"
            strength = abs(last_price - ema200) / ema200 * 100
            near_support = support is not None and abs(last_price - support) / support < 0.005
            if near_support:
                strength += 1.0
                reason += "Near support level. "
            if vol_ratio > 1.5:
                strength += 0.5
                reason += "Above avg volume. "
            reason += f"Bounced above EMA200 (₹{ema200:.2f}) after {candles_below}c below."

    # SELL: was above for >=3 candles, now crosses below
    elif prev_above and not last_above:
        candles_above = sum(1 for f in above_flags if f)
        if candles_above >= 2:
            direction = "SELL"
            strength = abs(last_price - ema200) / ema200 * 100
            near_resistance = resistance is not None and abs(last_price - resistance) / resistance < 0.005
            if near_resistance:
                strength += 1.0
                reason += "Near resistance level. "
            if vol_ratio > 1.5:
                strength += 0.5
                reason += "Above avg volume. "
            reason += f"Broke below EMA200 (₹{ema200:.2f}) after {candles_above}c above."

    if direction is None:
        return None

    return {
        "symbol": symbol.upper(),
        "direction": direction,
        "price": round(last_price, 2),
        "ema200": round(ema200, 2),
        "change_pct": round(change_pct, 2),
        "strength": round(strength, 2),
        "vol_ratio": round(vol_ratio, 2),
        "support": round(support, 2) if support else None,
        "resistance": round(resistance, 2) if resistance else None,
        "reason": reason.strip(),
        "timestamp": datetime.now().isoformat(),
    }


async def scan_for_bounces() -> List[Dict]:
    global _scan_cache, _scan_cache_ts
    now = time.time()
    if _scan_cache and (now - _scan_cache_ts) < _SCAN_TTL:
        return list(_scan_cache)

    loop = asyncio.get_event_loop()
    signals = []

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_detect_bounce, sym): sym for sym in _INDIAN}
        for future in as_completed(futures):
            try:
                result = future.result()
                if result:
                    signals.append(result)
            except Exception:
                pass

    signals.sort(key=lambda x: x.get("strength", 0), reverse=True)
    _scan_cache = signals
    _scan_cache_ts = time.time()
    return signals


async def get_recent_bounces(min_strength: float = 0.3) -> List[Dict]:
    signals = await scan_for_bounces()
    return [s for s in signals if s.get("strength", 0) >= min_strength]


def _backtest_stock(symbol: str) -> Optional[Dict]:
    ticker = _yf_ticker(symbol)
    try:
        stock = yf.Ticker(ticker)
        data = stock.history(period="6mo", interval="1d")
        if data.empty or len(data) < 210:
            return None
    except Exception:
        return None

    closes = data["Close"].values.tolist()
    dates = data.index.tolist()

    trades = []
    in_position = False
    entry_price = 0
    entry_date = None
    direction = None  # "BUY" or "SELL"
    entry_idx = 0

    for i in range(200, len(closes)):
        segment = closes[:i+1]
        ema200 = _calc_ema(segment, 200)
        if ema200 is None:
            continue
        ema200_prev = _calc_ema(closes[:i], 200)
        if ema200_prev is None:
            continue

        price = closes[i]
        prev_price = closes[i-1]
        above = price > ema200
        prev_above = prev_price > ema200_prev

        # Check context: was it below/above for last 3 candles?
        above_flags = []
        valid = True
        for j in range(6):
            idx = i - j
            if idx < 200:
                valid = False
                break
            sub_ema = _calc_ema(closes[:idx+1], 200)
            if sub_ema is None:
                valid = False
                break
            above_flags.append(closes[idx] > sub_ema)
        if not valid or len(above_flags) < 3:
            continue

        signal = None
        if not prev_above and above:  # BUY bounce
            candles_below = sum(1 for f in above_flags if not f)
            if candles_below >= 2:
                signal = "BUY"
        elif prev_above and not above:  # SELL breakdown
            candles_above = sum(1 for f in above_flags if f)
            if candles_above >= 2:
                signal = "SELL"

        if signal and not in_position:
            in_position = True
            entry_price = price
            entry_date = dates[i]
            direction = signal
            entry_idx = i
        elif in_position:
            # Check exits
            ret = (price - entry_price) / entry_price * 100
            if direction == "SELL":
                ret = -ret

            exit_reason = None
            if ret >= 10:
                exit_reason = "TARGET 10%"
            elif ret <= -5:
                exit_reason = "STOP -5%"
            elif i - entry_idx >= 20:
                exit_reason = "TIMEOUT 20d"

            if exit_reason:
                trades.append({
                    "symbol": symbol.upper(),
                    "direction": direction,
                    "entry_price": round(entry_price, 2),
                    "exit_price": round(price, 2),
                    "entry_date": str(entry_date.date()) if hasattr(entry_date, 'date') else str(entry_date),
                    "exit_date": str(dates[i].date()) if hasattr(dates[i], 'date') else str(dates[i]),
                    "return_pct": round(ret, 2),
                    "exit_reason": exit_reason,
                })
                in_position = False
                entry_price = 0
                direction = None

    if in_position:
        ret = (closes[-1] - entry_price) / entry_price * 100
        if direction == "SELL":
            ret = -ret
        trades.append({
            "symbol": symbol.upper(),
            "direction": direction,
            "entry_price": round(entry_price, 2),
            "exit_price": round(closes[-1], 2),
            "entry_date": str(entry_date.date()) if hasattr(entry_date, 'date') else str(entry_date),
            "exit_date": str(dates[-1].date()) if hasattr(dates[-1], 'date') else str(dates[-1]),
            "return_pct": round(ret, 2),
            "exit_reason": "OPEN",
        })

    return {"symbol": symbol.upper(), "trades": trades} if trades else None


def _calc_sharpe(returns: List[float], rf: float = 0.05) -> float:
    if len(returns) < 2:
        return 0.0
    avg_ret = np.mean(returns)
    std_ret = np.std(returns)
    if std_ret == 0:
        return 0.0
    return (avg_ret - rf/252) / std_ret * (252 ** 0.5)


async def run_backtest() -> Dict:
    """Backtest EMA 200 bounce strategy on daily data for all stocks."""
    from app.services.telegram_notifier import telegram_notifier
    await telegram_notifier.send_message("⏳ Backtesting EMA200 scalp on all 119 stocks (daily, 6mo)...")

    loop = asyncio.get_event_loop()
    all_trades = []
    stock_results = []

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_backtest_stock, sym): sym for sym in _INDIAN}
        for future in as_completed(futures):
            try:
                result = future.result()
                if result and result.get("trades"):
                    all_trades.extend(result["trades"])
                    stock_results.append(result)
            except Exception:
                pass

    if not all_trades:
        return {"status": "empty", "message": "No trades generated"}

    returns = [t["return_pct"] for t in all_trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    total = len(returns)
    win_rate = len(wins) / total * 100 if total else 0
    avg_return = np.mean(returns) if returns else 0
    avg_win = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0
    max_return = max(returns)
    min_return = min(returns)
    sharpe = _calc_sharpe(returns)

    # Best/worst trades
    sorted_trades = sorted(all_trades, key=lambda x: x["return_pct"], reverse=True)
    best5 = sorted_trades[:5]
    worst5 = sorted_trades[-5:]
    worst5.reverse()

    # Stats per direction
    buy_trades = [t for t in all_trades if t["direction"] == "BUY"]
    sell_trades = [t for t in all_trades if t["direction"] == "SELL"]
    buy_returns = [t["return_pct"] for t in buy_trades]
    sell_returns = [t["return_pct"] for t in sell_trades]

    return {
        "status": "ok",
        "total_trades": total,
        "win_rate": round(win_rate, 1),
        "avg_return": round(avg_return, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "max_return": round(max_return, 2),
        "min_return": round(min_return, 2),
        "sharpe": round(sharpe, 2),
        "buy_trades": len(buy_trades),
        "sell_trades": len(sell_trades),
        "buy_avg": round(np.mean(buy_returns), 2) if buy_returns else 0,
        "sell_avg": round(np.mean(sell_returns), 2) if sell_returns else 0,
        "best_trades": best5,
        "worst_trades": worst5,
        "stocks_with_signals": len(stock_results),
    }
