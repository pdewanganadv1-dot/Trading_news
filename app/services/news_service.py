from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from enum import Enum
import httpx
import xml.etree.ElementTree as ET


class NewsSource(Enum):
    FINNHUB = "finnhub"
    ALPHA_VANTAGE = "alpha_vantage"
    NEWSAPI = "newsapi"
    RSS = "rss"


class NewsItem:
    def __init__(
        self,
        title: str,
        description: str,
        url: str,
        source: str,
        published_at: datetime,
        symbols: Optional[List[str]] = None
    ):
        self.title = title
        self.description = description
        self.url = url
        self.source = source
        self.published_at = published_at
        self.symbols = symbols or []


class NewsService:
    """Service for fetching financial news from various sources."""

    def __init__(self):
        self.session = httpx.AsyncClient(timeout=30.0)
        self._news_cache: List[NewsItem] = []
        self._last_fetch: Optional[datetime] = None

    async def get_market_news(self, category: str = "general") -> List[NewsItem]:
        """Get general market news."""
        # Placeholder - requires API key configuration
        return self._get_sample_news()

    async def get_symbol_news(self, symbol: str) -> List[NewsItem]:
        """Get news specific to a symbol."""
        return self._get_sample_news(symbol=symbol)

    async def get_crypto_news(self) -> List[NewsItem]:
        """Get cryptocurrency news."""
        return self._get_sample_news(category="crypto")

    async def get_forex_news(self) -> List[NewsItem]:
        """Get forex news."""
        return self._get_sample_news(category="forex")

    async def get_commodities_news(self) -> List[NewsItem]:
        """Get commodities news (gold, silver, oil, etc.)."""
        return self._get_sample_news(category="commodities")

    def _get_sample_news(self, symbol: Optional[str] = None, category: str = "general") -> List[NewsItem]:
        """Return sample news for demonstration."""
        sample_news = [
            NewsItem(
                title="Bitcoin Surges Past $68,000 as Institutional Interest Grows",
                description="Major financial institutions continue to show increased interest in Bitcoin, driving prices to new monthly highs.",
                url="https://example.com/news/1",
                source="CryptoNews",
                published_at=datetime.utcnow() - timedelta(hours=1),
                symbols=["BTC", "ETH"]
            ),
            NewsItem(
                title="Gold Reaches All-Time High Amid Geopolitical Tensions",
                description="Safe-haven demand pushes gold to record levels as investors seek protection from global uncertainties.",
                url="https://example.com/news/2",
                source="MarketWatch",
                published_at=datetime.utcnow() - timedelta(hours=2),
                symbols=["XAU", "GOLD"]
            ),
            NewsItem(
                title="Federal Reserve Signals Potential Rate Cuts in 2024",
                description="Fed officials indicate they may begin cutting interest rates later this year if inflation continues to moderate.",
                url="https://example.com/news/3",
                source="Reuters",
                published_at=datetime.utcnow() - timedelta(hours=3),
                symbols=["USD", "EUR"]
            ),
            NewsItem(
                title="Silver Demand Surges Due to Industrial Use and Investment",
                description="Silver prices rally as both industrial demand and investment interest reach multi-year highs.",
                url="https://example.com/news/4",
                source="Commodities Today",
                published_at=datetime.utcnow() - timedelta(hours=4),
                symbols=["XAG", "SILVER"]
            ),
            NewsItem(
                title="Apple Reports Strong Quarterly Earnings, Beats Estimates",
                description="Apple Inc. reports Q4 earnings exceeding analyst expectations driven by services revenue growth.",
                url="https://example.com/news/5",
                source="Bloomberg",
                published_at=datetime.utcnow() - timedelta(hours=5),
                symbols=["AAPL"]
            ),
            NewsItem(
                title="Oil Prices Stabilize After OPEC+ Production Cuts Extended",
                description="Crude oil prices find support as major producers agree to maintain production limits.",
                url="https://example.com/news/6",
                source="Energy News",
                published_at=datetime.utcnow() - timedelta(hours=6),
                symbols=["OIL", "CL"]
            ),
        ]

        if symbol:
            return [n for n in sample_news if symbol.upper() in n.symbols] or sample_news[:2]
        return sample_news

    async def fetch_rss_feed(self, url: str) -> List[NewsItem]:
        """Fetch and parse RSS feed."""
        try:
            response = await self.session.get(url)
            root = ET.fromstring(response.text)
            items = []
            for item in root.findall(".//item")[:10]:
                title = item.findtext("title", "")
                description = item.findtext("description", "")[:200]
                link = item.findtext("link", "")
                pub_date = item.findtext("pubDate", "")
                try:
                    pub_dt = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %z")
                except:
                    pub_dt = datetime.utcnow()
                items.append(NewsItem(
                    title=title,
                    description=description,
                    url=link,
                    source="RSS Feed",
                    published_at=pub_dt
                ))
            return items
        except Exception:
            return []


class AlertService:
    """Service for managing news-based alerts."""

    def __init__(self):
        self.alerts: Dict[str, List[Dict[str, Any]]] = {
            "price": [],
            "news": [],
            "technical": []
        }

    def create_price_alert(
        self,
        symbol: str,
        condition: str,
        value: float,
        callback_url: str
    ) -> Dict[str, Any]:
        alert = {
            "id": len(self.alerts["price"]) + 1,
            "symbol": symbol,
            "condition": condition,
            "value": value,
            "callback_url": callback_url,
            "active": True,
            "created_at": datetime.utcnow().isoformat()
        }
        self.alerts["price"].append(alert)
        return alert

    def create_news_alert(
        self,
        keywords: List[str],
        callback_url: str
    ) -> Dict[str, Any]:
        alert = {
            "id": len(self.alerts["news"]) + 1,
            "keywords": keywords,
            "callback_url": callback_url,
            "active": True,
            "created_at": datetime.utcnow().isoformat()
        }
        self.alerts["news"].append(alert)
        return alert

    def get_alerts(self, alert_type: str = "all") -> Dict[str, List]:
        if alert_type == "all":
            return self.alerts
        return {alert_type: self.alerts.get(alert_type, [])}

    def delete_alert(self, alert_type: str, alert_id: int) -> bool:
        if alert_type in self.alerts:
            self.alerts[alert_type] = [a for a in self.alerts[alert_type] if a["id"] != alert_id]
            return True
        return False


news_service = NewsService()
alert_service = AlertService()