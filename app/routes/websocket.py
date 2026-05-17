from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import List
import asyncio
import json
from datetime import datetime

router = APIRouter(tags=["websocket"])


class ConnectionManager:
    """Manages WebSocket connections for real-time updates."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients."""
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass


manager = ConnectionManager()


@router.websocket("/ws/prices")
async def websocket_prices(websocket: WebSocket):
    """WebSocket endpoint for real-time price updates."""
    await manager.connect(websocket)
    print(f"Client connected. Total connections: {len(manager.active_connections)}")

    symbols = ['btc', 'eth', 'gold', 'silver']

    try:
        while True:
            # Import here to avoid circular imports
            from app.services.market_data_service import market_data_service, TechnicalIndicators, TradingSignals

            prices = {}
            signals = {}
            indicators = {}
            for symbol in symbols:
                data = await market_data_service.get_price_data(symbol)
                if data:
                    prices[symbol] = data

                    # Get 5min prices for signal and indicators
                    try:
                        prices_5m = await market_data_service.get_5min_prices(symbol, 100)
                        signal_data = TradingSignals.generate_signal(prices_5m, data['price'])
                        signals[symbol] = {
                            'signal': signal_data['signal'],
                            'confidence': signal_data['confidence'],
                            'reasons': signal_data['reasons'][:2]
                        }
                        # Include full indicators
                        indicators[symbol] = signal_data['indicators']
                    except:
                        pass

            await websocket.send_json({
                "type": "prices",
                "data": prices,
                "signals": signals,
                "indicators": indicators,
                "timestamp": datetime.now().isoformat()
            })

            # Wait 5 seconds before next update
            await asyncio.sleep(5)

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        print(f"Client disconnected. Total connections: {len(manager.active_connections)}")
    except Exception as e:
        print(f"WebSocket error: {e}")
        manager.disconnect(websocket)


@router.websocket("/ws/news")
async def websocket_news(websocket: WebSocket):
    """WebSocket endpoint for real-time news updates."""
    await manager.connect(websocket)
    print(f"News client connected. Total connections: {len(manager.active_connections)}")

    try:
        while True:
            from app.services.real_news import real_news_service

            news = await real_news_service.get_crypto_news()

            await websocket.send_json({
                "type": "news",
                "data": news,
                "timestamp": datetime.now().isoformat()
            })

            # Wait 30 seconds before next update
            await asyncio.sleep(30)

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        print(f"News client disconnected.")
    except Exception as e:
        print(f"WebSocket error: {e}")
        manager.disconnect(websocket)


@router.websocket("/ws/sentiment")
async def websocket_sentiment(websocket: WebSocket):
    """WebSocket endpoint for real-time sentiment updates."""
    await manager.connect(websocket)
    print(f"Sentiment client connected.")

    try:
        while True:
            from app.services.real_news import real_news_service
            from app.services.sentiment import sentiment_monitor

            news = await real_news_service.get_all_news()
            sentiment = await sentiment_monitor.get_market_sentiment(news)

            await websocket.send_json({
                "type": "sentiment",
                "data": sentiment,
                "timestamp": datetime.now().isoformat()
            })

            await asyncio.sleep(15)

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        print(f"WebSocket error: {e}")
        manager.disconnect(websocket)