from typing import Dict, List, Optional, Set
from datetime import datetime
import httpx
import xml.etree.ElementTree as ET
import asyncio
import re
import json

_COMPANY_NAMES: Dict[str, List[str]] = {
    "abb": ["abb india", "abb"],
    "abbotindia": ["abbott india", "abbott"],
    "adanient": ["adani enterprises", "adanient"],
    "adanigreen": ["adani green", "adanigreen"],
    "adaniports": ["adani ports", "adaniports", "adani ports & sez"],
    "adanipower": ["adani power", "adanipower"],
    "adanitrans": ["adani transmission", "adanitrans"],
    "alkem": ["alkem", "alkem laboratories"],
    "ambujacem": ["ambuja cement", "ambuja"],
    "angelone": ["angel one", "angel broking"],
    "apollohosp": ["apollo hospitals", "apollo hospital"],
    "asianpaint": ["asian paints"],
    "atgl": ["aarti industries", "atgl"],
    "auropharma": ["auro pharma", "auro pharma"],
    "axisbank": ["axis bank"],
    "bajaj-auto": ["bajaj auto"],
    "bajfinance": ["bajaj finance"],
    "bajajfinsv": ["bajaj finserv"],
    "bankbaroda": ["bank of baroda", "baroda"],
    "bataindia": ["bata india", "bata"],
    "bel": ["bharat electronics", "bel"],
    "bergepaint": ["berger paints", "berger"],
    "bharatforg": ["bharat forge"],
    "bhartiartl": ["airtel", "bharti airtel", "bhartiartl"],
    "boschltd": ["bosch", "bosch india"],
    "bpcl": ["bpcl", "bharat petroleum"],
    "britannia": ["britannia"],
    "cadilahc": ["cadila healthcare", "cadila", "zydus cadila"],
    "canbk": ["canara bank"],
    "cholafin": ["chola finance", "chola"],
    "cipla": ["cipla"],
    "coalindia": ["coal india"],
    "colpal": ["colgate palmolive", "colgate"],
    "concor": ["container corporation", "concor"],
    "crompton": ["crompton greaves", "crompton"],
    "cumminsind": ["cummins india", "cummins"],
    "dabur": ["dabur"],
    "divislab": ["divis laboratories", "divis"],
    "dlf": ["dlf"],
    "drreddy": ["dr reddy", "dr reddys", "drreddy"],
    "eichermot": ["eicher motors", "eicher"],
    "exideind": ["exide industries", "exide"],
    "federalbnk": ["federal bank"],
    "gail": ["gail", "gail india"],
    "godrejcp": ["godrej consumer", "godrej"],
    "godrejprop": ["godrej properties", "godrej prop"],
    "grasim": ["grasim"],
    "gujgasltd": ["gujarat gas", "gujgas"],
    "hal": ["hal", "hindustan aeronautics"],
    "havells": ["havells"],
    "hcltech": ["hcl technologies", "hcl tech", "hcl"],
    "hdfcbank": ["hdfc bank", "hdfc"],
    "hdfclife": ["hdfc life", "hdfc life insurance"],
    "heromotoco": ["hero motocorp", "hero moto", "hero"],
    "hindalco": ["hindalco", "hindalco industries"],
    "hindcopper": ["hindustan copper", "hindcopper"],
    "hindunilvr": ["hindustan unilever", "hul", "hindunilvr"],
    "hindzinc": ["hindustan zinc", "hindzinc"],
    "icicibank": ["icici bank", "icici"],
    "indusindbk": ["indusind bank", "indusind"],
    "infy": ["infosys", "infy"],
    "ioc": ["ioc", "indian oil", "indian oil corporation"],
    "irctc": ["irctc"],
    "irfc": ["irfc"],
    "itc": ["itc"],
    "jiofin": ["jio financial", "jiofin"],
    "jswenergy": ["jsw energy"],
    "jswsteel": ["jsw steel"],
    "jublfood": ["jubilant foodworks", "jubilant foods", "dominos india"],
    "kotakbank": ["kotak mahindra", "kotak"],
    "lici": ["lic", "lic india", "lici"],
    "lodha": ["lodha", "macrotech"],
    "lt": ["larsen & toubro", "larsen and toubro", "l&t"],
    "m&m": ["mahindra & mahindra", "mahindra"],
    "marico": ["marico"],
    "maruti": ["maruti suzuki", "maruti"],
    "mcdowell-n": ["mcdowell", "united spirits"],
    "motherson": ["motherson sumi", "motherson"],
    "mphasis": ["mphasis"],
    "mrf": ["mrf"],
    "muthootfin": ["muthoot finance", "muthoot"],
    "nationalum": ["national aluminium", "nalco"],
    "naukri": ["naukri", "info edge"],
    "nestleind": ["nestle india", "nestle"],
    "nhpc": ["nhpc"],
    "ntpc": ["ntpc"],
    "oil": ["oil india", "oil"],
    "ongc": ["ongc", "oil & natural gas"],
    "pageind": ["page industries", "page"],
    "pel": ["pricol", "pel"],
    "pfc": ["pfc", "power finance"],
    "pidilitind": ["pidilite", "pidilitind"],
    "pnb": ["pnb", "punjab national bank"],
    "polycab": ["polycab"],
    "poonawalla": ["poonawalla fincorp", "poonawalla"],
    "powergrid": ["powergrid", "power grid"],
    "pvrinox": ["pvr inox", "pvr"],
    "ramcocem": ["ramco cement", "ramco"],
    "recltd": ["rec", "rec ltd", "recltd"],
    "sbicard": ["sbi card"],
    "sbilife": ["sbi life", "sbi life insurance"],
    "sbin": ["sbi", "state bank of india"],
    "shreecem": ["shree cement"],
    "siemens": ["siemens india", "siemens"],
    "srtransfin": ["srei", "srtransfin"],
    "sunpharma": ["sun pharma", "sun pharmaceutical"],
    "suntv": ["sun tv", "sun network"],
    "syngene": ["syngene"],
    "tatacomm": ["tata communications", "tata comm"],
    "tataconsum": ["tata consumer", "tata consumer products"],
    "tataelxsi": ["tata elxsi"],
    "tatamotors": ["tata motors"],
    "tatapower": ["tata power"],
    "tatasteel": ["tata steel"],
    "tcs": ["tcs", "tata consultancy services"],
    "techm": ["tech mahindra", "techm"],
    "titan": ["titan"],
    "torntpharm": ["torrent pharma", "torrent pharmaceuticals"],
    "trent": ["trent"],
    "tvsmotor": ["tvs motor", "tvs"],
    "ubl": ["united breweries", "ubl"],
    "ultracemco": ["ultratech cement", "ultratech"],
    "vbl": ["varun beverages", "vbl"],
    "vedl": ["vedanta", "vedl"],
    "wipro": ["wipro"],
    "zomato": ["zomato", "blinkit"],
    "zyduslife": ["zydus lifesciences", "zydus"],
    "yesbank": ["yes bank"],
    "sail": ["sail", "steel authority of india"],
    "bse": ["bse", "bse india", "bombay stock exchange"],
    "bandhanbnk": ["bandhan bank", "bandhan"],
    "castrol": ["castrol india", "castrol"],
    "biocon": ["biocon"],
    "idfcfirstb": ["idfc first bank", "idfc"],
    "petronet": ["petronet lng", "petronet"],
    "tatachem": ["tata chemicals"],
    "thermax": ["thermax"],
    "voltas": ["voltas"],
    "tatacoffee": ["tata coffee"],
    "torrentpow": ["torrent power"],
    "ujjivan": ["ujjivan"],
    "unionbank": ["union bank of india"],
    "hindpetro": ["hpcl", "hindustan petroleum"],
    "chambalfert": ["chambal fertilizers", "chambal"],
    "navin": ["navin fluorin", "navin"],
    "gvk": ["gvk"],
    "abcap": ["aditya birla capital", "abcap"],
    "abfrl": ["aditya birla fashion", "abfrl"],
    "adanienergy": ["adani energy", "adani electricity"],
}

_POLITICAL_KEYWORDS = [
    "parliament", "lok sabha", "rajya sabha", "mp", "mps", "minister",
    "government", "ministry", "policy", "regulation", "bill", "amendment",
    "budget", "finance bill", "parliamentary committee", "standing committee",
    "question hour", "zero hour", "debate", "legislation", "cabinet",
    "union budget", "economic survey", "finance commission",
    "prs india", "prs legislative", "parliament session",
    "cag", "comptroller", "auditor general", "parliamentary panel",
    "select committee", "joint committee", "parliamentary probe",
    "investigation", "inquiry", "panel", "review committee",
    "subsidy", "tariff", "duty", "tax", "gst", "customs",
    "defence", "procurement", "import", "export", "trade policy",
    "disinvestment", "privatisation", "psu", "public sector",
    "banking", "insurance", "pension", "sebi", "rbi", "irda",
    "agriculture", "farm", "farmer", "food security",
    "mining", "coal", "oil", "gas", "petroleum", "renewable",
    "railways", "highway", "infrastructure", "road",
    "defence", "security", "border", "army", "navy", "air force",
    "electric vehicle", "ev", "solar", "wind energy", "green energy",
    "startup", "fintech", "digital", "data protection",
    "prison", "judicial", "supreme court", "high court",
]

_POLITICAL_SOURCES = [
    ("ET Parliament", "https://economictimes.indiatimes.com/news/news-by-industry/et-commentary/rssfeeds/1715247553.cms"),
    ("ET Politics", "https://economictimes.indiatimes.com/news/politics/nation/rssfeeds/13357242.cms"),
    ("Mint Politics", "https://www.livemint.com/rss/politics"),
    ("NDTV India", "https://feeds.feedburner.com/ndtvnews-india-news"),
    ("Business Standard Politics", "https://www.business-standard.com/rss/politics-103.rss"),
    ("The Hindu Politics", "https://www.thehindu.com/news/national/feeder/default.rss"),
    ("Indian Express", "https://indianexpress.com/feed/"),
    ("Times of India India", "https://timesofindia.indiatimes.com/rssfeeds/-2128936835.cms"),
]


class ParliamentNewsService:
    _cache: Dict = {}
    _cache_ttl = 600
    _last_unusual: Dict[str, float] = {}
    _unusual_cooldown = 7200

    def __init__(self):
        self.session = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )

    async def get_parliament_news(self, force: bool = False) -> List[Dict]:
        now = datetime.now().timestamp()
        if not force and self._cache.get("parliament_news") and \
           (now - self._cache.get("parliament_news_ts", 0)) < self._cache_ttl:
            return self._cache["parliament_news"]
        news = await self._fetch_parliament_news()
        self._cache["parliament_news"] = news
        self._cache["parliament_news_ts"] = now
        return news

    async def _fetch_parliament_news(self) -> List[Dict]:
        all_news = []
        tasks = [self._fetch_rss(url, name) for name, url in _POLITICAL_SOURCES]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, list):
                all_news.extend(result)

        google_queries = [
            "parliament+india+stock+market",
            "lok+sabha+company+announcement",
            "rajya+sabha+business+regulation",
            "parliament+committee+industry",
            "government+policy+stock+market+india",
        ]
        for query in google_queries:
            try:
                url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN"
                resp = await self.session.get(url, timeout=10)
                if resp.status_code == 200:
                    root = ET.fromstring(resp.text)
                    for item in root.iter("item"):
                        title = item.findtext("title", "")
                        link = item.findtext("link", "")
                        desc = item.findtext("description", "")[:300] if item.findtext("description") else ""
                        pub = item.findtext("pubDate", "")
                        source = item.findtext("source", "") or "Google News"
                        all_news.append({
                            "title": title.strip(),
                            "description": desc.strip(),
                            "url": link.strip() if isinstance(link, str) else "",
                            "source": source.upper(),
                            "published_at": pub,
                            "category": "parliament",
                        })
            except Exception as e:
                print(f"[ParliamentNews] Google RSS error ({query}): {e}")

        prs_news = await self._scrape_prs_india()
        all_news.extend(prs_news)

        seen = set()
        unique = []
        for n in all_news:
            t = n.get("title", "").strip()
            if t and t not in seen:
                seen.add(t)
                unique.append(n)
        return unique[:100]

    async def _fetch_rss(self, url: str, source_name: str) -> List[Dict]:
        try:
            response = await self.session.get(url)
            response.raise_for_status()
            try:
                root = ET.fromstring(response.text)
            except ET.ParseError:
                return []
            items = []
            for item in root.findall(".//item")[:15]:
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
                    "category": "parliament",
                })
            if not items:
                for entry in root.findall(".//entry")[:15]:
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
                        "category": "parliament",
                    })
            return items
        except Exception as e:
            print(f"[ParliamentNews] RSS error ({source_name}): {e}")
            return []

    async def _scrape_prs_india(self) -> List[Dict]:
        try:
            resp = await self.session.get("https://prsindia.org/", timeout=15)
            if resp.status_code != 200:
                return []
            import re as _re
            items = []
            for m in _re.finditer(
                r'<a\s+href="(https?://prsindia\.org[^"]+)"[^>]*>([^<]+)</a>',
                resp.text
            ):
                url = m.group(1)
                title = m.group(2).strip()
                if title and len(title) > 15:
                    items.append({
                        "title": title,
                        "description": title,
                        "url": url,
                        "source": "PRS INDIA",
                        "published_at": datetime.utcnow().isoformat(),
                        "category": "parliament",
                    })
            return items[:15]
        except Exception as e:
            print(f"[ParliamentNews] PRS scrape error: {e}")
            return []

    async def get_stock_mentions(self, force: bool = False) -> Dict[str, List[Dict]]:
        news = await self.get_parliament_news(force=force)
        mentions: Dict[str, List[Dict]] = {}
        for item in news:
            text = (item.get("title", "") + " " + item.get("description", "")).lower()
            for symbol, names in _COMPANY_NAMES.items():
                if any(k.lower() in text for k in names):
                    mentions.setdefault(symbol, []).append(item)
        return mentions

    async def get_unusual_mentions(self, force: bool = False) -> List[Dict]:
        mentions = await self.get_stock_mentions(force=force)
        unusual = []
        now = datetime.now().timestamp()
        for symbol, articles in mentions.items():
            cooldown_remaining = 0
            if symbol in self._last_unusual:
                elapsed = now - self._last_unusual[symbol]
                if elapsed < self._unusual_cooldown:
                    cooldown_remaining = self._unusual_cooldown - int(elapsed)
            unusual.append({
                "symbol": symbol.upper(),
                "company": _COMPANY_NAMES.get(symbol, [symbol])[0].title(),
                "article_count": len(articles),
                "articles": articles[:3],
                "first_seen": datetime.fromtimestamp(
                    self._last_unusual.get(symbol, now)
                ).isoformat() if symbol in self._last_unusual else None,
                "cooldown_remaining_sec": cooldown_remaining,
                "is_new": symbol not in self._last_unusual,
            })
        unusual.sort(key=lambda x: x["article_count"], reverse=True)
        return unusual

    def mark_alerted(self, symbol: str):
        self._last_unusual[symbol] = datetime.now().timestamp()

    async def get_signal_summary(self, symbol: str, price_data: Optional[Dict] = None) -> str:
        mentions = await self.get_stock_mentions()
        sym_lower = symbol.lower()
        articles = mentions.get(sym_lower, [])
        if not articles:
            return ""
        latest = articles[0]
        price_line = ""
        if price_data:
            price_line = (
                f"Current: ₹{price_data.get('price', 0):,.2f} "
                f"({price_data.get('change_pct', 0):+.2f}%)\n"
            )
        return (
            f"📜 *{symbol.upper()} — Parliament Mention*\n"
            f"{price_line}"
            f"📰 \"{latest.get('title', '')[:100]}\"\n"
            f"🔗 {latest.get('url', '')}\n"
            f"📊 {len(articles)} parliament articles found"
        )

    def is_political_text(self, text: str) -> bool:
        text_lower = text.lower()
        return any(k in text_lower for k in _POLITICAL_KEYWORDS)


parliament_news_service = ParliamentNewsService()
