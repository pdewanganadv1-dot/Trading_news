"""Signal Explanation Service — generates natural language explanations for trading signals.

Two modes:
  - LLM mode: Uses Google Gemini API (free tier available, set GEMINI_API_KEY)
  - Template mode: Smart template-based fallback (always works)
"""

import logging
from typing import Dict, Any, List, Optional
from app.config import settings

logger = logging.getLogger(__name__)


def _fmt_price(price: Optional[float]) -> str:
    if price is None:
        return "N/A"
    if price >= 1000:
        return f"${price:,.2f}" if price < 100000 else f"${price:,.0f}"
    return f"${price:.2f}"


def _trend_emoji(t: Optional[str]) -> str:
    if not t:
        return "⚪"
    t = t.lower()
    if t in ("bullish", "buy", "up"):
        return "🟢"
    if t in ("bearish", "sell", "down"):
        return "🔴"
    return "⚪"


class SignalExplainer:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.gemini_api_key
        self.model_name = settings.gemini_model
        self.use_llm = bool(self.api_key)
        if self.use_llm:
            try:
                import google.generativeai as genai
                genai.configure(api_key=self.api_key)
                self.model = genai.GenerativeModel(self.model_name)
                logger.info(f"Gemini client initialized ({self.model_name})")
            except ImportError:
                logger.warning("google-generativeai not installed, using template mode")
                self.use_llm = False
            except Exception as e:
                logger.warning(f"Failed to init Gemini: {e}, using template mode")
                self.use_llm = False

    def explain(
        self,
        symbol: str,
        signal: str,
        confidence: float,
        reasons: List[str],
        indicators: Dict[str, Any],
        mtf: Optional[Dict[str, Any]] = None,
        edge: Optional[Dict[str, Any]] = None,
        sentiment: Optional[Dict[str, Any]] = None,
        price: Optional[float] = None,
    ) -> str:
        if self.use_llm:
            try:
                return self._llm_explain(
                    symbol, signal, confidence, reasons, indicators, mtf, edge, sentiment, price
                )
            except Exception as e:
                logger.warning(f"Gemini explanation failed: {e}, falling back to template")
        return self._template_explain(
            symbol, signal, confidence, reasons, indicators, mtf, edge, sentiment, price
        )

    def _build_context(
        self,
        symbol: str,
        signal: str,
        confidence: float,
        reasons: List[str],
        indicators: Dict[str, Any],
        mtf: Optional[Dict[str, Any]] = None,
        edge: Optional[Dict[str, Any]] = None,
        sentiment: Optional[Dict[str, Any]] = None,
        price: Optional[float] = None,
    ) -> str:
        lines = [f"Symbol: {symbol}", f"Signal: {signal} (confidence: {confidence:.0%})"]
        if price:
            lines.append(f"Price: {_fmt_price(price)}")
        if reasons:
            lines.append(f"Reasons: {', '.join(reasons)}")
        ind = indicators or {}
        if "rsi" in ind:
            lines.append(f"RSI(14): {ind.get('rsi')}")
        sma = ind.get("sma", {})
        if sma:
            parts = []
            for k in ("sma9", "sma20", "sma50"):
                v = sma.get(k)
                if v:
                    parts.append(f"{k}={v}")
            if parts:
                lines.append(f"SMA: {', '.join(parts)}")
        macd = ind.get("macd", {})
        if macd:
            lines.append(f"MACD: line={macd.get('macd')}, sig={macd.get('signal')}, hist={macd.get('histogram')}")
        bb = ind.get("bb", {})
        if bb:
            lines.append(f"BB: upper={bb.get('upper')}, mid={bb.get('middle')}, lower={bb.get('lower')}")
        st = ind.get("supertrend", {})
        if st:
            lines.append(f"SuperTrend: {st.get('trend')} (dir={st.get('direction')})")
        ichi = ind.get("ichimoku", {})
        if ichi:
            lines.append(f"Ichimoku: {ichi.get('signal')} (cloud={ichi.get('cloud_bullish', '?')})")
        stoch = ind.get("stochastic", {})
        if stoch:
            lines.append(f"Stochastic: K={stoch.get('k')}, D={stoch.get('d')}")
        adx = ind.get("adx")
        if adx:
            lines.append(f"ADX: {adx}")
        if mtf:
            tf_parts = []
            for tf, data in mtf.items():
                s = data.get("signal", "?")
                c = data.get("confidence", 0)
                tf_parts.append(f"{tf}={s}({c:.0%})")
            lines.append(f"MTF: {', '.join(tf_parts)}")
        if edge:
            lines.append(f"Edge Score: {edge.get('score', '?')}/10, Vol: {edge.get('vol_ratio', '?')}x, RSI: {edge.get('rsi', '?')}")
        if sentiment:
            lines.append(f"News: {sentiment.get('news', '?')}, Social: {sentiment.get('social', '?')}")
        return "\n".join(lines)

    def _llm_explain(
        self,
        symbol: str,
        signal: str,
        confidence: float,
        reasons: List[str],
        indicators: Dict[str, Any],
        mtf: Optional[Dict[str, Any]] = None,
        edge: Optional[Dict[str, Any]] = None,
        sentiment: Optional[Dict[str, Any]] = None,
        price: Optional[float] = None,
    ) -> str:
        context = self._build_context(symbol, signal, confidence, reasons, indicators, mtf, edge, sentiment, price)
        prompt = (
            "You are a professional trading analyst. Explain this trading signal in 3-4 concise sentences "
            "for a trader. Cover: why the signal fired, key technical drivers, any multi-timeframe alignment "
            "or conflict, and what to watch for. Use plain English, avoid jargon overload. Be direct.\n\n"
            f"{context}"
        )
        resp = self.model.generate_content(prompt)
        return resp.text.strip()

    def _template_explain(
        self,
        symbol: str,
        signal: str,
        confidence: float,
        reasons: List[str],
        indicators: Dict[str, Any],
        mtf: Optional[Dict[str, Any]] = None,
        edge: Optional[Dict[str, Any]] = None,
        sentiment: Optional[Dict[str, Any]] = None,
        price: Optional[float] = None,
    ) -> str:
        ind = indicators or {}
        parts: List[str] = []

        emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}
        header = f"{emoji.get(signal, '')} {signal} — {symbol} ({confidence:.0%} confidence)"
        parts.append(header)

        drivers: List[str] = []

        st = ind.get("supertrend", {})
        if st.get("trend") == "bullish":
            drivers.append(f"SuperTrend {_trend_emoji('bullish')} bullish (trend up)")
        elif st.get("trend") == "bearish":
            drivers.append(f"SuperTrend {_trend_emoji('bearish')} bearish (trend down)")

        macd = ind.get("macd", {})
        macd_line = macd.get("macd")
        macd_signal = macd.get("signal")
        macd_hist = macd.get("histogram", 0)
        if macd_line is not None and macd_signal is not None:
            if macd_line > macd_signal and macd_hist > 0:
                drivers.append(f"MACD {_trend_emoji('bullish')} bullish cross (line above signal)")
            elif macd_line < macd_signal and macd_hist < 0:
                drivers.append(f"MACD {_trend_emoji('bearish')} bearish cross (line below signal)")
            elif macd_hist > 0:
                drivers.append(f"MACD {_trend_emoji('bullish')} bullish (histogram positive)")
            elif macd_hist < 0:
                drivers.append(f"MACD {_trend_emoji('bearish')} bearish (histogram negative)")

        rsi = ind.get("rsi")
        if rsi is not None:
            if rsi < 30:
                drivers.append(f"RSI {_trend_emoji('bullish')} oversold at {rsi:.1f} — potential bounce")
            elif rsi > 70:
                drivers.append(f"RSI {_trend_emoji('bearish')} overbought at {rsi:.1f} — caution")
            else:
                drivers.append(f"RSI neutral at {rsi:.1f} — room to run")

        ichi = ind.get("ichimoku", {})
        if ichi.get("signal") == "bullish":
            drivers.append(f"Ichimoku {_trend_emoji('bullish')} bullish (price above cloud)")
        elif ichi.get("signal") == "bearish":
            drivers.append(f"Ichimoku {_trend_emoji('bearish')} bearish (price below cloud)")

        sma = ind.get("sma", {})
        sma9 = sma.get("sma9")
        sma20 = sma.get("sma20")
        if sma9 and sma20:
            if sma9 > sma20:
                drivers.append(f"SMA {_trend_emoji('bullish')} 9 above 20 (short-term bullish)")
            else:
                drivers.append(f"SMA {_trend_emoji('bearish')} 9 below 20 (short-term bearish)")

        stoch = ind.get("stochastic", {})
        if stoch:
            k = stoch.get("k")
            if k is not None:
                if k < 20:
                    drivers.append(f"Stochastic {_trend_emoji('bullish')} oversold ({k:.1f})")
                elif k > 80:
                    drivers.append(f"Stochastic {_trend_emoji('bearish')} overbought ({k:.1f})")

        if drivers:
            parts.append("")
            parts.append("Key drivers:")
            for d in drivers[:4]:
                parts.append(f"  • {d}")

        if mtf:
            parts.append("")
            aligned = []
            conflicted = []
            for tf, data in mtf.items():
                s = data.get("signal", "?")
                e = _trend_emoji(s)
                c = data.get("confidence", 0)
                label = f"{e} {tf} {s} ({c:.0%})"
                if s == signal:
                    aligned.append(label)
                else:
                    conflicted.append(label)
            if aligned:
                parts.append(f"MTF alignment: {' | '.join(aligned)}")
            if conflicted:
                parts.append(f"MTF conflict: {' | '.join(conflicted)}")

        if edge and edge.get("score") is not None:
            parts.append("")
            score = edge.get("score", 0)
            vol = edge.get("vol_ratio", "?")
            rsi_e = edge.get("rsi", "?")
            signals = edge.get("signals", [])
            parts.append(f"Edge scan: score {score}/10, vol {vol}x avg, RSI daily {rsi_e}")
            if signals:
                parts.append(f"  {'; '.join(signals[:3])}")

        if sentiment:
            parts.append("")
            news_s = sentiment.get("news", "?")
            social_s = sentiment.get("social", "?")
            news_e = _trend_emoji(news_s)
            social_e = _trend_emoji(social_s)
            parts.append(f"Sentiment: News {news_e} {news_s} | Social {social_e} {social_s}")

        return "\n".join(parts)


signal_explainer = SignalExplainer()
