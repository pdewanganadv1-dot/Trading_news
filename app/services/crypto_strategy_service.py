import asyncio, time, json, os
import numpy as np
import pandas as pd
import httpx
from datetime import datetime
from typing import Dict, List, Optional

SYMBOLS = ["btc", "eth"]
BINANCE_MAP = {"btc": "BTCUSDT", "eth": "ETHUSDT"}
YF_MAP = {"gold": "GC=F", "silver": "SI=F"}
ALL_SYMBOLS = ["btc", "eth", "gold", "silver"]
CACHE_TTL = 20


def _ema(arr, period):
    out = np.empty_like(arr); out[:] = np.nan
    a = 2.0/(period+1)
    for i in range(len(arr)):
        if i==0: out[i]=arr[i]
        else: out[i]=arr[i]*a + out[i-1]*(1-a)
    return out

def _rma(arr, period):
    out = np.empty_like(arr); out[:] = np.nan
    for i in range(len(arr)):
        if i==0: out[i]=arr[i]
        else: out[i]=(arr[i]+out[i-1]*(period-1))/period
    return out

def compute_all_series(o, h, l, c, v):
    """Compute all indicator direction series as a dict of name -> np.array of +/-1/0."""
    n = len(c)
    diff = np.diff(c); up = np.where(diff>0, diff, 0); dn = np.where(diff<0, -diff, 0)
    gain = np.concatenate([[0], up]); loss = np.concatenate([[0], dn])
    avg_g = _rma(gain, 14); avg_l = _rma(loss, 14)
    rs = np.divide(avg_g, avg_l, out=np.ones_like(avg_g), where=avg_l!=0)
    rsi = 100 - 100/(1+rs)
    e12 = _ema(c, 12); e26 = _ema(c, 26)
    macd_line = e12 - e26; macd_signal = _ema(macd_line, 9)
    macd_hist = macd_line - macd_signal
    ema9 = _ema(c, 9); ema20 = _ema(c, 20); ema50 = _ema(c, 50)
    sma20_arr = np.array(pd.Series(c).rolling(20).mean())
    sma50_arr = np.array(pd.Series(c).rolling(50).mean())
    hl2 = (h+l)/2
    st_atr = _rma(np.maximum(h-l, np.maximum(abs(h-np.roll(c,1)), abs(l-np.roll(c,1)))), 10)
    upper = hl2 + 3*st_atr; lower = hl2 - 3*st_atr
    st_dir = np.zeros(n)
    for i in range(1, n):
        if c[i] > lower[i]: st_dir[i] = 1 if st_dir[i-1] >= 0 else 1
        elif c[i] < upper[i]: st_dir[i] = -1 if st_dir[i-1] <= 0 else -1
        else: st_dir[i] = st_dir[i-1]
    psar_dir = np.zeros(n); psar_val = np.zeros(n); psar_val[0] = h[0]
    af, ep, trend = 0.02, l[0], 1
    for i in range(1, n):
        if trend == 1:
            if c[i] < psar_val[i-1]:
                trend = -1; psar_val[i] = ep; af = 0.02; ep = h[i]
            else:
                if h[i] > ep: ep = h[i]; af = min(af+0.02, 0.2)
                psar_val[i] = psar_val[i-1] + af*(ep-psar_val[i-1])
        else:
            if c[i] > psar_val[i-1]:
                trend = 1; psar_val[i] = ep; af = 0.02; ep = l[i]
            else:
                if l[i] < ep: ep = l[i]; af = min(af+0.02, 0.2)
                psar_val[i] = psar_val[i-1] + af*(ep-psar_val[i-1])
        psar_dir[i] = trend
    e1 = _ema(c, 10); e2 = _ema(e1, 10); e3 = _ema(e2, 10)
    tema = 3*e1 - 3*e2 + e3
    tema_dir = np.zeros(n)
    for i in range(1, n):
        if not np.isnan(tema[i]) and not np.isnan(tema[i-1]):
            tema_dir[i] = 1 if tema[i] > tema[i-1] else -1
    stoch_k = np.zeros(n)
    for i in range(14, n):
        lo = min(l[i-13:i+1]); hi = max(h[i-13:i+1])
        stoch_k[i] = 100*(c[i]-lo)/(hi-lo+1e-10)
    zlema_dir = np.zeros(n)
    for i in range(1, n):
        if not np.isnan(ema9[i]) and not np.isnan(ema20[i]):
            zlema_dir[i] = 1 if ema9[i] > ema20[i] else -1
    dirs = {}
    dirs["PSAR"] = psar_dir
    dirs["TEMA"] = tema_dir
    dirs["SuperTrend"] = st_dir
    dirs["MACD"] = np.array([(1 if macd_hist[i]>0 else (-1 if macd_hist[i]<0 else 0)) for i in range(n)])
    dirs["RSI_V2"] = np.array([(1 if rsi[i]<35 else (-1 if rsi[i]>65 else 0)) for i in range(n)])
    dirs["Stochastic_V2"] = np.array([(1 if stoch_k[i]<20 else (-1 if stoch_k[i]>80 else 0)) for i in range(n)])
    dirs["ZLEMA"] = zlema_dir
    dirs["MA"] = np.array([(1 if sma20_arr[i]>sma50_arr[i] else (-1 if sma20_arr[i]<sma50_arr[i] else 0)) for i in range(n)])
    return dirs


class CryptoStrategyService:
    def __init__(self):
        self.session = httpx.AsyncClient(timeout=15.0)
        self._candles_cache: Dict[str, tuple] = {}
        self._signal_cache: Dict[str, tuple] = {}
        self._best_strategies: Dict[str, list] = {}

        self.strategies = {
            "btc": {"indicators": ["MACD", "PSAR", "RSI_V2", "Stochastic_V2", "ZLEMA"],
                    "threshold": 3, "gap_bars": 20, "flip": False,
                    "sl_pct": 5.0, "tp_pct": 10.0, "min_bars": 50,
                    "trailing_sl_pct": 2.0, "rr_ratio": 0},
            "eth": {"indicators": ["RSI_V2", "MACD", "Stochastic_V2"],
                    "threshold": 2, "gap_bars": 10, "flip": False,
                    "sl_pct": 5.0, "tp_pct": 10.0, "min_bars": 50,
                    "trailing_sl_pct": 2.0, "rr_ratio": 0},
            "gold": {"indicators": ["Stochastic_V2", "MACD", "RSI_V2"],
                     "threshold": 2, "gap_bars": 20, "flip": True,
                     "sl_pct": 5.0, "tp_pct": 10.0, "min_bars": 50,
                     "trailing_sl_pct": 2.0, "rr_ratio": 0},
            "silver": {"indicators": ["RSI_V2", "Stochastic_V2", "TEMA", "MA"],
                       "threshold": 3, "gap_bars": 10, "flip": False,
                       "sl_pct": 5.0, "tp_pct": 10.0, "min_bars": 50,
                       "trailing_sl_pct": 2.0, "rr_ratio": 0},
        }

    async def _fetch_binance(self, symbol: str, limit=1000) -> Optional[Dict]:
        now = time.time()
        sym = BINANCE_MAP.get(symbol)
        if not sym: return None
        try:
            r = await self.session.get(f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=5m&limit={limit}")
            if r.status_code != 200: return None
            d = r.json()
            o = np.array([float(k[1]) for k in d], dtype=np.float64)
            h = np.array([float(k[2]) for k in d], dtype=np.float64)
            l = np.array([float(k[3]) for k in d], dtype=np.float64)
            c = np.array([float(k[4]) for k in d], dtype=np.float64)
            v = np.array([float(k[5]) for k in d], dtype=np.float64)
            prices = [float(k[4]) for k in d]
            result = {"open": o, "high": h, "low": l, "close": c, "volume": v, "prices": prices, "current": float(d[-1][4])}
            self._candles_cache[symbol] = (now, result)
            return result
        except Exception as e:
            print(f"Binance error {symbol}: {e}")
            return None

    async def _fetch_yf(self, symbol: str) -> Optional[Dict]:
        import yfinance as yf
        now = time.time()
        yf_sym = YF_MAP.get(symbol)
        if not yf_sym: return None
        try:
            loop = asyncio.get_event_loop()
            df = await loop.run_in_executor(None, lambda: yf.download(yf_sym, period='7d', interval='5m', progress=False))
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] for c in df.columns]
            if df.empty or len(df) < 100:
                return None
            o = np.array([float(x) for x in df['Open']], dtype=np.float64)
            h = np.array([float(x) for x in df['High']], dtype=np.float64)
            l = np.array([float(x) for x in df['Low']], dtype=np.float64)
            c = np.array([float(x) for x in df['Close']], dtype=np.float64)
            v = np.array([int(x) for x in df['Volume']], dtype=np.float64)
            prices = [float(x) for x in df['Close']]
            result = {"open": o, "high": h, "low": l, "close": c, "volume": v, "prices": prices, "current": prices[-1]}
            self._candles_cache[symbol] = (now, result)
            return result
        except Exception as e:
            print(f"yfinance error {symbol}: {e}")
            return None

    async def _fetch(self, symbol: str, limit=1000) -> Optional[Dict]:
        now = time.time()
        ce = self._candles_cache.get(symbol)
        if ce and now - ce[0] < 30:
            return ce[1]
        if symbol in YF_MAP:
            return await self._fetch_yf(symbol)
        return await self._fetch_binance(symbol, limit)

    def _compute_consensus_dir(self, all_series: Dict, indicators: List[str], threshold: int):
        """Scored consensus: each indicator votes +/-1. Returns (combo_dir, score_array)."""
        n = len(next(iter(all_series.values())))
        result = np.zeros(n); scores = np.zeros(n)
        for i in range(n):
            score = 0
            for ind in indicators:
                d = all_series[ind][i]
                if d == 1: score += 1
                elif d == -1: score -= 1
            scores[i] = score
            if score >= threshold: result[i] = 1
            elif score <= -threshold: result[i] = -1
        return result, scores

    def _get_signal(self, symbol: str, data: Dict) -> Dict:
        cfg = self.strategies.get(symbol, self.strategies["btc"])
        o, h, l, c = data["open"], data["high"], data["low"], data["close"]
        n = len(c); min_bars = cfg["min_bars"]; gap = cfg["gap_bars"]
        flip = cfg["flip"]; sl = cfg["sl_pct"]; tp = cfg["tp_pct"]
        trailing = cfg.get("trailing_sl_pct", 2.0)
        indicators = cfg["indicators"]; threshold = cfg["threshold"]

        all_series = compute_all_series(o, h, l, c, data["volume"])
        combo_dir, score_arr = self._compute_consensus_dir(all_series, indicators, threshold)

        prev_dir = None; last_signal = 0
        for i in range(min_bars, n):
            d = combo_dir[i]
            if d == 0: continue
            if flip:
                if prev_dir is None or prev_dir == d: prev_dir = d; continue
                prev_dir = d
            else:
                if prev_dir is None: prev_dir = d; continue
                if d == prev_dir: continue
            if i - last_signal < gap: continue
            last_signal = i

            current = float(c[i])
            direction = "BUY" if d == 1 else "SELL"
            actual_score = abs(int(score_arr[i]))
            max_votes = len(indicators)
            confidence = int(actual_score / max_votes * 100)

            entry_price = round(current, 2)
            if direction == "BUY":
                sl_price = round(current * (1 - trailing / 100), 2)
                tp_price = round(current * (1 + tp / 100), 2)
            else:
                sl_price = round(current * (1 + trailing / 100), 2)
                tp_price = round(current * (1 - tp / 100), 2)

            for j in range(i + 1, n):
                chg = (c[j]/current - 1)*100
                if direction == "BUY":
                    if chg >= tp:
                        return {"signal": direction, "confidence": max(confidence, 85), "direction": d,
                                "tp_hit": True, "price": current, "entry_price": entry_price,
                                "sl_price": sl_price, "tp_price": tp_price, "trailing_sl_pct": trailing}
                    if chg <= -sl:
                        return {"signal": direction, "confidence": confidence, "direction": d,
                                "sl_hit": True, "price": current, "entry_price": entry_price,
                                "sl_price": sl_price, "tp_price": tp_price, "trailing_sl_pct": trailing}
                else:
                    if chg <= -tp:
                        return {"signal": direction, "confidence": max(confidence, 85), "direction": d,
                                "tp_hit": True, "price": current, "entry_price": entry_price,
                                "sl_price": sl_price, "tp_price": tp_price, "trailing_sl_pct": trailing}
                    if chg >= sl:
                        return {"signal": direction, "confidence": confidence, "direction": d,
                                "sl_hit": True, "price": current, "entry_price": entry_price,
                                "sl_price": sl_price, "tp_price": tp_price, "trailing_sl_pct": trailing}
            return {"signal": direction, "confidence": confidence, "direction": d, "price": current,
                    "entry_price": entry_price, "sl_price": sl_price, "tp_price": tp_price,
                    "trailing_sl_pct": trailing}

        return {"signal": "HOLD", "confidence": 0, "direction": 0}

    async def get_signal(self, symbol: str = "btc") -> Dict:
        now = time.time()
        ce = self._signal_cache.get(symbol)
        if ce and now - ce[0] < CACHE_TTL:
            return ce[1]

        data = await self._fetch(symbol)
        if not data:
            result = {"symbol": symbol.upper(), "signal": "HOLD", "confidence": 0,
                      "price": 0, "error": "No data", "timestamp": datetime.now().isoformat()}
            self._signal_cache[symbol] = (now, result)
            return result

        sig = self._get_signal(symbol, data)
        current_prices = data.get("prices", [])
        price = current_prices[-1] if current_prices else data.get("current", 0)
        price_1h = current_prices[-12] if len(current_prices) > 12 else current_prices[0] if current_prices else price
        change_pct = ((price - price_1h) / price_1h * 100) if price_1h else 0

        conf = sig.get("confidence", 0); signal = sig.get("signal", "HOLD")
        reasons = []
        cfg = self.strategies.get(symbol, self.strategies["btc"])
        ind_names = "+".join(cfg["indicators"])
        if signal == "BUY":
            reasons.append(f"Consensus flipped LONG ({ind_names})")
            if sig.get("tp_hit"): reasons.append("TP target reachable")
        elif signal == "SELL":
            reasons.append(f"Consensus flipped SHORT ({ind_names})")
            if sig.get("tp_hit"): reasons.append("TP target reachable")

        result = {
            "symbol": symbol.upper(),
            "price": round(price, 2),
            "change_1h": round(change_pct, 2),
            "signal": signal,
            "confidence": conf,
            "indicator": f"Consensus({cfg['threshold']}/{len(cfg['indicators'])})",
            "indicators": ind_names,
            "entry_price": sig.get("entry_price"),
            "sl_price": sig.get("sl_price"),
            "tp_price": sig.get("tp_price"),
            "trailing_sl_pct": sig.get("trailing_sl_pct"),
            "reasons": reasons,
            "timestamp": datetime.now().isoformat(),
            "source": "yfinance 5m" if symbol in YF_MAP else "Binance 5m",
        }
        self._signal_cache[symbol] = (now, result)
        return result

    async def get_all_signals(self) -> List[Dict]:
        return [await self.get_signal(s) for s in ALL_SYMBOLS]

    async def get_best_strategies(self, symbol: str = "btc") -> List[Dict]:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "crypto_best_strategies.json")
        try:
            with open(path) as f:
                data = json.load(f)
            return data.get("results", [])[:20]
        except Exception:
            return []

    async def backtest(self, symbol: str = "btc",
                        trailing_sl_pct: Optional[float] = None,
                        rr_ratio: Optional[float] = None) -> Dict:
        data = await self._fetch(symbol)
        if not data: return {"error": "No data", "trades": []}
        cfg = self.strategies.get(symbol, self.strategies["btc"])
        o, h, l, c = data["open"], data["high"], data["low"], data["close"]
        n = len(c); min_bars = cfg["min_bars"]; gap = cfg["gap_bars"]
        flip = cfg["flip"]

        base_sl = cfg["sl_pct"]; base_tp = cfg["tp_pct"]
        use_trailing = trailing_sl_pct if trailing_sl_pct is not None else cfg.get("trailing_sl_pct", 0)
        use_rr = rr_ratio if rr_ratio is not None else cfg.get("rr_ratio", 0)
        effective_tp = base_tp
        if use_rr > 0:
            effective_tp = round(base_sl * use_rr, 1)
        effective_sl = use_trailing if use_trailing > 0 else base_sl
        max_hold_bars = 60

        all_series = compute_all_series(o, h, l, c, data["volume"])
        combo_dir, _ = self._compute_consensus_dir(all_series, cfg["indicators"], cfg["threshold"])

        trades = []; in_pos = False; prev_d = None; last_idx = 0
        equity = [10000.0]; peak = 10000.0; max_dd = 0.0

        for i in range(min_bars, n):
            d = combo_dir[i]
            if d == 0: continue
            if flip:
                if prev_d is None or prev_d == d: prev_d = d; continue
                prev_d = d
            else:
                if prev_d is None: prev_d = d; continue
                if d == prev_d: continue
            if i - last_idx < gap: continue
            last_idx = i
            if in_pos: continue

            entry = float(c[i]); direction = "BUY" if d == 1 else "SELL"
            in_pos = True; best = entry
            jj = min(i + max_hold_bars, n - 1)
            exit_price = float(c[jj]); exit_reason = "expiry"

            for j in range(i + 1, min(i + max_hold_bars, n)):
                price = float(c[j])
                if direction == "BUY":
                    if price > best: best = price
                    trail_stop = best * (1 - use_trailing/100) if use_trailing else entry * (1 - base_sl/100)
                    tp_level = entry * (1 + effective_tp/100)
                    if price >= tp_level: exit_price = price; exit_reason = "TP"; jj=j; break
                    if price <= trail_stop: exit_price = price; exit_reason = "trail_SL" if use_trailing else "SL"; jj=j; break
                else:
                    if price < best: best = price
                    trail_stop = best * (1 + use_trailing/100) if use_trailing else entry * (1 + base_sl/100)
                    tp_level = entry * (1 - effective_tp/100)
                    if price <= tp_level: exit_price = price; exit_reason = "TP"; jj=j; break
                    if price >= trail_stop: exit_price = price; exit_reason = "trail_SL" if use_trailing else "SL"; jj=j; break

            pnl_pct = round((exit_price/entry - 1) * (100 if direction=="BUY" else -100), 2)
            trade_pnl = round(equity[-1] * pnl_pct / 100, 2)
            new_equity = equity[-1] + trade_pnl
            equity.append(new_equity)
            if new_equity > peak: peak = new_equity
            dd = (peak - new_equity) / peak * 100
            if dd > max_dd: max_dd = dd

            trades.append({
                "direction": direction, "entry_price": round(float(entry), 2),
                "exit_price": round(float(exit_price), 2), "pnl_pct": pnl_pct,
                "pnl_$": trade_pnl, "bars_held": jj - i, "exit_reason": exit_reason,
                "entry_idx": i, "exit_idx": jj,
            })
            in_pos = False

        if not trades: return {"trades": [], "total": 0}
        wins = sum(1 for t in trades if t["pnl_pct"] > 0)
        losses = len(trades) - wins; total = len(trades)
        total_returns = sum(t["pnl_pct"] for t in trades)
        avg_return = total_returns / total
        avg_win = np.mean([t["pnl_pct"] for t in trades if t["pnl_pct"] > 0]) if wins else 0
        avg_loss = abs(np.mean([t["pnl_pct"] for t in trades if t["pnl_pct"] <= 0])) if losses else 1
        pf = abs(avg_win*wins/(avg_loss*losses)) if losses and avg_loss else (99 if wins > 0 else 0)
        final_equity = equity[-1]
        total_return_pct = round((final_equity / 10000 - 1) * 100, 2)
        sharpe = round(np.mean([t["pnl_pct"] for t in trades]) / max(np.std([t["pnl_pct"] for t in trades]), 0.01) * np.sqrt(total), 2) if total > 1 else 0

        tp_trades = sum(1 for t in trades if t["exit_reason"] == "TP")
        sl_trades = sum(1 for t in trades if t["exit_reason"] in ("SL", "trail_SL"))
        expiry_trades = sum(1 for t in trades if t["exit_reason"] == "expiry")
        avg_bars = np.mean([t["bars_held"] for t in trades])
        win_streak = max(len(list(g)) for k, g in __import__('itertools').groupby(t["pnl_pct"] > 0 for t in trades) if k) if trades else 0
        loss_streak = max(len(list(g)) for k, g in __import__('itertools').groupby(t["pnl_pct"] <= 0 for t in trades) if k) if trades else 0

        return {
            "symbol": symbol.upper(),
            "strategy": f"Consensus({cfg['threshold']}/{len(cfg['indicators'])})",
            "indicators": "+".join(cfg["indicators"]),
            "gap": gap, "flip": flip,
            "sl_type": "trailing" if use_trailing else "fixed_entry",
            "sl_pct": effective_sl, "tp_pct": effective_tp, "rr_ratio": rr_ratio,
            "total_trades": total, "wins": wins, "losses": losses,
            "win_rate": round(wins/total*100, 1),
            "total_return_pct": total_return_pct,
            "total_return_$": round(final_equity - 10000, 2),
            "avg_return": round(avg_return, 2),
            "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
            "profit_factor": round(pf, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "sharpe_ratio": sharpe,
            "avg_bars_held": round(avg_bars, 1),
            "max_win_streak": win_streak, "max_loss_streak": loss_streak,
            "exits_tp": tp_trades, "exits_sl": sl_trades, "exits_expiry": expiry_trades,
            "starting_equity": 10000, "final_equity": round(final_equity, 2),
            "equity_curve": [round(e, 2) for e in equity[::max(1, len(equity)//50)]],
            "trades": trades[-50:],
        }


crypto_strategy_service = CryptoStrategyService()
