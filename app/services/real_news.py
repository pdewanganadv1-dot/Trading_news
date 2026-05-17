from typing import List, Dict, Any
from datetime import datetime
import httpx
import xml.etree.ElementTree as ET
import asyncio
import re


class RealNewsService:
    """Service for fetching real news from multiple sources."""

    _cache: Dict = {}
    _cache_ttl = 45  # seconds

    def __init__(self):
        self.session = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )

    async def get_all_news(self) -> List[Dict]:
        now = datetime.now().timestamp()
        if self._cache.get("all_news") and (now - self._cache.get("all_news_ts", 0)) < self._cache_ttl:
            return self._cache["all_news"]
        news = await self._fetch_all_news()
        self._cache["all_news"] = news
        self._cache["all_news_ts"] = now
        return news

    async def _fetch_all_news(self) -> List[Dict]:
        """Fetch news from ALL sources across all categories."""
        all_news = []

        results = await asyncio.gather(
            self.get_crypto_news(),
            self.get_metals_news(),
            self.get_forex_news(),
            self.get_stocks_news(),
            self.get_commodities_news(),
            return_exceptions=True
        )

        for result in results:
            if isinstance(result, list):
                all_news.extend(result)

        all_news.sort(key=lambda x: x.get('published_at', ''), reverse=True)

        return all_news[:50]

    async def get_crypto_news(self) -> List[Dict]:
        """Fetch crypto news from multiple sources."""
        news = []
        sources = [
            ("CoinTelegraph", "https://cointelegraph.com/rss"),
            ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/news/"),
            ("CryptoSlate", "https://cryptoslate.com/feed/"),
            ("Bitcoinist", "https://bitcoinist.com/feed/"),
            ("Decrypt", "https://decrypt.co/feed"),
            ("The Block", "https://www.theblock.co/rss.xml"),
            ("Blockworks", "https://blockworks.co/feed/"),
        ]

        tasks = [self._fetch_rss(url, name) for name, url in sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, list):
                news.extend(result)

        # Also try CoinGecko API
        try:
            response = await self.session.get("https://api.coingecko.com/api/v3/news", timeout=10)
            if response.status_code == 200:
                data = response.json()
                for item in data.get("data", [])[:10]:
                    news.append({
                        "title": item.get("title", ""),
                        "description": item.get("description", "")[:200] if item.get("description") else "",
                        "url": item.get("url", ""),
                        "source": item.get("news_site", "CoinGecko"),
                        "published_at": item.get("published_at", datetime.utcnow().isoformat()),
                        "category": "crypto"
                    })
        except Exception as e:
            print(f"CoinGecko API error: {e}")

        return news[:25]

    async def get_metals_news(self) -> List[Dict]:
        """Fetch precious metals news (gold, silver) from multiple sources."""
        news = []
        sources = [
            ("Kitco", "https://www.kitco.com/rss/newsatest.rss"),
            ("GoldBroker", "https://www.goldbroker.com/feed"),
            ("SilverDoctors", "https://www.silverdoctors.com/feed/"),
            ("Gold-Eagle", "https://www.gold-eagle.com/rss"),
            ("SchiffGold", "https://schiffgold.com/feed/"),
            ("MisesInstitute", "https://mises.org/rss/latest.xml"),
            ("CaseyResearch", "https://caseyresearch.com/feed/"),
        ]

        tasks = [self._fetch_rss(url, name) for name, url in sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, list):
                news.extend(result)

        return news[:20]

    async def get_forex_news(self) -> List[Dict]:
        """Fetch forex news from multiple sources."""
        news = []
        sources = [
            ("ForexLive", "https://www.forexlive.com/feed/news"),
            ("FXStreet", "https://www.fxstreet.com/rss/news"),
            ("DailyFX", "https://www.dailyfx.com/rss/market_news"),
            ("Investopedia", "https://www.investopedia.com/feedbuilder/feed/getfeed?feedName=rss_headline&count=30"),
            ("BabyPips", "https://www.babypips.com/feed"),
        ]

        tasks = [self._fetch_rss(url, name) for name, url in sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, list):
                news.extend(result)

        return news[:15]

    async def get_stock_news_from_yfinance(self, ticker: str) -> List[Dict]:
        """Fetch ticker-specific news from Yahoo Finance."""
        try:
            import yfinance as yf
            stock = yf.Ticker(ticker)
            news = stock.news
            if not news:
                return []
            items = []
            for item in news[:10]:
                title = item.get("title", "")
                link = item.get("link", "")
                desc = item.get("summary", "")[:200] if item.get("summary") else ""
                pub = datetime.fromtimestamp(item.get("providerPublishTime", 0)).isoformat() if item.get("providerPublishTime") else ""
                items.append({
                    "title": title,
                    "description": desc,
                    "url": link,
                    "source": "Yahoo Finance",
                    "published_at": pub,
                    "category": "stocks"
                })
            return items
        except Exception as e:
            print(f"YFinance news error for {ticker}: {e}")
            return []

    async def get_stocks_news(self) -> List[Dict]:
        """Fetch stocks/market news from global + Indian sources."""
        news = []
        sources = [
            ("YahooFinance", "https://finance.yahoo.com/news/rssindex"),
            ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories/"),
            ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
            ("Reuters", "https://www.reutersagency.com/feed/?taxonomy=best-topics&post_type=best"),
            ("Bloomberg", "https://feeds.bloomberg.com/markets/news.rss"),
            ("SeekingAlpha", "https://seekingalpha.com/feed.xml"),
            # Indian stock news sources
            ("EconomicTimes", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
            ("Moneycontrol", "https://www.moneycontrol.com/rss/business.xml"),
            ("NDTVProfit", "https://feeds.feedburner.com/ndtvprofit-latest"),
            ("BusinessStandard", "https://www.business-standard.com/rss/markets-101.rss"),
            ("Livemint", "https://www.livemint.com/rss/markets"),
        ]

        tasks = [self._fetch_rss(url, name) for name, url in sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, list):
                news.extend(result)

        return news[:25]

    async def get_commodities_news(self) -> List[Dict]:
        """Fetch commodities news (oil, gas, etc.) from multiple sources."""
        news = []
        sources = [
            ("OilPrice", "https://oilprice.com/feed/"),
            ("EnergyVoice", "https://www.energyvoice.com/feed/"),
            ("Commodities", "https://www.commodities-news.com/feed/"),
            ("Mining", "https://www.mining.com/feed/"),
            ("OilGas", "https://oilgas.net/feed/"),
        ]

        tasks = [self._fetch_rss(url, name) for name, url in sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, list):
                news.extend(result)

        return news[:15]

    async def _fetch_rss(self, url: str, source_name: str) -> List[Dict]:
        """Fetch and parse RSS/Atom feed."""
        try:
            response = await self.session.get(url)
            response.raise_for_status()

            try:
                root = ET.fromstring(response.text)
            except ET.ParseError:
                return []

            items = []
            # Try RSS format
            for item in root.findall(".//item")[:10]:
                title = item.findtext("title", "")
                desc_elem = item.find("description")
                description = ""
                if desc_elem is not None and desc_elem.text:
                    description = re.sub(r'<[^>]+>', '', desc_elem.text)[:300]

                link = item.findtext("link", "")
                pub_date = item.findtext("pubDate", "") or datetime.utcnow().isoformat()

                items.append({
                    "title": title.strip(),
                    "description": description.strip(),
                    "url": link.strip() if isinstance(link, str) else "",
                    "source": source_name.upper(),
                    "published_at": pub_date,
                    "category": self._detect_category(title, description)
                })

            # Try Atom format if no RSS items
            if not items:
                for entry in root.findall(".//entry")[:10]:
                    title = entry.findtext("title", "")
                    desc = entry.findtext("summary", "") or entry.findtext("content", "")
                    if desc:
                        desc = re.sub(r'<[^>]+>', '', desc)[:300]

                    link_elem = entry.find("link")
                    link = link_elem.get("href", "") if link_elem is not None else ""
                    pub_date = entry.findtext("published", "") or datetime.utcnow().isoformat()

                    items.append({
                        "title": title.strip(),
                        "description": desc.strip() if desc else "",
                        "url": link.strip(),
                        "source": source_name.upper(),
                        "published_at": pub_date,
                        "category": self._detect_category(title, desc or "")
                    })

            return items

        except Exception as e:
            print(f"RSS fetch error ({source_name} - {url}): {e}")
            return []

    def _detect_category(self, title: str, description: str) -> str:
        """Auto-detect news category from content."""
        text = (title + " " + description).lower()

        # Crypto keywords
        crypto_keywords = ["bitcoin", "btc", "ethereum", "eth", "crypto", "blockchain", "defi", "nft", "solana", "xrp", "cardano", "dogecoin", "binance", "coinbase"]
        if any(k in text for k in crypto_keywords):
            return "crypto"

        # Gold/Silver keywords
        metal_keywords = ["gold", "silver", "xau", "xag", "precious metal", "troy ounce", "goldman", "gold price"]
        if any(k in text for k in metal_keywords):
            return "metals"

        # Forex keywords
        forex_keywords = ["forex", "eur/usd", "gbp/usd", "usd/jpy", "dollar", "euro", "pound", "yen", "central bank", "fed", "ecb", "boe"]
        if any(k in text for k in forex_keywords):
            return "forex"

        # Oil/Commodities keywords
        commodity_keywords = ["oil", "crude", "opec", "nat gas", "natural gas", "copper", "wheat", "corn", "coffee"]
        if any(k in text for k in commodity_keywords):
            return "commodities"

        # Default to stocks/market
        return "stocks"

    def get_market_sentiment(self, news: List[Dict]) -> Dict[str, Any]:
        """Analyze sentiment of news items."""
        bullish_keywords = [
            "surge", "surges", "soar", "soars", "rallies", "rally", "gain", "gains", "jump", "jumps",
            "rise", "rises", "rising", "higher", "growth", "bullish", "positive", "optimistic",
            "record", "highs", "all-time", " ATH ", "strong", "stronger", "institutional", "adoption",
            "boom", "booming", "breakthrough", "upgrade", "approval", "approval", "bull run", "moon"
        ]
        bearish_keywords = [
            "fall", "falls", "drop", "drops", "crash", "crashes", "plunge", "plunges", "decline", "declines",
            "bearish", "negative", "pessimistic", "risk", "risks", "fear", "volatile", "volatility",
            "dump", "dumping", "slump", "slumps", "weak", "weaker", "selling", "pressure",
            "crisis", "lose", "loss", "bankruptcy", "hack", "scam", "regulation", "ban", "warning"
        ]

        bullish_count = 0
        bearish_count = 0
        neutral_count = 0
        key_bullish = []
        key_bearish = []

        for item in news:
            title = item.get('title', '')
            desc = item.get('description', '')
            text = (title + ' ' + desc).lower()

            b_count = sum(1 for k in bullish_keywords if k in text)
            br_count = sum(1 for k in bearish_keywords if k in text)

            if b_count > br_count:
                bullish_count += b_count
                if len(key_bullish) < 5 and len(title) > 10:
                    key_bullish.append(title)
            elif br_count > b_count:
                bearish_count += br_count
                if len(key_bearish) < 5 and len(title) > 10:
                    key_bearish.append(title)
            else:
                neutral_count += 1

        total = bullish_count + bearish_count + neutral_count
        if total == 0:
            total = 1

        bullish_pct = round(bullish_count / total * 100) if bullish_count > 0 else 20
        bearish_pct = round(bearish_count / total * 100) if bearish_count > 0 else 20
        neutral_pct = max(0, 100 - bullish_pct - bearish_pct)

        return {
            "bullish_pct": bullish_pct,
            "bearish_pct": bearish_pct,
            "neutral_pct": neutral_pct,
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "neutral_count": neutral_count,
            "sentiment": "bullish" if bullish_count > bearish_count else "bearish" if bearish_count > bullish_count else "neutral",
            "key_bullish": key_bullish,
            "key_bearish": key_bearish,
            "total_articles": len(news)
        }


real_news_service = RealNewsService()