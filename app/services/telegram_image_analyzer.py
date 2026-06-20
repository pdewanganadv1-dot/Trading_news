import base64
import logging
from typing import Optional
from app.config import settings

logger = logging.getLogger(__name__)


class TelegramImageAnalyzer:
    def __init__(self):
        self._client = None
        self._use_llm = bool(settings.groq_api_key)
        if self._use_llm:
            try:
                from groq import Groq
                self._client = Groq(api_key=settings.groq_api_key)
                logger.info("Image analyzer initialized with Groq")
            except Exception as e:
                self._use_llm = False
                logger.warning(f"Groq init failed: {e}")
        else:
            logger.warning("No GROQ_API_KEY set — image analysis disabled")

    def _encode_image(self, image_path: str) -> Optional[str]:
        try:
            with open(image_path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"Failed to encode image {image_path}: {e}")
            return None

    def analyze_image(self, image_path: str, caption: str = "") -> Optional[str]:
        if not self._use_llm or not self._client:
            return "Image analysis unavailable (Groq not configured)"

        b64 = self._encode_image(image_path)
        if not b64:
            return "Failed to read image"

        prompt = (
            "This is a trade signal chart. Extract EXACT price levels visible on the image.\n"
            "Return a JSON object with these fields:\n"
            "{\n"
            '  "symbol": "asset name",\n'
            '  "entry": 1234.56,\n'
            '  "take_profit": 1250.00,\n'
            '  "stop_loss": 1220.00,\n'
            '  "direction": "BUY or SELL",\n'
            '  "assessment": "brief one-line analysis"\n'
            "}\n"
            "Read numbers precisely from the chart labels. Do not guess."
        )
        if caption:
            prompt = f"Context from sender: {caption}\n\n{prompt}"

        try:
            completion = self._client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{b64}",
                                },
                            },
                        ],
                    }
                ],
                temperature=0.3,
                max_completion_tokens=500,
            )
            return completion.choices[0].message.content
        except Exception as e:
            logger.error(f"Groq vision analysis failed: {e}")
            return f"Analysis failed: {e}"


image_analyzer = TelegramImageAnalyzer()
