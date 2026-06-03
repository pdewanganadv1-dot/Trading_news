import math
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field, asdict
import pandas as pd
import numpy as np

from .data_loader import options_data_loader
from .strategies import (
    OptionStrategy, Signal, TradeResult, STRATEGIES
)


@dataclass
class BacktestConfig:
    symbol: str = "NIFTY"
    instrument: str = "OPTIDX"
    from_date: str = None
    to_date: str = None
    period_days: int = 60
    strategy_name: str = "combined"
    max_positions_per_day: int = 2
    trailing_stop_pct: float = 0.0
    fixed_target_pct: float = 0.0
    stop_loss_pct: float = 0.0
    max_hold_days: int = 3
    decay_stop_pct: float = 70.0
    lot_size: int = 50


@dataclass
class BacktestResult:
    config: BacktestConfig
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    gross_profit: float
    gross_loss: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    avg_win_pct: float
    avg_loss_pct: float
    expectancy: float
    max_drawdown: float
    max_drawdown_pct: float
    sharpe_ratio: float
    avg_days_held: float
    best_trade: dict
    worst_trade: dict
    trades: List[dict] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    daily_pnl: List[dict] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


STRATEGY_MAP = STRATEGIES


class OptionsBacktestEngine:
    def __init__(self):
        self.data_loader = options_data_loader

    def _get_signal_fn(self, name: str) -> Callable:
        return STRATEGY_MAP.get(name, STRATEGY_MAP["fii_filtered"])

    def _find_nearest_expiry(self, date_str: str, expiries: List[str]) -> Optional[str]:
        entry = pd.Timestamp(date_str)
        future = [e for e in expiries if pd.Timestamp(e) > entry]
        if future:
            best = min(future, key=lambda e: pd.Timestamp(e))
            days_to = (pd.Timestamp(best) - entry).days
            if days_to <= 45:
                return best
        return None

    def run(self, config: BacktestConfig) -> BacktestResult:
        df = self.data_loader.fetch_option_data(
            config.symbol, config.instrument,
            from_date=config.from_date, to_date=config.to_date,
            period_days=config.period_days
        )
        if df.empty:
            raise ValueError(f"No data for {config.symbol}")

        dates = sorted(df["TIMESTAMP"].unique())
        all_expiries = sorted(df["EXPIRY_DT"].unique())
        expiry_strs = [e.strftime("%Y-%m-%d") for e in all_expiries]

        signal_fn = self._get_signal_fn(config.strategy_name)
        strategy = OptionStrategy(config.strategy_name, signal_fn)

        # Pre-load FII/DII data for all dates
        fiidii_cache = {}
        for date in dates:
            date_str = date.strftime("%d-%m-%Y")
            poi = self.data_loader.fetch_participant_oi(date_str)
            if poi:
                fiidii_cache[date.strftime("%Y-%m-%d")] = poi

        equity = 0.0
        equity_curve = [0.0]
        snapshots = []
        open_trades: List[dict] = []
        closed_trades: List[TradeResult] = []
        daily_pnl_records = []

        for date in dates:
            date_str = date.strftime("%Y-%m-%d")
            snap = self.data_loader.build_daily_snapshot(df, date_str)
            if snap is None:
                daily_pnl_records.append({"date": date_str, "pnl": 0, "equity": equity})
                equity_curve.append(equity)
                continue

            snapshots.append(snap)
            fiidii = fiidii_cache.get(date_str)
            new_signals = strategy(date_str, snap, snapshots[:-1], fiidii)
            selected = new_signals[:config.max_positions_per_day]

            for sig in selected:
                price = sig.price or _find_price(snap, sig.strike, sig.action)
                if price is None or price <= 0:
                    continue
                trade_expiry = self._find_nearest_expiry(date_str, expiry_strs)
                if not trade_expiry:
                    continue
                is_short = "SELL" in sig.action
                open_trades.append({
                    "entry_date": date_str,
                    "strike": sig.strike,
                    "option_type": "CE" if "CE" in sig.action else "PE",
                    "expiry": trade_expiry,
                    "entry_price": price,
                    "qty": sig.qty * config.lot_size,
                    "entry_reason": sig.reason,
                    "underlying_entry": snap["underlying"],
                    "is_short": is_short,
                    "highest_price": price,
                    "lowest_price": price,
                })

            day_pnl = 0.0
            still_open = []

            for t in open_trades:
                optype = t["option_type"]
                expiry_str = t["expiry"]
                exp_snap = self.data_loader.build_daily_snapshot(df, date_str, expiry=expiry_str)
                if exp_snap is None:
                    still_open.append(t)
                    continue

                price_now = _find_price(exp_snap, t["strike"], optype)
                if price_now is None or price_now <= 0:
                    still_open.append(t)
                    continue

                entry_p = t["entry_price"]
                days_held = (pd.Timestamp(date_str) - pd.Timestamp(t["entry_date"])).days
                is_short = t.get("is_short", False)

                t["highest_price"] = max(t["highest_price"], price_now)
                t["lowest_price"] = min(t.get("lowest_price", entry_p), price_now)

                should_exit = False
                exit_reason = ""

                if is_short:
                    if config.trailing_stop_pct > 0 and t["lowest_price"] < entry_p:
                        stop_level = t["lowest_price"] * (1 + config.trailing_stop_pct / 100)
                        if price_now >= stop_level:
                            should_exit = True
                            exit_reason = f"Trailing stop ({config.trailing_stop_pct}% from trough)"
                    if config.stop_loss_pct > 0 and not should_exit:
                        loss_level = entry_p * (1 + config.stop_loss_pct / 100)
                        if price_now >= loss_level:
                            should_exit = True
                            exit_reason = f"Stop loss {config.stop_loss_pct}%"
                    if config.fixed_target_pct > 0 and not should_exit:
                        target = entry_p * (1 - config.fixed_target_pct / 100)
                        if price_now <= target:
                            should_exit = True
                            exit_reason = f"Target {config.fixed_target_pct}%"
                else:
                    if config.trailing_stop_pct > 0 and t["highest_price"] > entry_p:
                        stop_level = t["highest_price"] * (1 - config.trailing_stop_pct / 100)
                        if price_now <= stop_level:
                            should_exit = True
                            exit_reason = f"Trailing stop ({config.trailing_stop_pct}% from peak)"
                    if config.stop_loss_pct > 0 and not should_exit:
                        loss_level = entry_p * (1 - config.stop_loss_pct / 100)
                        if price_now <= loss_level:
                            should_exit = True
                            exit_reason = f"Stop loss {config.stop_loss_pct}%"
                    if config.fixed_target_pct > 0 and not should_exit:
                        target = entry_p * (1 + config.fixed_target_pct / 100)
                        if price_now >= target:
                            should_exit = True
                            exit_reason = f"Target {config.fixed_target_pct}%"

                if config.max_hold_days > 0 and days_held >= config.max_hold_days:
                    should_exit = True
                    exit_reason = f"Max hold {config.max_hold_days}d"

                if not is_short and not should_exit and config.decay_stop_pct > 0:
                    decay_level = entry_p * (1 - config.decay_stop_pct / 100)
                    if price_now <= decay_level:
                        should_exit = True
                        exit_reason = f"Decay stop ({config.decay_stop_pct}%)"

                if date_str >= expiry_str:
                    should_exit = True
                    exit_reason = "Expiry"

                if should_exit:
                    realized = (entry_p - price_now) * t["qty"] if is_short else (price_now - entry_p) * t["qty"]
                    pnl_pct = ((entry_p - price_now) / entry_p) * 100 if is_short else ((price_now - entry_p) / entry_p) * 100
                    day_pnl += realized
                    closed_trades.append(TradeResult(
                        entry_date=t["entry_date"], exit_date=date_str,
                        strike=t["strike"], option_type=t["option_type"],
                        entry_price=entry_p, exit_price=price_now,
                        qty=t["qty"], pnl=realized,
                        pnl_pct=round(pnl_pct, 2),
                        entry_reason=t["entry_reason"], exit_reason=exit_reason,
                        days_held=days_held,
                        underlying_entry=t["underlying_entry"],
                        underlying_exit=snap["underlying"],
                        is_short=is_short,
                    ))
                else:
                    still_open.append(t)

            open_trades = still_open
            equity += day_pnl
            daily_pnl_records.append({"date": date_str, "pnl": day_pnl, "equity": equity})
            equity_curve.append(equity)

        for t in open_trades:
            last_date = dates[-1].strftime("%Y-%m-%d")
            last_snap = snapshots[-1] if snapshots else None
            expiry_str = t["expiry"]
            exp_snap = self.data_loader.build_daily_snapshot(df, last_date, expiry=expiry_str) if last_snap else None
            price_now = _find_price(exp_snap or last_snap, t["strike"], t["option_type"]) if (exp_snap or last_snap) else 0
            if price_now is None:
                price_now = 0
            is_short = t.get("is_short", False)
            realized = ((t["entry_price"] - price_now) if is_short else (price_now - t["entry_price"])) * t["qty"]
            days_held = (pd.Timestamp(last_date) - pd.Timestamp(t["entry_date"])).days
            closed_trades.append(TradeResult(
                entry_date=t["entry_date"], exit_date=last_date,
                strike=t["strike"], option_type=t["option_type"],
                entry_price=t["entry_price"], exit_price=price_now or 0,
                qty=t["qty"], pnl=realized,
                pnl_pct=0,
                entry_reason=t["entry_reason"], exit_reason="End of backtest",
                days_held=days_held,
                underlying_entry=t["underlying_entry"],
                underlying_exit=last_snap["underlying"] if last_snap else 0,
                is_short=is_short,
            ))

        return self._compute_results(config, closed_trades, equity_curve, daily_pnl_records)

    def _compute_results(self, config, trades, equity_curve, daily_pnl):
        total = len(trades)
        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        win_count = len(wins)
        loss_count = len(losses)

        total_pnl = sum(t.pnl for t in trades)
        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        win_rate = win_count / total if total else 0
        profit_factor = gross_profit / gross_loss if gross_loss else 999.0

        avg_win = gross_profit / win_count if win_count else 0
        avg_loss = gross_loss / loss_count if loss_count else 0
        avg_win_pct = sum(t.pnl_pct for t in wins) / win_count if win_count else 0
        avg_loss_pct = sum(t.pnl_pct for t in losses) / loss_count if loss_count else 0
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss) if total else 0
        avg_days = sum(t.days_held for t in trades) / total if total else 0

        peak = 0.0
        max_dd = 0.0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_dd:
                max_dd = dd
        max_dd_pct = (max_dd / peak * 100) if peak > 0 else 0

        returns = []
        for i in range(1, len(equity_curve)):
            if equity_curve[i - 1] != 0:
                returns.append((equity_curve[i] - equity_curve[i - 1]) / abs(equity_curve[i - 1]))
        sharpe = 0.0
        if len(returns) > 1 and np.std(returns) > 0:
            sharpe = (np.mean(returns) / np.std(returns)) * math.sqrt(252)

        best = max(trades, key=lambda t: t.pnl) if trades else None
        worst = min(trades, key=lambda t: t.pnl) if trades else None

        return BacktestResult(
            config=config, total_trades=total,
            winning_trades=win_count, losing_trades=loss_count,
            win_rate=round(win_rate, 4),
            total_pnl=round(total_pnl, 2),
            gross_profit=round(gross_profit, 2),
            gross_loss=round(gross_loss, 2),
            profit_factor=round(profit_factor, 4),
            avg_win=round(avg_win, 2), avg_loss=round(avg_loss, 2),
            avg_win_pct=round(avg_win_pct, 2),
            avg_loss_pct=round(avg_loss_pct, 2),
            expectancy=round(expectancy, 2),
            max_drawdown=round(max_dd, 2),
            max_drawdown_pct=round(max_dd_pct, 2),
            sharpe_ratio=round(sharpe, 4),
            avg_days_held=round(avg_days, 1),
            best_trade={"entry_date": best.entry_date, "exit_date": best.exit_date,
                        "strike": best.strike, "option_type": best.option_type,
                        "pnl": round(best.pnl, 2), "pnl_pct": best.pnl_pct,
                        "entry_reason": best.entry_reason, "exit_reason": best.exit_reason} if best else {},
            worst_trade={"entry_date": worst.entry_date, "exit_date": worst.exit_date,
                         "strike": worst.strike, "option_type": worst.option_type,
                         "pnl": round(worst.pnl, 2), "pnl_pct": worst.pnl_pct,
                         "entry_reason": worst.entry_reason, "exit_reason": worst.exit_reason} if worst else {},
            trades=[asdict(t) for t in trades],
            equity_curve=equity_curve,
            daily_pnl=daily_pnl,
        )


def _find_price(snapshot: dict, strike: int, optype: str) -> Optional[float]:
    ot = "CE" if "CE" in optype else "PE"
    for r in snapshot.get("chain", []):
        if r["strike"] == strike:
            return r.get(f"{ot.lower()}_close", 0)
    return None


backtest_engine = OptionsBacktestEngine()
