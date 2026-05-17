import httpx
from typing import Optional
from app.config import settings


class TelegramNotifier:
    def __init__(self):
        self.token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        self.base_url = f"https://api.telegram.org/bot{self.token}"

    async def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self.base_url}/sendMessage",
                    json={"chat_id": self.chat_id, "text": text, "parse_mode": parse_mode},
                )
                return resp.status_code == 200
        except Exception as e:
            print(f"Telegram send error: {e}")
            return False

    async def send_signal_alert(self, symbol: str, signal: str, confidence: float, price: float, reasons: list) -> bool:
        emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}
        msg = (
            f"{emoji.get(signal, '⚡')} *{signal} SIGNAL* for *{symbol.upper()}*\n"
            f"💰 Price: `${price:,.2f}`\n"
            f"📊 Confidence: `{confidence*100:.0f}%`\n"
            f"📝 Reasons: `{', '.join(reasons[:3])}`"
        )
        return await self.send_message(msg)


telegram_notifier = TelegramNotifier()
