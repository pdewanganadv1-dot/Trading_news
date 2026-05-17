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
import asyncio


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.services.signal_monitor import signal_monitor_loop
    task = asyncio.create_task(signal_monitor_loop())
    yield
    task.cancel()


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
    return {"status": "healthy"}


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
