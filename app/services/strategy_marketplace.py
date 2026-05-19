import json
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any


class StrategyMarketplaceService:
    def __init__(self):
        self._strategies = self._seed_strategies()
        self._user_strategies: List[Dict] = []
        self._backtest_results: Dict[str, Any] = {}

    def _seed_strategies(self) -> List[Dict]:
        return [
            {
                "id": "ma-crossover",
                "name": "MA Crossover Pro",
                "author": "QuantDinger",
                "description": "Classic moving average crossover with dynamic trailing stop. Enters on golden cross (20/50 SMA) and exits on death cross.",
                "type": "Trend Following",
                "timeframe": "1D",
                "instruments": ["Stocks", "Indices"],
                "metrics": {
                    "total_return": "+34.2%",
                    "win_rate": "62%",
                    "max_drawdown": "-8.1%",
                    "sharpe": "1.84",
                    "trades": 47,
                    "avg_hold": "12 days",
                },
                "entry_rules": [
                    "SMA 20 crosses above SMA 50 (Golden Cross)",
                    "Volume > 20-day average volume",
                    "RSI(14) > 40 and < 70",
                ],
                "exit_rules": [
                    "SMA 20 crosses below SMA 50 (Death Cross)",
                    "OR trailing stop at 2x ATR(14)",
                ],
                "risk": {"stop_loss": "2x ATR", "position_size": "2% of capital", "max_open": 5},
                "popularity": 980,
                "copies": 1240,
                "rating": 4.7,
                "tags": ["trend", "moving-average", "conservative"],
                "created": "2025-11-15",
            },
            {
                "id": "rsi-mean-rev",
                "name": "RSI Mean Reversion",
                "author": "AlphaSeeker",
                "description": "Mean reversion strategy that buys oversold and sells overbought conditions with volume confirmation.",
                "type": "Mean Reversion",
                "timeframe": "15m-1H",
                "instruments": ["Stocks", "Crypto"],
                "metrics": {
                    "total_return": "+28.7%",
                    "win_rate": "71%",
                    "max_drawdown": "-5.4%",
                    "sharpe": "2.12",
                    "trades": 89,
                    "avg_hold": "4 hours",
                },
                "entry_rules": [
                    "RSI(14) < 25 (oversold) for long OR > 75 (overbought) for short",
                    "Volume spike > 1.5x 10-period average",
                    "Price near lower/upper Bollinger Band",
                ],
                "exit_rules": [
                    "RSI crosses back above 40 (long) or below 60 (short)",
                    "OR take profit at 2:1 risk:reward",
                ],
                "risk": {"stop_loss": "0.5% below entry", "position_size": "1% of capital", "max_open": 3},
                "popularity": 850,
                "copies": 1100,
                "rating": 4.5,
                "tags": ["mean-reversion", "rsi", "scalping"],
                "created": "2025-10-20",
            },
            {
                "id": "breakout-momentum",
                "name": "Breakout Momentum Hunter",
                "author": "TradingTitan",
                "description": "Aggressive breakout strategy using volume-weighted price channels and volatility expansion.",
                "type": "Momentum",
                "timeframe": "1D",
                "instruments": ["Stocks"],
                "metrics": {
                    "total_return": "+56.8%",
                    "win_rate": "48%",
                    "max_drawdown": "-15.2%",
                    "sharpe": "1.45",
                    "trades": 33,
                    "avg_hold": "8 days",
                },
                "entry_rules": [
                    "Price breaks above 20-day high with volume > 2x average",
                    "ATR(14) expanding > 20%",
                    "Market in uptrend (SMA 50 > SMA 200)",
                ],
                "exit_rules": [
                    "Price closes below 10-day SMA",
                    "OR volume drops below 50% of entry-day volume",
                    "OR trailing stop at 1.5x ATR",
                ],
                "risk": {"stop_loss": "1x ATR", "position_size": "1.5% of capital", "max_open": 4},
                "popularity": 1200,
                "copies": 1560,
                "rating": 4.3,
                "tags": ["momentum", "breakout", "aggressive"],
                "created": "2025-09-01",
            },
            {
                "id": "mean-rev-fo",
                "name": "F&O Mean Reversion",
                "author": "OptionWizard",
                "description": "Mean reversion on F&O stocks using PCR and OI changes to time entries on index options.",
                "type": "Options",
                "timeframe": "1D",
                "instruments": ["F&O Stocks", "Indices"],
                "metrics": {
                    "total_return": "+41.5%",
                    "win_rate": "65%",
                    "max_drawdown": "-6.8%",
                    "sharpe": "2.01",
                    "trades": 55,
                    "avg_hold": "3 days",
                },
                "entry_rules": [
                    "PCR OI > 1.2 (excessive puts = bounce) or < 0.6 (excessive calls = drop)",
                    "Max Pain > 1% away from current price",
                    "FII net buying > 500Cr",
                ],
                "exit_rules": [
                    "PCR reverts below 1.0 (or above 0.8)",
                    "OR take profit at 3:1",
                ],
                "risk": {"stop_loss": "ATM strike width", "position_size": "1 lot", "max_open": 2},
                "popularity": 720,
                "copies": 890,
                "rating": 4.6,
                "tags": ["options", "pcr", "mean-reversion"],
                "created": "2026-01-10",
            },
            {
                "id": "sector-rotation",
                "name": "Sector Rotation Tracker",
                "author": "MacroEdge",
                "description": "Rotates capital between top 3 performing sectors monthly based on relative strength ranking.",
                "type": "Rotation",
                "timeframe": "1W-1M",
                "instruments": ["Sector ETFs", "Stocks"],
                "metrics": {
                    "total_return": "+22.3%",
                    "win_rate": "58%",
                    "max_drawdown": "-7.2%",
                    "sharpe": "1.65",
                    "trades": 24,
                    "avg_hold": "28 days",
                },
                "entry_rules": [
                    "Rank all sectors by 4-week ROC (Rate of Change)",
                    "Buy top 3 sectors equally",
                    "Rebalance when ranking changes significantly",
                ],
                "exit_rules": [
                    "Sector drops out of top 5",
                    "OR weekly close below 20-week SMA",
                ],
                "risk": {"stop_loss": "5% per sector", "position_size": "33% each", "max_open": 3},
                "popularity": 650,
                "copies": 780,
                "rating": 4.4,
                "tags": ["sector", "rotation", "macro"],
                "created": "2025-08-15",
            },
            {
                "id": "fii-dii-flow",
                "name": "FII/DII Flow Follower",
                "author": "InstitutionalEdge",
                "description": "Tracks FII and DII institutional flows to align with smart money. Enters when FII buying exceeds threshold.",
                "type": "Flow-Based",
                "timeframe": "1D",
                "instruments": ["Large Cap Stocks"],
                "metrics": {
                    "total_return": "+31.8%",
                    "win_rate": "67%",
                    "max_drawdown": "-6.1%",
                    "sharpe": "1.92",
                    "trades": 38,
                    "avg_hold": "15 days",
                },
                "entry_rules": [
                    "FII net buying > 1000Cr for 3 consecutive days",
                    "Stock in Nifty 100",
                    "Price above 50-day SMA",
                ],
                "exit_rules": [
                    "FII net selling > 500Cr in a day",
                    "OR price closes below 50-day SMA",
                ],
                "risk": {"stop_loss": "3% below entry", "position_size": "3% of capital", "max_open": 3},
                "popularity": 540,
                "copies": 620,
                "rating": 4.2,
                "tags": ["institutional", "flow", "conservative"],
                "created": "2025-12-01",
            },
        ]

    def get_strategies(self, type_filter: Optional[str] = None) -> List[Dict]:
        strategies = self._strategies + self._user_strategies
        if type_filter:
            strategies = [s for s in strategies if s["type"].lower() == type_filter.lower()]
        return strategies

    def get_strategy(self, strategy_id: str) -> Optional[Dict]:
        all_s = self._strategies + self._user_strategies
        return next((s for s in all_s if s["id"] == strategy_id), None)

    def get_types(self) -> List[str]:
        types = set(s["type"] for s in self._strategies + self._user_strategies)
        return sorted(types)

    def add_strategy(self, strategy: Dict) -> Dict:
        import uuid
        strategy["id"] = f"user-{uuid.uuid4().hex[:8]}"
        strategy["created"] = datetime.now().strftime("%Y-%m-%d")
        strategy["popularity"] = 0
        strategy["copies"] = 0
        strategy["rating"] = 0
        strategy["metrics"] = {
            "total_return": "0%",
            "win_rate": "0%",
            "max_drawdown": "0%",
            "sharpe": "0",
            "trades": 0,
            "avg_hold": "0 days",
        }
        self._user_strategies.append(strategy)
        return strategy

    def run_backtest(self, strategy_id: str, symbol: str = "NIFTY", days: int = 365) -> Dict:
        cache_key = f"{strategy_id}_{symbol}_{days}"
        if cache_key in self._backtest_results:
            return self._backtest_results[cache_key]

        days_count = days
        mock_prices = []
        price = 23000
        for i in range(days_count):
            change = random.uniform(-0.02, 0.02)
            price *= (1 + change)
            mock_prices.append({"date": (datetime.now() - timedelta(days=days_count-i)).strftime("%Y-%m-%d"), "close": round(price, 2)})

        trades = []
        capital = 100000
        in_position = False
        entry_price = 0
        entry_date = ""
        for i in range(50, len(mock_prices)):
            if not in_position and random.random() < 0.03:
                in_position = True
                entry_price = mock_prices[i]["close"]
                entry_date = mock_prices[i]["date"]
            elif in_position and random.random() < 0.04:
                exit_price = mock_prices[i]["close"]
                exit_date = mock_prices[i]["date"]
                pnl_pct = (exit_price - entry_price) / entry_price * 100
                trades.append({
                    "entry_date": entry_date,
                    "exit_date": exit_date,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "pnl_pct": round(pnl_pct, 2),
                    "pnl": round(capital * pnl_pct / 100, 2),
                    "days_held": (datetime.strptime(exit_date, "%Y-%m-%d") - datetime.strptime(entry_date, "%Y-%m-%d")).days,
                })
                in_position = False

        wins = [t for t in trades if t["pnl_pct"] > 0]
        losses = [t for t in trades if t["pnl_pct"] <= 0]
        total_pnl = sum(t["pnl"] for t in trades)
        total_return = total_pnl / capital * 100

        result = {
            "strategy_id": strategy_id,
            "symbol": symbol,
            "period": f"{days_count} days",
            "initial_capital": capital,
            "final_capital": round(capital + total_pnl, 2),
            "total_return_pct": round(total_return, 2),
            "total_trades": len(trades),
            "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
            "avg_win": round(sum(t["pnl_pct"] for t in wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(t["pnl_pct"] for t in losses) / len(losses), 2) if losses else 0,
            "profit_factor": round(abs(sum(t["pnl"] for t in wins) / sum(t["pnl"] for t in losses)), 2) if losses and sum(t["pnl"] for t in losses) != 0 else "inf",
            "max_drawdown": round(-min(min(t["pnl_pct"] for t in trades), 0) if trades else 0, 2),
            "trades": trades[-20:],
            "equity_curve": [{"date": m["date"], "equity": capital + sum(t["pnl"] for t in trades if t["exit_date"] <= m["date"])} for m in mock_prices[::20]],
        }

        self._backtest_results[cache_key] = result
        return result


strategy_marketplace_service = StrategyMarketplaceService()
