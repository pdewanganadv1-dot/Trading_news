from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.routes.market import router as market_router
from app.routes.trading import router as trading_router
from app.routes.webhooks import router as webhooks_router
from app.routes.dashboard import router as dashboard_router
from app.routes.news import router as news_router
from app.routes.sentiment import router as sentiment_router
from app.routes.debug import router as debug_router
from app.routes.indicators import router as indicators_router
from app.routes.market_realtime import router as market_realtime_router
from app.routes.websocket import router as websocket_router
from app.routes.signals_log import router as signals_log_router
from app.routes.fundamentals import router as fundamentals_router
from app.routes.nifty100 import router as nifty100_router
from app.routes.edge import router as edge_router
from app.routes.sentiment_pipeline import router as sentiment_pipeline_router
from app.routes.agent import router as agent_router
from app.services.signal_monitor import get_cache_stats
import asyncio


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.services.signal_monitor import signal_monitor_loop
    from app.services.telegram_bot import telegram_poll_loop
    from app.services.daily_report import daily_report_loop
    from app.services.news_sentiment_pipeline import sentiment_pipeline_loop
    from app.services.market_edge_service import auto_update_fii_dii
    task1 = asyncio.create_task(signal_monitor_loop())
    task2 = asyncio.create_task(telegram_poll_loop())
    task3 = asyncio.create_task(daily_report_loop())
    task4 = asyncio.create_task(sentiment_pipeline_loop())
    task5 = asyncio.create_task(auto_update_fii_dii())
    yield
    task1.cancel()
    task2.cancel()
    task3.cancel()
    task4.cancel()
    task5.cancel()


app = FastAPI(
    title="Tradingview Integration API",
    description="Full-stack Tradingview integration for all markets",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {"status": "online", "service": "Tradingview Integration API"}


@app.get("/health")
def health_check():
    stats = get_cache_stats()
    return {
        "status": "healthy",
        "cache": stats,
    }


app.include_router(market_router)
app.include_router(trading_router)
app.include_router(webhooks_router)
app.include_router(dashboard_router)
app.include_router(news_router)
app.include_router(sentiment_router)
app.include_router(debug_router)
app.include_router(indicators_router)
app.include_router(market_realtime_router)
app.include_router(websocket_router)
app.include_router(signals_log_router)
app.include_router(fundamentals_router)
app.include_router(nifty100_router)
app.include_router(edge_router)
app.include_router(agent_router)
app.include_router(sentiment_pipeline_router)
