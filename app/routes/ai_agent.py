from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from app.services.ai_agent_service import ai_agent_service
import os

router = APIRouter(tags=["ai-agent"])


@router.get("/ai-agent")
async def get_ai_agent_page():
    path = os.path.join(os.path.dirname(__file__), "../templates/ai_agent.html")
    return FileResponse(path)


@router.get("/api/ai-agent/analyze")
async def analyze_stock(symbol: str = Query("NIFTY"), days: int = Query(50)):
    result = await ai_agent_service.analyze_stock(symbol, days)
    return result
