from datetime import datetime, timedelta
import pandas as pd
from nselib import capital_market
from app.data.sectors import SECTORAL_INDICES, get_sector_for_industry


class SectorRotationService:
    def __init__(self):
        self._sector_stocks_cache = None

    async def _get_sector_stocks(self):
        if self._sector_stocks_cache is not None:
            return self._sector_stocks_cache
        try:
            nifty50 = capital_market.nifty50_equity_list()
            next50 = capital_market.niftynext50_equity_list()
            combined = pd.concat([nifty50, next50], ignore_index=True)
            combined = combined.drop_duplicates(subset=["Symbol"])
            combined["Sector"] = combined["Industry"].apply(get_sector_for_industry)
            self._sector_stocks_cache = combined
            return combined
        except Exception as e:
            return pd.DataFrame()

    def _invalidate_cache(self):
        self._sector_stocks_cache = None

    async def get_sector_performance(self, period: str = "1W"):
        results = []
        for index_name, sector_name in SECTORAL_INDICES.items():
            try:
                data = capital_market.index_data(index_name, period=period)
                if data is None or data.empty:
                    continue
                if len(data) >= 2:
                    first_close = float(data.iloc[-1]["CLOSE_INDEX_VAL"])
                    last_close = float(data.iloc[0]["CLOSE_INDEX_VAL"])
                else:
                    first_close = float(data.iloc[0]["OPEN_INDEX_VAL"])
                    last_close = float(data.iloc[0]["CLOSE_INDEX_VAL"])
                change_pct = round((last_close - first_close) / first_close * 100, 2)
                results.append({
                    "sector": sector_name,
                    "index_name": index_name,
                    "current": last_close,
                    "previous": first_close,
                    "change_pct": change_pct,
                    "high": float(data["HIGH_INDEX_VAL"].max()),
                    "low": float(data["LOW_INDEX_VAL"].min()),
                    "timestamp": str(data.iloc[0]["TIMESTAMP"]),
                })
            except Exception:
                continue

        results.sort(key=lambda x: x["change_pct"], reverse=True)
        for i, r in enumerate(results):
            r["rank"] = i + 1
        return results

    async def get_sector_breakdown(self):
        stocks = await self._get_sector_stocks()
        if stocks.empty:
            return []
        breakdown = (
            stocks.groupby("Sector")
            .agg(
                stock_count=("Symbol", "count"),
                industries=("Industry", lambda x: list(x.unique())),
                stocks_list=("Symbol", lambda x: sorted(x.tolist())),
            )
            .reset_index()
            .sort_values("stock_count", ascending=False)
        )
        return breakdown.to_dict(orient="records")

    async def get_stocks_by_sector(self):
        stocks = await self._get_sector_stocks()
        if stocks.empty:
            return {}
        result = {}
        for _, row in stocks.iterrows():
            sector = row["Sector"]
            if sector not in result:
                result[sector] = []
            result[sector].append({
                "symbol": row["Symbol"],
                "company": row["Company Name"],
                "industry": row["Industry"],
            })
        return result

    async def get_full_rotation_view(self, period: str = "1W"):
        sector_perf = await self.get_sector_performance(period)
        sector_stocks = await self.get_stocks_by_sector()
        breakdown = await self.get_sector_breakdown()

        for s in sector_perf:
            s_name = s["sector"]
            s["stocks"] = sector_stocks.get(s_name, [])
            b = next((b for b in breakdown if b["Sector"] == s_name), None)
            s["stock_count"] = b["stock_count"] if b else 0

        return {
            "sectors": sector_perf,
            "total_stocks": sum(len(v) for v in sector_stocks.values()),
            "total_sectors": len(sector_perf),
            "period": period,
            "timestamp": datetime.now().isoformat(),
        }


sector_rotation_service = SectorRotationService()
