from datetime import datetime, timedelta
import pandas as pd
from nselib import capital_market


class InsiderTradingService:
    async def get_bulk_deals(self, period: str = "1M"):
        try:
            df = capital_market.bulk_deal_data(period=period)
            if df is None or df.empty:
                return []
            df = df.copy()
            for col in ["QuantityTraded", "TradePrice/Wght.Avg.Price"]:
                if col in df.columns:
                    df[col] = (
                        df[col]
                        .astype(str)
                        .str.replace(",", "", regex=False)
                        .str.replace(" ", "", regex=False)
                    )
                    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
            df["TotalValue"] = df["QuantityTraded"] * df["TradePrice/Wght.Avg.Price"]
            df = df.sort_values("Date", ascending=False)
            return df.to_dict(orient="records")
        except Exception as e:
            return {"error": str(e)}

    async def get_block_deals(self, period: str = "1M"):
        try:
            df = capital_market.block_deals_data(period=period)
            if df is None or df.empty:
                return []
            df = df.copy()
            for col in ["QuantityTraded", "TradePrice/Wght.Avg.Price"]:
                if col in df.columns:
                    df[col] = (
                        df[col]
                        .astype(str)
                        .str.replace(",", "", regex=False)
                        .str.replace(" ", "", regex=False)
                    )
                    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
            df["TotalValue"] = df["QuantityTraded"] * df["TradePrice/Wght.Avg.Price"]
            df = df.sort_values("Date", ascending=False)
            return df.to_dict(orient="records")
        except Exception as e:
            return {"error": str(e)}

    async def get_insider_summary(self, period: str = "1M"):
        bulk = await self.get_bulk_deals(period)
        block = await self.get_block_deals(period)

        if isinstance(bulk, dict) and "error" in bulk:
            bulk = []
        if isinstance(block, dict) and "error" in block:
            block = []

        summary = {"bulk_total": len(bulk), "block_total": len(block)}

        if bulk:
            bulk_df = pd.DataFrame(bulk)
            buys = bulk_df[bulk_df["Buy/Sell"].str.upper() == "BUY"]
            sells = bulk_df[bulk_df["Buy/Sell"].str.upper() == "SELL"]
            summary["bulk_buy_value"] = round(buys["TotalValue"].sum(), 2)
            summary["bulk_sell_value"] = round(sells["TotalValue"].sum(), 2)
            summary["bulk_net"] = round(summary["bulk_buy_value"] - summary["bulk_sell_value"], 2)
            top_buyers = (
                buys.groupby("ClientName")["TotalValue"]
                .sum()
                .sort_values(ascending=False)
                .head(10)
                .reset_index()
                .to_dict(orient="records")
            )
            top_sellers = (
                sells.groupby("ClientName")["TotalValue"]
                .sum()
                .sort_values(ascending=False)
                .head(10)
                .reset_index()
                .to_dict(orient="records")
            )
            summary["top_buyers"] = top_buyers
            summary["top_sellers"] = top_sellers

        if block:
            block_df = pd.DataFrame(block)
            buys = block_df[block_df["Buy/Sell"].str.upper() == "BUY"]
            sells = block_df[block_df["Buy/Sell"].str.upper() == "SELL"]
            summary["block_buy_value"] = round(buys["TotalValue"].sum(), 2)
            summary["block_sell_value"] = round(sells["TotalValue"].sum(), 2)
            summary["block_net"] = round(summary["block_buy_value"] - summary["block_sell_value"], 2)

        return summary


insider_trading_service = InsiderTradingService()
