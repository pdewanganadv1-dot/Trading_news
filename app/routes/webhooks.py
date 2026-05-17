from fastapi import APIRouter, HTTPException
from typing import Optional
from pydantic import BaseModel
from app.services.webhooks import webhook_service
from app.models.schemas import WebhookAlert

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


class CreateAlertRequest(BaseModel):
    symbol: str
    condition: str
    value: float
    callback_url: str


@router.post("/alerts", response_model=WebhookAlert)
async def create_alert(request: CreateAlertRequest):
    """Create a new webhook alert."""
    valid_conditions = ["price_above", "price_below", "cross_over", "cross_under"]
    if request.condition not in valid_conditions:
        raise HTTPException(status_code=400, detail=f"Invalid condition. Must be one of: {valid_conditions}")
    return webhook_service.create_alert(request.symbol, request.condition, request.value, request.callback_url)


@router.get("/alerts", response_model=list[WebhookAlert])
async def list_alerts(symbol: Optional[str] = None):
    """List all webhook alerts, optionally filtered by symbol."""
    return webhook_service.list_alerts(symbol)


@router.get("/alerts/{alert_id}", response_model=WebhookAlert)
async def get_alert(alert_id: int):
    """Get a specific alert by ID."""
    alert = webhook_service.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


@router.delete("/alerts/{alert_id}")
async def delete_alert(alert_id: int):
    """Delete a webhook alert."""
    if not webhook_service.delete_alert(alert_id):
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"status": "deleted", "alert_id": alert_id}