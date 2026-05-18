import httpx
from typing import Optional, List
from app.config import settings

_INDIAN_STOCKS = {
    'reliance', 'tcs', 'hdfcbank', 'infy', 'icicibank', 'sbin', 'lt', 'wipro', 'itc',
    'bhartiartl', 'maruti', 'nestleind', 'hindunilvr', 'asianpaint', 'sunpharma', 'titan',
    'bajajfinsv', 'hcltech', 'kotakbank', 'axisbank', 'ntpc', 'tatasteel', 'cipla', 'ultracemco'
}


def _price_fmt(price: float, symbol: str) -> str:
    s = symbol.lower()
    if s in _INDIAN_STOCKS:
        return f'₹{price:,.2f}'
    if s in ('btc', 'eth'):
        return f'${price:,.2f}'
    if price >= 1000:
        return f'${price:,.2f}'
    return f'${price:.2f}'


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

    async def send_signal_alert(
        self,
        symbol: str,
        signal: str,
        confidence: float,
        price: float,
        reasons: list,
        explanation: Optional[str] = None,
    ) -> bool:
        emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}
        msg = (
            f"{emoji.get(signal, '⚡')} *{signal} SIGNAL* for *{symbol.upper()}*\n"
            f"💰 Price: `{_price_fmt(price, symbol)}`\n"
            f"📊 Confidence: `{confidence*100:.0f}%`\n"
            f"📝 Reasons: `{', '.join(reasons[:3])}`"
        )
        if explanation:
            lines = explanation.split("\n")
            header = lines[0] if lines else ""
            mtf_lines = [l for l in lines if l.startswith("MTF ")]
            key_parts = [header] + mtf_lines[:2]
            summary = " | ".join(key_parts).strip()
            if summary:
                msg += f"\n\n💡 *AI Explanation:* {summary}"
        return await self.send_message(msg)


telegram_notifier = TelegramNotifier()
