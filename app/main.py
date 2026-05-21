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
from app.routes.options_chain import router as options_chain_router
from app.routes.insider_trading import router as insider_trading_router
from app.routes.sector_rotation import router as sector_rotation_router
from app.routes.ai_agent import router as ai_agent_router
from app.routes.strategy_marketplace import router as strategy_marketplace_router
from app.routes.politician_trades import router as politician_trades_router
from app.services.signal_monitor import get_cache_stats
import asyncio


import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_background_tasks: list = []


def _safe_task(coro, name: str, delay: int = 0):
    """Create a background task that logs crashes and restarts (staggered start)."""
    async def wrapper():
        if delay:
            await asyncio.sleep(delay)
        while True:
            try:
                await coro
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"Background task '{name}' crashed: {e}", exc_info=True)
            await asyncio.sleep(30)  # restart delay
    t = asyncio.create_task(wrapper())
    _background_tasks.append(t)
    return t


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.services.signal_monitor import signal_monitor_loop
    from app.services.telegram_bot import telegram_poll_loop
    from app.services.daily_report import daily_report_loop
    from app.services.news_sentiment_pipeline import sentiment_pipeline_loop
    from app.services.market_edge_service import auto_update_fii_dii
    from app.services.market_feed import feed_loop
    from app.services.live_analysis import volume_spike_loop
    from app.services.strategy_builder import strategy_builder_loop
    from app.services.dhanhq_service import auto_renew_loop
    _safe_task(signal_monitor_loop(), "signal_monitor", delay=0)
    _safe_task(telegram_poll_loop(), "telegram_poll", delay=5)
    _safe_task(daily_report_loop(), "daily_report", delay=10)
    _safe_task(sentiment_pipeline_loop(), "sentiment_pipeline", delay=15)
    _safe_task(auto_update_fii_dii(), "fii_dii", delay=20)
    _safe_task(feed_loop(), "market_feed", delay=25)
    _safe_task(volume_spike_loop(), "volume_spike", delay=30)
    _safe_task(strategy_builder_loop(), "strategy_builder", delay=35)
    _safe_task(auto_renew_loop(), "dhan_token_renew", delay=40)
    yield
    for t in _background_tasks:
        t.cancel()


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
app.include_router(options_chain_router)
app.include_router(insider_trading_router)
app.include_router(sector_rotation_router)
app.include_router(ai_agent_router)
app.include_router(strategy_marketplace_router)
app.include_router(politician_trades_router)
