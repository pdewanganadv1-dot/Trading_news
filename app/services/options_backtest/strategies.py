import math
import statistics
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field


@dataclass
class Signal:
    action: str
    strike: int
    qty: int = 1
    reason: str = ""
    price: Optional[float] = None


@dataclass
class TradeResult:
    entry_date: str
    exit_date: str
    strike: int
    option_type: str
    entry_price: float
    exit_price: float
    qty: int
    pnl: float
    pnl_pct: float
    entry_reason: str
    exit_reason: str
    days_held: int
    underlying_entry: float
    underlying_exit: float
    is_short: bool = False


class OptionStrategy:
    def __init__(self, name: str, signal_fn: Callable):
        self.name = name
        self.signal_fn = signal_fn
        self.trades: List[TradeResult] = []

    def __call__(self, date: str, snap: dict, history: List[dict], fiidii: Optional[dict] = None) -> List[Signal]:
        return self.signal_fn(date, snap, history, fiidii)

    def reset(self):
        self.trades = []


def _get_atm_strike(spot: float) -> int:
    return round(spot / 50) * 50


def _get_strike_offset(spot: float, offset: int) -> int:
    base = round(spot / 50) * 50
    return base + offset * 50


def _find_price(snapshot: dict, strike: int, optype: str) -> float:
    ot = "CE" if "CE" in optype else "PE"
    for r in snapshot.get("chain", []):
        if r["strike"] == strike:
            return r.get(f"{ot.lower()}_close", 0)
    return 0


def _fii_bias(fiidii: Optional[dict]) -> str:
    if not fiidii:
        return "neutral"
    cl = fiidii.get("fii_call_long", 0)
    cs = fiidii.get("fii_call_short", 0)
    pl = fiidii.get("fii_put_long", 0)
    ps = fiidii.get("fii_put_short", 0)
    net_option = (cl + ps) - (cs + pl)
    fut_net = fiidii.get("fii_fut_long", 0) - fiidii.get("fii_fut_short", 0)
    net = net_option + fut_net
    if net > 0:
        return "bullish"
    elif net < 0:
        return "bearish"
    return "neutral"


def fii_filtered_strategy(date: str, snap: dict, history: List[dict], fiidii: Optional[dict] = None) -> List[Signal]:
    if not snap or not snap.get("chain") or len(history) < 3:
        return []
    spot = snap["underlying"]
    chain = snap["chain"]
    signals = []
    atm = _get_atm_strike(spot)
    pcr = snap.get("pcr_oi", 1.0)
    bias = _fii_bias(fiidii)

    # Use PCR as sentiment proxy when FII data is unavailable
    if bias == "neutral" and fiidii is None:
        bias = "bullish" if pcr > 1.0 else "bearish"

    prev = history[-1]
    spot_chg = (spot - prev.get("underlying", spot)) / prev.get("underlying", spot) if prev.get("underlying", 0) else 0
    pcr_trend = pcr - history[-1].get("pcr_oi", 1.0)
    pcr_5d = pcr - history[-3].get("pcr_oi", 1.0) if len(history) >= 3 else 0

    spot_3d_ago = history[-3].get("underlying", spot) if len(history) >= 3 else spot
    spot_3d_trend = (spot - spot_3d_ago) / spot_3d_ago

    atm_row = next((r for r in chain if r["strike"] == atm), None)
    if not atm_row:
        return signals
    ce_vol = atm_row.get("ce_volume", 0)
    pe_vol = atm_row.get("pe_volume", 0)
    ce_oi_chng = atm_row.get("ce_chng_oi", 0)
    pe_oi_chng = atm_row.get("pe_chng_oi", 0)

    vol_threshold = 30000

    # Buy calls: uptrend + volume + bullish FII bias
    if bias in ("bullish", "neutral") and spot_chg > 0.003 and spot_3d_trend > 0.003 and ce_vol > vol_threshold and ce_oi_chng > 0:
        price = _find_price(snap, atm, "CE")
        if price > 0:
            signals.append(Signal("BUY_CE", atm, 1, f"FII={bias} spot↑{spot_chg*100:.1f}% 3d={spot_3d_trend*100:.1f}% vol={ce_vol}", price))

    # Buy puts: downtrend + volume + bearish FII bias
    if bias in ("bearish", "neutral") and spot_chg < -0.003 and spot_3d_trend < -0.003 and pe_vol > vol_threshold and pe_oi_chng > 0:
        price = _find_price(snap, atm, "PE")
        if price > 0:
            signals.append(Signal("BUY_PE", atm, 1, f"FII={bias} spot↓{spot_chg*100:.1f}% 3d={spot_3d_trend*100:.1f}% vol={pe_vol}", price))

    # Extreme contrarian: extreme PCR + FII confirmation
    if pcr < 0.7 and spot_chg < -0.01 and bias == "bearish":
        price = _find_price(snap, atm, "PE")
        if price > 0:
            signals.append(Signal("BUY_PE", atm, 1, f"Contrarian put PCR={pcr} FII={bias} spot↓{spot_chg*100:.1f}%", price))

    if pcr > 1.3 and spot_chg > 0.01 and bias == "bullish":
        price = _find_price(snap, atm, "CE")
        if price > 0:
            signals.append(Signal("BUY_CE", atm, 1, f"Contrarian call PCR={pcr} FII={bias} spot↑{spot_chg*100:.1f}%", price))

    return signals


def short_premium_strategy(date: str, snap: dict, history: List[dict], fiidii: Optional[dict] = None) -> List[Signal]:
    if not snap or not snap.get("chain") or len(history) < 6:
        return []
    spot = snap["underlying"]
    chain = snap["chain"]
    signals = []
    atm = _get_atm_strike(spot)
    bias = _fii_bias(fiidii)

    atm_row = next((r for r in chain if r["strike"] == atm), None)
    if not atm_row:
        return signals

    atm_ce_price = atm_row.get("ce_close", 0)
    atm_pe_price = atm_row.get("pe_close", 0)

    ce_prices = [s.get("atm_ce_price", 0) for s in history[-6:] if s.get("atm_ce_price", 0) > 0]
    pe_prices = [s.get("atm_pe_price", 0) for s in history[-6:] if s.get("atm_pe_price", 0) > 0]

    if len(ce_prices) >= 4:
        ce_avg = statistics.mean(ce_prices)
        ce_ratio = atm_ce_price / ce_avg if ce_avg > 0 else 0
        if ce_ratio > 1.3:
            otm_s = _get_strike_offset(spot, 3)
            price = _find_price(snap, otm_s, "CE")
            if price > 0:
                signals.append(Signal("SELL_CE", otm_s, 1,
                    f"CE premium {ce_ratio:.1f}x avg, sell OTM@{otm_s}", price))

    if len(pe_prices) >= 4:
        pe_avg = statistics.mean(pe_prices)
        pe_ratio = atm_pe_price / pe_avg if pe_avg > 0 else 0
        if pe_ratio > 1.3:
            otm_s = _get_strike_offset(spot, -3)
            price = _find_price(snap, otm_s, "PE")
            if price > 0:
                signals.append(Signal("SELL_PE", otm_s, 1,
                    f"PE premium {pe_ratio:.1f}x avg, sell OTM@{otm_s}", price))

    return signals


def ultra_selective_strategy(date: str, snap: dict, history: List[dict], fiidii: Optional[dict] = None) -> List[Signal]:
    if not snap or not snap.get("chain") or len(history) < 10:
        return []
    spot = snap["underlying"]
    chain = snap["chain"]
    signals = []
    atm = _get_atm_strike(spot)
    pcr = snap.get("pcr_oi", 1.0)
    bias = _fii_bias(fiidii)

    if bias == "neutral" and fiidii is None:
        bias = "bullish" if pcr > 1.0 else "bearish"

    prev1 = history[-1]
    prev2 = history[-2]
    spot_d1 = (spot - prev1.get("underlying", spot)) / prev1.get("underlying", spot) if prev1.get("underlying", 0) else 0
    spot_d2 = (prev1.get("underlying", spot) - prev2.get("underlying", spot)) / prev2.get("underlying", spot) if prev2.get("underlying", 0) else 0

    spot_5d_ago = history[-5].get("underlying", spot) if len(history) >= 5 else spot
    spot_5d_trend = (spot - spot_5d_ago) / spot_5d_ago

    atm_row = next((r for r in chain if r["strike"] == atm), None)
    if not atm_row:
        return signals
    ce_vol = atm_row.get("ce_volume", 0)
    pe_vol = atm_row.get("pe_volume", 0)
    ce_oi_chng = atm_row.get("ce_chng_oi", 0)
    pe_oi_chng = atm_row.get("pe_chng_oi", 0)

    vol_5d_ce = [s.get("atm_ce_volume", 0) for s in history[-5:] if s.get("atm_ce_volume", 0) > 0]
    vol_5d_pe = [s.get("atm_pe_volume", 0) for s in history[-5:] if s.get("atm_pe_volume", 0) > 0]
    avg_ce_vol = statistics.mean(vol_5d_ce) if len(vol_5d_ce) >= 3 else 0
    avg_pe_vol = statistics.mean(vol_5d_pe) if len(vol_5d_pe) >= 3 else 0

    if (spot_d1 > 0.004 and spot_d2 > 0 and spot_5d_trend > 0.003
        and pcr > 1.0 and ce_vol > avg_ce_vol * 1.2 and ce_vol > 30000
        and ce_oi_chng > 0 and bias in ("bullish", "neutral")):
        price = _find_price(snap, atm, "CE")
        if price > 0:
            signals.append(Signal("BUY_CE", atm, 1,
                f"ULTRA CALL: 2d↑{spot_d1*100:.1f}%/{spot_d2*100:.1f}% 5d↑{spot_5d_trend*100:.1f}% vol={ce_vol}({avg_ce_vol:.0f}) PCR={pcr:.2f} FII={bias}", price))

    if (spot_d1 < -0.004 and spot_d2 < 0 and spot_5d_trend < -0.003
        and pcr < 1.0 and pe_vol > avg_pe_vol * 1.2 and pe_vol > 30000
        and pe_oi_chng > 0 and bias in ("bearish", "neutral")):
        price = _find_price(snap, atm, "PE")
        if price > 0:
            signals.append(Signal("BUY_PE", atm, 1,
                f"ULTRA PUT: 2d↓{spot_d1*100:.1f}%/{spot_d2*100:.1f}% 5d↓{spot_5d_trend*100:.1f}% vol={pe_vol}({avg_pe_vol:.0f}) PCR={pcr:.2f} FII={bias}", price))

    return signals


def mega_strategy(date: str, snap: dict, history: List[dict], fiidii: Optional[dict] = None) -> List[Signal]:
    signals = []
    s1 = fii_filtered_strategy(date, snap, history, fiidii)
    s2 = ultra_selective_strategy(date, snap, history, fiidii)
    s3 = short_premium_strategy(date, snap, history, fiidii)
    signals.extend(s1)
    signals.extend(s2)
    signals.extend(s3)
    return signals


STRATEGIES = {
    "mega": mega_strategy,
    "fii_filtered": fii_filtered_strategy,
    "short_premium": short_premium_strategy,
    "ultra_selective": ultra_selective_strategy,
}
