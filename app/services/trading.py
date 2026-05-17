from typing import Dict, Any, Callable, Optional
from datetime import datetime
from enum import Enum


class SignalType(Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class TradingSignal:
    symbol: str
    signal: SignalType
    price: float
    confidence: float
    timestamp: datetime
    strategy: str


class Strategy:
    """Base strategy class for trading logic."""

    def __init__(self, name: str):
        self.name = name

    def analyze(self, data: list) -> TradingSignal:
        raise NotImplementedError


class MovingAverageStrategy(Strategy):
    """Simple moving average crossover strategy."""

    def __init__(self, short_period: int = 20, long_period: int = 50):
        super().__init__("Moving Average Crossover")
        self.short_period = short_period
        self.long_period = long_period

    def analyze(self, data: list) -> TradingSignal:
        if len(data) < self.long_period:
            return TradingSignal(
                symbol=data[-1].get("symbol", "UNKNOWN"),
                signal=SignalType.HOLD,
                price=data[-1].get("close", 0),
                confidence=0.0,
                timestamp=datetime.utcnow(),
                strategy=self.name
            )

        closes = [d.get("close", 0) for d in data[-self.long_period:]]
        short_ma = sum(closes[-self.short_period:]) / self.short_period
        long_ma = sum(closes) / self.long_period

        if short_ma > long_ma:
            signal = SignalType.BUY
            confidence = 0.7
        elif short_ma < long_ma:
            signal = SignalType.SELL
            confidence = 0.7
        else:
            signal = SignalType.HOLD
            confidence = 0.0

        return TradingSignal(
            symbol=data[-1].get("symbol", "UNKNOWN"),
            signal=signal,
            price=closes[-1],
            confidence=confidence,
            timestamp=datetime.utcnow(),
            strategy=self.name
        )


class TradingEngine:
    """Engine for running trading strategies."""

    def __init__(self):
        self.strategies: Dict[str, Strategy] = {}
        self.register_default_strategies()

    def register_default_strategies(self):
        self.strategies["ma_crossover"] = MovingAverageStrategy()
        self.strategies["ma_short"] = MovingAverageStrategy(10, 30)

    def add_strategy(self, name: str, strategy: Strategy):
        self.strategies[name] = strategy

    def run(self, symbol: str, data: list, strategy: str = "ma_crossover") -> TradingSignal:
        if strategy not in self.strategies:
            raise ValueError(f"Strategy {strategy} not found")
        return self.strategies[strategy].analyze(data)


trading_engine = TradingEngine()