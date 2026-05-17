import asyncio
import httpx
import json
from datetime import datetime
from typing import Dict, List


_STOCKTWITS_API = "https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
_REDDIT_API = "https://www.reddit.com/r/{sub}/search.json?q={ticker}&restrict_sr=on&sort=new&t=week&limit=5"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) trading-dashboard/1.0"
_SUBREDDITS = ("wallstreetbets", "stocks", "investing", "cryptocurrency")


async def fetch_stocktwits(ticker: str) -> Dict:
    data = {"source": "stocktwits", "ticker": ticker.upper(), "messages": [], "bullish": 0, "bearish": 0, "unlabeled": 0}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.get(_STOCKTWITS_API.format(ticker=ticker.upper()), headers={"User-Agent": _UA})
            if resp.status_code != 200:
                return {**data, "error": f"HTTP {resp.status_code}"}
            body = resp.json()
            messages = body.get("messages", [])[:20]
            for m in messages:
                entities = m.get("entities") or {}
                sentiment_obj = entities.get("sentiment") or {}
                sentiment = sentiment_obj.get("basic") if isinstance(sentiment_obj, dict) else None
                body_text = (m.get("body") or "").replace("\n", " ")[:200]
                if sentiment == "Bullish":
                    data["bullish"] += 1
                elif sentiment == "Bearish":
                    data["bearish"] += 1
                else:
                    data["unlabeled"] += 1
                data["messages"].append({
                    "body": body_text,
                    "sentiment": sentiment.lower() if sentiment else "neutral",
                    "created_at": m.get("created_at", ""),
                })
            total = data["bullish"] + data["bearish"] + data["unlabeled"]
            data["bullish_pct"] = round(100 * data["bullish"] / total, 1) if total else 0
            data["bearish_pct"] = round(100 * data["bearish"] / total, 1) if total else 0
    except Exception as e:
        data["error"] = str(e)
    return data


async def fetch_reddit(ticker: str) -> Dict:
    data = {"source": "reddit", "ticker": ticker.upper(), "posts": [], "total_score": 0}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            for sub in _SUBREDDITS:
                url = _REDDIT_API.format(sub=sub, ticker=ticker.upper())
                try:
                    resp = await c.get(url, headers={"User-Agent": _UA})
                    if resp.status_code != 200:
                        continue
                    payload = resp.json()
                    children = (payload.get("data") or {}).get("children") or []
                    for child in children[:5]:
                        p = child.get("data", {})
                        title = (p.get("title") or "").replace("\n", " ").strip()
                        score = p.get("score", 0)
                        data["total_score"] += score
                        data["posts"].append({
                            "subreddit": sub,
                            "title": title,
                            "score": score,
                            "comments": p.get("num_comments", 0),
                            "created_utc": p.get("created_utc", 0),
                        })
                except Exception:
                    pass
        data["post_count"] = len(data["posts"])
    except Exception as e:
        data["error"] = str(e)
    return data


async def analyze_social_sentiment(ticker: str = "BTC") -> Dict:
    stocktwits, reddit = await asyncio.gather(fetch_stocktwits(ticker), fetch_reddit(ticker))

    combined_bullish = stocktwits.get("bullish", 0)
    combined_bearish = stocktwits.get("bearish", 0)
    total_labeled = combined_bullish + combined_bearish

    # Reddit score sentiment: positive score = bullish signal
    reddit_score = reddit.get("total_score", 0)
    reddit_bullish = max(0, reddit_score / 10) if reddit_score > 0 else 0
    reddit_bearish = abs(reddit_score / 10) if reddit_score < 0 else 0

    combined_bullish += reddit_bullish
    combined_bearish += reddit_bearish

    if combined_bullish > combined_bearish:
        overall = "bullish"
        score = combined_bullish / (combined_bullish + combined_bearish) if combined_bullish + combined_bearish > 0 else 0.5
    elif combined_bearish > combined_bullish:
        overall = "bearish"
        score = combined_bearish / (combined_bullish + combined_bearish) if combined_bullish + combined_bearish > 0 else 0.5
    else:
        overall = "neutral"
        score = 0.5

    return {
        "overall": overall,
        "score": round(score, 2),
        "stocktwits": stocktwits,
        "reddit": reddit,
        "total_bullish": int(combined_bullish),
        "total_bearish": int(combined_bearish),
    }
