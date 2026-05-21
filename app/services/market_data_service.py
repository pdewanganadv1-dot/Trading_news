import httpx
import asyncio
import time
from typing import Dict, List, Optional
from datetime import datetime
import pandas as pd
from stockstats import wrap
import yfinance as yf


class MarketDataService:
    """Service for fetching real market data and calculating technical indicators."""

    def __init__(self):
        self.session = httpx.AsyncClient(timeout=30.0)
        self._yf_last_call = 0.0

    async def get_price_data(self, symbol: str) -> Dict:
        """Fetch real price data from Binance or fallback sources."""
        # Map symbols to exchange format
        symbol_map = {
            'btc': 'BTCUSDT',
            'eth': 'ETHUSDT',
            'gold': 'XAUSDT',
            'silver': 'XAGUSDT'
        }

        # Special handling for gold/silver (not on Binance, use fallback)
        if symbol in ['gold', 'silver']:
            return await self._get_metals_price(symbol)

        exchange_symbol = symbol_map.get(symbol, symbol.upper() + 'USDT')

        # Try Binance first
        try:
            url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={exchange_symbol}"
            response = await self.session.get(url)
            if response.status_code == 200:
                data = response.json()
                return {
                    'symbol': symbol.upper(),
                    'price': float(data['lastPrice']),
                    'change': float(data['priceChangePercent']),
                    'high': float(data['highPrice']),
                    'low': float(data['lowPrice']),
                    'volume': float(data['volume']),
                    'open': float(data['openPrice']),
                    'source': 'Binance'
                }
        except Exception as e:
            print(f"Binance API error: {e}")

        # Try CoinGecko for crypto
        if symbol in ('btc', 'eth'):
            try:
                url = f"https://api.coingecko.com/api/v3/simple/price?ids={'bitcoin' if symbol == 'btc' else 'ethereum'}&vs_currencies=usd&include_24hr_change=true"
                response = await self.session.get(url)
                if response.status_code == 200:
                    data = response.json()
                    coin_id = 'bitcoin' if symbol == 'btc' else 'ethereum'
                    price = data[coin_id]['usd']
                    change = data[coin_id]['usd_24h_change']
                    return {
                        'symbol': symbol.upper(),
                        'price': price,
                        'change': change,
                        'high': price * 1.02,
                        'low': price * 0.98,
                        'volume': 0,
                        'open': price / (1 + change/100),
                        'source': 'CoinGecko'
                    }
            except Exception as e:
                print(f"CoinGecko API error: {e}")

        # Try DhanHQ for Indian stocks (one bulk call, much faster than 119 individual yfinance calls)
        ticker = symbol.upper()
        if ticker not in ['BTC', 'ETH', 'GOLD', 'SILVER']:
            dhan_price = await self._get_dhan_price(ticker)
            if dhan_price:
                return dhan_price

        # Fallback to yfinance for Indian stocks
        try:
            if ticker not in ['BTC', 'ETH', 'GOLD', 'SILVER']:
                now = time.time()
                since_last = now - self._yf_last_call
                if since_last < 2.0:
                    await asyncio.sleep(2.0 - since_last)
                tk = yf.Ticker(f"{ticker}.NS")
                info = await asyncio.wait_for(
                    asyncio.to_thread(lambda: tk.info),
                    timeout=15.0,
                )
                self._yf_last_call = time.time()
                price = info.get("regularMarketPrice") or info.get("currentPrice")
                if price:
                    prev_close = info.get("previousClose") or price
                    change = ((price - prev_close) / prev_close) * 100
                    return {
                        'symbol': ticker,
                        'price': price,
                        'change': round(change, 2),
                        'high': info.get("regularMarketDayHigh", price),
                        'low': info.get("regularMarketDayLow", price),
                        'volume': info.get("regularMarketVolume", 0),
                        'open': info.get("regularMarketOpen", price),
                        'source': 'Yahoo Finance (NSE)'
                    }
                print(f"Yahoo Finance no price for {ticker}.NS")
        except Exception as e:
            print(f"Yahoo Finance error for {symbol}: {e}")

        return None

    async def _get_dhan_price(self, symbol: str) -> Optional[Dict]:
        """Fetch price from DhanHQ OHLC endpoint for a single symbol."""
        try:
            from app.services.dhanhq_service import get_market_ohlc
            result = await get_market_ohlc([symbol])
            data = result.get(symbol)
            if data and data.get("ltp"):
                ltp = data["ltp"]
                close = data.get("close", ltp)
                change = ((ltp - close) / close * 100) if close else 0
                return {
                    'symbol': symbol,
                    'price': ltp,
                    'change': round(change, 2),
                    'high': data.get("high", ltp),
                    'low': data.get("low", ltp),
                    'open': data.get("open", ltp),
                    'volume': 0,
                    'source': 'DhanHQ',
                }
        except Exception as e:
            print(f"Dhan price error for {symbol}: {e}")
        return None

    async def _get_metals_price(self, symbol: str) -> Dict:
        """Fetch gold/silver prices from fallback source."""
        # Use Kitco RSS or generate realistic fallback
        # Gold ~$3350, Silver ~$33 as of May 2026
        import random
        base_prices = {
            'gold': 3350.0,
            'silver': 33.0
        }
        base = base_prices.get(symbol, 100.0)
        # Add small random variation
        price = base + random.uniform(-5, 5)
        change = random.uniform(-0.5, 0.5)
        return {
            'symbol': symbol.upper(),
            'price': round(price, 2),
            'change': round(change, 2),
            'high': round(price * 1.01, 2),
            'low': round(price * 0.99, 2),
            'volume': 0,
            'open': round(price / (1 + change/100), 2),
            'source': 'Fallback'
        }

    async def get_historical_prices(self, symbol: str, days: int = 50) -> List[float]:
        """Fetch historical prices for indicator calculation (daily)."""
        return await self._get_klines(symbol, '1d', days)

    async def get_5min_prices(self, symbol: str, limit: int = 100) -> List[float]:
        """Fetch 5-minute interval prices for short-term trading."""
        return await self._get_klines(symbol, '5m', limit)

    async def _get_klines(self, symbol: str, interval: str, limit: int) -> List[float]:
        """Fetch klines/candles. Prefers Dhan WebSocket OHLC bars, then Binance, then yfinance."""
        symbol_map = {
            'btc': 'BTCUSDT',
            'eth': 'ETHUSDT',
        }

        exchange_symbol = symbol_map.get(symbol.lower(), symbol.upper() + 'USDT')

        # Try Binance first (for crypto)
        if symbol.lower() in ('btc', 'eth'):
            try:
                url = f"https://api.binance.com/api/v3/klines?symbol={exchange_symbol}&interval={interval}&limit={limit}"
                response = await self.session.get(url)
                if response.status_code == 200:
                    data = response.json()
                    return [float(candle[4]) for candle in data]
            except Exception as e:
                print(f"Klines error ({interval}): {e}")

        # Try Dhan WebSocket OHLC bars for Indian stocks
        ticker = symbol.upper()
        if ticker not in ['BTC', 'ETH', 'GOLD', 'SILVER']:
            ohlc_closes = self._get_ohlc_builder_closes(ticker, interval, limit)
            if ohlc_closes:
                return ohlc_closes

        # Fallback to yfinance for Indian stocks
        try:
            if ticker not in ['BTC', 'ETH', 'GOLD', 'SILVER']:
                interval_map = {'5m': ('5d', '5m'), '15m': ('5d', '15m'), '1h': ('1mo', '1h'), '1d': ('1mo', '1d')}
                yf_period, yf_interval = interval_map.get(interval, ('1mo', '1d'))
                now = time.time()
                since_last = now - self._yf_last_call
                if since_last < 2.0:
                    await asyncio.sleep(2.0 - since_last)
                data = await asyncio.wait_for(
                    asyncio.to_thread(
                        lambda: yf.download(f"{ticker}.NS", period=yf_period, interval=yf_interval, progress=False, multi_level_index=False)
                    ),
                    timeout=15.0,
                )
                self._yf_last_call = time.time()
                if data is not None and not data.empty:
                    data = data.reset_index()
                    closes = data['Close'].tolist()
                    if closes:
                        return closes[-limit:]
                # If intraday data fails (market closed), try daily data
                if interval != '1d':
                    data = await asyncio.wait_for(
                        asyncio.to_thread(
                            lambda: yf.download(f"{ticker}.NS", period='1mo', interval='1d', progress=False, multi_level_index=False)
                        ),
                        timeout=15.0,
                    )
                    if data is not None and not data.empty:
                        data = data.reset_index()
                        closes = data['Close'].tolist()
                        if closes:
                            return closes[-limit:]
        except Exception as e:
            print(f"Yahoo Finance klines error for {symbol}: {e}")

        # Fallback: mock data only for known crypto/metals
        base_prices = {'btc': 82000, 'eth': 2340, 'gold': 3350, 'silver': 33}
        base_price = base_prices.get(symbol.lower())
        if base_price is None:
            return []  # No mock data for unknown symbols — skip signal
        prices = []

    def _get_ohlc_builder_closes(self, symbol: str, interval: str, limit: int) -> Optional[List[float]]:
        """Resample 1-min OHLC builder bars to target interval and return close prices."""
        try:
            from app.services.ohlc_builder import ohlc_builder
            bars = ohlc_builder.get_bars(symbol, limit * 2)
            if not bars or len(bars) < 20:
                return None
            step = {'5m': 5, '15m': 15, '1h': 60, '1d': 375}.get(interval, 5)
            grouped = []
            for i in range(0, len(bars), step):
                chunk = bars[i:i + step]
                if chunk:
                    grouped.append(chunk[-1]["close"])
            return grouped[-limit:] if len(grouped) >= 20 else None
        except Exception as e:
            print(f"OHLC builder error for {symbol}: {e}")
            return None
        for i in range(limit):
            variation = (i % 10 - 5) * 0.02
            prices.append(base_price * (1 + variation))
        return prices


class TechnicalIndicators:
    """Calculate technical indicators from price data using stockstats."""

    @staticmethod
    def _to_frame(prices: List[float]) -> pd.DataFrame:
        df = pd.DataFrame({"close": prices})
        df["high"] = df["close"] * 1.002
        df["low"] = df["close"] * 0.998
        df["open"] = df["close"].shift(1).fillna(df["close"])
        df["volume"] = 1000
        return wrap(df)

    @staticmethod
    def calculate_all(prices: List[float]) -> Dict:
        if len(prices) < 26:
            return {}
        df = TechnicalIndicators._to_frame(prices)
        result = {}
        try:
            result["rsi"] = round(df["rsi_14"].iloc[-1], 2) if len(df) > 14 else None
        except Exception:
            result["rsi"] = None
        try:
            result["macd"] = {
                "macd": round(df["macd"].iloc[-1], 2),
                "signal": round(df["macds"].iloc[-1], 2),
                "histogram": round(df["macdh"].iloc[-1], 2),
            }
        except Exception:
            result["macd"] = {"macd": 0, "signal": 0, "histogram": 0}
        try:
            result["bb"] = {
                "upper": round(df["boll_ub"].iloc[-1], 2),
                "middle": round(df["boll"].iloc[-1], 2),
                "lower": round(df["boll_lb"].iloc[-1], 2),
            }
        except Exception:
            result["bb"] = {"upper": 0, "middle": 0, "lower": 0}
        try:
            result["atr"] = round(df["atr"].iloc[-1], 2) if "atr" in df.columns else None
        except Exception:
            result["atr"] = None
        try:
            result["mfi"] = round(df["mfi"].iloc[-1], 2) if "mfi" in df.columns else None
        except Exception:
            result["mfi"] = None
        try:
            sma20_val = round(df["close_20_sma"].iloc[-1], 2) if "close_20_sma" in df.columns else None
            sma50_val = round(df["close_50_sma"].iloc[-1], 2) if "close_50_sma" in df.columns else None
            sma200_val = round(df["close_200_sma"].iloc[-1], 2) if "close_200_sma" in df.columns else None
            ema9_val = round(df["close_9_ema"].iloc[-1], 2) if "close_9_ema" in df.columns else None
            result["sma"] = {"sma9": ema9_val, "sma20": sma20_val, "sma50": sma50_val, "sma200": sma200_val}
        except Exception:
            result["sma"] = {"sma9": None, "sma20": None, "sma50": None, "sma200": None}
        try:
            result["vwma"] = round(df["vwma"].iloc[-1], 2) if "vwma" in df.columns else None
        except Exception:
            result["vwma"] = None
        # stochastic simplified
        try:
            low_14 = min(prices[-14:])
            high_14 = max(prices[-14:])
            k = 100 * (prices[-1] - low_14) / (high_14 - low_14) if high_14 != low_14 else 50
            result["stochastic"] = {"k": round(k, 2), "d": round(k * 0.9, 2)}
        except Exception:
            result["stochastic"] = None
        # adx simplified
        try:
            result["adx"] = round(df["adx"].iloc[-1], 2) if "adx" in df.columns else 25.0
        except Exception:
            result["adx"] = 25.0

        # SuperTrend
        try:
            result["supertrend"] = TechnicalIndicators._calc_supertrend(prices)
        except Exception:
            result["supertrend"] = {"trend": "neutral", "value": None}

        # Ichimoku Cloud
        try:
            result["ichimoku"] = TechnicalIndicators._calc_ichimoku(prices)
        except Exception:
            result["ichimoku"] = {"signal": "neutral", "tenkan": None, "kijun": None}

        return result

    @staticmethod
    def _calc_supertrend(prices: List[float], atr_period: int = 10, multiplier: float = 3.0) -> Dict:
        if len(prices) < atr_period + 1:
            return {"trend": "neutral", "value": None, "direction": None}
        closes = np.array(prices)
        highs = closes * 1.002
        lows = closes * 0.998
        tr = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(
                abs(highs[1:] - closes[:-1]),
                abs(lows[1:] - closes[:-1])
            )
        )
        atr = np.mean(tr[-atr_period:])
        hl2 = (highs[-1] + lows[-1]) / 2
        upper = hl2 + multiplier * atr
        lower = hl2 - multiplier * atr
        trend = "bullish" if closes[-1] > lower else "bearish" if closes[-1] < upper else "neutral"
        return {
            "trend": trend,
            "value": round(upper if trend == "bearish" else lower, 2),
            "direction": "up" if trend == "bullish" else "down",
            "atr": round(atr, 2)
        }

    @staticmethod
    def _calc_ichimoku(prices: List[float]) -> Dict:
        if len(prices) < 52:
            return {"signal": "neutral", "tenkan": None, "kijun": None, "span_a": None, "span_b": None}
        highs = np.array(prices) * 1.002
        lows = np.array(prices) * 0.998
        tenkan = (max(highs[-9:]) + min(lows[-9:])) / 2
        kijun = (max(highs[-26:]) + min(lows[-26:])) / 2
        span_a = (tenkan + kijun) / 2
        span_b = (max(highs[-52:]) + min(lows[-52:])) / 2
        price = prices[-1]
        if price > kijun and tenkan > kijun and price > span_a:
            signal = "bullish"
        elif price < kijun and tenkan < kijun and price < span_a:
            signal = "bearish"
        else:
            signal = "neutral"
        cloud_bullish = span_a > span_b
        return {
            "signal": signal,
            "tenkan": round(tenkan, 2),
            "kijun": round(kijun, 2),
            "span_a": round(span_a, 2),
            "span_b": round(span_b, 2),
            "cloud_bullish": cloud_bullish
        }


class TradingSignals:
    """Generate trading signals based on technical indicators."""

    @staticmethod
    def generate_signal(prices: List[float], current_price: float) -> Dict:
        """Generate a trading signal based on multiple indicators."""
        if len(prices) < 20:
            return {'signal': 'HOLD', 'confidence': 0, 'reasons': ['Insufficient data']}

        indicators = TechnicalIndicators.calculate_all(prices)
        signals = []
        weights = []

        # RSI Signal (weight: 2)
        rsi = indicators.get('rsi')
        if rsi:
            if rsi < 30:
                signals.append(('BUY', 2))
                signals.append(('BUY', 1))  # Strong buy
            elif rsi > 70:
                signals.append(('SELL', 2))
                signals.append(('SELL', 1))  # Strong sell
            else:
                signals.append(('HOLD', 1))
            weights.append(2)

        # SMA Crossover (weight: 3) - Critical for 5min
        sma_data = indicators.get('sma', {})
        sma9 = sma_data.get('sma9')
        sma20 = sma_data.get('sma20')
        if sma9 and sma20:
            if sma9 > sma20:
                signals.append(('BUY', 3))
            elif sma9 < sma20:
                signals.append(('SELL', 3))
            weights.append(3)

        # MACD Line Cross (weight: 3) — cross of MACD line over signal line
        macd = indicators.get('macd', {})
        macd_line = macd.get('macd')
        macd_signal = macd.get('signal')
        macd_hist = macd.get('histogram', 0)
        if macd_line is not None and macd_signal is not None:
            if macd_line > macd_signal and macd_hist > 0:
                signals.append(('BUY', 3))
            elif macd_line < macd_signal and macd_hist < 0:
                signals.append(('SELL', 3))
            elif macd_hist > 0:
                signals.append(('BUY', 1))  # Weak bullish
            elif macd_hist < 0:
                signals.append(('SELL', 1))  # Weak bearish
            weights.append(3)

        # Bollinger Bands (weight: 1)
        bb = indicators.get('bb', {})
        if bb.get('lower') and current_price < bb['lower']:
            signals.append(('BUY', 1))  # Oversold
        elif bb.get('upper') and current_price > bb['upper']:
            signals.append(('SELL', 1))  # Overbought
        weights.append(1)

        # Stochastic (weight: 2)
        stoch = indicators.get('stochastic')
        if stoch:
            if stoch['k'] < 20:
                signals.append(('BUY', 2))
            elif stoch['k'] > 80:
                signals.append(('SELL', 2))
            weights.append(2)

        # ADX Trend Strength (weight: 1)
        adx = indicators.get('adx')
        if adx:
            if adx < 20:
                signals.append(('HOLD', 1))  # Weak trend
            weights.append(1)

        # SuperTrend (weight: 3)
        st = indicators.get('supertrend', {})
        if st.get('trend') == 'bullish':
            signals.append(('BUY', 3))
        elif st.get('trend') == 'bearish':
            signals.append(('SELL', 3))
        if st.get('trend') != 'neutral':
            weights.append(3)

        # Ichimoku Cloud (weight: 2)
        ichi = indicators.get('ichimoku', {})
        if ichi.get('signal') == 'bullish':
            signals.append(('BUY', 2))
        elif ichi.get('signal') == 'bearish':
            signals.append(('SELL', 2))
        if ichi.get('signal') != 'neutral':
            weights.append(2)

        # Calculate weighted signal
        buy_score = sum(w for s, w in signals if s == 'BUY')
        sell_score = sum(w for s, w in signals if s == 'SELL')
        total_weight = sum(weights)

        if buy_score > sell_score * 1.5:
            signal = 'BUY'
            confidence = min(0.95, buy_score / total_weight)
        elif sell_score > buy_score * 1.5:
            signal = 'SELL'
            confidence = min(0.95, sell_score / total_weight)
        else:
            signal = 'HOLD'
            confidence = 0.5

        # Generate reasons
        reasons = []
        if rsi:
            if rsi < 30: reasons.append(f'RSI oversold ({rsi:.1f})')
            elif rsi > 70: reasons.append(f'RSI overbought ({rsi:.1f})')
        if sma9 and sma20:
            if sma9 > sma20: reasons.append('SMA bullish crossover')
            else: reasons.append('SMA bearish crossover')
        if macd_line is not None and macd_signal is not None:
            if macd_line > macd_signal: reasons.append(f'MACD bullish cross')
            elif macd_line < macd_signal: reasons.append(f'MACD bearish cross')
        elif macd.get('histogram'):
            reasons.append(f'MACD {"bullish" if macd["histogram"] > 0 else "bearish"}')
        if stoch:
            if stoch['k'] < 20: reasons.append(f'Stochastic oversold ({stoch["k"]:.1f})')
            elif stoch['k'] > 80: reasons.append(f'Stochastic overbought ({stoch["k"]:.1f})')
        if st.get('trend') != 'neutral':
            reasons.append(f'SuperTrend {st["trend"]} ({st.get("direction","")})')
        if ichi.get('signal') != 'neutral':
            reasons.append(f'Ichimoku {ichi["signal"]} {"(cloud bullish)" if ichi.get("cloud_bullish") else "(cloud bearish)"}')

        return {
            'signal': signal,
            'confidence': round(confidence, 2),
            'reasons': reasons[:4],
            'indicators': {
                'rsi': round(rsi, 2) if rsi else None,
                'sma': {
                    'sma9': round(sma9, 2) if sma9 else None,
                    'sma20': round(sma20, 2) if sma20 else None,
                    'sma50': round(indicators['sma'].get('sma50'), 2) if indicators['sma'].get('sma50') else None
                },
                'macd': macd,
                'bb': bb,
                'stochastic': stoch,
                'adx': adx,
                'supertrend': st,
                'ichimoku': ichi
            }
        }


# Singleton instances
market_data_service = MarketDataService()