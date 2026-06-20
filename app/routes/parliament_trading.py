from fastapi import APIRouter, Query
from app.services.parliament_news_service import parliament_news_service
from app.services.parliament_tracker import get_parliament_snapshot, buy_from_parliament

router = APIRouter(prefix="/api/parliament", tags=["parliament"])


@router.get("/news")
async def get_parliament_news(force: bool = Query(False)):
    news = await parliament_news_service.get_parliament_news(force=force)
    return {"total": len(news), "articles": news[:30]}


@router.get("/mentions")
async def get_stock_mentions(force: bool = Query(False)):
    mentions = await parliament_news_service.get_stock_mentions(force=force)
    flattened = []
    for sym, articles in mentions.items():
        flattened.append({
            "symbol": sym.upper(),
            "article_count": len(articles),
            "latest": articles[0].get("title", "")[:100] if articles else "",
        })
    flattened.sort(key=lambda x: x["article_count"], reverse=True)
    return {"total_symbols": len(flattened), "mentions": flattened[:20]}


@router.get("/unusual")
async def get_unusual(force: bool = Query(False)):
    unusual = await parliament_news_service.get_unusual_mentions(force=force)
    return {"total": len(unusual), "unusual": unusual[:20]}


@router.get("/snapshot")
async def snapshot():
    return await get_parliament_snapshot()


@router.post("/buy/{symbol}")
async def buy_signal(symbol: str, qty: int = Query(100)):
    result = await buy_from_parliament(symbol, qty)
    return result
