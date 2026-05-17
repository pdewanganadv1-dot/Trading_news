import json
import os
from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/nifty100", tags=["nifty100"])

_DATA_PATH = os.path.join(os.path.dirname(__file__), "../data/nifty100.json")

_stocks_cache = None


@router.get("")
async def get_nifty100():
    global _stocks_cache
    if _stocks_cache is None:
        with open(_DATA_PATH) as f:
            _stocks_cache = json.load(f)
    return _stocks_cache


@router.get("/search/{query}")
async def search_nifty100(query: str):
    global _stocks_cache
    if _stocks_cache is None:
        with open(_DATA_PATH) as f:
            _stocks_cache = json.load(f)
    q = query.upper()
    results = [s for s in _stocks_cache if q in s["symbol"] or q in s["name"].upper()]
    return results[:20]
