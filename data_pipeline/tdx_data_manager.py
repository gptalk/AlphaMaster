"""编排 ParquetStore + TdxDataFetcher，给上层一个干净的入口。

缓存策略：本地有缓存 → 直接 load；否则 fetch + save。
"""
from __future__ import annotations
from pathlib import Path
import pandas as pd
import torch

from .parquet_store import ParquetStore
from .tdx_fetcher import TdxDataFetcher


class TdxDataManager:
    def __init__(self,
                 store: ParquetStore | None = None,
                 fetcher: TdxDataFetcher | None = None,
                 root: Path | str | None = None):
        if store is not None:
            self.store = store
        elif root is not None:
            self.store = ParquetStore(root=Path(root))
        else:
            self.store = ParquetStore()
        self.fetcher = fetcher or TdxDataFetcher()

    def get_or_fetch(self, code: str, start: str, end: str,
                     period: str = "1d",
                     force_refresh: bool = False) -> pd.DataFrame:
        if not force_refresh and self.store.exists(code, period):
            cached = self.store.load(code, period)
            if cached is not None and len(cached) > 0:
                ts_start = pd.to_datetime(start)
                ts_end = pd.to_datetime(end)
                mask = (cached["time"] >= ts_start) & (cached["time"] <= ts_end)
                return cached[mask].reset_index(drop=True)
        df = self.fetcher.fetch_ohlcv(code, start, end, period=period)
        self.store.save(code, df, period=period)
        return df

    def bulk_ensure_cached(self, codes: list[str], start: str, end: str,
                           period: str = "1d") -> dict[str, str]:
        status: dict[str, str] = {}
        need_fetch: list[str] = []
        for code in codes:
            if self.store.exists(code, period):
                status[code] = "cached"
            else:
                status[code] = "missing"
                need_fetch.append(code)
        if not need_fetch:
            return status
        result = self.fetcher.fetch_universe(need_fetch, start, end, period=period)
        for code, df in result.items():
            if code == "_failed":
                continue
            self.store.save(code, df, period=period)
            status[code] = "fetched"
        for code, _reason in result.get("_failed", []):
            status[code] = "failed"
        return status

    def build_training_matrix(self, code: str, start: str, end: str,
                              period: str = "1d"
                              ) -> tuple[torch.Tensor, pd.DataFrame]:
        df = self.get_or_fetch(code, start, end, period=period)
        ohlcv = df[["open", "high", "low", "close", "volume"]].to_numpy(dtype="float32")
        tensor = torch.from_numpy(ohlcv).unsqueeze(0)
        return tensor, df

    def close(self) -> None:
        self.fetcher.close()
