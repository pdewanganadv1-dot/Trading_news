from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum
from app.services.social_sentiment import analyze_social_sentiment


class Sentiment(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class SentimentAnalysis:
    """News sentiment analysis for trading signals."""

    # Keywords for sentiment detection
    BULLISH_KEYWORDS = [
        "surge", "surges", "soar", "soars", "rally", "rallies", "gain", "gains",
        "rise", "rises", "up", "higher", "high", "growth", "growing", "bullish",
        "positive", "optimistic", "institutional", "adoption", "breakthrough",
        "record", "highs", "new high", "boom", "booming", "strong", "strength"
    ]

    BEARISH_KEYWORDS = [
        "fall", "falls", "drop", "drops", "crash", "crashes", "plunge", "plunges",
        "decline", "declines", "down", "lower", "low", "bearish", "negative",
        "pessimistic", "sell", "selling", "pressure", "crisis", "risk", "fear",
        "volatile", "volatility", "dump", "dumping", "slump", "slumps", "weak"
    ]

    # Symbol-specific keywords
    CRYPTO_BULLISH = ["institutional", "etf", "adoption", "upgrade", "halving", "spot"]
    CRYPTO_BEARISH = ["regulation", "ban", "hack", "scam", "China", "sec"]

    GOLD_BULLISH = ["geopolitical", "tension", "inflation", "safe-haven", "central bank"]
    GOLD_BEARISH = ["rate hike", "dollar strength", "tapering"]

    async def analyze_news_sentiment(self, news_items: List[Dict[str, Any]], symbol: Optional[str] = None) -> Dict[str, Any]:
        """Analyze sentiment of news items and return aggregated signal, optionally with social sentiment."""
        result = self._analyze_news_only(news_items)

        if symbol:
            social = await analyze_social_sentiment(symbol)
            result["social"] = social

            combined_bullish = result["bullish_count"] + social["total_bullish"]
            combined_bearish = result["bearish_count"] + social["total_bearish"]

            if combined_bullish > combined_bearish:
                result["sentiment"] = "bullish"
            elif combined_bearish > combined_bullish:
                result["sentiment"] = "bearish"
            else:
                result["sentiment"] = "neutral"

        return result

    def _analyze_news_only(self, news_items: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not news_items:
            return {
                "sentiment": Sentiment.NEUTRAL.value,
                "score": 0,
                "bullish_count": 0,
                "bearish_count": 0,
                "neutral_count": 0,
                "summary": "No news available"
            }

        bullish_count = 0
        bearish_count = 0
        neutral_count = 0
        total_score = 0

        for item in news_items:
            title = item.get("title", "").lower()
            desc = item.get("description", "").lower()
            text = f"{title} {desc}"

            sentiment, score = self._analyze_single(text)
            if sentiment == Sentiment.BULLISH:
                bullish_count += 1
                total_score += score
            elif sentiment == Sentiment.BEARISH:
                bearish_count += 1
                total_score -= score
            else:
                neutral_count += 1

        if bullish_count > bearish_count + 1:
            overall = Sentiment.BULLISH
        elif bearish_count > bullish_count + 1:
            overall = Sentiment.BEARISH
        else:
            overall = Sentiment.NEUTRAL

        return {
            "sentiment": overall.value,
            "score": total_score,
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "neutral_count": neutral_count,
            "total_news": len(news_items),
            "summary": self._generate_summary(bullish_count, bearish_count, total_score)
        }

    def _analyze_single(self, text: str) -> tuple[Sentiment, int]:
        """Analyze sentiment of a single news item."""
        bullish_score = 0
        bearish_score = 0

        for keyword in self.BULLISH_KEYWORDS:
            if keyword in text:
                bullish_score += 1
        for keyword in self.BEARISH_KEYWORDS:
            if keyword in text:
                bearish_score += 1

        if bullish_score > bearish_score:
            return Sentiment.BULLISH, bullish_score
        elif bearish_score > bullish_score:
            return Sentiment.BEARISH, bearish_score
        return Sentiment.NEUTRAL, 0

    def _generate_summary(self, bullish: int, bearish: int, score: int) -> str:
        """Generate human-readable summary."""
        if bullish > bearish + 2:
            return f"Strong bullish sentiment ({bullish} positive vs {bearish} negative)"
        elif bullish > bearish:
            return f"Mildly bullish ({bullish} positive vs {bearish} negative)"
        elif bearish > bullish + 2:
            return f"Strong bearish sentiment ({bearish} negative vs {bullish} positive)"
        elif bearish > bullish:
            return f"Mildly bearish ({bearish} negative vs {bullish} positive)"
        return "Mixed/neutral sentiment"

    def get_symbol_sentiment(self, symbol: str, news_items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Get sentiment specific to a symbol."""
        # Filter news relevant to symbol
        relevant_news = []
        symbol_upper = symbol.upper()

        symbol_keywords = {
            "BTC": ["bitcoin", "btc", "crypto"],
            "ETH": ["ethereum", "eth", "crypto"],
            "XAU": ["gold", "gold", "gold"],
            "XAG": ["silver", "silver", "silver"],
            "EUR": ["euro", "forex", "fx"],
            "AAPL": ["apple", "aapl"]
        }

        keywords = symbol_keywords.get(symbol_upper, [symbol.lower()])

        for item in news_items:
            text = f"{item.get('title', '')} {item.get('description', '')}".lower()
            if any(kw in text for kw in keywords):
                relevant_news.append(item)

        if not relevant_news:
            return self.analyze_news_sentiment(news_items)

        return self.analyze_news_sentiment(relevant_news)


class RealTimeMonitor:
    """Real-time news monitoring with sentiment tracking."""

    def __init__(self):
        self.sentiment_service = SentimentAnalysis()
        self._last_update: datetime = datetime.utcnow()
        self._cache: Dict[str, Any] = {}

    async def get_market_sentiment(self, news_items: List[Dict[str, Any]], symbol: str = "BTC") -> Dict[str, Any]:
        """Get overall market sentiment with social data."""
        return await self.sentiment_service.analyze_news_sentiment(news_items, symbol)

    async def get_symbol_sentiment(self, symbol: str, news_items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Get symbol-specific sentiment with social data."""
        return await self.sentiment_service.analyze_news_sentiment(news_items, symbol)

    def generate_trading_signal(self, sentiment: Dict[str, Any]) -> Dict[str, Any]:
        """Generate trading signal based on sentiment."""
        score = sentiment.get("score", 0)
        sentiment_val = sentiment.get("sentiment", "neutral")

        if score >= 2 or sentiment_val == "bullish":
            signal = "BUY"
            confidence = min(0.9, 0.5 + abs(score) * 0.1)
        elif score <= -2 or sentiment_val == "bearish":
            signal = "SELL"
            confidence = min(0.9, 0.5 + abs(score) * 0.1)
        else:
            signal = "HOLD"
            confidence = 0.5

        return {
            "signal": signal,
            "confidence": round(confidence, 2),
            "sentiment": sentiment_val,
            "reason": sentiment.get("summary", ""),
            "timestamp": datetime.utcnow().isoformat()
        }


sentiment_analyzer = SentimentAnalysis()
sentiment_monitor = RealTimeMonitor()