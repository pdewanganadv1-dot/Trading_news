import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from app.config import settings
from app.services.market_data_service import market_data_service, TechnicalIndicators, TradingSignals
from app.services.real_news import real_news_service
from app.services.social_sentiment import analyze_social_sentiment
from app.services.sentiment import sentiment_analyzer
from app.services.market_edge_service import get_fii_dii_summary, get_fii_dii_history


class AIAgentService:
    def __init__(self):
        self._client = None
        self._model = settings.groq_model
        self._use_llm = bool(settings.groq_api_key)
        if self._use_llm:
            try:
                from groq import Groq
                self._client = Groq(api_key=settings.groq_api_key)
            except Exception:
                self._use_llm = False

    async def analyze_stock(self, symbol: str, days: int = 50) -> Dict:
        symbol = symbol.upper().strip()
        tasks = {
            "price_data": self._get_price_data(symbol, days),
            "news": self._get_news(symbol),
            "social": self._get_social_sentiment(symbol),
            "fiidii": self._get_fii_dii(),
            "technicals": self._get_technicals(symbol, days),
        }
        results = {}
        for key, coro in tasks.items():
            try:
                results[key] = await asyncio.wait_for(coro, timeout=30)
            except Exception as e:
                results[key] = {"error": str(e)}

        verdict = await self._generate_verdict(symbol, results)
        return {
            "symbol": symbol,
            "timestamp": datetime.now().isoformat(),
            "verdict": verdict,
            "data": results,
        }

    async def _get_price_data(self, symbol: str, days: int) -> Dict:
        data = await market_data_service.get_price_data(symbol)
        prices = await market_data_service.get_historical_prices(symbol, days)
        return {"current": data, "history_count": len(prices) if prices else 0, "prices": prices}

    async def _get_news(self, symbol: str) -> Dict:
        try:
            news = await real_news_service.get_stock_news_from_yfinance(symbol)
            if not news:
                news = []
            analyzed = []
            for item in (news or [])[:10]:
                title = item.get("title", "")
                sentiment = sentiment_analyzer._analyze_single(title)
                analyzed.append({"title": title[:100], "sentiment": sentiment, "link": item.get("link", "")})
            return {"count": len(analyzed), "articles": analyzed}
        except Exception as e:
            return {"error": str(e), "count": 0, "articles": []}

    async def _get_social_sentiment(self, symbol: str) -> Dict:
        try:
            social = await analyze_social_sentiment(symbol)
            return social or {"error": "No data"}
        except Exception as e:
            return {"error": str(e)}

    async def _get_fii_dii(self) -> Dict:
        try:
            data = await get_fii_dii_summary()
            history = get_fii_dii_history(5)
            trend = "rising" if len(history) >= 2 and history[-1].get("fii_net", 0) > history[-2].get("fii_net", 0) else "falling" if len(history) >= 2 else "flat"
            return {"current": data, "trend": {"fii_trend": trend}}
        except Exception as e:
            return {"error": str(e)}

    async def _get_technicals(self, symbol: str, days: int) -> Dict:
        try:
            prices = await market_data_service.get_historical_prices(symbol, days)
            if not prices or len(prices) < 20:
                return {"error": "Insufficient price data"}
            closing = [p["close"] for p in prices] if isinstance(prices[0], dict) else prices
            indicators = TechnicalIndicators.calculate_all(closing)
            signal = TradingSignals.generate_signal(closing, closing[-1])
            return {"indicators": indicators, "signal": signal}
        except Exception as e:
            return {"error": str(e)}

    async def _generate_verdict(self, symbol: str, data: Dict) -> Dict:
        context = self._build_context(symbol, data)
        if self._use_llm:
            prompt = (
                f"You are a professional multi-modal trading analyst. Analyze {symbol} using ALL available "
                f"data below and produce a concise trading verdict. Include:\n"
                f"1. Overall directional bias (BULLISH/BEARISH/NEUTRAL) with conviction (HIGH/MEDIUM/LOW)\n"
                f"2. Key drivers from each data source\n"
                f"3. Risk factors\n"
                f"4. Suggested action (BUY/SELL/HOLD/WATCH)\n"
                f"5. Key levels to watch\n\n"
                f"DATA:\n{context}\n\n"
                f"Keep it under 300 words, use plain English, be direct."
            )
            try:
                resp = self._client.chat.completions.create(
                    model=self._model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                )
                analysis = resp.choices[0].message.content.strip()
            except Exception as e:
                analysis = self._template_verdict(symbol, data)
        else:
            analysis = self._template_verdict(symbol, data)

        bias = "BULLISH" if "BULLISH" in analysis.upper()[:200] else "BEARISH" if "BEARISH" in analysis.upper()[:200] else "NEUTRAL"
        action = "BUY" if "BUY" in analysis.upper()[:300] else "SELL" if "SELL" in analysis.upper()[:300] else "HOLD"
        if "WATCH" in analysis.upper()[:300]:
            action = "WATCH"
        conviction = "HIGH" if "HIGH" in analysis.upper()[:200] else "MEDIUM" if "MEDIUM" in analysis.upper()[:200] else "LOW"

        return {
            "analysis": analysis,
            "bias": bias,
            "conviction": conviction,
            "action": action,
            "source": "groq" if self._use_llm else "template",
        }

    def _build_context(self, symbol: str, data: Dict) -> str:
        lines = [f"=== {symbol} ANALYSIS ==="]

        pd = data.get("price_data", {})
        if "current" in pd and pd["current"]:
            c = pd["current"]
            if isinstance(c, dict):
                lines.append(f"\nPRICE: ${c.get('price', 'N/A')} | Change: {c.get('change', 'N/A')}% | "
                             f"High: ${c.get('high', 'N/A')} | Low: ${c.get('low', 'N/A')} | Volume: {c.get('volume', 'N/A')}")

        tech = data.get("technicals", {})
        if "signal" in tech and tech["signal"]:
            s = tech["signal"]
            lines.append(f"\nTECHNICAL SIGNAL: {s.get('signal', 'N/A')} at {s.get('confidence', 0)*100:.0f}% confidence")
            if s.get("reasons"):
                lines.append(f"Reasons: {', '.join(s['reasons'][:5])}")
            ind = tech.get("indicators", {})
            if ind:
                lines.append(f"RSI: {ind.get('rsi', 'N/A')} | MACD: {ind.get('macd', {}).get('macd', 'N/A')} | "
                             f"BB Upper: {ind.get('bb', {}).get('upper', 'N/A')} | Lower: {ind.get('bb', {}).get('lower', 'N/A')}")

        news_d = data.get("news", {})
        if news_d.get("articles"):
            def _get_news_score(article):
                s = article.get("sentiment")
                if isinstance(s, dict):
                    return s.get("score", 0)
                if isinstance(s, (int, float)):
                    return s
                return 0
            bull = sum(1 for a in news_d["articles"] if _get_news_score(a) > 0)
            bear = sum(1 for a in news_d["articles"] if _get_news_score(a) < 0)
            lines.append(f"\nNEWS: {news_d['count']} articles (bullish: {bull}, bearish: {bear})")
            for a in news_d["articles"][:5]:
                title = a.get("title", "")
                if isinstance(title, str):
                    lines.append(f"  - {title}")

        social = data.get("social", {})
        if social and isinstance(social, dict) and "overall" in social:
            lines.append(f"\nSOCIAL SENTIMENT: {social.get('overall', 'N/A')}")

        fii = data.get("fiidii", {})
        if "current" in fii and fii["current"]:
            c = fii["current"]
            lines.append(f"\nFII/DII: FII Net={c.get('fii_net', 'N/A')}Cr, DII Net={c.get('dii_net', 'N/A')}Cr")
        if "trend" in fii and fii["trend"]:
            lines.append(f"FII Trend: {fii['trend'].get('fii_trend', 'N/A')}")

        return "\n".join(lines)

    def _template_verdict(self, symbol: str, data: Dict) -> str:
        tech = data.get("technicals", {}).get("signal", {})
        news_d = data.get("news", {})
        fii = data.get("fiidii", {}).get("current", {})

        bias_parts = []
        if tech and tech.get("signal"):
            if tech["signal"] == "BUY":
                bias_parts.append("technicals bullish")
            elif tech["signal"] == "SELL":
                bias_parts.append("technicals bearish")
        if news_d.get("articles"):
            def _ns(a):
                s = a.get("sentiment")
                if isinstance(s, dict): return s.get("score", 0)
                if isinstance(s, (int, float)): return s
                return 0
            bull = sum(1 for a in news_d["articles"] if _ns(a) > 0)
            bear = sum(1 for a in news_d["articles"] if _ns(a) < 0)
            if bull > bear:
                bias_parts.append("news positive")
            elif bear > bull:
                bias_parts.append("news negative")
        if fii:
            fii_net = fii.get("fii_net", 0) or 0
            if fii_net > 1000:
                bias_parts.append("FII strong buying")
            elif fii_net < -1000:
                bias_parts.append("FII strong selling")

        if not bias_parts:
            return f"NEUTRAL | Conviction: LOW | No strong signals detected for {symbol}. Monitor for clearer setup."

        if len(bias_parts) >= 2 and all("bull" in b or "positive" in b or "buying" in b for b in bias_parts):
            return f"BULLISH | Conviction: HIGH | Multiple confirmations: {', '.join(bias_parts)}. Consider buying on dips."
        if len(bias_parts) >= 2 and all("bear" in b or "negative" in b or "selling" in b for b in bias_parts):
            return f"BEARISH | Conviction: HIGH | Multiple warnings: {', '.join(bias_parts)}. Consider reducing exposure."

        return f"NEUTRAL | Conviction: MEDIUM | Mixed signals: {', '.join(bias_parts)}. Wait for clearer direction."


ai_agent_service = AIAgentService()
