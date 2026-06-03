from datetime import datetime, timedelta
from typing import List, Optional, Tuple
import pandas as pd
import numpy as np
from nselib import derivatives


class OptionsDataLoader:
    SYMBOLS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
    INSTRUMENTS = {"index": "OPTIDX", "stock": "OPTSTK"}

    def __init__(self):
        self._cache: dict = {}

    def _fmt_date(self, dt) -> str:
        if isinstance(dt, str):
            return dt
        return dt.strftime("%d-%m-%Y")

    def fetch_option_data(
        self, symbol: str, instrument: str = "OPTIDX",
        from_date: str = None, to_date: str = None,
        period_days: int = 60
    ) -> pd.DataFrame:
        if from_date is None:
            to = datetime.today()
            from_date = (to - timedelta(days=period_days)).strftime("%d-%m-%Y")
        if to_date is None:
            to_date = datetime.today().strftime("%d-%m-%Y")

        cache_key = f"{symbol}_{instrument}_{from_date}_{to_date}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            df = derivatives.option_price_volume_data(
                symbol, instrument,
                from_date=from_date, to_date=to_date
            )
        except ValueError:
            df = self._fetch_chunked(symbol, instrument, from_date, to_date)

        if df.empty:
            return df

        df["TIMESTAMP"] = pd.to_datetime(df["TIMESTAMP"], format="%d-%b-%Y")
        df["EXPIRY_DT"] = pd.to_datetime(df["EXPIRY_DT"], format="%d-%b-%Y")
        df = df.sort_values(["TIMESTAMP", "STRIKE_PRICE"]).reset_index(drop=True)
        self._cache[cache_key] = df
        return df.copy()

    def _fetch_chunked(
        self, symbol: str, instrument: str,
        from_date: str, to_date: str, chunk_days: int = 30
    ) -> pd.DataFrame:
        fd = datetime.strptime(from_date, "%d-%m-%Y")
        td = datetime.strptime(to_date, "%d-%m-%Y")
        chunks = []
        cursor = fd
        while cursor < td:
            chunk_end = min(cursor + timedelta(days=chunk_days), td)
            try:
                chunk = derivatives.option_price_volume_data(
                    symbol, instrument,
                    from_date=cursor.strftime("%d-%m-%Y"),
                    to_date=chunk_end.strftime("%d-%m-%Y")
                )
                if not chunk.empty:
                    chunks.append(chunk)
            except Exception:
                pass
            cursor = chunk_end + timedelta(days=1)
        if chunks:
            return pd.concat(chunks, ignore_index=True)
        return pd.DataFrame()

    def get_weekly_expiries(self, df: pd.DataFrame) -> List[str]:
        exps = sorted(df["EXPIRY_DT"].unique())
        weekly = []
        for e in exps:
            days_to = (e - pd.Timestamp.now()).days
            if 0 <= days_to <= 45:
                weekly.append(e)
        if not weekly and len(exps) > 0:
            weekly = [exps[0]]
        return weekly

    def build_daily_snapshot(
        self, df: pd.DataFrame, date: str, expiry: str = None
    ) -> dict:
        day_data = df[df["TIMESTAMP"] == pd.Timestamp(date)]
        if day_data.empty:
            return None

        underlying = day_data["UNDERLYING_VALUE"].iloc[0]

        if expiry is None:
            exps = sorted(day_data["EXPIRY_DT"].unique())
            today = pd.Timestamp(date)
            future = [e for e in exps if e >= today]
            use_expiry = future[0] if future else exps[0]
            expiry = use_expiry.strftime("%Y-%m-%d")
            day_data = day_data[day_data["EXPIRY_DT"] == use_expiry]
        else:
            day_data = day_data[day_data["EXPIRY_DT"] == pd.Timestamp(expiry)]

        if day_data.empty:
            return None

        ce = day_data[day_data["OPTION_TYPE"] == "CE"].copy()
        pe = day_data[day_data["OPTION_TYPE"] == "PE"].copy()

        chain = []
        merged = pd.merge(ce, pe, on="STRIKE_PRICE", how="outer", suffixes=("_ce", "_pe"))
        merged = merged.fillna(0)
        for _, row in merged.iterrows():
            chain.append({
                "strike": int(row["STRIKE_PRICE"]),
                "ce_close": row.get("CLOSING_PRICE_ce", 0),
                "ce_oi": row.get("OPEN_INT_ce", 0),
                "ce_chng_oi": row.get("CHANGE_IN_OI_ce", 0),
                "ce_volume": row.get("TOT_TRADED_QTY_ce", 0),
                "pe_close": row.get("CLOSING_PRICE_pe", 0),
                "pe_oi": row.get("OPEN_INT_pe", 0),
                "pe_chng_oi": row.get("CHANGE_IN_OI_pe", 0),
                "pe_volume": row.get("TOT_TRADED_QTY_pe", 0),
            })

        total_ce_oi = sum(r["ce_oi"] for r in chain)
        total_pe_oi = sum(r["pe_oi"] for r in chain)
        total_ce_vol = sum(r["ce_volume"] for r in chain)
        total_pe_vol = sum(r["pe_volume"] for r in chain)

        atm_strike = round(underlying / 50) * 50
        atm_row = next((r for r in chain if r["strike"] == atm_strike), None)
        atm_ce_price = atm_row["ce_close"] if atm_row else 0
        atm_pe_price = atm_row["pe_close"] if atm_row else 0
        atm_ce_vol = atm_row["ce_volume"] if atm_row else 0
        atm_pe_vol = atm_row["pe_volume"] if atm_row else 0

        return {
            "date": date,
            "underlying": underlying,
            "chain": chain,
            "pcr_oi": round(total_pe_oi / total_ce_oi, 2) if total_ce_oi else 0,
            "pcr_vol": round(total_pe_vol / total_ce_vol, 2) if total_ce_vol else 0,
            "atm_ce_price": atm_ce_price,
            "atm_pe_price": atm_pe_price,
            "atm_ce_volume": atm_ce_vol,
            "atm_pe_volume": atm_pe_vol,
        }


    def fetch_participant_oi(self, date: str) -> Optional[dict]:
        try:
            poi = derivatives.participant_wise_open_interest(date)
            if poi.empty:
                return None
            fii = poi[poi["Client Type"] == "FII"]
            if fii.empty:
                return None
            fii = fii.iloc[0]
            return {
                "fii_call_long": int(fii.get("Option Index Call Long", 0)),
                "fii_call_short": int(fii.get("Option Index Call Short", 0)),
                "fii_put_long": int(fii.get("Option Index Put Long", 0)),
                "fii_put_short": int(fii.get("Option Index Put Short", 0)),
                "fii_fut_long": int(fii.get("Future Index Long", 0)),
                "fii_fut_short": int(fii.get("Future Index Short", 0)),
            }
        except Exception:
            return None

    def get_pcr_timeseries(self, symbol: str, instrument: str = "OPTIDX", days: int = 60) -> pd.DataFrame:
        df = self.fetch_option_data(symbol, instrument, period_days=days)
        dates = sorted(df["TIMESTAMP"].unique())
        rows = []
        for d in dates:
            snap = self.build_daily_snapshot(df, d)
            if snap:
                rows.append(snap)
        return pd.DataFrame(rows)


options_data_loader = OptionsDataLoader()
