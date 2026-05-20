"""
1-minute OHLC bar builder from live tick stream.
Aggregates WebSocket ticks into 1-minute candles for strategy calculations.
"""
import time
from collections import deque, defaultdict
from typing import Dict, List, Optional


class MinuteBar:
    """Represents a single 1-minute OHLC bar."""

    def __init__(self, minute_key: int, open_price: float, volume: int = 0):
        self.minute_key = minute_key  # unix timestamp truncated to minute
        self.open = open_price
        self.high = open_price
        self.low = open_price
        self.close = open_price
        self.volume = 0
        self.tick_count = 0

    def update(self, price: float, volume_delta: int = 0):
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += volume_delta
        self.tick_count += 1

    def to_dict(self) -> dict:
        return {
            "minute": self.minute_key,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "tick_count": self.tick_count,
        }


class OHLCBuilder:
    """
    Builds 1-minute OHLC bars from live tick data.
    Maintains a ring buffer of recent bars per symbol.
    """

    def __init__(self, max_bars: int = 200):
        self.max_bars = max_bars
        # symbol -> current active minute bar
        self._current: Dict[str, MinuteBar] = {}
        # symbol -> deque of recent finalized minute bars
        self._history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=max_bars))
        # symbol -> previous cumulative volume (to compute bar volume delta)
        self._prev_volumes: Dict[str, int] = {}
        # symbol -> previous price (for tick detection)
        self._prev_prices: Dict[str, float] = {}

    def _get_minute_key(self, ts: float = None) -> int:
        return int((ts if ts else time.time()) // 60) * 60

    def process_tick(self, symbol: str, price: float, volume: Optional[int] = None,
                     timestamp: Optional[float] = None):
        """
        Process a single tick from the market feed.
        Call this for every incoming packet.
        """
        symbol = symbol.upper()
        ts = timestamp if timestamp else time.time()
        minute_key = self._get_minute_key(ts)

        # Compute volume delta if we have cumulative volume
        vol_delta = 0
        if volume is not None:
            prev_vol = self._prev_volumes.get(symbol, volume)
            vol_delta = max(0, volume - prev_vol)
            self._prev_volumes[symbol] = volume

        current_bar = self._current.get(symbol)

        if current_bar is None or current_bar.minute_key != minute_key:
            # Finalize previous bar if it exists
            if current_bar is not None:
                self._history[symbol].append(current_bar)

            # Start new bar
            self._current[symbol] = MinuteBar(minute_key, price, vol_delta)
        else:
            # Update existing bar
            current_bar.update(price, vol_delta)

        self._prev_prices[symbol] = price

    def get_current_bar(self, symbol: str) -> Optional[dict]:
        """Get the current (in-progress) minute bar."""
        bar = self._current.get(symbol.upper())
        return bar.to_dict() if bar else None

    def get_bars(self, symbol: str, n: int = 50) -> List[dict]:
        """
        Get the last N finalized 1-minute bars plus the current bar.
        Returns oldest first, newest last as dicts.
        """
        symbol = symbol.upper()
        bars = [b.to_dict() for b in self._history.get(symbol, [])]
        current = self._current.get(symbol)
        if current:
            bars = bars + [current.to_dict()]
        return bars[-n:]

    def get_bars_since(self, symbol: str, since_minute: int) -> List[dict]:
        """Get bars since a given minute key."""
        return [b for b in self.get_bars(symbol, 500) if b["minute"] >= since_minute]

    def to_lists(self, symbol: str, min_bars: int = 20) -> Optional[tuple]:
        """
        Convert OHLC bars to lists of (opens, highs, lows, closes, volumes).
        Returns None if fewer than min_bars available.
        """
        bars = self.get_bars(symbol, 200)
        if len(bars) < min_bars:
            return None
        opens = [b["open"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        closes = [b["close"] for b in bars]
        volumes = [b["volume"] for b in bars]
        return opens, highs, lows, closes, volumes

    def get_all_symbols_with_bars(self, min_bars: int = 20) -> List[str]:
        """Get all symbols that have at least min_bars of data."""
        result = []
        for sym, bars in self._history.items():
            total = len(bars)
            if sym in self._current:
                total += 1
            if total >= min_bars:
                result.append(sym)
        return result


# Singleton
ohlc_builder = OHLCBuilder()
