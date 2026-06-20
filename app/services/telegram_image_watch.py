import os
import json
import httpx
import asyncio
import logging
from datetime import datetime
from app.config import settings

_WATCH_DIR = os.path.join(settings.persistent_dir, "telegram_images")
os.makedirs(_WATCH_DIR, exist_ok=True)

logger = logging.getLogger(__name__)


def _next_id() -> int:
    existing = [f for f in os.listdir(_WATCH_DIR) if f.endswith(".json")]
    ids = [int(f.replace(".json", "")) for f in existing if f.replace(".json", "").isdigit()]
    return max(ids) + 1 if ids else 1


async def download_photo(file_id: str, caption: str = "", chat_info: dict = None) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/getFile",
                json={"file_id": file_id},
            )
            data = resp.json()
            if not data.get("ok"):
                logger.warning(f"getFile failed: {data}")
                return None

            file_path = data["result"]["file_path"]
            photo_resp = await client.get(
                f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{file_path}"
            )
            if photo_resp.status_code != 200:
                logger.warning(f"Download failed: {photo_resp.status_code}")
                return None

            image_id = _next_id()
            ext = os.path.splitext(file_path)[1] or ".jpg"
            image_filename = f"{image_id}{ext}"
            image_path = os.path.join(_WATCH_DIR, image_filename)

            with open(image_path, "wb") as f:
                f.write(photo_resp.content)

            meta = {
                "id": image_id,
                "file_id": file_id,
                "image_path": image_path,
                "caption": caption or "",
                "chat": chat_info or {},
                "downloaded_at": datetime.now().isoformat(),
                "file_size": len(photo_resp.content),
            }
            meta_path = os.path.join(_WATCH_DIR, f"{image_id}.json")
            with open(meta_path, "w") as f:
                json.dump(meta, f, indent=2, default=str)

            logger.info(f"Saved image #{image_id}: {image_path} caption={caption[:50]}")
            return meta

    except Exception as e:
        logger.error(f"Download image error: {e}")
        return None


def get_latest_images(limit: int = 5) -> list[dict]:
    meta_files = sorted(
        [f for f in os.listdir(_WATCH_DIR) if f.endswith(".json")],
        reverse=True,
    )[:limit]
    results = []
    for mf in meta_files:
        path = os.path.join(_WATCH_DIR, mf)
        try:
            with open(path) as f:
                meta = json.load(f)
                if os.path.exists(meta.get("image_path", "")):
                    results.append(meta)
        except Exception:
            pass
    return results


def get_pending_count() -> int:
    return len([f for f in os.listdir(_WATCH_DIR) if f.endswith(".json")])
