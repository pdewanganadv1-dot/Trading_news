from fastapi import APIRouter
from datetime import datetime
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


class RespondBody(BaseModel):
    text: str


@router.get("/instructions")
async def get_pending_instructions():
    """Get pending instructions from Telegram (consumed by AI agent)."""
    from app.services.telegram_bot import _agent_instructions

    items = list(_agent_instructions)
    return {
        "status": "ok",
        "count": len(items),
        "instructions": [
            {
                "text": i["text"],
                "timestamp": i["timestamp"],
            }
            for i in items
        ],
    }


@router.post("/instructions/clear")
async def clear_instructions():
    """Clear all pending instructions after reading."""
    from app.services.telegram_bot import _agent_instructions

    _agent_instructions.clear()
    return {"status": "ok", "cleared": True}


@router.post("/respond")
async def respond_to_telegram(body: RespondBody):
    """Send a response back to the Telegram chat (called by AI agent after processing)."""
    from app.services.telegram_notifier import telegram_notifier

    ok = await telegram_notifier.send_message(body.text)
    return {"status": "ok" if ok else "error", "sent": ok}
