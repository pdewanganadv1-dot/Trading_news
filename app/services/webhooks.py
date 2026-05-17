from typing import Dict, Any, Optional
from datetime import datetime
import httpx
from app.models.schemas import WebhookAlert


class WebhookService:
    """Service for managing webhook alerts."""

    def __init__(self):
        self.alerts: Dict[int, WebhookAlert] = {}
        self._next_id = 1
        self.session = httpx.AsyncClient(timeout=10.0)

    def create_alert(
        self,
        symbol: str,
        condition: str,
        value: float,
        callback_url: str
    ) -> WebhookAlert:
        alert = WebhookAlert(
            id=self._next_id,
            symbol=symbol,
            condition=condition,
            value=value,
            callback_url=callback_url,
            active=True,
            created_at=datetime.utcnow()
        )
        self.alerts[self._next_id] = alert
        self._next_id += 1
        return alert

    def get_alert(self, alert_id: int) -> Optional[WebhookAlert]:
        return self.alerts.get(alert_id)

    def list_alerts(self, symbol: Optional[str] = None) -> list[WebhookAlert]:
        alerts = list(self.alerts.values())
        if symbol:
            alerts = [a for a in alerts if a.symbol == symbol]
        return alerts

    def delete_alert(self, alert_id: int) -> bool:
        if alert_id in self.alerts:
            del self.alerts[alert_id]
            return True
        return False

    async def check_and_trigger(self, symbol: str, current_price: float) -> list:
        triggered = []
        for alert in self.alerts.values():
            if not alert.active or alert.symbol != symbol:
                continue

            should_trigger = False
            if alert.condition == "price_above" and current_price > alert.value:
                should_trigger = True
            elif alert.condition == "price_below" and current_price < alert.value:
                should_trigger = True

            if should_trigger:
                try:
                    await self.session.post(
                        alert.callback_url,
                        json={"symbol": symbol, "price": current_price, "alert_id": alert.id}
                    )
                    triggered.append(alert)
                except Exception:
                    pass  # Log in production

        return triggered


webhook_service = WebhookService()